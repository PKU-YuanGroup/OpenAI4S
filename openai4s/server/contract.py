"""The externally-reachable HTTP/WebSocket surface.

The proposal requires that every external route and event be covered by a
contract inventory, and that the inventory be *checkable* rather than a list
someone maintains by hand. A hand-maintained list is wrong the first time
somebody adds a route in a hurry, and its being wrong is invisible — which is
the failure mode a contract exists to prevent.

HTTP routing is being migrated incrementally to declarative ``RouteSpec``
objects. A route module that exports ``ROUTES`` contributes those exact runtime
declarations, and legacy gateway branches keep the static source extractor, so
the migration does not force a high-risk rewrite of the whole routing chain in
one change.

The two readings are unioned, never traded off. Selecting one *per module* --
skipping the source scan the moment a module grew its first ``RouteSpec`` --
made the half-migrated module, the entire point of an incremental migration,
the case that silently lost routes; and reading ``ROUTES`` by name meant a
module whose table was called anything else lost all of them. Reading both
always is idempotent when they agree and loud when they do not.

The source fallback remains deliberately strict. An extractor that misses an
idiom reports *full coverage of an incomplete inventory* — false confidence,
which is worse than no check, and neither ``--check`` script can see it because
the missing routes are absent from both sides of the comparison. The first
version handled only ``sub == "..."`` and ``re.fullmatch``, and silently omitted
``/frames`` (matched query-aware as ``sub.split("?")[0] == "/frames"``), the
``sub in (...)`` tuples, and ``sub.startswith(...)``. A test asserting that a
few obviously-present routes are found is what caught it, and is why that test
exists alongside the coverage assertions rather than being folded into them.

What this is not: a schema. It answers "which paths exist", not "what shape do
they return". Response schemas are the next layer of §4.6 and are not inferable
from a routing chain.
"""

from __future__ import annotations

import ast
import importlib
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The verbs the gateway can actually dispatch. Membership, not spelling: an
#: "uppercase and alphabetic" test accepts `GTE`, `BANANA`, and the full-width
#: `ＧＥＴ` (`str.isalpha()` is Unicode-wide and all three are `.upper()`-stable).
#: A route declared with a typo'd verb is dead for every real request, but the
#: inventory still lists its *pattern* -- `http_routes` drops the method -- and
#: `response_capture.unroutable` only checks that the path concretises. The five
#: probe verbs then all earn the dispatcher's 404, the entry freezes as
#: `{"statuses": [404]}`, and `--check` is green: full coverage of a route that
#: cannot be reached, which is the failure this module exists to prevent.
_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class RouteSpec:
    """One HTTP route identity plus its state-mutation contract."""

    name: str
    method: str
    pattern: str
    mutates: bool

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("route name must be non-empty")
        if self.method not in _HTTP_METHODS:
            raise ValueError(
                f"route method must be one of {sorted(_HTTP_METHODS)}: "
                f"{self.method!r}"
            )
        if not self.pattern.startswith("/"):
            raise ValueError(f"route pattern must start with '/': {self.pattern!r}")
        if not isinstance(self.mutates, bool):
            raise TypeError("route mutates flag must be bool")
        try:
            compiled = re.compile(self.pattern)
        except re.error as exc:
            raise ValueError(f"invalid route pattern {self.pattern!r}: {exc}") from exc
        # Keep what the validation already built. `match` used to re-resolve the
        # pattern *string* through `re`'s module cache on every request -- a
        # 2.4x tax per lookup, up to twelve times per request, against a cache
        # that is capped at 512 entries and cleared wholesale on overflow.
        # `object.__setattr__` is the frozen-dataclass idiom; the field is not a
        # dataclass field, so `__eq__`/`__hash__` are unaffected.
        object.__setattr__(self, "_compiled", compiled)

    def match(self, method: str, path: str) -> re.Match[str] | None:
        """Match only when both the HTTP method and path belong to this route.

        Wrong-method matches deliberately return ``None``. The gateway's route
        chain must then continue to its ordinary 404 instead of treating a path
        match as a handled request.
        """
        if method != self.method:
            return None
        compiled: re.Pattern[str] = self._compiled  # type: ignore[attr-defined]
        return compiled.fullmatch(path)


