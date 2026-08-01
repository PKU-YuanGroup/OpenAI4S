"""Directory-listing control tool."""

from __future__ import annotations

import os
import time
from pathlib import Path

from openai4s.tools.base import Tool
from openai4s.tools.contexts import WorkspaceToolContext

#: How many entries one listing returns. `list_dir` had no cap at all: it
#: sorted every entry and built a dict and a `stat()` per entry, so listing a
#: results directory with a million files built a million dicts in the daemon
#: to produce a reply nothing could read. The number is `glob`'s, so the two
#: tools truncate at the same place and report it the same way.
_MAX_ENTRIES = 1000


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
        from openai4s.host.files import (
            MAX_SCAN_ENTRIES,
            MAX_SCAN_SECONDS,
            BoundedSelection,
        )

        relative = arguments.get("path") or "."
        base = workspace.resolve(relative) if relative != "." else workspace.workspace()
        if not base.exists():
            return {"error": f"list_dir: no such directory: {relative}"}
        selection = BoundedSelection(_MAX_ENTRIES)
        scan_truncated = False
        try:
            deadline = time.monotonic() + MAX_SCAN_SECONDS
            with os.scandir(base) as scan:
                for entry in scan:
                    # Seconds as well as entries: see `MAX_SCAN_SECONDS`. One
                    # directory can hold enough cold entries that `scandir`
                    # outlives the caller's timeout well under the entry cap.
                    if (
                        selection.seen >= MAX_SCAN_ENTRIES
                        or time.monotonic() > deadline
                    ):
                        scan_truncated = True
                        break
                    selection.offer(entry.name, entry)
        except OSError as error:
            # Was an unhandled `NotADirectoryError` when the path named a file.
            # Soft-failing keeps it in the same shape as the missing-directory
            # answer just above, which is the same mistake by the agent.
            return {"error": f"list_dir: {error}"}
        entries = []
        # A `stat()` only for what survived the bound. It used to be paid for
        # every entry in the directory, including the ones no reply mentioned.
        for entry in selection.values():
            path = Path(entry.path)
            entries.append(
                {
                    "name": entry.name,
                    "path": workspace.relative(path) or entry.name,
                    "is_dir": entry.is_dir(),
                    "size_bytes": entry.stat().st_size if entry.is_file() else None,
                }
            )
        result = {"path": relative}
        result.update(selection.counters(scan_truncated=scan_truncated))
        result["entries"] = entries
        return result


__all__ = ["ListDirectoryTool"]
