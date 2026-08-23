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

import base64
import hashlib
import json
import re
import tempfile
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from openai4s.server import contract

# Safe at module scope: `errors` imports nothing from this package, which is
# the whole reason it was carved out of the gateway. The lazy import inside
# `drive_all_routes` below is about the *gateway*, not about this.
from openai4s.server.errors import public_failure
from openai4s.server.response_schema import infer, merge, validate

#: Where the frozen shapes live. A file in the repo, so a change to what a
#: route returns arrives as a reviewable diff rather than as a surprise in
#: someone's client.
ARTIFACT = Path(__file__).resolve().parents[2] / "docs" / "response-schemas.json"

SCHEMA_VERSION = 1

#: Subtrees that describe the machine rather than the API. A response may
#: embed a live kernel environment snapshot, and inside it the sandbox block
#: reports what the host can actually enforce: on a developer's macOS it is
#: `backend: "seatbelt", warning: null`, and on a CI runner with no bubblewrap
#: it is `backend: null, warning: "<why>"`. The *types* legitimately differ, so
#: freezing that shape pins the machine the capture ran on and calls every
#: other machine a breaking change. Recorded opaquely: the field is still known
#: to exist, its interior is not a promise.
#:
#: Add to this only for a subtree whose type varies with the host. It is not a
#: place to park a route whose shape is merely inconvenient -- that is what the
#: coverage number is for.
#: Property names whose *shape* describes the machine rather than the API.
#:
#: `sandbox` was the first: its field types differ between a host that can
#: enforce a sandbox and one that cannot. `default_host` is the same thing one
#: level down — the registry documents it as ``"<alias>" | null``, so which of
#: the two you observe depends on whether this machine has an ssh alias
#: configured, not on the contract. Freezing it as `string` pinned the
#: developer's ssh config and told CI, which has none, that the API had made a
#: breaking change.
#: `gpu_name` and `cuda_version` are the third instance of the same thing, and
#: the local GPU probe is as host-dependent as a field gets: on a machine with
#: nvidia-smi they are strings, and on one without — every CI runner here —
#: they are null. Freezing either answer calls the other machine a breaking
#: change. `_detect_gpu` was made to return a constant *key* set for the same
#: reason (see gateway.py), because eliding a value cannot rescue a shape whose
#: required list moves; these two are the part that genuinely varies.
#:
#: Both names are unique to `GET /compute/gpu` in the frozen document, which is
#: what makes eliding them by name safe. `note`, which also appears there, is
#: deliberately *not* here: it is shared with `/environments/status` and
#: `/kernel/packages`, and eliding by name is depth-blind, so listing it would
#: quietly drop two unrelated routes' contracts as well.
_MACHINE_STATE_KEYS = frozenset({"sandbox", "default_host", "gpu_name", "cuda_version"})

#: A different failure that looks like the one above, and must not be fixed the
#: same way.
#:
#: `merge` widens across observations, so a nullable field ends up `["null",
#: "string"]` *if the suite observes it both ways*. A field the suite only ever
#: sees in one state freezes as that state alone -- and the resulting entry
#: describes the run, not the API. `POST /example/session` was the first one
#: caught: its `error` is `str | None` straight from `last_error()`, but the
#: suite observed it exactly once, immediately after `start()` cleared the error
#: and spawned a thread that may or may not have failed yet. Both threads then
#: contend for one lock, so the field froze as `string` on Linux/CI and `null`
#: on macOS, and the gate called the other machine a breaking change.
#:
#: The fix is a test that exercises the other state, never an entry here. These
#: fields are not host-dependent -- they are *under-observed*, and eliding them
#: would delete a real guarantee to silence a coverage gap. A survey at the time
#: of writing found 94 fields frozen as null-only across 43 route entries
#: (`frame_id` on both `/example/session` verbs among them); each is a latent
#: version of this, waiting for the first capture that sees the populated state.

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


def elide_machine_state(schema: dict[str, Any]) -> dict[str, Any]:
    """Replace machine-state subtrees with an opaque object.

    Applied when the document is written, not while observing, so merging never
    sees the marker and two captures of the same route still generalise
    normally before their host-specific parts are dropped.
    """
    if not isinstance(schema, dict):
        return schema
    result: dict[str, Any] = {
        key: value
        for key, value in schema.items()
        if key not in ("properties", "items", "values")
    }
    properties = schema.get("properties")
    if isinstance(properties, dict):
        result["properties"] = {
            key: (
                {"type": "object", "machine_state": True}
                if key in _MACHINE_STATE_KEYS
                else elide_machine_state(value)
            )
            for key, value in properties.items()
        }
    for nested in ("items", "values"):
        child = schema.get(nested)
        if isinstance(child, dict):
            result[nested] = elide_machine_state(child)
    return result


