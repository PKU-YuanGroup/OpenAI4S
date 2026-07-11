"""Exact-string workspace editing control tool."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openai4s.tools.base import Tool
from openai4s.tools.contexts import WorkspaceToolContext


class EditFileTool(Tool):
    """Replace one exact string, or every match when explicitly requested."""

    name = "edit_file"
    host_method = "edit_file"
    description = "Replace an exact string in a workspace file (unique unless replace_all)."
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
