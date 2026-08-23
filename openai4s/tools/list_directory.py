"""Directory-listing control tool."""

from __future__ import annotations

import os
import stat
import time

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
    description = (
        "List entries in one workspace directory only. For the Skill catalog, "
        "use list_skills."
    )
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
            UnsafeWorkspaceCandidate,
        )

        relative = arguments.get("path") or "."
        try:
            directory = workspace.open_verified_directory(relative)
        except FileNotFoundError:
            return {"error": f"list_dir: no such directory: {relative}"}
        selection = BoundedSelection(_MAX_ENTRIES)
        scan_truncated = False
        scanned = 0
        try:
            with directory:
                deadline = time.monotonic() + MAX_SCAN_SECONDS
                with os.scandir(directory.fd) as scan:
                    for entry in scan:
                        scanned += 1
                        # Seconds as well as entries: see `MAX_SCAN_SECONDS`. One
                        # directory can hold enough cold entries that `scandir`
                        # outlives the caller's timeout well under the entry cap.
                        if scanned > MAX_SCAN_ENTRIES or time.monotonic() > deadline:
                            scan_truncated = True
                            break
                        try:
                            metadata = directory.inspect_entry(entry.name)
                        except UnsafeWorkspaceCandidate:
                            continue
                        except ValueError as error:
                            return {"error": f"list_dir: {error}"}
                        selection.offer(entry.name, (entry.name, metadata))
        except OSError as error:
            # Was an unhandled `NotADirectoryError` when the path named a file.
            # Soft-failing keeps it in the same shape as the missing-directory
            # answer just above, which is the same mistake by the agent.
            return {"error": f"list_dir: {error}"}
        entries = []
        # Metadata was captured with a no-follow stat through the same pinned
        # directory FD during enumeration; never reopen a retained pathname.
        for name, metadata in selection.values():
            entry_path = (
                name if directory.relative == "." else f"{directory.relative}/{name}"
            )
            entries.append(
                {
                    "name": name,
                    "path": entry_path,
                    "is_dir": stat.S_ISDIR(metadata.st_mode),
                    "size_bytes": (
                        int(metadata.st_size)
                        if stat.S_ISREG(metadata.st_mode)
                        else None
                    ),
                }
            )
        result = {"path": relative}
        result.update(selection.counters(scan_truncated=scan_truncated))
        result["entries"] = entries
        return result


__all__ = ["ListDirectoryTool"]
