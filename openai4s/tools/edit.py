"""Exact-string workspace editing control tool."""

from __future__ import annotations

import io
import os
import stat
from typing import TYPE_CHECKING, Any, TextIO

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
        relative = arguments.get("path", "")
        old = arguments.get("old_string", "")
        new = arguments.get("new_string", "")
        replace_all = bool(arguments.get("replace_all"))
        if not old:
            return {"error": "edit_file: old_string not found"}
        parent = workspace.secure_parent(relative)
        with parent:
            opened = parent.open_verified_read()
            try:
                descriptor, staged = parent.create_staged(suffix=".edit")
            except BaseException:
                opened.close()
                raise
            descriptor_open = True
            try:
                with opened:
                    # Both text wrappers consume descriptors already acquired
                    # through the same pinned parent. There is no pathname
                    # reopen between identity validation and source reading.
                    sink = os.fdopen(
                        descriptor,
                        "w",
                        encoding="utf-8",
                        newline="",
                        closefd=True,
                    )
                    descriptor_open = False
                    with (
                        sink,
                        io.TextIOWrapper(
                            opened.handle, encoding="utf-8", newline=""
                        ) as source,
                    ):
                        matches = _stream_replace(source, sink, old, new, replace_all)
                        os.fchmod(sink.fileno(), stat.S_IMODE(opened.metadata.st_mode))
            except UnicodeDecodeError:
                if descriptor_open:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                parent.discard(staged)
                # A binary file the agent mistook for text is a bad argument,
                # not a broken tool, and the soft-fail contract tells the cell.
                return {"error": "edit_file: not a UTF-8 text file"}
            except OSError as error:
                if descriptor_open:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                parent.discard(staged)
                return {"error": f"edit_file: {error}"}
            except BaseException:
                if descriptor_open:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                parent.discard(staged)
                raise
            # Discarded before anything is swapped in: the uniqueness rule is
            # only a rule if a refused edit leaves the original untouched.
            if matches == 0:
                parent.discard(staged)
                return {"error": "edit_file: old_string not found"}
            if matches > 1 and not replace_all:
                parent.discard(staged)
                return {
                    "error": (
                        f"edit_file: old_string is not unique ({matches} matches); "
                        "pass replace_all=True or add more context"
                    )
                }
            try:
                parent.publish(staged)
            except OSError as error:
                parent.discard(staged)
                return {"error": f"edit_file: {error}"}
        return {"path": parent.target_relative, "replaced": matches}


def _stream_replace(
    source: TextIO, sink: TextIO, old: str, new: str, replace_all: bool
) -> int:
    """Stream ``source`` into ``sink`` and return the exact match count."""
    # A chunk must be longer than `old`, or the carry below -- which is what
    # catches a match straddling the boundary -- grows without bound and
    # re-materialises the file this exists to avoid.
    chunk_chars = max(_CHUNK_CHARS, 2 * len(old))
    keep = len(old) - 1
    matches = 0
    carry = ""
    while True:
        chunk = source.read(chunk_chars)
        final = not chunk
        buffer = carry + chunk
        # Tail characters could still begin a match that continues into the
        # next chunk, so they are not emitted yet.
        safe_end = len(buffer) if final else max(0, len(buffer) - keep)
        position = 0
        while True:
            index = buffer.find(old, position)
            if index < 0 or index >= safe_end:
                break
            matches += 1
            sink.write(buffer[position:index])
            # Later matches are written back verbatim unless `replace_all`, so
            # the staged file is already correct if uniqueness turns out to pass.
            sink.write(new if (replace_all or matches == 1) else old)
            position = index + len(old)
        flush_end = max(position, safe_end)
        sink.write(buffer[position:flush_end])
        carry = buffer[flush_end:]
        if final:
            break
    return matches


def static_edit_precheck(arguments: Any) -> str | None:
    """Compatibility wrapper for the former module-level precheck helper."""
    if not isinstance(arguments, dict):
        return None
    return EditFileTool.native_precheck(arguments)


if TYPE_CHECKING:
    edit_file: EditFileTool


def __getattr__(name: str) -> Any:
    """Resolve the former singleton through the canonical registry lazily."""
    if name == "edit_file":
        from openai4s.tools.registry import get_tool

        return get_tool("edit_file")
    raise AttributeError(name)


__all__ = ["EditFileTool", "edit_file", "static_edit_precheck"]
