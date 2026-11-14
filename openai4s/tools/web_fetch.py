"""Single-URL web-fetch control tool."""

from __future__ import annotations

import re
from typing import Any

from openai4s.tools.base import Tool
from openai4s.tools.taxonomy import resource_key


class WebFetchTool(Tool):
    """Normalize fetch arguments and preserve the host soft-fail contract."""

    name = "web_fetch"
    host_method = "web_fetch"
    description = "Fetch a URL and return its content as markdown/text/html/json."
    parameters = {
        "properties": {
            "url": {
                "type": "string",
                "minLength": 1,
                "description": "URL to fetch.",
            },
            "format": {
                "type": "string",
                "enum": ["markdown", "text", "html", "json"],
                "description": "One of markdown|text|html|json (default markdown).",
            },
            "max_chars": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1000000,
                "description": "Truncate the returned content to this many characters.",
            },
            "timeout": {
                "type": "number",
                "minimum": 1,
                "maximum": 120,
                "description": "Seconds to wait before giving up (default 30).",
            },
            "method": {
                "type": "string",
                "enum": ["GET", "HEAD"],
                "description": (
                    "HEAD asks only whether the resource exists, without "
                    "downloading it."
                ),
            },
            "user_agent": {
                "type": "string",
                "description": (
                    "Override the User-Agent. Some scholarly APIs serve their "
                    "polite pool only to callers that send a contactable one."
                ),
            },
        },
        "required": ["url"],
    }
    needs_network = True
    screen_untrusted_output = True

    def permission_target(self, arguments: Any) -> str:
        if not isinstance(arguments, dict):
            return ""
        url = str(arguments.get("url") or "")
        return re.sub(r"^https?://(www\.)?", "", url).split("/")[0]

    def resource_keys(self, arguments: Any) -> tuple[str, ...]:
        return (resource_key("network", self.permission_target(arguments)),)

    def render_observation(self, result: Any) -> str | None:
        """Say how to get the rest of a page the tool result cannot hold.

        A tool result holds at most ``output_limit`` characters whatever
        ``max_chars`` asks for, and the shared renderer cut a long page with a
        bare marker. Kimi K3.1 Pro then repeated the identical call, asking for
        a million characters each time, until the progress circuit stopped the
        turn. A Python cell's ``host.web_fetch`` receives the whole content.
        """
        if not isinstance(result, dict) or not isinstance(result.get("content"), str):
            return None
        from openai4s.tools.registry import _render_result_body

        header = len(f"[Tool: {self.name}]\n")
        body = _render_result_body(result)
        if header + len(body) <= self.output_limit and not result.get("truncated"):
            return None
        size = result.get("raw_bytes")
        whole = f"The response is {size:,} bytes. " if isinstance(size, int) else ""
        note = (
            f"\n… [truncated. {whole}A tool result holds at most "
            f"{self.output_limit:,} characters whatever max_chars asks, so "
            "repeating this call returns the same text. To use the whole "
            "document, call host.web_fetch(url, format=..., max_chars=...) in a "
            "```python cell and parse it there.]"
        )
        overflow = header + len(body) + len(note) - self.output_limit
        if overflow > 0:
            content = result["content"]
            keep = max(0, len(content) - overflow)
            body = _render_result_body({**result, "content": content[:keep]})
        return body + note

    def execute(self, runtime: Any, arguments: dict) -> dict:
        from openai4s import egress, webtools

        try:
            return runtime.fetch_web(
                arguments.get("url", ""),
                fmt=arguments.get("format", "markdown"),
                timeout=float(arguments.get("timeout") or 30),
                max_chars=int(arguments.get("max_chars") or 20000),
                method=arguments.get("method") or "GET",
                user_agent=arguments.get("user_agent") or None,
            )
        except (
            webtools.NetworkDisabled,
            webtools.SSRFBlocked,
            webtools.ResponseTooLarge,
            egress.EgressBlocked,
        ) as error:
            return {"error": str(error)}
        except Exception as error:  # noqa: BLE001
            return {"error": f"web_fetch: {error}"}


__all__ = ["WebFetchTool"]