class Recorder:
    """Accumulates one schema per ``METHOD route`` and status class."""

    def __init__(self) -> None:
        self.shapes: dict[str, dict[str, Any]] = {}
        #: Non-JSON answers, keyed by route: what kind of thing came back, with
        #: which statuses and content types. A stream and an unexercised route
        #: are not the same fact and must not look alike.
        self.kinds: dict[str, dict[str, set]] = {}
        self.counts: dict[str, int] = {}
        self.unmatched: set[str] = set()
        #: ``METHOD route`` -> the exception that stopped it, for verbs that
        #: raised something the dispatcher would not have raised. Kept because
        #: swallowing them silently is how a route came to be published as
        #: something it is not: the crashing verb records nothing, its
        #: *unimplemented* siblings still record the dispatcher's 404, and the
        #: route then looks both covered and JSON-shaped. Not part of
        #: ``document()`` — it describes this run, not the API.
        self.drive_failures: dict[str, str] = {}
        #: Set while a test drives routes against stubbed services. Their
        #: responses are fabrications, and this file's entire claim is that it
        #: was captured from real ones -- a made-up shape published as a promise
        #: is worse than an absent one, because it gets believed.
        self.paused = False
        self._patterns = _patterns()

    def _declared(self, route: str | None) -> str | None:
        """A driver's declared route, but only if the inventory has that entry.

        Declaring the entry is the only way to tell two entries apart when both
        match the same concrete path -- ``/artifacts/{id}`` is matched by
        ``/artifacts/(.+)`` and ``/artifacts/([^/]+)``, and only the first one
        serves the bytes.

        But the declaration is a hand-written string in a driver table, and a
        stale or mistyped one names no entry at all. Honouring that verbatim
        files a real observation under a route the contract does not have: the
        entry that served the response then looks like it never answered, and
        the coverage line still says 152/152 because the count is of entries
        driven, not of entries attributed. Falling back to matching the path is
        what the recorder did before any driver declared anything, so an
        unrecognised declaration costs nothing.
        """
        if route is None:
            return None
        return route if any(route == known for _, known in self._patterns) else None

    def observe(
        self,
        method: str,
        path: str,
        code: int,
        body: Any,
        route: str | None = None,
    ) -> None:
        if self.paused:
            return
        if not isinstance(body, dict) and not isinstance(body, list):
            # Only JSON documents have a shape worth freezing.
            return
        # A driver that knows which route it is probing says so. Re-deriving it
        # from the path cannot distinguish two inventory entries that match the
        # same concrete path -- `/frames/([^/]+)/shares` and
        # `/frames/[^/]+/shares` are both real entries, and only one of them
        # can ever win a lookup, so the other would be permanently unattributed
        # and counted as uncovered forever.
        route = self._declared(route) or route_for(path, self._patterns)
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
        # A JSON body is also a *kind* of answer, with a status. Recording the
        # shape without the status left the contract entry for a JSON-only
        # route claiming no status code at all.
        self._note(method, route, int(code), JSON, "application/json")

    def observe_raw(
        self,
        method: str,
        path: str,
        code: int,
        content_type: str,
        length: int,
        route: str | None = None,
    ) -> None:
        """Record a response that is not a JSON document.

        A route that streams, redirects, or hands back bytes still has a
        contract — it is just not a schema. Recording only ``_json`` meant those
        routes were indistinguishable from routes nothing exercised, so the
        coverage number could never reach the surface it was measuring.
        """
        if self.paused:
            return
        route = self._declared(route) or route_for(path, self._patterns)
        if route is None:
            self.unmatched.add(f"{method} {path}")
            return
        self._note(
            method,
            route,
            int(code),
            _kind_of(code, content_type, length),
            str(content_type or "").split(";")[0].strip(),
        )

    def _note(
        self, method: str, route: str, code: int, kind: str, content_type: str
    ) -> None:
        record = self.kinds.setdefault(
            f"{method} {route}",
            {"kinds": set(), "statuses": set(), "content_types": set()},
        )
        record["kinds"].add(kind)
        record["statuses"].add(int(code))
        if content_type:
            record["content_types"].add(content_type)

    def contracts(self) -> dict[str, dict[str, Any]]:
        """One contract record per route, whichever way it answers."""
        merged: dict[str, dict[str, Any]] = {}
        for key, record in self.kinds.items():
            target = merged.setdefault(
                key, {"kinds": set(), "statuses": set(), "content_types": set()}
            )
            for field in ("kinds", "statuses", "content_types"):
                target[field] |= record[field]
        return {
            key: {
                "kinds": sorted(value["kinds"]),
                "statuses": sorted(value["statuses"]),
                "content_types": sorted(value["content_types"]),
            }
            for key, value in sorted(merged.items())
        }

    def shapes_snapshot(self) -> dict[str, Any]:
        """The raw, un-elided shapes, for reassembling a split run.

        Un-elided on purpose. `document()` runs `elide_machine_state` over each
        shape, and eliding a share before merging it is not the same operation
        as eliding the merge: the elision replaces whole subtrees, so a field
        that only one share saw could be elided out of that share and then have
        nothing left to widen against. Shares carry what `observe` produced, and
        the elision happens once, at the end, exactly where it does in a
        single-process run.
        """
        return {key: self.shapes[key] for key in sorted(self.shapes)}

    def absorb(self, shapes: Mapping[str, Any]) -> None:
        """Merge another process's shapes in, exactly as `observe` would have.

        The same `merge` call, on the same values, in a deterministic order.
        `observe` widens each new observation into what it already had; this
        widens each share into what it already has. Two observations of one
        route reach the same schema whether they landed in one process or two.
        """
        for key in sorted(shapes):
            observed = shapes[key]
            existing = self.shapes.get(key)
            self.shapes[key] = (
                observed if existing is None else merge(existing, observed)
            )

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
                key: {"schema": elide_machine_state(self.shapes[key])}
                for key in sorted(self.shapes)
            },
        }


#: Response kinds. Every route answers with exactly one of these, and a route
#: that answers with none of them is one nothing has exercised.
JSON = "json"
STREAM = "stream"
REDIRECT = "redirect"
EMPTY = "empty"
BINARY = "binary"


def _kind_of(code: int, content_type: str, length: int) -> str:
    """Classify by what actually came back, never by what a route is named.

    Derived rather than declared: a hand-maintained list of "these routes
    stream" drifts the moment a route changes, and drifts silently, which is
    the failure this whole artifact exists to avoid.
    """
    if 300 <= int(code) < 400:
        return REDIRECT
    ctype = str(content_type or "").split(";")[0].strip().lower()
    if ctype == "text/event-stream":
        return STREAM
    if ctype == "application/json":
        return JSON
    if int(length) == 0:
        return EMPTY
    return BINARY


#: The correlation id the route driver presents. Fixed rather than generated:
#: the artifacts it produces are committed and diffed, so anything varying per
#: run would show up as drift on every capture.
_CAPTURE_REQUEST_ID = "contract-capture"


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
                    # The body the dispatcher SENDS, not the one it was handed.
                    # `_json` enriches every 4xx/5xx with code/status/request_id,
                    # and observing before that ran is why `request_id` appeared
                    # nowhere in either frozen artifact: the published contract
                    # described a body the server does not send. Same callable
                    # the dispatcher uses, so the two cannot drift again.
                    recorder.observe(
                        method,
                        sub,
                        code,
                        public_failure(
                            value,
                            code,
                            # `_route` assigns a correlation id before any guard
                            # can answer, so no real response carries a null
                            # one. Tests that build a handler with
                            # `object.__new__` skip that, and letting their ""
                            # through froze `request_id` as nullable on 17
                            # routes -- publishing a property of the harness as
                            # a property of the API.
                            getattr(self, "_correlation_id", "") or _CAPTURE_REQUEST_ID,
                        ),
                        # Absent for a real server request, where matching the
                        # path is the only option; present for a probe, which
                        # named its entry up front.
                        route=getattr(self, "_capture_route", None),
                    )
                except Exception:  # noqa: BLE001 - never break a response
                    pass
                return reply(value, code)

            raw = self._send
            had_own_send = "_send" in self.__dict__

            def observing_send(code, body, ctype, extra=None):
                try:
                    recorder.observe_raw(
                        method,
                        sub,
                        code,
                        ctype,
                        len(body or b""),
                        route=getattr(self, "_capture_route", None),
                    )
                except Exception:  # noqa: BLE001 - never break a response
                    pass
                return raw(code, body, ctype, extra)

            self._json = observing
            self._send = observing_send
            try:
                return inner_api(self, method, sub)
            finally:
                if had_own:
                    self._json = reply
                else:
                    self.__dict__.pop("_json", None)
                if had_own_send:
                    self._send = raw
                else:
                    self.__dict__.pop("_send", None)

        handler_class._api = _api
        return handler_class

    gateway_module.make_handler = make_handler
    return original


#: The verbs every route is probed with. A route that implements only GET
#: answers the rest with a 404/405, which is that pair's contract.
PROBE_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def _sample(pattern: str, index: int = 0) -> tuple[str, int]:
    """Walk a route pattern and build one string it matches.

    Substitution could not do this. A pattern like
    ``/frames/([^/]+)/(?:action-timeline|…|recovery(?:/actions…)?|…)`` has an
    alternation whose branches contain nested optional groups, and stripping
    ``(?:[^)]*)`` stops at the *first* closing parenthesis — leaving a mangled
    remainder that matches nothing. The path then routed nowhere, the handler
    answered 404 because there was no such endpoint, and that 404 was recorded
    as this route's observed contract.

    The first alternative is taken and optional groups are omitted, which
    yields the shortest real path through the pattern.
    """
    branches: list[list[str]] = [[]]
    while index < len(pattern):
        char = pattern[index]
        if char == ")":
            break
        if char == "|":
            branches.append([])
            index += 1
            continue
        if char == "(":
            inner_start = index + 1
            if pattern.startswith("?:", inner_start):
                inner_start += 2
            inner, index = _sample(pattern, inner_start)
            if index < len(pattern) and pattern[index] == ")":
                index += 1
            if index < len(pattern) and pattern[index] in "?*":
                inner = ""  # optional: the shortest match omits it
                index += 1
            elif index < len(pattern) and pattern[index] == "+":
                index += 1
            branches[-1].append(inner)
            continue
        if char == "[":
            close = pattern.find("]", index + 1)
            if close < 0:  # pragma: no cover - not a pattern we emit
                branches[-1].append(char)
                index += 1
                continue
            klass = pattern[index : close + 1]
            index = close + 1
            if index < len(pattern) and pattern[index] in "+*?":
                index += 1
            branches[-1].append("1" if "0-9" in klass else "probe-id")
            continue
        if char == ".":
            index += 1
            if index < len(pattern) and pattern[index] in "+*?":
                index += 1
            branches[-1].append("probe-id")
            continue
        if char == "\\":
            branches[-1].append(pattern[index + 1 : index + 2])
            index += 2
            continue
        branches[-1].append(char)
        index += 1
    return "".join(branches[0]), index


