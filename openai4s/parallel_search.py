"""Opt-in anonymous Parallel Search via the project's stdlib MCP transport."""

from __future__ import annotations

import json
import time

from openai4s.mcp_http import MCPHTTPConnection
from openai4s.mcp_protocol import MCPError

ENDPOINT = "https://mcp.parallel.ai/mcp"
USER_AGENT = (
    "OpenAI4S/0.3.1 (https://github.com/PKU-YuanGroup/OpenAI4S; Parallel search)"
)


def search(query: str, num_results: int, timeout: float) -> list[dict]:
    """Search anonymously, retaining source excerpts in the normal snippet field.

    Fast is the service's documented anonymous default. No credentials, extra
    transports, fetch tools or provider fallback are introduced here. MCP and
    HTTP failures surface through WebSearchTool's existing soft-error envelope.
    """
    if timeout <= 0:
        return []
    deadline = time.monotonic() + timeout
    connection = MCPHTTPConnection(
        ENDPOINT, headers={"User-Agent": USER_AGENT}, timeout=timeout
    )
    try:
        connection._timeout = max(0.001, deadline - time.monotonic())
        tools = connection.list_tools()
        if not any(tool.get("name") == "web_search" for tool in tools):
            raise MCPError("Parallel MCP does not expose web_search")
        connection._timeout = max(0.001, deadline - time.monotonic())
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
            out.append(
                {"url": url, "title": row.get("title") or url, "snippet": snippet}
            )
            if len(out) >= num_results:
                break
        return out
    finally:
        connection.close()
