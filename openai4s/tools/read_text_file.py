"""UTF-8 workspace file-reading control tool."""

from __future__ import annotations

from pathlib import Path

from openai4s.tools.base import Tool
from openai4s.tools.contexts import WorkspaceToolContext

#: How much decoded text one window may return. `limit` bounds how many lines
#: come back and says nothing about how long they are, so a thousand-line
#: window over megabyte-long lines was a gigabyte of "bounded" reply.
_MAX_CONTENT_CHARS = 400_000


class ReadTextFileTool(Tool):
    """Read a bounded line window, preserving the binary-file response shape."""

    name = "read_text_file"
    host_method = "read_file"
    description = "Read a UTF-8 text file from the workspace, optionally a line window."
    parameters = {
        "properties": {
            "path": {
                "type": "string",
                "minLength": 1,
                "description": "File to read.",
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "description": "0-based first line to return.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10000,
                "description": "Maximum number of lines to return.",
            },
        },
        "required": ["path"],
    }
    permission_target_key = "path"
    secret_path_key = "path"
    resource_key_prefix = "workspace"
    resource_target_key = "path"

    def execute(self, workspace: WorkspaceToolContext, arguments: dict) -> dict:
        # Deferred: `openai4s.host` is a ~40 ms package import and
        # `openai4s.tools` sits on the CLI's startup path, while by the time a
        # tool executes the dispatcher has already imported it. `host.files`
        # reaches into `tools.registry` the same way, from the other side.
        from openai4s.host.files import BoundedTextReader

        path = workspace.resolve(arguments.get("path", ""), must_exist=True)
        offset = max(0, int(arguments.get("offset") or 0))
        limit = max(1, int(arguments.get("limit") or 2000))
        window_end = offset + limit
        # Streamed under a byte budget instead of `read_bytes()` -> `decode()`
        # -> `splitlines()` -> slice. Measured before this: returning two lines
        # of a 256 MB output peaked at 768 MB in the daemon -- the bytes, the
        # decoded string and the line list -- for a ten-character reply.
        reader = BoundedTextReader(path)
        window: list[str] = []
        retained_chars = 0
        content_truncated = False
        try:
            for index, line in enumerate(reader.lines()):
                # Lines past the window are still counted, never kept: that is
                # what keeps `total_lines` meaningful inside the budget.
                if index < offset or index >= window_end or content_truncated:
                    continue
                room = _MAX_CONTENT_CHARS - retained_chars
                if len(line) <= room:
                    window.append(line)
                    retained_chars += len(line)
                    continue
                # One line larger than the whole budget must still be cut, or
                # the bound is defeated by a single no-newline blob.
                if room > 0:
                    window.append(line[:room])
                    retained_chars += room
                content_truncated = True
        except UnicodeDecodeError:
            return {
                "path": workspace.relative(path),
                "binary": True,
                "size_bytes": self._size_bytes(path, reader),
                "content": "",
            }
        except OSError as error:
            return {"error": f"read_file: {error}"}
        result = {
            "path": workspace.relative(path),
            "total_lines": reader.lines_read,
            "offset": offset,
            "content": "\n".join(window),
            "truncated": (
                content_truncated
                or reader.budget_exhausted
                or window_end < reader.lines_read
            ),
        }
        if content_truncated or reader.budget_exhausted:
            # Only when a budget actually bit, so an ordinary read keeps the
            # shape callers already parse. The counters are `jobs.py`'s:
            # `truncated` alone says something was dropped but not how much,
            # and here it also cannot say that `total_lines` stopped being a
            # total -- `scanned_bytes` under `size_bytes` is what says that.
            result["budget_truncated"] = True
            result["seen_chars"] = reader.chars_read
            result["retained_chars"] = retained_chars
            result["dropped_chars"] = max(0, reader.chars_read - retained_chars)
            result["scanned_bytes"] = reader.bytes_read
            result["size_bytes"] = self._size_bytes(path, reader)
            if reader.long_line_split:
                result["long_line_split"] = True
        return result

    @staticmethod
    def _size_bytes(path: Path, reader) -> int:
        """The file's real size, falling back to what was actually read.

        `read_bytes()` knew the size because it held the file; a streaming read
        has to ask, and a reply that cannot say how much it did not read is the
        shape this whole change exists to remove.
        """
        try:
            return path.stat().st_size
        except OSError:
            return reader.bytes_read


__all__ = ["ReadTextFileTool"]