def concrete_path(route: str) -> str:
    """A concrete path the route pattern matches.

    Placeholders are filled with an id shaped like the ones the app mints, so a
    handler parses the segment and then fails on the *lookup* — a 404 for a
    missing resource is a contract, a 500 from an unparseable id is not.
    """
    return _sample(route)[0] or "/"


def unroutable(route: str) -> bool:
    """Does this entry fail to describe a path that reaches it?

    A route whose own concretisation does not match it drives nothing: the
    request lands on no handler, and the 404 that comes back is a fact about
    the probe rather than about the surface. Counting it as covered inflates
    the number that is supposed to mean "every endpoint has an observed
    response".
    """
    if not contract.is_complete_matcher(route):
        return True
    try:
        return re.fullmatch(route, concrete_path(route)) is None
    except re.error:  # pragma: no cover - is_complete_matcher already compiled
        return True


def _probe_handler(
    recorder: "Recorder",
    handler_class,
    method: str,
    path: str,
    route: str,
    headers: dict[str, str] | None = None,
    query: dict[str, list[str]] | None = None,
    body: dict | None = None,
):
    """A handler whose three response writers report into ``recorder``.

    Shared by the parameterless sweep and the seeded pass below, because a
    second copy of "how a probe answers" is how one of them comes to record a
    route the other cannot.
    """
    handler = object.__new__(handler_class)
    # The inventory entry this probe means to exercise. Without it the
    # recorder re-derives the entry by matching the *path*, and several
    # frame sub-resources are matched by two entries at once -- the specific
    # one and the broad workbench alternation. Only one can win a lookup, so
    # `/frames/<id>/checkpoints` had its real 200 filed under the broad entry
    # and its own entry kept only the sweep's 404: a success the surface
    # really serves, published as if it did not exist. The driver knows which
    # entry it is driving; the path does not.
    handler._capture_route = route
    handler._query = lambda: dict(query or {})
    handler._body = lambda: dict(body or {})
    # The driver calls `_api` directly, so the token gate in `_route`
    # is not on its path today. The credential is presented anyway: the
    # gate is one refactor from moving, and a driver that only works
    # while authentication happens to be elsewhere would fail as a wall
    # of 401s at exactly the moment the contract mattered most.
    handler.headers = dict(headers or {})
    # Deterministic and non-empty. `_route` gives every real request a
    # correlation id (an inbound X-Request-Id, else `new_correlation_id()`),
    # so `request_id` is a string on the wire and never null. Leaving
    # this "" made `public_failure` emit null, and the frozen schema
    # would then have declared the field's type as `null` -- describing
    # the driver rather than the server. A fixed literal keeps the
    # artifact byte-identical across runs.
    handler._correlation_id = _CAPTURE_REQUEST_ID
    handler._last_status = 0
    handler._json = lambda value, code=200: recorder.observe(
        method,
        path,
        code,
        public_failure(value, code, _CAPTURE_REQUEST_ID),
        route=route,
    )
    handler._send = lambda code, body, ctype, extra=None: recorder.observe_raw(
        method, path, code, ctype, len(body or b""), route=route
    )

    # The third writer, and the one whose absence published a lie.
    # `_stream_file` builds its own headers straight on the
    # BaseHTTPRequestHandler instead of going through `_json`/`_send`,
    # so on the synthetic handler built here it raised inside
    # `send_response` (no `requestline`) and the caller's `except Exception`
    # swallowed it. The route was not left blank, though: the four
    # *unimplemented* verbs still produced the dispatcher's 404, so both
    # `artifacts.zip` routes were published as `kinds: ["json"],
    # statuses: [404]` — a download endpoint documented as a JSON error,
    # with the coverage gate reporting it as covered. It is also why no
    # route ever recorded STREAM or BINARY despite the module deriving
    # both kinds.
    #
    # Mirrors the real method exactly, including its own 404: a file it
    # cannot open is a JSON error there too, not a stream.
    def _stub_stream(file_path, ctype, extra=None):
        try:
            size = file_path.stat().st_size
        except OSError:
            recorder.observe(method, path, 404, {"error": "not found"}, route=route)
            return
        recorder.observe_raw(method, path, 200, ctype, size, route=route)

    handler._stream_file = _stub_stream
    return handler


#: What a seeded capture writes. Fixed rather than generated: the artifacts it
#: feeds are committed and diffed, so anything varying per run is drift.
_CAPTURE_PROJECT = "contract-capture"
_CAPTURE_FILENAME = "capture.bin"
_CAPTURE_BYTES = b"contract-capture\n"
_CAPTURE_TEXT_FILENAME = "capture.txt"
_CAPTURE_HISTORY_FILENAME = "capture-history.txt"
# Fixed owner ids, so the captured projection is byte-identical across runs
# and the fixture can assert which ticket it is looking at.
_CAPTURE_OWNER_ID = "capture-owner"
_CAPTURE_QUEUED_ID = "capture-queued"
_CAPTURE_TEXT_BYTES = b"contract capture, editable\n"
# Fixed, so a re-capture produces a byte-identical artifact. Long enough
# to satisfy the route's own 24-character floor for a client-supplied id.
_CAPTURE_RESERVATION = "resv-contractcapture000000001"
#: Fixed, so the resolved decision is byte-identical across runs.
_CAPTURE_DECISION_ID = "dec-contractcapture0001"
#: How long the seeded pass waits for the turns its 202s accepted. Not a budget
#: for the turn -- it fails immediately without a provider -- but a ceiling, so
#: a gate can never block on one.
_SEEDED_JOB_WAIT_S = 30.0


#: The two listings whose entries carry `python_version`, driven a second time
#: below with an environment that has no interpreter.
_ENVIRONMENT_LISTING_ROUTES = ("/environments", "/frames/([^/]+)/environments")


def _drive_environment_states(
    recorder: "Recorder",
    handler_class,
    authenticated_headers: dict[str, str] | None = None,
) -> None:
    """Drive the environment listings again with an R-only environment present.

    `python_version` is `str | None` -- None for an environment that has no
    interpreter, which is exactly what an R-only conda env is, and
    `setup.sh --with-kernel-envs` builds one. Which state the suite observed was
    therefore decided by the host: a CI runner has no such env and froze
    `string`, a developer machine with an R env observes `["null", "string"]`,
    and the gate calls whichever ran second a breaking change. Test *ordering*
    was hiding it too -- `tests/test_environments.py` leaves a populated
    discovery cache behind, so the sweep saw the null only when it ran in a
    process that file had not already touched. Under `--dist loadfile` that
    stopped being alphabetical, which is how this surfaced.

    So the other state is exercised here instead of left to the host. That is
    what the note on `_MACHINE_STATE_KEYS` prescribes for an under-observed
    field: eliding `python_version` would delete a real guarantee to silence a
    coverage gap, and freezing one host's answer publishes the host rather than
    the API.

    Arranged, not fabricated. The response is whatever the real handler makes of
    a real `Environment`, through the same `to_dict` every other listing goes
    through; nothing here supplies a response body. Only *discovery* is
    arranged -- and discovery is the host state this exists to stop depending
    on. The real environments stay in the list, so the pass adds a state rather
    than replacing the machine's.
    """
    from openai4s.kernel import environments as envmod

    real_discover = envmod.discover_environments
    without_an_interpreter = envmod.Environment(
        name="r-only-probe",
        language="r",
        root=Path(tempfile.gettempdir()) / "openai4s-r-only-probe",
        python=None,
        rscript=None,
        is_conda=True,
        builtin=False,
    )

    def _with_an_r_only_env(force: bool = False):
        return list(real_discover(force=force)) + [without_an_interpreter]

    envmod.discover_environments = _with_an_r_only_env
    try:
        for route in _ENVIRONMENT_LISTING_ROUTES:
            path = concrete_path(route)
            handler = _probe_handler(
                recorder, handler_class, "GET", path, route, authenticated_headers
            )
            try:
                handler._api("GET", path)
            except Exception:  # noqa: BLE001
                # The parameterless sweep already recorded this route; this pass
                # only adds the state that one could not reach, so a failure
                # here withholds nothing the contract did not already have.
                continue
    finally:
        envmod.discover_environments = real_discover


