"""Directory-listing control tool."""

from __future__ import annotations

from openai4s.tools.base import Tool
from openai4s.tools.contexts import WorkspaceToolContext


class ListDirectoryTool(Tool):
    """List one workspace directory without exposing paths outside it."""

    name = "list_dir"
    host_method = "list_dir"
    description = "List the entries of a workspace directory."
    parameters = {
        "properties": {
            "path": {
                "type": "string",
                "minLength": 1,
                "description": "Directory to list, relative to the workspace "
                "(default '.').",
            },
        },
        "required": [],
    }
    permission_target_key = "path"
    permission_target_default = "."
    resource_key_prefix = "workspace"
    resource_target_key = "path"
    resource_target_default = "."

    def execute(self, workspace: WorkspaceToolContext, arguments: dict) -> dict:
        relative = arguments.get("path") or "."
        base = workspace.resolve(relative) if relative != "." else workspace.workspace()
        if not base.exists():
            return {"error": f"list_dir: no such directory: {relative}"}
        entries = []
        for path in sorted(base.iterdir()):
            entries.append(
                {
                    "name": path.name,
                    "path": workspace.relative(path) or path.name,
                    "is_dir": path.is_dir(),
                    "size_bytes": path.stat().st_size if path.is_file() else None,
                }
            )
        return {"path": relative, "count": len(entries), "entries": entries}


__all__ = ["ListDirectoryTool"]
