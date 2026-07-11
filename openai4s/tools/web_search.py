"""Keyless live web-search control tool."""

from __future__ import annotations

from typing import Any

from openai4s.tools.base import Tool


class WebSearchTool(Tool):
    """Normalize search arguments and preserve the host soft-fail contract."""

    name = "web_search"
    host_method = "web_search"
    description = "Live keyless web search; returns a list of {title, url, snippet}."
    parameters = {
        "properties": {
            "query": {
                "type": "string",
                "minLength": 1,
                "description": "Search query.",
            },
            "num_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": "Maximum results to return (default 8).",
            },
            "timeout": {
                "type": "number",
                "minimum": 1,
                "maximum": 120,
                "description": "Seconds to wait before giving up (default 20).",
            },
        },
        "required": ["query"],
    }
    needs_network = True
    screen_untrusted_output = True
    permission_target_key = "query"
    resource_key_prefix = "network"
    resource_target_default = "search"

    def execute(self, _runtime: Any, arguments: dict) -> dict:
        from openai4s import egress, webtools

        try:
            return webtools.web_search(
                arguments.get("query", ""),
                num_results=int(arguments.get("num_results") or 8),
                timeout=float(arguments.get("timeout") or 20),
            )
        except (webtools.NetworkDisabled, egress.EgressBlocked) as error:
            return {"error": str(error)}
        except Exception as error:  # noqa: BLE001
            return {"error": f"web_search: {error}"}


__all__ = ["WebSearchTool"]