def _drive_seeded_downloads(
    recorder: "Recorder",
    handler_class,
    runner,
    headers: dict[str, str] | None = None,
) -> None:
    """Record the success of routes the parameterless sweep can only refuse.

    A notebook export, a Session package and an artifact download each need
    something to serve, so against a probe id every one of them 404s. That left
    them worse than uncovered: the four unimplemented verbs still recorded the
    dispatcher's 404, so a download endpoint was published as
    `kinds: ["json"], statuses: [404]` — a contract describing only how the
    route refuses — and the coverage gate counted it.

    The same shape reaches beyond downloads, which is why this is no longer
    only about bytes. Any route whose success needs a row to exist first
    freezes as refusal-only, and the two here were found by auditing the
    contract for exactly that: `POST /frames/<id>/plan/approve` needs a draft
    plan, and `PATCH /memory/<id>` needs a memory. Nothing about either
    success was written down.

    These are real successes, not stubbed ones: a seeded draft really is
    approved, and the 202 is written before the turn it spawns — that turn then
    fails on the capture environment's absent provider, which is the same thing
    it does for every other route here and is not what is being recorded. A
    fabricated 200 would be worse than an absent one, because it would be
    believed.

    One frame, one file, one plan and one memory is the whole fixture, created
    *after* the sweep so the unknown-resource 404s it froze are unchanged. Both
    callers hand this a Store in a temp directory that is removed on the way
    out.

    A failure here is recorded, never swallowed: the seeded pass exists because
    a silently skipped success republishes the refusal-only contract it was
    written to replace.
    """
    store = runner.store
    # A real project row. Without one every project-scoped route answers
    # "project not found", which is a fact about the fixture rather than about
    # the surface.
    try:
        store.create_project(name="contract capture", project_id=_CAPTURE_PROJECT)
    except Exception:  # noqa: BLE001 - already present on a re-drive
        pass
    frame_id = store.new_frame(kind="turn", project_id=_CAPTURE_PROJECT, status="ready")
    artifact = runner.artifacts.upload(
        {
            "frame_id": frame_id,
            "project_id": _CAPTURE_PROJECT,
            "filename": _CAPTURE_FILENAME,
            "content_base64": base64.b64encode(_CAPTURE_BYTES).decode("ascii"),
        }
    )
    store.create_plan(
        frame_id=frame_id,
        project_id=_CAPTURE_PROJECT,
        title="capture",
        rationale="",
        confidence="high",
        steps=[{"id": "s1", "title": "step", "detail": "", "deliverables": []}],
        status="draft",
    )
    # A pending approval this session owns, so `POST /frames/<id>/decision`
    # has something real to resolve. Without it the route only ever recorded a
    # refusal.
    # Idempotent, like `create_project` above: `drive_all_routes` runs more
    # than once against one store during a schemas capture, and the id is fixed
    # so a re-capture stays byte-identical. Without this the second drive hit
    # `UNIQUE constraint failed: permission_requests.decision_id` and took four
    # coverage tests down with it -- which passed in isolation, because each of
    # those gets a fresh store.
    try:
        store.create_permission_request(
            decision_id=_CAPTURE_DECISION_ID,
            tool="save_artifact",
            target=_CAPTURE_FILENAME,
            root_frame_id=frame_id,
            frame_id=frame_id,
            project_id=_CAPTURE_PROJECT,
            # Stage 2 makes every allow-shaped restart decision prove the
            # immutable action envelope it resolves.  Seed a real envelope,
            # rather than asking the route driver to approve a legacy row
            # whose exact arguments can no longer be reconstructed.
            canonical_arguments={
                "filename": _CAPTURE_FILENAME,
                "content_sha256": hashlib.sha256(_CAPTURE_BYTES).hexdigest(),
            },
        )
    except Exception:  # noqa: BLE001 - already present on a re-drive
        pass
    memory = store.add_memory(
        content="capture memory", block="general", project_id=_CAPTURE_PROJECT
    )
    artifact_id = artifact["artifact_id"]
    version_id = store.get_artifact(artifact_id)["latest_version_id"]
    probes: tuple[tuple[str, str, dict[str, list[str]]], ...] = (
        # Both notebook forms. The default is a zip *bundle* and a named
        # language is an `.ipynb`; a contract saying only "binary" cannot tell a
        # client which of the two it asked for.
        (
            r"/frames/([^/]+)/notebook/export",
            f"/frames/{frame_id}/notebook/export",
            {},
        ),
        (
            r"/frames/([^/]+)/notebook/export",
            f"/frames/{frame_id}/notebook/export",
            {"language": ["python"]},
        ),
        (r"/frames/([^/]+)/session/export", f"/frames/{frame_id}/session/export", {}),
        # `(.+)`, because that is the entry that serves the bytes:
        # `gateway.py` dispatches the download from `/artifacts/(.+)` ->
        # `_serve_artifact`, while `/artifacts/([^/]+)` is DELETE and the
        # sub-resources. Both entries matched this concrete path, and the
        # capture used to record every probe twice -- once under the declared
        # route, once under whichever pattern the path matched first -- so the
        # frozen contract credited `binary` to both. Only one of them ever
        # served it.
        (r"/artifacts/(.+)", f"/artifacts/{artifact_id}", {}),
        (
            r"/artifacts/versions/([^/]+)",
            f"/artifacts/versions/{version_id}",
            {},
        ),
    )
    # Re-driven with a row present. The parameterless sweep runs before this
    # fixture exists, so it observes `memories: []` -- and an empty array
    # teaches the recorder nothing about what an element looks like, so the
    # published contract for a *populated* list was silently no contract at
    # all. `merge` widens across observations, so driving them again here adds
    # the element shape rather than replacing anything.
    probes = probes + (
        (r"/memory", "/memory", {"project_id": ["all"]}),
        (r"/memory/categories", "/memory/categories", {"project_id": ["all"]}),
        (r"/memory/context", "/memory/context", {"project_id": [_CAPTURE_PROJECT]}),
    )
    for route, path, query in probes:
        handler = _probe_handler(
            recorder, handler_class, "GET", path, route, headers, query
        )
        try:
            handler._api("GET", path)
        except Exception as error:  # noqa: BLE001
            recorder.drive_failures[f"GET {route} (seeded)"] = (
                f"{type(error).__name__}: {error}"
            )

    # A pin to send. Created here rather than reused, so the capture does not
    # depend on some other seeded step having left one `open`.
    annotation_id = runner.store.add_annotation(
        root_frame_id=frame_id,
        # The real Artifact, not a literal. A pin naming an artifact id that
        # does not exist is accepted here and then fails every path that
        # validates the pair -- a checkpoint capture takes it, and the restore
        # refuses the whole snapshot with "checkpoint annotation Artifact is
        # unavailable", which took revert/apply, revert/undo and branch
        # activation out of the capture entirely.
        artifact_id=artifact_id,
        artifact_name=_CAPTURE_FILENAME,
        rel_x=0.5,
        rel_y=0.5,
        body="capture pin",
    )["annotation_id"]

    writes: tuple[tuple[str, str, str, dict[str, list[str]], dict], ...] = (
        (
            "POST",
            r"/frames/([^/]+)/plan/(approve|resume|revise|discard)",
            f"/frames/{frame_id}/plan/approve",
            {},
            {},
        ),
        (
            "PATCH",
            r"/memory/([^/]+)",
            f"/memory/{memory['memory_id']}",
            {"project_id": [_CAPTURE_PROJECT]},
            {"content": "capture memory, corrected"},
        ),
        # A real `wait:false` turn carrying a real pin. The 202 grows two
        # fields when annotations ride along (`annotations`,
        # `annotation_reservation_id`), and a probe that never sends one
        # freezes the shape without them -- publishing a contract for a
        # response the server does not send in the case that needs it most.
        (
            "POST",
            r"/frames/([^/]+)/message",
            f"/frames/{frame_id}/message",
            {},
            {
                "request": "capture turn with a pinned comment",
                "wait": False,
                "annotation_ids": [annotation_id],
            },
        ),
    )
    for method, route, path, query, body in writes:
        handler = _probe_handler(
            recorder, handler_class, method, path, route, headers, query, body
        )
        try:
            handler._api(method, path)
        except Exception as error:  # noqa: BLE001
            recorder.drive_failures[f"{method} {route} (seeded)"] = (
                f"{type(error).__name__}: {error}"
            )

    _drive_session_surface(recorder, handler_class, runner, frame_id, artifact, headers)

    # A 202 means work was accepted, and the work really starts: `approve`
    # spawns a background turn that writes into the Store and the workspace.
    # The callers here run against a temp directory they delete on the way out,
    # and without this wait the delete raced a live thread and failed with
    # "Directory not empty" -- the capture succeeding and the harness crashing
    # afterwards. Bounded, because a hung turn must not hang the gate; the turn
    # itself fails fast on the capture environment's absent provider.
    deadline = time.monotonic() + _SEEDED_JOB_WAIT_S
    for job in list(getattr(runner, "_jobs", {}).values()):
        job.done.wait(max(0.0, deadline - time.monotonic()))