#: Where the HTTP API lives. Defined here rather than in the gateway because
#: two very different callers need it and neither should guess: the gateway
#: routes on it, and the CLI builds daemon URLs with it. `openai4s share`
#: hard-coded "/api/" and every one of its subcommands 404'd against the
#: daemon's own "the API is versioned" refusal -- a whole feature that had
#: never reached a route.
API_ROOT = "/api/v1"

_GATEWAY = Path(__file__).with_name("gateway.py")
# Events are emitted from the focused services too, not only the composition
# adapter. Scanning gateway.py alone left fifteen live event types invisible to
# the inventory and therefore undocumented.
_SERVER_PKG = Path(__file__).parent
_AGENT_PKG = _SERVER_PKG.parent / "agent"

# `sub == "/config/llm"` — an exact route, after the /api/v1 prefix is stripped.
# Also matches the query-aware form `sub.split("?")[0] == "/frames"`.
_EXACT = re.compile(r'sub(?:\.split\("\?"\)\[0\])?\s*==\s*"(/[^"]*)"')
# `sub in ("/memory/categories", "/memory/context")` — a tuple of exact routes.
_MEMBERSHIP = re.compile(r"sub\s+in\s+\(([^)]*)\)")
_MEMBER_ITEM = re.compile(r'"(/[^"]*)"')
# `sub.startswith("/frames?")` — a prefix route.
_PREFIX = re.compile(r'sub\.startswith\(\s*"(/[^"?]*)')
# `re.fullmatch(r"/frames/([^/]+)/kernel", sub)` — a parameterised legacy
# route. Only patterns anchored at "/" are routes; the files also use
# fullmatch to validate hashes and identifiers.
_PATTERN = re.compile(r're\.fullmatch\(\s*r"(/[^"]*)"')
# WebSocket client messages are dispatched on `t == "view_session"` — or on
# `t in {"cancel_execution", "cancel"}`, a form the equality-only pattern
# missed, leaving two real inbound types out of the inventory.
_WS_INBOUND = re.compile(r't\s*==\s*"([a-z_]+)"')
_WS_INBOUND_SET = re.compile(r"t\s+in\s+[({]([^)}]*)[)}]")
_WS_INBOUND_ITEM = re.compile(r'"([a-z_]+)"')
# Server-emitted events carry their own type.
_WS_OUTBOUND = re.compile(r'"type"\s*:\s*"([a-z_]+)"')


#: Modules that hold route branches carved out of `Handler._api`. The
#: decomposition moves groups of routes into siblings, and a route that moved
#: is still surface -- but `_source()` read gateway.py alone, so the first
#: extraction would have dropped 12 routes out of the inventory and orphaned 11
#: frozen response shapes. The tempting repair (regenerate the artifact until
#: the tests pass) is the damaging one: those shapes get re-filed under the
#: catch-all `/frames/([^/]+)(?:/.*)?` and the per-route contract is gone.
#:
#: Same reasoning that already widened WS-event scanning to the whole package
#: above; HTTP routes were simply never widened with it.
#:
#: The membership is *derived*, not listed. A hand-maintained tuple has the
#: same defect as a hand-maintained inventory: it is wrong the first time
#: somebody extracts a route group in a hurry, and its being wrong is
#: invisible, because the extractor then reports full coverage of an incomplete
#: inventory. Neither `--check` script catches that -- the routes are missing
#: from both sides of the comparison. So the convention is the naming, and
#: `test_contract_inventory` fails the build when a module carries routing
#: idioms without following it.
#:
#: A module that has migrated also exposes ``ROUTES``; those declarations are
#: read live *in addition to* the source scan, never instead of it.
_ROUTE_MODULE_GLOB = "*_routes.py"

#: Modules that use the same routing idioms but are **not** reachable through
#: `Handler._route`, so their paths are a different surface. `ShareRouter` is
#: constructed for the outbound tunnel client and dispatched to directly; its
#: `/api/artifacts/([^/]+)` never passes through the gateway chain. Counting it
#: here would add a path the contract driver cannot drive, and an undrivable
#: route is indistinguishable from an uncovered one.
_NON_GATEWAY_ROUTE_MODULES = frozenset({"share_router.py"})


