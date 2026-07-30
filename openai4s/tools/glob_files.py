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
        from openai4s.host.files import MAX_SCAN_ENTRIES, BoundedSelection

        pattern = arguments.get("pattern") or "**/*"
        base = (
            workspace.resolve(arguments.get("path"))
            if arguments.get("path")
            else workspace.workspace()
        )
        # `sorted(base.glob(pattern))` built and ordered every match in the
        # tree before slicing to `_MAX_MATCHES`, so a directory of a million
        # files cost a million retained paths to answer a thousand-path
        # question. The generator is consumed one path at a time now and only
        # the smallest `_MAX_MATCHES` names are ever held -- same answer, same
        # order, bounded memory.
        matches = BoundedSelection(_MAX_MATCHES)
        scanned = 0
        scan_truncated = False
        for path in base.glob(pattern):
            scanned += 1
            if scanned > MAX_SCAN_ENTRIES:
                # Walking the tree is unbounded work, not merely unbounded
                # memory; stopping silently would make a partial answer look
                # exhaustive, so the receipt below says the walk was cut.
                scan_truncated = True
                break
            if not path.is_file():
                continue
            relative = workspace.relative(path)
            if relative is None or workspace.is_secret_path(relative):
                continue
            matches.offer(relative)
        # `count` used to be the PRE-slice total beside a sliced list, with no
        # `truncated` key: a 5000-file glob answered `count: 5000` next to 1000
        # entries, and the UI printed "5000 items" over 1000 rows. It also
        # disagreed with `content_search`, whose `count` is the retained
        # number -- one field name, two meanings, in the same tool family.
        #
        # `count` is now what was returned, everywhere. `total_count` keeps the
        # information the old field carried, `dropped` says how much the two
        # differ by, and `truncated` says plainly that they do.
        result = {"pattern": pattern}
        result.update(matches.counters(scan_truncated=scan_truncated))
        result["matches"] = matches.values()
        return result


__all__ = ["GlobFilesTool"]