def _drive_session_surface(
    recorder: "Recorder",
    handler_class,
    runner,
    frame_id: str,
    artifact: dict,
    headers: dict[str, str] | None = None,
) -> None:
    """The session surface, asked against resources that exist.

    Most of this file's routes are frame-scoped, and the parameterless sweep
    probes them with an id shaped like a real one that names nothing. Every one
    of them answered 404, and 404 became their published contract -- so the
    artifact described how thirty routes refuse and said nothing about what
    they return. A client generating against it would have had no shape for the
    Timeline, the execution queue, the branch list or the recovery journal.

    The fixture is the one `_drive_seeded_downloads` already built, extended
    with the rows each group needs, and every response here is a real one: a
    real checkpoint over a real workspace tree, a real fork, a real revert and
    its real undo. Nothing is stubbed, because a fabricated 200 in a file whose
    entire claim is "captured from real responses" is worse than an absent one.

    What is deliberately *not* here, and why -- each needs something this
    process cannot honestly produce, so their refusal-only entries stand as the
    truth about what can be observed offline rather than being papered over:

    * `compute/tasks/<id>/refresh` contacts a real BYOC provider. There is no
      read-only probe; the refresh *is* the harvest.
    * `delegations/<id>/stop` and `/steer` resolve a live in-process delegation
      runner, which exists only during a real LLM turn.
    * `kernel/*` answers 403 under the deny-by-default posture, and that 403 is
      the contract for an unapproved session rather than a gap in it.
    * `artifacts/promote` needs a cell that really executed.
    """
    artifact_id = artifact["artifact_id"]
    store = runner.store

    # A text artifact, because `edit` refuses anything it cannot treat as text
    # and the octet-stream fixture above is deliberately opaque.
    text_path = runner.workspace_for(frame_id) / _CAPTURE_TEXT_FILENAME
    text_path.write_bytes(_CAPTURE_TEXT_BYTES)
    text_artifact = store.save_artifact(
        path=str(text_path),
        filename=_CAPTURE_TEXT_FILENAME,
        content_type="text/plain",
        size_bytes=len(_CAPTURE_TEXT_BYTES),
        checksum=hashlib.sha256(_CAPTURE_TEXT_BYTES).hexdigest(),
        frame_id=frame_id,
        root_frame_id=frame_id,
        project_id=_CAPTURE_PROJECT,
    )
    # Through the domain service, not the repository: it snapshots the real
    # workspace into the CAS, so the checkpoint names a tree that exists. A
    # hand-written tree id parses and then fails the lookup, which is how an
    # earlier attempt at this produced KeyError instead of a contract.
    checkpoint = runner.session_domain.create_checkpoint(frame_id, reason="capture")
    checkpoint_id = checkpoint["checkpoint_id"]
    branch = runner.session_domain.fork_branch(
        root_frame_id=frame_id,
        from_checkpoint_id=checkpoint_id,
        name="capture branch",
    )

    reservation = _CAPTURE_RESERVATION
    pin = store.add_annotation(
        root_frame_id=frame_id,
        artifact_id=artifact_id,
        artifact_name=_CAPTURE_FILENAME,
        rel_x=0.25,
        rel_y=0.25,
        body="capture pin, reserved",
    )["annotation_id"]
    store.reserve_with_admission(
        reservation_id=reservation,
        root_frame_id=frame_id,
        annotation_ids=[pin],
    )

    # Two real coordinator tickets, so the execution projections describe a
    # session that is genuinely busy.
    #
    # Without them these two routes answered 200 by accident: the seeded plan
    # and message turns above leave short-lived background jobs holding real
    # tickets, and whether the capture caught one was a race with how fast they
    # failed on the absent provider. On a slower or faster host the same run
    # publishes an empty projection -- a contract for the idle case, frozen as
    # the contract for the route, with nothing saying which one it is.
    #
    # The first ticket is held by a barrier for the length of the reads, so it
    # is unambiguously the owner; the second is submitted behind it and is
    # unambiguously queued. Released in `finally`, because a held ticket
    # outliving this function blocks everything the rest of the capture
    # submits.
    # Submitted in this order and no other: the coordinator promotes the first
    # ticket a session receives, so whichever is submitted first is the active
    # one. This was `owner_ticket = None` with only the second submit present,
    # which inverted the whole fixture: the ticket named "queued" became the
    # active one 25ms after submit, `admitted(None)` raised and killed the
    # holder thread on its first statement, and `drain_queued` in the `finally`
    # cancels *queued* tickets only -- so the active one was never released and
    # every later write waited on an admission that could not arrive.
    owner_ticket = runner.executions.submit(
        frame_id, owner="user", owner_id=_CAPTURE_OWNER_ID, language="python"
    )
    # Submitted for its effect, not its value: it exists so the queue
    # projection below has an entry to describe. `drain_queued` in the
    # `finally` cancels it by session, so nothing here needs to hold it.
    runner.executions.submit(
        frame_id, owner="user", owner_id=_CAPTURE_QUEUED_ID, language="python"
    )
    holding = threading.Event()
    finished = threading.Event()

    def _hold_owner() -> None:
        try:
            with runner.executions.admitted(
                owner_ticket, cancel_event=threading.Event(), timeout=_SEEDED_JOB_WAIT_S
            ):
                holding.set()
                finished.wait(_SEEDED_JOB_WAIT_S)
        except BaseException as error:  # noqa: BLE001
            recorder.drive_failures["execution fixture (owner ticket)"] = (
                f"{type(error).__name__}: {error}"
            )
            holding.set()

    holder = threading.Thread(target=_hold_owner, name="capture-owner", daemon=True)
    holder.start()
    if not holding.wait(_SEEDED_JOB_WAIT_S):
        recorder.drive_failures["execution fixture (owner ticket)"] = (
            "the owner ticket never reached running"
        )
    # `snapshot` names the active ticket `owner`, not `running`. Reading the
    # wrong key reported a fixture failure on a correct fixture -- and the
    # assertion is the only thing standing between "the projection describes a
    # busy session" and "the projection happened to be empty".
    projection = runner.executions.snapshot(frame_id)
    active = projection.get("owner") or {}
    if str((active.get("owner") or {}).get("id") or "") != _CAPTURE_OWNER_ID:
        recorder.drive_failures["execution fixture (owner ticket)"] = (
            f"the held ticket is not the active one: {active!r}"
        )
    if not any(
        str((item.get("owner") or {}).get("id") or "") == _CAPTURE_QUEUED_ID
        for item in projection.get("queue") or []
    ):
        recorder.drive_failures["execution fixture (queued ticket)"] = (
            f"the second ticket is not queued: {projection.get('queue')!r}"
        )

    base = f"/frames/{frame_id}"
    reads: tuple[tuple[str, str], ...] = (
        (r"/frames/([^/]+)/action-timeline", f"{base}/action-timeline"),
        (r"/frames/([^/]+)/execution", f"{base}/execution"),
        (r"/frames/([^/]+)/execution-queue", f"{base}/execution-queue"),
        (r"/frames/([^/]+)/context", f"{base}/context"),
        (r"/frames/([^/]+)/security", f"{base}/security"),
        (r"/frames/([^/]+)/delegations", f"{base}/delegations"),
        (r"/frames/([^/]+)/recovery", f"{base}/recovery"),
        (r"/frames/([^/]+)/recovery/actions", f"{base}/recovery/actions"),
        (r"/frames/([^/]+)/auto-mode", f"{base}/auto-mode"),
        (r"/frames/([^/]+)/auto-audits", f"{base}/auto-audits"),
        (r"/frames/([^/]+)/branches", f"{base}/branches"),
        # One inventory entry covers both spellings; there is no bare
        # `/frames/<id>/checkpoints` pattern, and naming one would publish a
        # route the surface does not have.
        (
            r"/frames/([^/]+)/(?:checkpoints|branches/checkpoints)",
            f"{base}/checkpoints",
        ),
        (
            r"/frames/([^/]+)/(?:checkpoints|branches/checkpoints)",
            f"{base}/branches/checkpoints",
        ),
        (r"/frames/([^/]+)/review-settings", f"{base}/review-settings"),
        (r"/frames/([^/]+)/kernel/variables", f"{base}/kernel/variables"),
        (r"/frames/([^/]+)/compute/tasks", f"{base}/compute/tasks"),
        (r"/frames/([^/]+)/revert/operations", f"{base}/revert/operations"),
        (
            r"/frames/([^/]+)/admissions/([^/]+)",
            f"{base}/admissions/{reservation}",
        ),
        (r"/artifacts/([^/]+)/renderer", f"/artifacts/{artifact_id}/renderer"),
        (
            r"/projects/([^/]+)/skills/catalog",
            f"/projects/{_CAPTURE_PROJECT}/skills/catalog",
        ),
        # The one entry whose pattern is deliberately broad: it matches every
        # frame sub-resource above, and only one inventory entry can win a
        # lookup, so it is driven explicitly or it stays uncovered forever.
        (
            r"/frames/([^/]+)/(?:action-timeline|execution-queue|context|security"
            r"|delegations|recovery(?:/actions(?:/(?:restore|retry|restart_fresh))?)?"
            r"|branches(?:/(?:checkpoints|fork|revert-preview|revert"
            r"|[^/]+/activate))?|checkpoints|revert/(?:preview|apply|undo"
            r"|operations)|notebook/export|session/export|kernel/variables"
            r"|execution)",
            f"{base}/execution",
        ),
    )
    try:
        for route, path in reads:
            # "GET", because that is what is dispatched on the next line.
            # `_probe_handler` installs its own `_json` collector bound to this
            # argument, and `install()`'s wrapper records under the method it
            # dispatches and *then* calls that collector -- so every probed
            # response is filed twice, and a mismatch here files the second
            # copy under a verb the route does not serve. Passing "DELETE"
            # published `[ok]` shapes for DELETE on eighteen GET-only frame
            # sub-resources: a contract promising deletes that 405 in reality.
            handler = _probe_handler(
                recorder, handler_class, "GET", path, route, headers
            )
            try:
                handler._api("GET", path)
            except Exception as error:  # noqa: BLE001
                recorder.drive_failures[f"GET {route} (session surface)"] = (
                    f"{type(error).__name__}: {error}"
                )
    finally:
        # In `finally`, because a held ticket that outlives this block stops
        # everything below from being admitted at all. One raised read would
        # otherwise have taken the rest of the capture with it.
        runner.executions.drain_queued(frame_id, reason="contract capture")
        finished.set()
        holder.join(timeout=_SEEDED_JOB_WAIT_S)
        if holder.is_alive():
            recorder.drive_failures["execution fixture (owner ticket)"] = (
                "the held ticket was never released"
            )

    # Checked rather than assumed, and checked *outside* the `finally` so it
    # cannot mask an in-flight exception. The writes below wait on an admission
    # with no deadline, so a fixture that failed to release is not a failed row
    # -- it is a capture that never returns. Refusing here keeps the gate
    # falsifiable: a gate that goes red is evidence, a gate that blocks forever
    # is not.
    stranded = (runner.executions.snapshot(frame_id) or {}).get("owner") or {}
    if stranded:
        raise RuntimeError(
            "the execution fixture left "
            f"{((stranded.get('owner') or {}).get('id'))!r} active; every write "
            "below would wait on an admission that cannot arrive"
        )

    writes: list[tuple[str, str, str, dict]] = [
        (
            "POST",
            r"/artifacts/([^/]+)/rename",
            f"/artifacts/{artifact_id}/rename",
            {"filename": "renamed.bin"},
        ),
        (
            "POST",
            r"/artifacts/([^/]+)/edit",
            f"/artifacts/{text_artifact['artifact_id']}/edit",
            {"content": "capture, edited\n"},
        ),
        (
            "POST",
            r"/frames/([^/]+)/branches/fork",
            f"{base}/branches/fork",
            {"from_checkpoint_id": checkpoint_id, "name": "second capture branch"},
        ),
        (
            "POST",
            r"/frames/([^/]+)/branches/([^/]+)/activate",
            f"{base}/branches/{branch['branch_id']}/activate",
            {"checkpoint_id": checkpoint_id},
        ),
        (
            "POST",
            r"/frames/([^/]+)/(?:revert/preview|branches/revert-preview)",
            f"{base}/revert/preview",
            {"target_checkpoint_id": checkpoint_id},
        ),
        (
            "POST",
            r"/frames/[^/]+/(?:revert/preview|branches/revert-preview)",
            f"{base}/branches/revert-preview",
            {"target_checkpoint_id": checkpoint_id},
        ),
        (
            "POST",
            r"/frames/([^/]+)/decision",
            f"{base}/decision",
            # A real decision id, resolved through the durable path. The body
            # used to be `{"decision": "approve"}` -- no `decision_id` at all --
            # so this route answered `decision_id is required` every time, and
            # because that refusal was HTTP 200 the contract froze a FAILURE as
            # the route's success shape. `(200, {"ok"})` was satisfied by
            # `{"ok": false}`. The success shape had never been captured.
            {"decision_id": _CAPTURE_DECISION_ID, "allow": True, "scope": "once"},
        ),
        ("POST", r"/frames/([^/]+)/review", f"{base}/review", {}),
        (
            "POST",
            r"/skills",
            "/skills",
            {
                "name": "capture-skill",
                "description": "created by the contract capture",
                "body": "# capture-skill\n\nA real authored Skill.\n",
            },
        ),
        (
            "POST",
            r"/skills/import",
            "/skills/import",
            {
                "name": "capture-import",
                "content": "# capture-import\n\nAn imported Skill document.\n",
            },
        ),
    ]
    for method, route, path, body in writes:
        handler = _probe_handler(
            recorder, handler_class, method, path, route, headers, {}, body
        )
        try:
            handler._api(method, path)
        except Exception as error:  # noqa: BLE001
            recorder.drive_failures[f"{method} {route} (session surface)"] = (
                f"{type(error).__name__}: {error}"
            )

    # Edit and restore, back to back, on an artifact of their own.
    #
    # The route takes a *historical, non-current* version, so an artifact with
    # one version answers 404 -- which is what the first attempt recorded, and
    # silently, by skipping when it found nothing to restore. Reusing the
    # edited artifact from the table above does not work either: the branch
    # activation and revert in between roll its versions back, so by the time
    # the restore ran there was again nothing historical to name. Adjacent, on
    # a private artifact, with nothing scheduled between them.
    history_bytes = b"contract capture, versioned\n"
    history_artifact = runner.artifacts.upload(
        {
            "frame_id": frame_id,
            "project_id": _CAPTURE_PROJECT,
            "filename": _CAPTURE_HISTORY_FILENAME,
            "content_text": history_bytes.decode("utf-8"),
        }
    )
    # Edited twice to exercise a real historical version through the same
    # exact writer the route itself uses. The fixture's initial upload already
    # has immutable bytes; the second edit keeps the intended newest-history
    # selection deterministic.
    edit_path = f"/artifacts/{history_artifact['artifact_id']}/edit"
    for revision in ("second", "third"):
        handler = _probe_handler(
            recorder,
            handler_class,
            "POST",
            edit_path,
            r"/artifacts/([^/]+)/edit",
            headers,
            {},
            {"content": f"contract capture, {revision} version\n"},
        )
        try:
            handler._api("POST", edit_path)
        except Exception as error:  # noqa: BLE001
            recorder.drive_failures[
                f"POST /artifacts/([^/]+)/edit ({revision} version)"
            ] = f"{type(error).__name__}: {error}"
    # Newest first, so the most recent non-current version is the one the
    # second edit displaced -- an edit-made version, which has a snapshot.
    history = [
        version
        for version in store.list_versions(history_artifact["artifact_id"])
        if not version.get("is_latest") and version.get("version_id")
    ]
    restore_route = r"/artifacts/([^/]+)/versions/([^/]+)/restore"
    if not history:
        recorder.drive_failures[f"POST {restore_route} (session surface)"] = (
            "the edit above minted no second version, so nothing is restorable"
        )
    else:
        path = (
            f"/artifacts/{history_artifact['artifact_id']}"
            f"/versions/{history[0]['version_id']}/restore"
        )
        handler = _probe_handler(
            recorder, handler_class, "POST", path, restore_route, headers, {}, {}
        )
        seen: dict = {}
        observing = handler._json
        handler._json = lambda payload, code=200, *a, **k: (
            seen.update({"code": code, "payload": payload}),
            observing(payload, code, *a, **k),
        )[1]
        try:
            handler._api("POST", path)
            if int(seen.get("code") or 0) >= 400:
                # Recorded, not swallowed: a refusal here means the fixture did
                # not reach the state the route needs, and a silent 404 would
                # republish the refusal-only contract this pass exists to
                # replace.
                recorder.drive_failures[f"POST {restore_route} (session surface)"] = (
                    f"{seen.get('code')}: {seen.get('payload')}"
                )
        except Exception as error:  # noqa: BLE001
            recorder.drive_failures[f"POST {restore_route} (session surface)"] = (
                f"{type(error).__name__}: {error}"
            )

    # Apply, then undo the exact operation apply minted. `undo` needs the id
    # from the apply response, so this pair cannot be driven from the table
    # above -- and driving `undo` with a guessed id records a 400 and calls the
    # route covered.
    applied = _capture_json(
        recorder,
        handler_class,
        "POST",
        r"/frames/([^/]+)/(?:revert/apply|branches/revert)",
        f"{base}/revert/apply",
        headers,
        {"target_checkpoint_id": checkpoint_id},
    )
    # The *revert* checkpoint, not the undo target. `undo_revert` reads
    # `undo_checkpoint_id` out of the named checkpoint's own metadata, so
    # handing it the undo id asks it to undo the thing being undone.
    reverted = (applied or {}).get("checkpoint") or {}
    undo_id = reverted.get("checkpoint_id") if isinstance(reverted, dict) else None
    if undo_id:
        handler = _probe_handler(
            recorder,
            handler_class,
            "POST",
            f"{base}/revert/undo",
            r"/frames/([^/]+)/revert/undo",
            headers,
            {},
            {"revert_checkpoint_id": undo_id},
        )
        try:
            handler._api("POST", f"{base}/revert/undo")
        except Exception as error:  # noqa: BLE001
            recorder.drive_failures[
                "POST /frames/([^/]+)/revert/undo (session surface)"
            ] = f"{type(error).__name__}: {error}"
    else:
        recorder.drive_failures[
            "POST /frames/([^/]+)/revert/undo (session surface)"
        ] = "revert/apply named no revert checkpoint"


