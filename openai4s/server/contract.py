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

import re
from pathlib import Path

_GATEWAY = Path(__file__).with_name("gateway.py")

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
# WebSocket client messages are dispatched on `t == "view_session"`.
_WS_INBOUND = re.compile(r't\s*==\s*"([a-z_]+)"')
# Server-emitted events carry their own type.
_WS_OUTBOUND = re.compile(r'"type"\s*:\s*"([a-z_]+)"')


def _source() -> str:
    return _GATEWAY.read_text("utf-8")


def http_routes(source: str | None = None) -> set[str]:
    """Every path `Handler._api` can match, relative to the API root."""
    text = source if source is not None else _source()
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
    return set(_WS_INBOUND.findall(text[start:]))


def websocket_outbound(source: str | None = None) -> set[str]:
    """Event types the server may emit over the socket."""
    text = source if source is not None else _source()
    return set(_WS_OUTBOUND.findall(text))


def inventory() -> dict:
    text = _source()
    return {
        "http_routes": sorted(http_routes(text)),
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
