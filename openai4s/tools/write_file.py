"""Workspace file-writing control tool."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from openai4s.tools.base import Tool
from openai4s.tools.contexts import WorkspaceToolContext


class WriteFileTool(Tool):
    """Create or overwrite one UTF-8 file inside the session workspace."""

    name = "write_file"
    host_method = "write_file"
    description = "Create or overwrite a workspace file with the given content."
    parameters = {
        "properties": {
            "path": {
                "type": "string",
                "minLength": 1,
                "description": "File to write.",
            },
            "content": {"type": "string", "description": "Full file contents."},
        },
        "required": ["path", "content"],
    }
    read_only = False
    writes_files = True
    permission_target_key = "path"
    secret_path_key = "path"
    side_effect_class = "workspace_write"
    resource_key_prefix = "workspace"
    resource_target_key = "path"

    def execute(self, workspace: WorkspaceToolContext, arguments: dict) -> dict:
        path = workspace.resolve(arguments.get("path", ""))
        content = arguments.get("content", "")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Staged beside the target, then `os.replace`d -- the same shape
        # `edit_file` already uses, and for the reason its comment gives:
        # `write_text` truncates first and writes second, so a failure in
        # between (a full disk, an interrupt) leaves a half-written file where
        # a complete one used to be, and the previous contents are gone. An
        # overwrite that can destroy the old bytes without producing the new
        # ones is the one outcome this tool must not have.
        #
        # `mkstemp` in the target's own directory keeps the rename atomic (same
        # filesystem) and gives the staged file 0600; `copymode` puts the
        # target's permissions back, or an overwrite would silently tighten
        # them.
        descriptor, name = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.name}.", suffix=".write"
        )
        staged = Path(name)
        try:
            with open(descriptor, "w", encoding="utf-8", newline="") as sink:
                sink.write(content)
            if path.exists():
                shutil.copymode(path, staged)
            os.replace(str(staged), str(path))
        except Exception:
            staged.unlink(missing_ok=True)
            raise
        return {
            "path": workspace.relative(path),
            "bytes": len(content.encode("utf-8")),
        }


__all__ = ["WriteFileTool"]