def _capture_json(
    recorder: "Recorder",
    handler_class,
    method: str,
    route: str,
    path: str,
    headers: dict[str, str] | None,
    body: dict,
) -> dict | None:
    """Drive one route and keep its decoded body, still observing it.

    The recorder wraps `_json`, so reading the payload means wrapping the
    wrapper rather than replacing it -- replacing it would take the
    observation with it and silently drop the route from the contract.
    """
    handler = _probe_handler(
        recorder, handler_class, method, path, route, headers, {}, body
    )
    captured: dict = {}
    observing = handler._json

    def _both(payload, code=200, *args, **kwargs):
        if isinstance(payload, dict):
            captured.update(payload)
        return observing(payload, code, *args, **kwargs)

    handler._json = _both
    try:
        handler._api(method, path)
    except Exception as error:  # noqa: BLE001
        recorder.drive_failures[f"{method} {route} (session surface)"] = (
            f"{type(error).__name__}: {error}"
        )
        return None
    return captured


def drive_all_routes(
    recorder: "Recorder",
    make_handler,
    config,
    runner,
    *,
    authenticated_headers: dict[str, str] | None = None,
) -> None:
    """Ask every known route for an answer, against a real handler.

    One implementation, shared by the two capture scripts and the coverage
    test, because three copies of "how to drive the surface" is three chances
    for the published contract to describe something nothing drives.

    Observations are attributed to the route being probed rather than derived
    from the path: two inventory entries can match the same concrete path
    (``/frames/([^/]+)/shares`` and ``/frames/[^/]+/shares`` are both real), and
    only one can ever win a lookup — the other would be permanently
    unattributed and counted as uncovered forever.
    """
    from openai4s.server.errors import GatewayError, gateway_error_payload

    # The handler factory is passed in, exactly as `install` takes the gateway
    # module: this file must not import the gateway, because the gateway
    # imports this file's siblings and the declared facade boundary is what
    # keeps that from becoming a cycle.
    handler_class = make_handler(config, runner.hub, runner)
    for route in sorted(contract.http_routes()):
        path = concrete_path(route)
        for method in PROBE_METHODS:
            handler = _probe_handler(
                recorder, handler_class, method, path, route, authenticated_headers
            )
            try:
                handler._api(method, path)
            except GatewayError as error:
                # What the dispatcher does with a raised GatewayError, recorded
                # here rather than through the handler: the capture wrapper
                # restores `_json` in its `finally`, so by this line the
                # observing collector is already gone.
                recorder.observe(
                    method,
                    path,
                    error.code,
                    public_failure(
                        gateway_error_payload(error), error.code, _CAPTURE_REQUEST_ID
                    ),
                    route=route,
                )
            except Exception as error:  # noqa: BLE001
                # Recorded rather than merely skipped. The old comment here said
                # a route that cannot answer has no contract to record — but it
                # does get one, from whichever *other* verbs answered. That is
                # how both `artifacts.zip` routes came to be published as
                # `kinds: ["json"], statuses: [404]`: GET raised, and the four
                # unimplemented verbs' dispatcher 404s became the contract for a
                # zip download, with the coverage gate calling it covered.
                recorder.drive_failures[f"{method} {route}"] = (
                    f"{type(error).__name__}: {error}"
                )
                continue
    _drive_seeded_downloads(recorder, handler_class, runner, authenticated_headers)
    _drive_environment_states(recorder, handler_class, authenticated_headers)


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


