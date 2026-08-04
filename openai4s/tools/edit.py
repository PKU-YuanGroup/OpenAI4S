"""Exact-string workspace editing control tool."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from openai4s.tools.base import Tool
from openai4s.tools.contexts import WorkspaceToolContext

#: Characters held at once while rewriting. The old implementation was
#: `read_text()` -> `str.replace` -> `write_text()`, which measured a 64 MiB
#: peak to change seven characters in a 32 MiB file: the cost scaled with the
#: file the agent named rather than with the edit it asked for.
_CHUNK_CHARS = 256 * 1024


class EditFileTool(Tool):
    """Replace one exact string, or every match when explicitly requested."""

    name = "edit_file"
    host_method = "edit_file"
    description = (
        "Replace an exact string in a workspace file (unique unless replace_all)."
    )
    parameters = {
        "properties": {
            "path": {
                "type": "string",
                "minLength": 1,
                "description": "File to edit.",
            },
            "old_string": {
                "type": "string",
                "minLength": 1,
                "description": "Exact text to replace (must be unique unless replace_all).",
            },
            "new_string": {
                "type": "string",
                "description": "Replacement text.",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace every occurrence instead of requiring uniqueness.",
            },
        },
        "required": ["path", "old_string", "new_string"],
    }
    read_only = False
    writes_files = True
    permission_target_key = "path"
    secret_path_key = "path"
    side_effect_class = "workspace_write"
    resource_key_prefix = "workspace"
    resource_target_key = "path"

    @staticmethod
    def native_precheck(arguments: dict) -> str | None:
        """Reject degenerate model calls before asking for edit approval."""
        old = arguments.get("old_string", "")
        new = arguments.get("new_string", "")
        if not isinstance(old, str) or not old:
            return "edit_file: old_string must be a non-empty string"
        if old == new:
            return "edit_file: old_string and new_string are identical (no-op edit)"
        return None

    def execute(self, workspace: WorkspaceToolContext, arguments: dict) -> dict:
        path = workspace.resolve(arguments.get("path", ""), must_exist=True)
        old = arguments.get("old_string", "")
        new = arguments.get("new_string", "")
        replace_all = bool(arguments.get("replace_all"))
        if not old:
            return {"error": "edit_file: old_string not found"}
        try:
            matches, staged = _stream_replace(path, old, new, replace_all)
        except UnicodeDecodeError:
            # Was an unhandled exception out of `read_text`. A binary file the
            # agent mistook for text is a bad argument, not a broken tool, and
            # the soft-fail contract is how the cell gets told which.
            return {"error": "edit_file: not a UTF-8 text file"}
        except OSError as error:
            return {"error": f"edit_file: {error}"}
        # Discarded before anything is swapped in: the uniqueness rule is only
        # a rule if a refused edit leaves the original untouched.
        if matches == 0:
            staged.unlink(missing_ok=True)
            return {"error": "edit_file: old_string not found"}
        if matches > 1 and not replace_all:
            staged.unlink(missing_ok=True)
            return {
                "error": f"edit_file: old_string is not unique ({matches} matches); "
                "pass replace_all=True or add more context"
            }
        try:
            # The staged file was created 0600 by `mkstemp`; without this the
            # rename would silently tighten the file's permissions.
            shutil.copymode(path, staged)
            os.replace(staged, path)
        except OSError as error:
            staged.unlink(missing_ok=True)
            return {"error": f"edit_file: {error}"}
        return {"path": workspace.relative(path), "replaced": matches}


def _stream_replace(
    path: Path, old: str, new: str, replace_all: bool
) -> tuple[int, Path]:
    """Rewrite ``path`` into a staged sibling, holding one chunk at a time.

    Returns the match count and the staged file, which the caller swaps in or
    discards. It has to be one pass that writes: the count that decides whether
    the edit is allowed is only known at the end, and a first pass just to
    count would read the file twice for the same answer.

    Staged beside the target so the final `os.replace` is atomic on the same
    filesystem -- a crash mid-rewrite leaves the original, where the old
    `write_text` left a half-written file.
    """
    # A chunk must be longer than `old`, or the carry below -- which is what
    # catches a match straddling the boundary -- grows without bound and
    # re-materialises the file this exists to avoid.
    chunk_chars = max(_CHUNK_CHARS, 2 * len(old))
    keep = len(old) - 1
    matches = 0
    descriptor, name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".edit"
    )
    staged = Path(name)
    try:
        # `newline=""` on both sides: universal-newline translation would
        # rewrite every CRLF in a file the agent only asked to edit in one
        # place, and the diff would blame the edit.
        with open(descriptor, "w", encoding="utf-8", newline="") as sink, open(
            path, "r", encoding="utf-8", newline=""
        ) as source:
            carry = ""
            while True:
                chunk = source.read(chunk_chars)
                final = not chunk
                buffer = carry + chunk
                # Tail characters could still begin a match that continues into
                # the next chunk, so they are not emitted yet.
                safe_end = len(buffer) if final else max(0, len(buffer) - keep)
                position = 0
                while True:
                    index = buffer.find(old, position)
                    if index < 0 or index >= safe_end:
                        break
                    matches += 1
                    sink.write(buffer[position:index])
                    # Later matches are written back verbatim unless
                    # `replace_all`, so the staged file is already correct if
                    # the uniqueness check turns out to pass.
                    sink.write(new if (replace_all or matches == 1) else old)
                    position = index + len(old)
                flush_end = max(position, safe_end)
                sink.write(buffer[position:flush_end])
                carry = buffer[flush_end:]
                if final:
                    break
    except BaseException:
        # Including the decode failure the caller turns into a soft error: a
        # rejected edit must not leave a stray dotfile in the workspace.
        staged.unlink(missing_ok=True)
        raise
    return matches, staged


def static_edit_precheck(arguments) -> str | None:
    """Compatibility wrapper for the former module-level precheck helper."""
    if not isinstance(arguments, dict):
        return None
    return EditFileTool.native_precheck(arguments)


if TYPE_CHECKING:
    edit_file: EditFileTool


def __getattr__(name: str):
    """Resolve the former singleton through the canonical registry lazily."""
    if name == "edit_file":
        from openai4s.tools.registry import get_tool

        return get_tool("edit_file")
    raise AttributeError(name)


__all__ = ["EditFileTool", "edit_file", "static_edit_precheck"]