def _route_modules() -> tuple[str, ...]:
    """Every module that owns route branches extracted from `Handler._api`."""
    return tuple(
        sorted(
            path.name
            for path in _SERVER_PKG.glob(_ROUTE_MODULE_GLOB)
            if path.name not in _NON_GATEWAY_ROUTE_MODULES
        )
    )


def _route_module_specs(name: str) -> tuple[RouteSpec, ...]:
    """Return one route module's executable declarations, if it has migrated.

    Route modules are intentionally import-safe handler modules. Importing one
    is now useful because a declarative module exposes the exact table the
    runtime consumes; modules without that table stay on the source fallback.
    """
    module_name = f"{__package__}.{name[:-3]}"
    module = importlib.import_module(module_name)
    declared = getattr(module, "ROUTES", None)
    if declared is None:
        return ()
    if not isinstance(declared, (tuple, list)):
        raise TypeError(f"{module_name}.ROUTES must be a tuple/list of RouteSpec")
    specs = tuple(declared)
    invalid = [spec for spec in specs if not isinstance(spec, RouteSpec)]
    if invalid:
        raise TypeError(f"{module_name}.ROUTES contains non-RouteSpec values")
    return specs


#: A stand-in for one parameterised segment, used to ask "could an earlier route
#: have swallowed this one?". Deliberately boring: it must not contain "/" or a
#: regex metacharacter.
_SAMPLE_SEGMENT = "x"
# Both alternatives in the former nested expression matched the ``?`` prefix
# character-by-character, so a malformed declaration such as repeated ``?a``
# without a closing parenthesis caused exponential backtracking during startup.
# The special-prefix arm did not add any accepted text: ``[^()]`` already
# covers every character it matched.  Keep the exact non-nested-group language
# with the single linear alternative.
_GROUP = re.compile(r"\([^()]*\)")


def _sample_path(pattern: str) -> str | None:
    """A concrete path this pattern matches, or None if we cannot build one.

    Only used to detect shadowing, so it fails closed: if the sample does not
    match the pattern it came from, we learned nothing and say so rather than
    guessing. Byte-equal `(method, pattern)` pairs are not the ambiguity that
    matters -- two *different* regexes matching the same path are, and those
    used to validate cleanly while the second route was permanently dead.
    """
    sample = _GROUP.sub(_SAMPLE_SEGMENT, pattern)
    if set(sample) & set(".*+?[]{}|()^$\\"):
        return None
    return sample if re.fullmatch(pattern, sample) else None


def _validate_route_specs(specs: tuple[RouteSpec, ...]) -> tuple[RouteSpec, ...]:
    """Reject ambiguous protocol identities before they enter the inventory."""
    names: set[str] = set()
    method_paths: set[tuple[str, str]] = set()
    for index, spec in enumerate(specs):
        if spec.name in names:
            raise ValueError(f"duplicate route name: {spec.name!r}")
        identity = (spec.method, spec.pattern)
        if identity in method_paths:
            raise ValueError(
                f"duplicate HTTP route declaration: {spec.method} {spec.pattern}"
            )
        names.add(spec.name)
        method_paths.add(identity)
        # Shadowing: an earlier route in the chain already answers a path this
        # one claims. The later route is unreachable at runtime, yet it enters
        # the inventory as live surface and `response_capture` attributes a
        # response to it -- a documented route that can only ever 404.
        sample = _sample_path(spec.pattern)
        if sample is None:
            continue
        for earlier in specs[:index]:
            if earlier.match(spec.method, sample) is not None:
                raise ValueError(
                    f"route {spec.name!r} ({spec.method} {spec.pattern}) is "
                    f"shadowed by {earlier.name!r} ({earlier.method} "
                    f"{earlier.pattern}): both match {sample!r}"
                )
    return specs


