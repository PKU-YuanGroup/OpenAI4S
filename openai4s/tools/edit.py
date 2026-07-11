"""Exact-string workspace editing control tool."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openai4s.tools.base import Tool

if TYPE_CHECKING:
    from openai4s.host.files import WorkspaceFileService


class EditFileTool(Tool):
    """Replace one exact string, or every match when explicitly requested."""

    name = "edit_file"
    host_method = "edit_file"
    description = "Replace an exact string in a workspace file (unique unless replace_all)."
    parameters = {
        "properties": {
            "path": {"type": "string", "description": "File to edit."},
            "old_string": {
                "type": "string",
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

    def native_precheck(self, arguments: dict) -> str | None:
        """Reject degenerate model calls before asking for edit approval."""
        old = arguments.get("old_string", "")
        new = arguments.get("new_string", "")
        if not isinstance(old, str) or not old:
            return "edit_file: old_string must be a non-empty string"
        if old == new:
            return "edit_file: old_string and new_string are identical (no-op edit)"
        return None

    def execute(self, workspace: "WorkspaceFileService", arguments: dict) -> dict:
        path = workspace.resolve(arguments.get("path", ""), must_exist=True)
        old = arguments.get("old_string", "")
        new = arguments.get("new_string", "")
        replace_all = bool(arguments.get("replace_all"))
        content = path.read_text(encoding="utf-8")
        matches = content.count(old)
        if not old or matches == 0:
            return {"error": "edit_file: old_string not found"}
        if matches > 1 and not replace_all:
            return {
                "error": f"edit_file: old_string is not unique ({matches} matches); "
                "pass replace_all=True or add more context"
            }
        content = (
            content.replace(old, new)
            if replace_all
            else content.replace(old, new, 1)
        )
        path.write_text(content, encoding="utf-8")
        return {"path": workspace.relative(path), "replaced": matches}


def static_edit_precheck(arguments) -> str | None:
    """Compatibility wrapper for the former module-level precheck helper."""
    if not isinstance(arguments, dict):
        return None
    return EditFileTool().native_precheck(arguments)


# Compatibility name retained for callers that imported the former singleton.
edit_file = EditFileTool()


__all__ = ["EditFileTool", "edit_file", "static_edit_precheck"]
