"""Workflow progress and approved-plan control tools."""

from __future__ import annotations

from openai4s.tools.base import Tool
from openai4s.tools.contexts import ControlToolContext

_TODO_STATUS = ["pending", "in_progress", "completed", "cancelled"]
_PLAN_STATUS = ["pending", "in_progress", "completed", "failed", "skipped"]


class ReadTodosTool(Tool):
    name = "read_todos"
    host_method = "todo_read"
    description = "Read the current session's lightweight task list."
    parameters = {"properties": {}, "required": []}
    requires_approval = False
    resource_key_prefix = "workflow"
    resource_target_default = "todos"

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        del arguments
        return runtime.invoke(self.host_method)


class WriteTodosTool(Tool):
    name = "write_todos"
    host_method = "todo_write"
    description = "Replace the session task list with explicit progress states."
    parameters = {
        "properties": {
            "todos": {
                "type": "array",
                "maxItems": 100,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "minLength": 1},
                        "content": {"type": "string", "minLength": 1},
                        "status": {"type": "string", "enum": _TODO_STATUS},
                        "priority": {
                            "type": "string",
                            "enum": ["low", "medium", "high"],
                        },
                    },
                    "required": ["content", "status"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["todos"],
    }
    read_only = False
    requires_approval = False
    side_effect_class = "runtime_mutation"
    resource_key_prefix = "workflow"
    resource_target_default = "todos"

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        return runtime.invoke(self.host_method, {"todos": list(arguments["todos"])})


class ReadPlanTool(Tool):
    name = "read_plan"
    host_method = "plan_read"
    description = "Read the approved plan and its live step states."
    parameters = {"properties": {}, "required": []}
    requires_approval = False
    resource_key_prefix = "workflow"
    resource_target_default = "plan"

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        del arguments
        return runtime.invoke(self.host_method)


class UpdatePlanStepTool(Tool):
    name = "update_plan_step"
    host_method = "plan_update"
    description = "Update one step of the approved plan with observed progress."
    parameters = {
        "properties": {
            "step_id": {"type": "string", "minLength": 1},
            "status": {"type": "string", "enum": _PLAN_STATUS},
            "note": {"type": "string", "maxLength": 4000},
        },
        "required": ["step_id", "status"],
    }
    read_only = False
    requires_approval = False
    side_effect_class = "runtime_mutation"
    resource_key_prefix = "workflow_step"
    resource_target_key = "step_id"

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        return runtime.invoke(self.host_method, dict(arguments))


__all__ = [
    "ReadPlanTool",
    "ReadTodosTool",
    "UpdatePlanStepTool",
    "WriteTodosTool",
]