def validate_routes(specs: tuple[RouteSpec, ...]) -> tuple[RouteSpec, ...]:
    """Validate a route module's table *at the point it is declared*.

    `_validate_route_specs` was reachable only from `declared_http_routes()`, so
    a duplicated or shadowed registry imported cleanly and the daemon served it
    -- `gateway.py` calls neither function. The earliest catch was whichever
    contract test happened to run, which is why deleting the call left all the
    tests green. Wrapping the tuple at module scope makes the daemon fail fast
    on the same defect the inventory would have found later.
    """
    return _validate_route_specs(tuple(specs))


def declared_http_routes() -> tuple[RouteSpec, ...]:
    """All validated RouteSpec declarations on the gateway HTTP surface."""
    specs: list[RouteSpec] = []
    for name in _route_modules():
        specs.extend(_route_module_specs(name))
    return _validate_route_specs(tuple(specs))


def _route_sources() -> list[str]:
    """gateway.py plus every module that owns extracted route branches.

    Every module is read, migrated or not. Skipping a module the moment it grew
    one `RouteSpec` was all-or-nothing on a migration the docstring above calls
    incremental: a module holding one spec plus two surviving `re.fullmatch`
    branches lost those two branches from the inventory, which is missing from
    *both* sides of each `--check` comparison. Reading it unconditionally is
    idempotent for a fully migrated module -- `RouteSpec(...)` calls are not
    matched by `_EXACT`/`_PATTERN`/`_PREFIX`/`_MEMBERSHIP`, and the declarative
    patterns arrive again via `_route_spec_patterns` -- and correct for a
    partial one, so over-counting is not a risk the skip was buying anything
    against.
    """
    texts = [_GATEWAY.read_text("utf-8")]
    for name in _route_modules():
        path = _SERVER_PKG / name
        if path.is_file():
            texts.append(path.read_text("utf-8"))
    return texts


def _source() -> str:
    return _GATEWAY.read_text("utf-8")


def _callee_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _fullmatch_patterns(text: str) -> set[str]:
    """Parameterised legacy routes, read as constant expressions.

    A regex scan takes the first string literal it sees. The gateway builds one
    matcher out of adjacent raw literals across several lines, so the scan can
    produce a fragment that cannot match anything. Python's parser joins those
    literals before the AST exists; non-constant matchers are left out rather
    than half-read.
    """
    try:
        tree = ast.parse(textwrap.dedent(text))
    except SyntaxError:
        # A fragment rather than a module — the tests hand this function one,
        # and so would any future caller scanning a snippet. Fall back to the
        # scan, which reads the first literal of a concatenation: the
        # completeness filter in `http_routes` is what stops the truncation
        # that produces from being counted, in either path.
        return set(_PATTERN.findall(text))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if _callee_name(node) != "fullmatch":
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            found.add(first.value)
    return found


def _route_spec_patterns(text: str) -> set[str]:
    """Read constant RouteSpec patterns out of source.

    A declarative module's live ``ROUTES`` table is the better answer where one
    exists, but this is what sees a spec declared somewhere the table walk does
    not reach, and what lets a test compare a module in isolation.

    Only *constant* patterns are read, the same choice `_fullmatch_patterns`
    makes and for the same reason: a pattern built by concatenation or an
    f-string is not something this can describe, so it is left out rather than
    half-read. Note the asymmetry that leaves behind — `http_routes` unions this
    with the live table, so a computed pattern still reaches the inventory from
    `ROUTES`; it is only invisible to a caller that hands us text alone.
    """
    # `_callee_name` can only match text containing this literal, so a scan of
    # anything else is a full dedent+parse+walk that cannot find anything. The
    # inventory sweeps hand this function 40 server modules and 589 KiB of
    # gateway.py, none of which contain it: ~150ms of parsing per suite run.
    if "RouteSpec" not in text:
        return set()
    try:
        tree = ast.parse(textwrap.dedent(text))
    except SyntaxError:
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _callee_name(node) != "RouteSpec":
            continue
        if len(node.args) >= 3:
            pattern = node.args[2]
        else:
            pattern = next(
                (kw.value for kw in node.keywords if kw.arg == "pattern"),
                None,
            )
        if isinstance(pattern, ast.Constant) and isinstance(pattern.value, str):
            found.add(pattern.value)
    return found


