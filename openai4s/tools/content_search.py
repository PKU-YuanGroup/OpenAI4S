"""Workspace regex-search control tool."""

from __future__ import annotations

import re

from openai4s.tools.base import Tool
from openai4s.tools.contexts import WorkspaceToolContext


class ContentSearchTool(Tool):
    """Regex-search UTF-8 workspace files and return bounded structured hits."""

    name = "content_search"
    host_method = "grep"
    description = "Regex-search the contents of workspace files."
    parameters = {
        "properties": {
            "pattern": {
                "type": "string",
                "minLength": 1,
                "description": "Regular expression.",
            },
            "path": {
                "type": "string",
                "minLength": 1,
                "description": "Directory to search under (default the workspace root).",
            },
            "include": {
                "type": "string",
                "minLength": 1,
                "description": "Glob limiting which files are searched, e.g. '*.py'.",
            },
        },
        "required": ["pattern"],
    }
    permission_target_key = "pattern"
    resource_key_prefix = "workspace"
    resource_target_key = "path"
    resource_target_default = "."

    def execute(self, workspace: WorkspaceToolContext, arguments: dict) -> dict:
        pattern = arguments.get("pattern") or ""
        if not pattern:
            return {"error": "grep: empty pattern"}
        try:
            regex = re.compile(pattern)
        except re.error as error:
            return {"error": f"grep: bad regex: {error}"}
        include = arguments.get("include")
        base = (
            workspace.resolve(arguments.get("path"))
            if arguments.get("path")
            else workspace.workspace()
        )
        hits: list[dict] = []
        paths = base.glob(include) if include else base.rglob("*")
        for path in sorted(paths):
            if not path.is_file():
                continue
            relative = workspace.relative(path)
            if relative is None or workspace.is_secret_path(relative):
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for line_number, line in enumerate(content.splitlines(), 1):
                if regex.search(line):
                    hits.append(
                        {"file": relative, "line": line_number, "text": line[:400]}
                    )
                    if len(hits) >= 200:
                        return {
                            "pattern": pattern,
                            "count": len(hits),
                            "matches": hits,
                            "truncated": True,
                        }
        return {"pattern": pattern, "count": len(hits), "matches": hits}


__all__ = ["ContentSearchTool"]
