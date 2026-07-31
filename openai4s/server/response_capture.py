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

import hashlib
import json
import re
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
        route = route or route_for(path, self._patterns)
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
        route = route or route_for(path, self._patterns)
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
                    )
                except Exception:  # noqa: BLE001 - never break a response
                    pass
                return reply(value, code)

            raw = self._send
            had_own_send = "_send" in self.__dict__

            def observing_send(code, body, ctype, extra=None):
                try:
                    recorder.observe_raw(method, sub, code, ctype, len(body or b""))
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
):
    """A handler whose three response writers report into ``recorder``.

    Shared by the parameterless sweep and the seeded pass below, because a
    second copy of "how a probe answers" is how one of them comes to record a
    route the other cannot.
    """
    handler = object.__new__(handler_class)
    handler._query = lambda: dict(query or {})
    handler._body = lambda: {}
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


def _drive_seeded_downloads(
    recorder: "Recorder",
    handler_class,
    runner,
    headers: dict[str, str] | None = None,
) -> None:
    """Record the 200 of the routes whose success is bytes, not JSON.

    The parameterless sweep cannot reach these: a notebook export, a Session
    package and an artifact download each need something to serve, so against a
    probe id every one of them 404s. That left them worse than uncovered. The
    four unimplemented verbs still recorded the dispatcher's 404, so a download
    endpoint was published as `kinds: ["json"], statuses: [404]` — a contract
    that describes only how the route refuses — and the coverage gate counted
    it. Nothing a client of a download depends on was written down anywhere.

    One frame and one file is the whole fixture, created *after* the sweep so
    the unknown-resource 404s it froze are unchanged. Both callers hand this a
    Store in a temp directory that is removed on the way out.

    A failure here is recorded, never swallowed: the seeded pass exists because
    a silently skipped success republishes the 404-only contract it was written
    to replace.
    """
    store = runner.store
    frame_id = store.new_frame(kind="turn", project_id=_CAPTURE_PROJECT, status="ready")
    blob = runner.workspace_for(frame_id) / _CAPTURE_FILENAME
    blob.write_bytes(_CAPTURE_BYTES)
    artifact = store.save_artifact(
        path=str(blob),
        filename=_CAPTURE_FILENAME,
        # Opaque bytes on purpose. This route echoes the stored artifact's own
        # content type, so seeding a `text/plain` would freeze a fact about the
        # fixture; `application/octet-stream` is what the route promises when it
        # knows nothing about the file, which is the honest general case.
        content_type="application/octet-stream",
        size_bytes=len(_CAPTURE_BYTES),
        checksum=hashlib.sha256(_CAPTURE_BYTES).hexdigest(),
        frame_id=frame_id,
        root_frame_id=frame_id,
        project_id=_CAPTURE_PROJECT,
    )
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
        (r"/artifacts/(.+)", f"/artifacts/{artifact['artifact_id']}", {}),
    )
    for route, path, query in probes:
        handler = _probe_handler(
            recorder, handler_class, "GET", path, route, headers, query
        )
        try:
            handler._api("GET", path)
        except Exception as error:  # noqa: BLE001
            recorder.drive_failures[
                f"GET {route} (seeded)"
            ] = f"{type(error).__name__}: {error}"


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
                recorder.drive_failures[
                    f"{method} {route}"
                ] = f"{type(error).__name__}: {error}"
                continue
    _drive_seeded_downloads(recorder, handler_class, runner, authenticated_headers)


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
