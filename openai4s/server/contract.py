"""The externally-reachable HTTP/WebSocket surface.

The proposal requires that every external route and event be covered by a
contract inventory, and that the inventory be *checkable* rather than a list
someone maintains by hand. A hand-maintained list is wrong the first time
somebody adds a route in a hurry, and its being wrong is invisible — which is
the failure mode a contract exists to prevent.

HTTP routing is being migrated incrementally to declarative ``RouteSpec``
objects. A route module that exports ``ROUTES`` is inventoried from those exact
runtime declarations. Legacy gateway branches and route modules without a
registry still use the static source extractor, so the migration does not force
a high-risk rewrite of the whole routing chain in one change.

The source fallback remains deliberately strict. An extractor that misses an
idiom reports *full coverage of an incomplete inventory* — false confidence,
which is worse than no check. The first version handled only ``sub == ...`` and
``re.fullmatch`` and silently omitted several real spellings. Tests therefore
pin both obvious routes and the extraction properties while the declarative
surface grows.

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


@dataclass(frozen=True)
class RouteSpec:
    """One HTTP method + path matcher shared by routing and contract code."""

    name: str
    method: str
    pattern: str

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("route name must be non-empty")
        if self.method != self.method.upper() or not self.method.isalpha():
            raise ValueError(
                f"route method must be uppercase HTTP verb: {self.method!r}"
            )
        if not self.pattern.startswith("/"):
            raise ValueError(f"route pattern must start with '/': {self.pattern!r}")
        try:
            re.compile(self.pattern)
        except re.error as exc:
            raise ValueError(f"invalid route pattern {self.pattern!r}: {exc}") from exc

    def match(self, method: str, path: str) -> re.Match[str] | None:
        """Match only when both the HTTP method and path belong to this route.

        Wrong-method matches deliberately return ``None``. The gateway's route
        chain must then continue to its ordinary 404 instead of treating a path
        match as a handled request.
        """
        if method != self.method:
            return None
        return re.fullmatch(self.pattern, path)


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


#: Modules that hold route branches carved out of `Handler._api`. Declarative
#: modules expose ``ROUTES``; legacy ones continue to be source-scanned until
#: they are migrated. Discovery stays convention-based so adding a route module
#: cannot require remembering a second list.
_ROUTE_MODULE_GLOB = "*_routes.py"

#: Modules that use the same routing idioms but are **not** reachable through
#: `Handler._route`, so their paths are a different surface. `ShareRouter` is
#: constructed for the outbound tunnel client and dispatched to directly; its
#: `/api/artifacts/([^/]+)` never passes through the gateway chain.
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


def declared_http_routes() -> tuple[RouteSpec, ...]:
    """All executable RouteSpec declarations on the gateway HTTP surface."""
    specs: list[RouteSpec] = []
    for name in _route_modules():
        specs.extend(_route_module_specs(name))
    return tuple(specs)


def _route_sources() -> list[str]:
    """Gateway plus only route modules that still need source extraction."""
    texts = [_GATEWAY.read_text("utf-8")]
    for name in _route_modules():
        if _route_module_specs(name):
            continue
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
    """Read constant RouteSpec patterns from a supplied source fragment.

    Normal inventory does not depend on this parser for declarative modules —
    it reads their live ``ROUTES`` table. This helper keeps source-oriented
    contract tests meaningful and lets them compare a module in isolation.
    """
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
    """Can this entry stand for a route at all?"""
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
        declared = _route_spec_patterns(text)
    routes = declared | set(_EXACT.findall(text)) | _fullmatch_patterns(text)
    routes |= set(_PREFIX.findall(text))
    for group in _MEMBERSHIP.findall(text):
        routes |= set(_MEMBER_ITEM.findall(group))
    return {route for route in routes if is_complete_matcher(route)}


def websocket_inbound(source: str | None = None) -> set[str]:
    """Message types a client may send over the socket."""
    text = source if source is not None else _source()
    start = text.find("def _handle_ws")
    if start < 0:
        return set()
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
    """Event type literals in one module, by AST rather than by regex."""
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
    """The machine-readable surface: every route and event this build exposes."""
    text = _source()
    return {
        "http_routes": sorted(http_routes()),
        "ws_inbound": sorted(websocket_inbound(text)),
        "ws_outbound": sorted(websocket_outbound(text)),
    }


def route_family(route: str) -> str:
    """The first stable path segment, e.g. "/frames/([^/]+)/kernel" -> "frames"."""
    parts = [p for p in route.split("/") if p]
    return parts[0] if parts else ""


def route_families(source: str | None = None) -> set[str]:
    return {
        family
        for family in (route_family(r) for r in http_routes(source))
        if family and not family.startswith("(")
    }


__all__ = [
    "RouteSpec",
    "declared_http_routes",
    "http_routes",
    "inventory",
    "route_families",
    "route_family",
    "websocket_inbound",
    "websocket_outbound",
]
