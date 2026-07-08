"""Search tools: filename glob and regex content search.

Both route through the HostDispatcher (`glob` / `grep`), which confines the
search to the workspace and skips secret files. No re-implementation here.
"""
from __future__ import annotations

from openai4s.tools.base import Tool

glob_files = Tool(
    name="glob_files",
    host_method="glob",
    description="Find workspace files by glob pattern, e.g. '**/*.csv'.",
    parameters={
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern."},
            "path": {
                "type": "string",
                "description": "Directory to glob under (default the workspace root).",
            },
        },
        "required": ["pattern"],
    },
    read_only=True,
)

content_search = Tool(
    name="content_search",
    host_method="grep",
    description="Regex-search the contents of workspace files.",
    parameters={
        "properties": {
            "pattern": {"type": "string", "description": "Regular expression."},
            "path": {
                "type": "string",
                "description": "Directory to search under (default the workspace root).",
            },
            "include": {
                "type": "string",
                "description": "Glob limiting which files are searched, e.g. '*.py'.",
            },
        },
        "required": ["pattern"],
    },
    read_only=True,
)
