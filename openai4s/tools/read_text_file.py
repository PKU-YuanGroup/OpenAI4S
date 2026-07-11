"""UTF-8 workspace file-reading control tool."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openai4s.tools.base import Tool

if TYPE_CHECKING:
    from openai4s.host.files import WorkspaceFileService


class ReadTextFileTool(Tool):
    """Read a bounded line window, preserving the binary-file response shape."""

    name = "read_text_file"
    host_method = "read_file"
    description = "Read a UTF-8 text file from the workspace, optionally a line window."
    parameters = {
        "properties": {
            "path": {"type": "string", "description": "File to read."},
            "offset": {
                "type": "integer",
                "description": "0-based first line to return.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of lines to return.",
            },
        },
        "required": ["path"],
    }

    def execute(self, workspace: "WorkspaceFileService", arguments: dict) -> dict:
        path = workspace.resolve(arguments.get("path", ""), must_exist=True)
        offset = max(0, int(arguments.get("offset") or 0))
        limit = max(1, int(arguments.get("limit") or 2000))
        try:
            data = path.read_bytes()
        except OSError as error:
            return {"error": f"read_file: {error}"}
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            return {
                "path": workspace.relative(path),
                "binary": True,
                "size_bytes": len(data),
                "content": "",
            }
        lines = content.splitlines()
        window = lines[offset : offset + limit]
        return {
            "path": workspace.relative(path),
            "total_lines": len(lines),
            "offset": offset,
            "content": "\n".join(window),
            "truncated": (offset + limit) < len(lines),
        }


__all__ = ["ReadTextFileTool"]
