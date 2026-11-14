"""Workspace file-writing control tool."""

from __future__ import annotations

import os
import stat
from typing import Any

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

    def render_observation(self, result: Any) -> str | None:
        """Say that a script written here has not been run.

        Doubao Seed 2.1 Pro wrote its analysis script with this tool, said it
        would "use a code cell", and wrote the identical file again -- five
        times, a byte count the only answer -- until the no-progress circuit
        stopped the turn. Nothing but a cell runs a script, and the result
        gave no sign that it had not. A Python cell still receives the plain
        ``{path, bytes}``.
        """
        path = result.get("path") if isinstance(result, dict) else None
        if not isinstance(path, str):
            return None
        lowered = path.lower()
        if lowered.endswith(".py"):
            run = f"exec(open({path!r}).read())"
            fence = "python"
        elif lowered.endswith(".r"):
            run = f"source({path!r})"
            fence = "r"
        else:
            return None
        from openai4s.tools.registry import _render_result_body

        return (
            f"{_render_result_body(result)}\n"
            "The file is written; nothing has run it. To run it now, reply "
            f"with one fenced ```{fence} cell and no tool calls, for example "
            f"`{run}`."
        )

    def execute(self, workspace: WorkspaceToolContext, arguments: dict) -> dict:
        relative = arguments.get("path", "")
        content = arguments.get("content", "")
        parent = workspace.secure_parent(relative, create_parents=True)
        # Staged beside the target, then `os.replace`d -- the same shape
        # `edit_file` already uses, and for the reason its comment gives:
        # `write_text` truncates first and writes second, so a failure in
        # between (a full disk, an interrupt) leaves a half-written file where
        # a complete one used to be, and the previous contents are gone. An
        # overwrite that can destroy the old bytes without producing the new
        # ones is the one outcome this tool must not have.
        #
        # The staged file and final rename are addressed through the same
        # verified parent descriptor. A concurrent `subdir -> outside` symlink
        # swap therefore cannot redirect either operation out of the workspace.
        with parent:
            descriptor, staged = parent.create_staged(suffix=".write")
            descriptor_open = True
            try:
                existing = parent.target_metadata()
                sink = os.fdopen(
                    descriptor, "w", encoding="utf-8", newline="", closefd=True
                )
                descriptor_open = False
                with sink:
                    sink.write(content)
                    if existing is not None and stat.S_ISREG(existing.st_mode):
                        os.fchmod(sink.fileno(), stat.S_IMODE(existing.st_mode))
                parent.publish(staged)
            except BaseException:
                if descriptor_open:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                parent.discard(staged)
                raise
        return {
            "path": parent.target_relative,
            "bytes": len(content.encode("utf-8")),
        }


__all__ = ["WriteFileTool"]
