"""Workspace filename-globbing control tool."""

from __future__ import annotations

from openai4s.tools.base import Tool
from openai4s.tools.contexts import WorkspaceToolContext

#: How many matches a single glob returns. Named rather than inline so the
#: bound and the `truncated` flag that reports it cannot drift apart.
_MAX_MATCHES = 1000


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
        # `count` used to be the PRE-slice total beside a sliced list, with no
        # `truncated` key: a 5000-file glob answered `count: 5000` next to 1000
        # entries, and the UI printed "5000 items" over 1000 rows. It also
        # disagreed with `content_search`, whose `count` is the retained
        # number -- one field name, two meanings, in the same tool family.
        #
        # `count` is now what was returned, everywhere. `total_count` keeps the
        # information the old field carried, and `truncated` says plainly that
        # the two differ.
        returned = matches[:_MAX_MATCHES]
        result = {
            "pattern": pattern,
            "count": len(returned),
            "total_count": len(matches),
            "matches": returned,
        }
        if len(returned) < len(matches):
            result["truncated"] = True
        return result


__all__ = ["GlobFilesTool"]