def is_complete_matcher(route: str) -> bool:
    """Can this entry stand for a route at all?

    A path, and a regex that compiles. An entry that cannot form a complete
    matcher describes no surface, so counting it as covered — on the strength
    of the 404 it earns for not being a route — inflates the number that is
    supposed to mean "every endpoint has an observed response".
    """
    if not route.startswith("/"):
        return False
    try:
        re.compile(route)
    except re.error:
        return False
    return True


def http_routes(source: str | None = None) -> set[str]:
    """Every path the HTTP surface can match, relative to the API root.

    With no ``source`` override, declarative route modules contribute their live
    RouteSpec patterns and only legacy modules are source-scanned. A supplied
    source is treated as an isolated fragment for extractor tests.
    """
    if source is None:
        text = "\n".join(_route_sources())
        declared = {spec.pattern for spec in declared_http_routes()}
    else:
        text = source
        declared = set()
    # On both paths. Applying the RouteSpec reader only to a supplied source
    # left the real inventory blind to the idiom this module now depends on: a
    # module whose table is not literally named `ROUTES` fell back to source
    # scanning, and the legacy extractors have no RouteSpec idiom, so all twelve
    # kernel routes left the inventory (152 -> 140) with no error raised. The
    # same silent branch is reachable from a partially-initialised module. It
    # also keeps a spec declared outside a `*_routes.py` module -- gateway.py,
    # the obvious next step -- from vanishing, since `_route_modules` globs.
    declared |= _route_spec_patterns(text)
    routes = declared | set(_EXACT.findall(text)) | _fullmatch_patterns(text)
    routes |= set(_PREFIX.findall(text))
    for group in _MEMBERSHIP.findall(text):
        routes |= set(_MEMBER_ITEM.findall(group))
    # A route is a path that can match something. Anything else is not surface,
    # and must not be counted as covered.
    return {route for route in routes if is_complete_matcher(route)}


def websocket_inbound(source: str | None = None) -> set[str]:
    """Message types a client may send over the socket."""
    text = source if source is not None else _source()
    # Bounded to the socket handler so unrelated `t == "..."` comparisons
    # elsewhere in the gateway cannot inflate the surface.
    start = text.find("def _handle_ws")
    if start < 0:
        return set()
    # Bounded at BOTH ends. Scanning to end-of-file swept in an unrelated
    # truthiness check hundreds of lines later that happens to use the same
    # loop variable name, which would have put "false"/"no"/"off" in the
    # inventory as client message types.
    body = text[start:]
    end = re.search(r"\n(?=def |class )", body)
    handler = body[: end.start()] if end else body
    inbound = set(_WS_INBOUND.findall(handler))
    for group in _WS_INBOUND_SET.findall(handler):
        inbound |= set(_WS_INBOUND_ITEM.findall(group))
    return inbound


#: Names that dispatch an event onto the socket. A dict literal handed to one
#: of these is an event even when it carries no frame id of its own — the hub's
#: `emitter` fills that in.
_EMIT_CALLS = frozenset(
    {"emit", "broadcast", "send_json", "_record_domain_event", "sink"}
)
#: A dict literal carrying one of these is addressed at a session, which is
#: what distinguishes an event from the many other `{"type": ...}` dicts in the
#: tree — JSON-schema fragments, ledger states, and result payloads all use the
#: same key and are not surface.
_EVENT_ADDRESS_KEYS = frozenset({"root_frame_id", "frame_id"})


