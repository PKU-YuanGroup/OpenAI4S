"""Workspace regex-search control tool."""

from __future__ import annotations

import re
import time

from openai4s.tools.base import Tool
from openai4s.tools.contexts import WorkspaceToolContext

#: Hits one search returns. Named rather than inline so the bound and the
#: `truncated` flag that reports it cannot drift apart.
_MAX_HITS = 200
#: Files one search will open. `sorted(base.rglob(...))` materialised every
#: path in the tree before the first file was read, so the hit cap bounded the
#: reply while nothing bounded the work.
_MAX_FILES = 5000


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
                "description": (
                    "Glob limiting which files are searched, e.g. '*.py'. "
                    "Applied recursively, like the unfiltered search."
                ),
            },
        },
        "required": ["pattern"],
    }
    permission_target_key = "pattern"
    resource_key_prefix = "workspace"
    resource_target_key = "path"
    resource_target_default = "."

    def execute(self, workspace: WorkspaceToolContext, arguments: dict) -> dict:
        from openai4s.host.files import (
            MAX_SCAN_ENTRIES,
            MAX_SCAN_SECONDS,
            BoundedSelection,
            BoundedTextReader,
            UnsafeWorkspaceCandidate,
        )

        pattern = arguments.get("pattern") or ""
        if not pattern:
            return {"error": "grep: empty pattern"}
        try:
            regex = re.compile(pattern)
        except re.error as error:
            return {"error": f"grep: bad regex: {error}"}
        include = arguments.get("include")
        base = (
            workspace.resolve(str(arguments.get("path") or ""))
            if arguments.get("path")
            else workspace.workspace()
        )
        # `rglob`, not `glob`. `Path.glob("*.py")` matches only direct
        # children, so passing the schema's own example made the search
        # NARROWER than the unfiltered default -- one directory level instead
        # of the whole tree -- and said nothing about it. A model that followed
        # the documentation got a confident empty or partial answer, which is
        # the worst shape a search result can have.
        #
        # It is consumed lazily now: `sorted(paths)` used to materialise the
        # entire tree before the first file was opened. The candidates are
        # still name-ordered -- an unordered search is unreproducible, and a
        # bounded one has to be able to say which files it chose.
        paths = base.rglob(include) if include else base.rglob("*")
        candidates = BoundedSelection(_MAX_FILES)
        open_candidate = workspace.verified_read_opener()
        scanned = 0
        scan_truncated = False
        deadline = time.monotonic() + MAX_SCAN_SECONDS
        for path in paths:
            scanned += 1
            # Two budgets, one flag. The entry cap bounds syscalls; this bounds
            # the seconds they take, which the entry cap cannot -- how long
            # 100,000 entries need is the filesystem's property, not ours.
            if scanned > MAX_SCAN_ENTRIES or time.monotonic() > deadline:
                scan_truncated = True
                break
            if not path.is_file():
                continue
            try:
                with open_candidate(path) as opened:
                    relative = opened.relative
            except (FileNotFoundError, OSError, UnsafeWorkspaceCandidate):
                continue
            except ValueError as error:
                return {"error": f"grep: {error}"}
            candidates.offer(relative, path)
        hits: list[dict] = []
        hit_cap = False
        files_searched = 0
        files_truncated = 0
        for _relative, path in candidates.items():
            try:
                opened = open_candidate(path)
            except (FileNotFoundError, OSError, UnsafeWorkspaceCandidate):
                continue
            except ValueError as error:
                return {"error": f"grep: {error}"}
            files_searched += 1
            # Streamed under a byte budget from the same descriptor whose
            # identity and link count were validated.  Reopening `path` here
            # would recreate the check/use race this boundary exists to close.
            reader = BoundedTextReader(opened.handle)
            try:
                with opened:
                    for line_number, line in enumerate(reader.lines(), 1):
                        if regex.search(line):
                            hits.append(
                                {
                                    "file": opened.relative,
                                    "line": line_number,
                                    "text": line[:400],
                                }
                            )
                            if len(hits) >= _MAX_HITS:
                                hit_cap = True
                                break
            except (OSError, UnicodeDecodeError):
                continue
            if reader.budget_exhausted:
                files_truncated += 1
            if hit_cap:
                break
        result = {"pattern": pattern, "count": len(hits), "matches": hits}
        if hit_cap or candidates.dropped or scan_truncated or files_truncated:
            # An unqualified empty-ish result reads as "not in this tree", so a
            # search that stopped early has to say which bound stopped it: the
            # hit cap, the file cap, the walk, or a file too large to finish.
            result["truncated"] = True
            result["files_searched"] = files_searched
            result["files_seen"] = candidates.seen
            if candidates.dropped:
                result["files_dropped"] = candidates.dropped
            if files_truncated:
                result["files_truncated"] = files_truncated
            if scan_truncated:
                result["scan_truncated"] = True
        return result


__all__ = ["ContentSearchTool"]
