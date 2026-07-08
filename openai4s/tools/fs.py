"""Filesystem tools: list a directory, read a text file, write a file.

Each routes to the matching `host.*` file method through the HostDispatcher
(`list_dir` / `read_file` / `write_file`), so workspace confinement, the
secret-file guard, and artifact capture all still apply — nothing here reads
or writes the disk directly.
"""
from __future__ import annotations

from openai4s.tools.base import Tool

list_dir = Tool(
    name="list_dir",
    host_method="list_dir",
    description="List the entries of a workspace directory.",
    parameters={
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory to list, relative to the workspace "
                "(default '.').",
            },
        },
        "required": [],
    },
    read_only=True,
)

read_text_file = Tool(
    name="read_text_file",
    host_method="read_file",
    description="Read a UTF-8 text file from the workspace, optionally a line window.",
    parameters={
        "properties": {
            "path": {"type": "string", "description": "File to read."},
            "offset": {
                "type": "integer",
                "description": "0-based first line to return.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of lines to return.",
            },
        },
        "required": ["path"],
    },
    read_only=True,
)

write_file = Tool(
    name="write_file",
    host_method="write_file",
    description="Create or overwrite a workspace file with the given content.",
    parameters={
        "properties": {
            "path": {"type": "string", "description": "File to write."},
            "content": {"type": "string", "description": "Full file contents."},
        },
        "required": ["path", "content"],
    },
    read_only=False,
    writes_files=True,
)
