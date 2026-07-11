"""Workspace file-writing control tool."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openai4s.tools.base import Tool

if TYPE_CHECKING:
    from openai4s.host.files import WorkspaceFileService


class WriteFileTool(Tool):
    """Create or overwrite one UTF-8 file inside the session workspace."""

    name = "write_file"
    host_method = "write_file"
    description = "Create or overwrite a workspace file with the given content."
    parameters = {
        "properties": {
            "path": {"type": "string", "description": "File to write."},
            "content": {"type": "string", "description": "Full file contents."},
        },
        "required": ["path", "content"],
    }
    read_only = False
    writes_files = True

    def execute(self, workspace: "WorkspaceFileService", arguments: dict) -> dict:
        path = workspace.resolve(arguments.get("path", ""))
        content = arguments.get("content", "")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {
            "path": workspace.relative(path),
            "bytes": len(content.encode("utf-8")),
        }


__all__ = ["WriteFileTool"]
