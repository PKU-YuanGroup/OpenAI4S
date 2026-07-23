"""The externally-reachable HTTP/WebSocket surface, extracted from the code.

The proposal requires that every external route and event be covered by a
contract inventory, and that the inventory be *checkable* rather than a list
someone maintains by hand. A hand-maintained list is wrong the first time
somebody adds a route in a hurry, and its being wrong is invisible — which is
the failure mode a contract exists to prevent.

So the inventory is derived from the gateway source instead of duplicated
beside it. It is deliberately a **static** read of the routing chain rather than
runtime introspection: `Handler._api` is a long `if` / `re.fullmatch` chain
inside a closure, so there is no route table to enumerate at runtime, and
importing the module to walk it would not tell us which branches are reachable.
Parsing the source is the honest approximation, and it fails loudly (empty
inventory) if the routing style ever changes enough to invalidate it.

The risk this carries is worth naming, because it bit on the first run: an
extractor that misses an idiom reports *full coverage of an incomplete
inventory* — false confidence, which is worse than no check. The first version
handled only `sub == "..."` and `re.fullmatch`, and silently omitted `/frames`
(matched query-aware as `sub.split("?")[0] == "/frames"`), the `sub in (...)`
tuples, and `sub.startswith(...)`. A test asserting that a few obviously-present
routes are found is what caught it, and is why that test exists alongside the
coverage assertions rather than being folded into them.

What this is not: a schema. It answers "which paths exist", not "what shape do
they return". Response schemas are the next layer of §4.6 and are not inferable
from a routing chain.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

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
# `re.fullmatch(r"/frames/([^/]+)/kernel", sub)` — a parameterised route. Only
# patterns anchored at "/" are routes; the file also uses fullmatch to validate
# hashes and identifiers, and those must not be mistaken for surface.
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
_ROUTE_MODULES = ("kernel_routes.py",)


def _route_sources() -> list[str]:
    """gateway.py plus every module that owns extracted route branches."""
    texts = [_GATEWAY.read_text("utf-8")]
    for name in _ROUTE_MODULES:
        path = _SERVER_PKG / name
        if path.is_file():
            texts.append(path.read_text("utf-8"))
    return texts


def _source() -> str:
    return _GATEWAY.read_text("utf-8")


def http_routes(source: str | None = None) -> set[str]:
    """Every path the HTTP surface can match, relative to the API root."""
    text = source if source is not None else "\n".join(_route_sources())
    routes = set(_EXACT.findall(text)) | set(_PATTERN.findall(text))
    routes |= set(_PREFIX.findall(text))
    for group in _MEMBERSHIP.findall(text):
        routes |= set(_MEMBER_ITEM.findall(group))
    # A route is a path; anything else that slipped through is not surface.
    return {r for r in routes if r.startswith("/")}


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


def _callee_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


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

    ``source`` overrides the gateway text only, for the tests that feed a
    synthetic routing chain; the service modules are always read from disk,
    since there is no single text that could stand in for all of them.
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
    — gateway.py *plus* the modules route branches were extracted into.
    Handing it the gateway text alone defeated the very widening
    ``_route_sources`` exists for: ``http_routes()`` reported 144 routes while
    ``inventory()["http_routes"]`` reported 132, and the 12 endpoints in
    kernel_routes.py were absent from the artifact that is supposed to be the
    contract. A surface missing from the inventory is a surface nothing checks.

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


__all__ = [
    "http_routes",
    "inventory",
    "route_families",
    "route_family",
    "websocket_inbound",
    "websocket_outbound",
]
