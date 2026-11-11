"""Opt-in anonymous Parallel Search via the project's stdlib MCP transport."""

from __future__ import annotations

import json
import threading
import time

from openai4s import __version__
from openai4s.mcp_http import MCPHTTPConnection
from openai4s.mcp_protocol import MCPError

ENDPOINT = "https://mcp.parallel.ai/mcp"
USER_AGENT = (
    f"OpenAI4S/{__version__} "
    "(https://github.com/PKU-YuanGroup/OpenAI4S; Parallel search)"
)
#: Per-exchange ceiling; every search also bounds the session by its deadline.
_EXCHANGE_TIMEOUT = 15.0
#: Idle sessions are reused so a search costs one round trip, not three. They
#: are short-lived (a stale server-side session would only cost a re-handshake)
#: and few (concurrent searches each check out their own).
_IDLE_TTL = 60.0
_MAX_IDLE = 2
_idle: list[tuple[str, MCPHTTPConnection, float]] = []
_idle_lock = threading.Lock()


def _checkout(deadline: float) -> MCPHTTPConnection:
    now = time.monotonic()
    stale: list[MCPHTTPConnection] = []
    reused = None
    with _idle_lock:
        while _idle:
            endpoint, connection, parked = _idle.pop()
            if (
                reused is None
                and endpoint == ENDPOINT
                and now - parked < _IDLE_TTL
                and not connection.faulted()
            ):
                reused = connection
            else:
                stale.append(connection)
    for connection in stale:
        connection.close()
    if reused is not None:
        reused.set_deadline(deadline)
        return reused
    return MCPHTTPConnection(
        ENDPOINT,
        headers={"User-Agent": USER_AGENT},
        timeout=_EXCHANGE_TIMEOUT,
        deadline=deadline,
    )


def _checkin(connection: MCPHTTPConnection) -> None:
    connection.set_deadline(None)
    with _idle_lock:
        if len(_idle) < _MAX_IDLE and not connection.faulted():
            _idle.append((ENDPOINT, connection, time.monotonic()))
            return
    connection.close()


def search(query: str, num_results: int, timeout: float) -> list[dict]:
    """Search anonymously, retaining source excerpts in the normal snippet field.

    Fast is the service's documented anonymous default. No credentials, extra
    transports, fetch tools or provider fallback are introduced here. MCP and
    HTTP failures raise ``MCPError``; ``webtools.web_search`` records the reason
    and moves on to the next switched-on engine.
    """
    if timeout <= 0:
        return []
    connection = _checkout(time.monotonic() + timeout)
    healthy = False
    try:
        # A missing tool already fails the call with a JSON-RPC error, so no
        # separate tools/list probe is spent on every search.
        reply = connection.call_tool(
            "web_search", {"objective": query, "search_queries": [query]}
        )
        if reply["is_error"]:
            raise MCPError("Parallel web_search returned a tool error")
        payload = reply["raw"].get("structuredContent")
        if not isinstance(payload, dict):
            try:
                payload = json.loads(reply["text"])
            except (ValueError, TypeError):
                raise MCPError(
                    "Parallel web_search returned invalid search data"
                ) from None
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise MCPError("Parallel web_search returned no results list")
        out = []
        for row in results:
            if not isinstance(row, dict):
                continue
            url = row.get("url")
            excerpts = row.get("excerpts", [])
            if not isinstance(url, str) or not url.startswith(("https://", "http://")):
                continue
            if isinstance(excerpts, str):
                excerpts = [excerpts]
            snippet = (
                "\n".join(e for e in excerpts if isinstance(e, str))
                if isinstance(excerpts, list)
                else ""
            )
            title = row.get("title")
            title = title.strip() if isinstance(title, str) else ""
            # Same shape and snippet cap as the Tavily/You.com mappers.
            out.append(
                {"url": url, "title": title or url, "snippet": snippet.strip()[:500]}
            )
            if len(out) >= num_results:
                break
        healthy = True
        return out
    finally:
        if healthy:
            _checkin(connection)
        else:
            connection.close()