def _event_types_in_module(text: str) -> set[str]:
    """Event type literals in one module, by AST rather than by regex.

    A plain `"type": "..."` scan cannot be used here: `finalize.py` alone
    contributes `string`/`number`/`object`/`array` from JSON-schema fragments,
    and a contract inventory that lists non-events is as wrong as one that
    omits events. Two signals mark a real one — the dict is addressed at a
    session, or it is handed to something that emits.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:  # pragma: no cover - the tree is import-checked in CI
        return set()

    found: set[str] = set()
    assigned: dict[str, ast.Dict] = {}

    def collect(node: ast.Dict) -> None:
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "type"
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                found.add(value.value)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned[target.id] = node.value
        if isinstance(node, ast.Dict):
            keys = {
                k.value
                for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
            if keys & _EVENT_ADDRESS_KEYS:
                collect(node)
        if isinstance(node, ast.Call) and _callee_name(node) in _EMIT_CALLS:
            for arg in node.args:
                if isinstance(arg, ast.Dict):
                    collect(arg)
                elif isinstance(arg, ast.Name) and arg.id in assigned:
                    collect(assigned[arg.id])
    return found


def _event_source_files() -> list[Path]:
    """Every module that can put an event on the socket."""
    files = [_GATEWAY]
    for package in (_SERVER_PKG, _AGENT_PKG):
        files.extend(path for path in sorted(package.rglob("*.py")) if path != _GATEWAY)
    return files


def websocket_outbound(source: str | None = None) -> set[str]:
    """Event types the server may emit over the socket.

    ``source`` overrides the gateway text only, for tests that feed a synthetic
    routing chain; service modules are always read from disk.
    """
    text = source if source is not None else _source()
    outbound = set(_WS_OUTBOUND.findall(text))
    for path in _event_source_files():
        if path == _GATEWAY:
            continue
        try:
            outbound |= _event_types_in_module(path.read_text("utf-8"))
        except OSError:  # pragma: no cover
            continue
    return outbound


def inventory() -> dict:
    """The machine-readable surface: every route and event this build exposes.

    ``http_routes`` gets no ``source`` argument so it reads the full route set
    — gateway.py *plus* the modules route branches were extracted into, plus
    their live ``ROUTES`` tables. Handing it the gateway text alone defeated the
    very widening ``_route_sources`` exists for: ``http_routes()`` reported 144
    routes while ``inventory()["http_routes"]`` reported 132, and the 12
    endpoints in kernel_routes.py were absent from the artifact that is supposed
    to be the contract. A surface missing from the inventory is a surface
    nothing checks.

    The two websocket scans keep the gateway text on purpose: inbound types are
    bounded to the socket handler that lives there, and ``websocket_outbound``
    reads the service modules from disk itself.
    """
    text = _source()
    return {
        "http_routes": sorted(http_routes()),
        "ws_inbound": sorted(websocket_inbound(text)),
        "ws_outbound": sorted(websocket_outbound(text)),
    }


def route_family(route: str) -> str:
    """The first stable path segment, e.g. "/frames/([^/]+)/kernel" -> "frames".

    Documentation is organised by family rather than by exact path — a doc that
    had to enumerate every parameterised variant would be unmaintainable and
    would therefore stop being maintained.
    """
    parts = [p for p in route.split("/") if p]
    return parts[0] if parts else ""


def route_families(source: str | None = None) -> set[str]:
    return {
        family
        for family in (route_family(r) for r in http_routes(source))
        if family and not family.startswith("(")
    }


class QueryParamError(ValueError):
    """A client-supplied query parameter that is not what it claims to be.

    Carries the wire shape so a route can answer 400 instead of letting a
    bare `int()` raise into the gateway's catch-all, which prints a
    traceback and answers 500 -- telling the caller the server is broken
    when the request was.
    """

    def __init__(self, name: str, message: str, code: str = "invalid_query_param"):
        super().__init__(message)
        self.name = name
        self.code = code


def int_param(
    values: Any,
    default: int | None = None,
    *,
    name: str = "limit",
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    """One query parameter as an int, clamped, or `QueryParamError`.

    `values` is the raw `parse_qs` list, so callers pass `q.get("limit")`
    directly. The clamp runs *after* parsing, which is the ordering the
    hand-rolled copies got wrong: their repository-side `max(1, min(...))`
    sat downstream of a throw that never reached it.
    """
    raw = (values or [None])[0]
    if raw is None or raw == "":
        return default
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        raise QueryParamError(name, f"{name} must be an integer") from None
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


__all__ = [
    "API_ROOT",
    "QueryParamError",
    "RouteSpec",
    "int_param",
    "declared_http_routes",
    "http_routes",
    "is_complete_matcher",
    "validate_routes",
    "inventory",
    "route_families",
    "route_family",
    "websocket_inbound",
    "websocket_outbound",
]
