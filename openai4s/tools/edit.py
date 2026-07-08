"""Edit tool: exact-string replacement in a workspace file.

Routes to the HostDispatcher `edit_file` method, which owns the real
unique-match / replace_all / diff logic. `static_edit_precheck` is only a
cheap guard against obviously-degenerate arguments; it does NOT duplicate the
host's matching logic.
"""
from __future__ import annotations

from openai4s.tools.base import Tool

edit_file = Tool(
    name="edit_file",
    host_method="edit_file",
    description="Replace an exact string in a workspace file (unique unless replace_all).",
    parameters={
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
    },
    read_only=False,
    writes_files=True,
)


def static_edit_precheck(arguments) -> str | None:
    """Cheap pre-dispatch guard. Returns an error string for a degenerate edit
    (empty `old_string`, or `old_string == new_string`), else None.

    The real not-found / not-unique checks live in the host's `_m_edit_file`;
    this only catches no-op requests before they reach the dispatcher. Never
    raises on odd input.
    """
    if not isinstance(arguments, dict):
        return None
    old = arguments.get("old_string", "")
    new = arguments.get("new_string", "")
    if not isinstance(old, str) or not old:
        return "edit_file: old_string must be a non-empty string"
    if old == new:
        return "edit_file: old_string and new_string are identical (no-op edit)"
    return None
