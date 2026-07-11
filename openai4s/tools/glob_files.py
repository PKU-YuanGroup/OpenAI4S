"""Workspace filename-globbing control tool."""

from __future__ import annotations

from openai4s.tools.base import Tool
from openai4s.tools.contexts import WorkspaceToolContext


class GlobFilesTool(Tool):
    """Find files by glob while filtering credential-shaped basenames."""

    name = "glob_files"
    host_method = "glob"
    description = "Find workspace files by glob pattern, e.g. '**/*.csv'."
    parameters = {
        "properties": {
            "pattern": {
                "type": "string",
                "minLength": 1,
                "description": "Glob pattern.",
            },
            "path": {
                "type": "string",
                "minLength": 1,
                "description": "Directory to glob under (default the workspace root).",
            },
        },
        "required": ["pattern"],
    }
    permission_target_key = "pattern"
    resource_key_prefix = "workspace"
    resource_target_key = "path"
    resource_target_default = "."

    def execute(self, workspace: WorkspaceToolContext, arguments: dict) -> dict:
        pattern = arguments.get("pattern") or "**/*"
        base = (
            workspace.resolve(arguments.get("path"))
            if arguments.get("path")
            else workspace.workspace()
        )
        matches = []
        for path in sorted(base.glob(pattern)):
            relative = workspace.relative(path) if path.is_file() else None
            if relative is not None and not workspace.is_secret_path(relative):
                matches.append(relative)
        return {
            "pattern": pattern,
            "count": len(matches),
            "matches": matches[:1000],
        }


__all__ = ["GlobFilesTool"]