#: What one xdist worker's share of a split capture is called. The share sits
#: beside the destination rather than in it, so a half-finished run leaves no
#: file anything downstream would mistake for a capture.
PARTIAL_SUFFIX = ".share.json"


def partial_path(destination: Path, worker: str) -> Path:
    """Where one worker of a split run leaves its share."""
    return Path(destination).with_name(
        f"{Path(destination).name}.{worker}{PARTIAL_SUFFIX}"
    )


def save_partial(
    recorder: Recorder,
    destination: Path,
    worker: str,
    *,
    worker_count: int | None = None,
    run_id: str | None = None,
) -> Path:
    """Write one worker's un-elided shapes for `assemble` to merge later.

    Xdist supplies ``worker_count`` and ``run_id``.  They let assembly prove it
    has one complete, unmixed run rather than treating whatever files happened
    to arrive as the whole capture.  The optional form keeps the helper useful
    for serial unit assembly outside pytest.
    """
    worker_id = str(worker).strip()
    if not worker_id:
        raise ValueError("worker must not be empty")
    if (worker_count is None) != (run_id is None):
        raise ValueError("worker_count and run_id must be supplied together")
    target = partial_path(destination, worker_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "worker": worker_id,
        "shapes": recorder.shapes_snapshot(),
    }
    if worker_count is not None:
        count = int(worker_count)
        if count < 1:
            raise ValueError("worker_count must be positive")
        record["worker_count"] = count
    if run_id is not None:
        identifier = str(run_id).strip()
        if not identifier:
            raise ValueError("run_id must not be empty")
        record["run_id"] = identifier
    payload = json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
    )
    temporary: Path | None = None
    try:
        # Publish only a complete JSON document.  If a worker is killed while
        # writing, its ignored temporary file may remain but the final share is
        # absent; completeness validation then rejects the run instead of
        # parsing a truncated file or publishing narrower evidence.
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
        temporary.replace(target)
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    return target


