"""Web tools: keyless search and single-URL fetch.

Routes to the host `web_search` / `web_fetch` methods, which apply the egress
fence and injection screening to the returned content. Read-only, but they
reach the network.
"""
from __future__ import annotations

from openai4s.tools.base import Tool

web_search = Tool(
    name="web_search",
    host_method="web_search",
    description="Live keyless web search; returns a list of {title, url, snippet}.",
    parameters={
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "num_results": {
                "type": "integer",
                "description": "Maximum results to return (default 8).",
            },
            "timeout": {
                "type": "number",
                "description": "Seconds to wait before giving up (default 20).",
            },
        },
        "required": ["query"],
    },
    read_only=True,
    needs_network=True,
)

web_fetch = Tool(
    name="web_fetch",
    host_method="web_fetch",
    description="Fetch a URL and return its content as markdown/text/html/json.",
    parameters={
        "properties": {
            "url": {"type": "string", "description": "URL to fetch."},
            "format": {
                "type": "string",
                "description": "One of markdown|text|html|json (default markdown).",
            },
            "max_chars": {
                "type": "integer",
                "description": "Truncate the returned content to this many characters.",
            },
            "timeout": {
                "type": "number",
                "description": "Seconds to wait before giving up (default 30).",
            },
        },
        "required": ["url"],
    },
    read_only=True,
    needs_network=True,
)
