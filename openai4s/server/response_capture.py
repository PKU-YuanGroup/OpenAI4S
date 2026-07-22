"""Collect what routes really return, and freeze it.

Nothing here runs in production, and nothing in the gateway was changed to
support it. The capture wraps ``make_handler``'s ``_api`` from the outside and
watches the response body on its way to ``_json``. That placement is not
incidental: the gateway tests replace ``handler._json`` with their own
collector, so a hook installed *inside* the real ``_json`` would have missed
almost every route the suite exercises while looking like it worked.

The grouping key is ``METHOD /route/pattern``, not the concrete path a request
happened to use. ``/frames/f-abc123/kernel`` and ``/frames/f-def456/kernel``
are one route observed twice, and treating them as two would produce a file
that grows with the fixtures rather than with the surface.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from openai4s.server import contract
from openai4s.server.response_schema import infer, merge, validate

#: Where the frozen shapes live. A file in the repo, so a change to what a
#: route returns arrives as a reviewable diff rather than as a surprise in
#: someone's client.
ARTIFACT = Path(__file__).resolve().parents[2] / "docs" / "response-schemas.json"

SCHEMA_VERSION = 1

#: How many incompatibilities to name per route before summarising the rest.
#: One structural change can break dozens of nested fields, and a wall of them
#: buries the first one, which is usually the cause of all the others.
_MAX_REPORTED = 6


def specificity(route: str) -> int:
    """How much of a route is fixed text rather than wildcard.

    Ordering candidates by raw length picks the wrong one: the inventory holds
    the catch-all ``/frames/([^/]+)(?:/.*)?`` alongside the specific
    ``/frames/([^/]+)/kernel``, and the catch-all is the longer string. Both
    fullmatch a kernel path, so length would file every sub-route's shape under
    the catch-all. Literal characters outside any group are the honest measure.
    """
    literal = 0
    depth = 0
    for char in route:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and char not in "?*+.[]{}|^$\\":
            literal += 1
    return literal


def _patterns() -> list[tuple[re.Pattern[str] | None, str]]:
    """Every known route as a matcher, most specific first.

    The gateway also dispatches some routes by prefix, so a path that no
    pattern fullmatches may still belong to a known route; `route_for` falls
    back to that only after every pattern has failed.
    """
    compiled: list[tuple[re.Pattern[str] | None, str]] = []
    for route in contract.http_routes():
        try:
            compiled.append((re.compile(f"^{route}$"), route))
        except re.error:
            # Not a usable pattern; it can still serve as a literal prefix.
            compiled.append((None, route))
    compiled.sort(key=lambda item: (specificity(item[1]), len(item[1])), reverse=True)
    return compiled


def route_for(path: str, patterns=None) -> str | None:
    """The route pattern a concrete path came from, or None if unrecognised."""
    if not path:
        return None
    known = patterns if patterns is not None else _patterns()
    bare = str(path).split("?", 1)[0]
    for matcher, route in known:
        if matcher is not None and (matcher.match(bare) or matcher.match(path)):
            return route
    # Prefix dispatch is a real form in the gateway, so a path that no pattern
    # fullmatches may still belong to a known route. The prefix has to end on a
    # segment boundary: without that, "/frames" claims "/frameshift" and every
    # unrecognised path gets filed under some route and counted as covered --
    # and coverage is the number this whole exercise reports.
    for _matcher, route in known:
        if not bare.startswith(route):
            continue
        if len(bare) == len(route) or bare[len(route)] == "/":
            return route
    return None


class Recorder:
    """Accumulates one schema per ``METHOD route`` and status class."""

    def __init__(self) -> None:
        self.shapes: dict[str, dict[str, Any]] = {}
        self.counts: dict[str, int] = {}
        self.unmatched: set[str] = set()
        self._patterns = _patterns()

    def observe(self, method: str, path: str, code: int, body: Any) -> None:
        if not isinstance(body, dict) and not isinstance(body, list):
            # Only JSON documents have a shape worth freezing.
            return
        route = route_for(path, self._patterns)
        if route is None:
            self.unmatched.add(f"{method} {path}")
            return
        # Success and failure are different contracts for the same route.
        # Merging them yields a schema in which every field is optional, which
        # is the same as having no schema at all.
        family = "ok" if int(code) < 400 else "error"
        key = f"{method} {route} [{family}]"
        observed = infer(body)
        existing = self.shapes.get(key)
        self.shapes[key] = observed if existing is None else merge(existing, observed)
        self.counts[key] = self.counts.get(key, 0) + 1

    def document(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "note": (
                "Captured from real responses, not written by hand. Regenerate "
                "with scripts/capture_response_schemas.py. A route absent here "
                "is one no offline test exercises: that is a coverage fact, not "
                "permission to change it freely."
            ),
            # Shapes only. The observation counts stay on the recorder and are
            # printed by the capture script: writing them here would make every
            # unrelated new test that happens to touch a route produce a diff,
            # and the file is worth reviewing only when a shape moved.
            "routes": {
                key: {"schema": self.shapes[key]} for key in sorted(self.shapes)
            },
        }


def install(gateway_module, recorder: Recorder):
    """Wrap ``make_handler`` so every ``_api`` call reports its response.

    Returns the original factory so a caller can undo this. The wrapper reads
    ``self._json`` at call time rather than at class time, because that is when
    a test's own collector is in place.
    """
    original = gateway_module.make_handler

    def make_handler(*args, **kwargs):
        handler_class = original(*args, **kwargs)
        inner_api = handler_class._api

        def _api(self, method, sub):
            reply = self._json
            # Whether the caller installed its own collector as an instance
            # attribute, so the restore puts back exactly what it found.
            had_own = "_json" in self.__dict__

            def observing(value, code=200):
                try:
                    recorder.observe(method, sub, code, value)
                except Exception:  # noqa: BLE001 - never break a response
                    pass
                return reply(value, code)

            self._json = observing
            try:
                return inner_api(self, method, sub)
            finally:
                if had_own:
                    self._json = reply
                else:
                    self.__dict__.pop("_json", None)

        handler_class._api = _api
        return handler_class

    gateway_module.make_handler = make_handler
    return original


def load(path: Path | None = None) -> dict[str, Any]:
    target = Path(path) if path else ARTIFACT
    if not target.is_file():
        return {"schema_version": SCHEMA_VERSION, "routes": {}}
    try:
        return json.loads(target.read_text("utf-8"))
    except (OSError, ValueError):
        return {"schema_version": SCHEMA_VERSION, "routes": {}}


def save(document: dict[str, Any], path: Path | None = None) -> Path:
    target = Path(path) if path else ARTIFACT
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def check(observed: dict[str, Any], frozen: dict[str, Any]) -> list[str]:
    """How a fresh capture departs from the frozen artifact.

    Reports both directions. A route that gained a field and a route that lost
    one are both drift, and only a human can say which was intended, so this
    states the difference rather than deciding what it means.
    """
    problems: list[str] = []
    frozen_routes = frozen.get("routes") or {}
    observed_routes = observed.get("routes") or {}

    for key in sorted(set(observed_routes) - set(frozen_routes)):
        problems.append(f"{key}: newly covered, not in the frozen artifact")
    for key in sorted(set(frozen_routes) - set(observed_routes)):
        problems.append(f"{key}: frozen but no longer observed")

    for key in sorted(set(frozen_routes) & set(observed_routes)):
        expected = frozen_routes[key].get("schema") or {}
        actual = observed_routes[key].get("schema") or {}
        if expected == actual:
            continue
        # Say which way it moved, and for a break say exactly which field. A
        # bare "shape changed (BREAKING)" on a response that nests an
        # environment snapshot ten levels deep tells a reader that something is
        # wrong and nothing about where, which is a bug report nobody can act
        # on -- and this gate's whole job is to be actionable.
        breaks = check_compatible(expected, actual)
        if not breaks:
            problems.append(f"{key}: shape changed (additive)")
            continue
        detail = "; ".join(breaks[:_MAX_REPORTED])
        if len(breaks) > _MAX_REPORTED:
            detail += f"; (+{len(breaks) - _MAX_REPORTED} more)"
        problems.append(f"{key}: shape changed (BREAKING) -- {detail}")
    return problems


def check_compatible(frozen: dict[str, Any], observed: dict[str, Any]) -> list[str]:
    """Ways ``observed`` breaks a client written against ``frozen``.

    Additive change -- a new optional field -- is not a break and is not
    reported. A removed guarantee or a changed type is.
    """
    problems: list[str] = []
    frozen_types = set(
        frozen.get("type")
        if isinstance(frozen.get("type"), list)
        else [frozen.get("type")]
    )
    observed_types = set(
        observed.get("type")
        if isinstance(observed.get("type"), list)
        else [observed.get("type")]
    )
    if frozen_types != observed_types and not observed_types <= frozen_types:
        problems.append(
            f"type widened from {sorted(frozen_types)} to {sorted(observed_types)}"
        )

    if frozen.get("keys") == "data" or observed.get("keys") == "data":
        # A map has no per-key guarantees to lose, so the only promise that can
        # break is the shape of its values. Comparing a map against a record
        # would otherwise report every key of one as dropped from the other.
        frozen_values = frozen.get("values")
        observed_values = observed.get("values")
        if frozen_values and observed_values:
            for problem in check_compatible(frozen_values, observed_values):
                problems.append(f"[*].{problem}")
        return problems

    lost = set(frozen.get("required") or ()) - set(observed.get("required") or ())
    for key in sorted(lost):
        problems.append(f"no longer guarantees {key!r}")

    frozen_props = frozen.get("properties") or {}
    observed_props = observed.get("properties") or {}
    for key in sorted(set(frozen_props) & set(observed_props)):
        for problem in check_compatible(frozen_props[key], observed_props[key]):
            problems.append(f"{key}.{problem}")
    for key in sorted(set(frozen_props) - set(observed_props)):
        problems.append(f"dropped field {key!r}")

    frozen_items = frozen.get("items")
    observed_items = observed.get("items")
    if frozen_items and observed_items:
        for problem in check_compatible(frozen_items, observed_items):
            problems.append(f"[].{problem}")
    return problems


__all__ = [
    "ARTIFACT",
    "Recorder",
    "check",
    "check_compatible",
    "install",
    "load",
    "route_for",
    "save",
    "validate",
]