def assemble(destination: Path, *, require_complete: bool = False) -> int:
    """Merge a split run's shares into one capture at `destination`.

    Returns how many shares were merged, and 0 when the run was not split --
    a single-process run writes `destination` itself and there is nothing here
    to do.  ``require_complete`` is for the xdist capture gate: every share
    must carry one consistent run ID and expected worker count, and assembly
    refuses to write unless exactly that many unique workers arrived.

    Deliberately called *after* pytest has exited rather than from a controller
    hook: at that point every worker process is gone, so the assembly cannot
    race a writer.  A worker that died without publishing its share is an
    incomplete run, not a narrower schema: its missing observations may only
    have made a field nullable or optional, which the compatibility checker
    intentionally treats as non-breaking drift.
    """
    destination = Path(destination)
    parent = destination.parent
    if not parent.is_dir():
        if require_complete:
            raise ValueError(f"capture share directory does not exist: {parent}")
        return 0
    prefix = f"{destination.name}."
    shares = sorted(
        (
            path
            for path in parent.iterdir()
            if path.is_file()
            and path.name.startswith(prefix)
            and path.name.endswith(PARTIAL_SUFFIX)
        ),
        key=lambda path: path.name,
    )
    if not shares and require_complete:
        raise ValueError("capture assembly found no xdist worker shares")
    if not shares:
        return 0

    payloads: list[tuple[Path, Mapping[str, Any]]] = []
    workers: set[str] = set()
    expected_counts: set[int] = set()
    run_ids: set[str] = set()
    has_completion_metadata = False
    for share in shares:
        try:
            loaded = json.loads(share.read_text("utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError(f"invalid capture share {share.name}: {error}") from error
        if not isinstance(loaded, Mapping):
            raise ValueError(f"invalid capture share {share.name}: expected an object")
        if loaded.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(
                f"invalid capture share {share.name}: schema_version must be "
                f"{SCHEMA_VERSION}"
            )
        shapes = loaded.get("shapes")
        if not isinstance(shapes, Mapping):
            raise ValueError(
                f"invalid capture share {share.name}: shapes must be an object"
            )
        worker = str(loaded.get("worker") or "").strip()
        if not worker:
            raise ValueError(f"invalid capture share {share.name}: worker is missing")
        if partial_path(destination, worker).name != share.name:
            raise ValueError(
                f"invalid capture share {share.name}: payload names worker {worker!r}"
            )
        if worker in workers:
            raise ValueError(f"duplicate capture share for worker {worker!r}")
        workers.add(worker)

        count_value = loaded.get("worker_count")
        run_value = loaded.get("run_id")
        if count_value is not None or run_value is not None:
            has_completion_metadata = True
        if count_value is not None:
            if isinstance(count_value, bool) or not isinstance(count_value, int):
                raise ValueError(
                    f"invalid capture share {share.name}: worker_count must be an integer"
                )
            if count_value < 1:
                raise ValueError(
                    f"invalid capture share {share.name}: worker_count must be positive"
                )
            expected_counts.add(count_value)
        if run_value is not None:
            run_id = str(run_value).strip()
            if not run_id:
                raise ValueError(
                    f"invalid capture share {share.name}: run_id must not be empty"
                )
            run_ids.add(run_id)
        payloads.append((share, loaded))

    if require_complete or has_completion_metadata:
        if any(
            "worker_count" not in payload or "run_id" not in payload
            for _share, payload in payloads
        ):
            raise ValueError("capture shares do not all carry completion metadata")
        if len(expected_counts) != 1:
            raise ValueError("capture shares disagree about the expected worker count")
        if len(run_ids) != 1:
            raise ValueError("capture shares come from different xdist runs")
        expected = next(iter(expected_counts))
        if len(workers) != expected:
            raise ValueError(
                f"incomplete capture: expected {expected} worker shares, "
                f"found {len(workers)}"
            )

    recorder = Recorder()
    for _share, payload in payloads:
        recorder.absorb(payload["shapes"])
    save(recorder.document(), destination)
    # Shares belong to one assembly, not to the destination forever.  Leaving
    # them behind lets a later run with fewer workers silently inherit routes
    # or variants from the earlier run.
    for share in shares:
        share.unlink()
    return len(shares)


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
