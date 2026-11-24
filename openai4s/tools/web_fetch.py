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
            "offset": {
                "type": "integer",
                "minimum": 0,
                "description": (
                    "Start this many characters into the document, to read on "
                    "from a truncated result's next_offset."
                ),
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
        turn. The note names the character the shown text stops at, of how
        many, and the ``offset`` that reads on (CLI-3); a Python cell's
        ``host.web_fetch`` can also take the whole content at once.
        """
        if not isinstance(result, dict) or not isinstance(result.get("content"), str):
            return None
        content_type = str(result.get("content_type") or "").lower()
        if content_type.startswith("application/pdf") or result["content"].startswith(
            "%PDF-"
        ):
            # The raw PDF stream, cut at the tool limit, was all the model got:
            # it spent turns trying to rebuild the file from the first 20 KB of
            # a 1.6 MB response, and went looking for another place to get it.
            size = result.get("raw_bytes")
            how = f"{size:,} bytes" if isinstance(size, int) else "binary"
            return (
                f"[PDF, {how}] web_fetch cannot read a PDF as text; what it "
                "returned is the raw file, not words. To read it, save it with "
                "web_download (needs approval) and extract the text with pypdf "
                "in a ```python cell. Otherwise look for an HTML or abstract "
                "page of the same paper."
            )
        from openai4s.tools.registry import _render_result_body

        header = len(f"[Tool: {self.name}]\n")
        body = _render_result_body(result)
        if header + len(body) <= self.output_limit and not result.get("truncated"):
            return None
        content = result["content"]
        start = int(result.get("offset") or 0)
        total = result.get("total_chars")
        size = result.get("raw_bytes")

        def note_for(shown: int) -> str:
            # CLI-3: where the text the model actually sees stops, of how
            # much, and the offset that reads on from there.
            end = start + shown
            of = f" of {total}" if isinstance(total, int) else ""
            whole = f" The response is {size:,} bytes." if isinstance(size, int) else ""
            return (
                f"\n… [truncated at {end}{of} chars.{whole} Call web_fetch again "
                f"with offset={end} to read on; a tool result holds at most "
                f"{self.output_limit:,} characters whatever max_chars asks. To "
                "use the whole document at once, call host.web_fetch(url, "
                "format=..., max_chars=...) in a ```python cell and parse it "
                "there.]"
            )

        keep = len(content)
        overflow = header + len(body) + len(note_for(keep)) - self.output_limit
        if overflow > 0:
            keep = max(0, len(content) - overflow)
            # The note can only grow by a digit or two as `keep` shrinks.
            while keep and (
                header
                + len(_render_result_body({**result, "content": content[:keep]}))
                + len(note_for(keep))
                > self.output_limit
            ):
                keep -= 1
            body = _render_result_body({**result, "content": content[:keep]})
        return body + note_for(keep)

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
                offset=int(arguments.get("offset") or 0),
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
