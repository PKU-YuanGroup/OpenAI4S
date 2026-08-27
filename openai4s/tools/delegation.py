"""Sub-agent orchestration control tools."""

from __future__ import annotations

from typing import Any

from openai4s.tools.base import Tool
from openai4s.tools.contexts import ControlToolContext


class DelegateTaskTool(Tool):
    """Start one bounded sub-agent task."""

    name = "delegate_task"
    host_method = "delegate"
    description = "Delegate one self-contained task to a sub-agent or specialist."
    parameters = {
        "properties": {
            "request": {
                "type": "string",
                "minLength": 1,
                "maxLength": 100000,
                "description": "Self-contained task with inputs and desired output.",
            },
            "name": {
                "type": "string",
                "minLength": 1,
                "description": "Optional enabled specialist profile name.",
            },
            "context_summary": {"type": "string", "maxLength": 20000},
            "output_schema": {"type": "object", "additionalProperties": True},
            "steps": {"type": "integer", "minimum": 1, "maximum": 1000},
            "max_steps": {"type": "integer", "minimum": 1, "maximum": 1000},
            "max_turns": {"type": "integer", "minimum": 1, "maximum": 1000},
            "permissions": {
                "type": "object",
                "additionalProperties": {
                    "type": "string",
                    "enum": ["allow", "ask", "deny"],
                },
            },
            "capabilities": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "maxItems": 100,
            },
            "unrestricted": {"type": "boolean"},
            "require_artifacts": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "maxItems": 50,
                "description": (
                    "Artifact filenames (exact, or trailing-star globs) the "
                    "child must produce; missing ones downgrade its "
                    "task_status to at most partial."
                ),
            },
            "retries": {
                "type": "integer",
                "description": (
                    "Re-run a partial/blocked/failed child up to this many "
                    "times (clamped to 0-2, default 0; wait=true only)."
                ),
            },
            "wait": {
                "type": "boolean",
                "description": "Wait for completion (default true).",
            },
        },
        "required": ["request"],
    }
    read_only = False
    side_effect_class = "runtime_mutation"
    permission_target_key = "name"
    permission_target_default = "sub-agent"
    resource_key_prefix = "delegation"
    resource_target_key = "name"
    resource_target_default = "sub-agent"

    @staticmethod
    def _without_nulls(arguments: dict) -> dict:
        """Explicit null == absent == default, scoped to this tool.

        The SDK door has always dropped top-level ``None`` values at the wire
        codec, so a provider-emitted ``"steps": null`` through the native door
        must mean the same thing — use the default — rather than a schema
        refusal. Top level only, symmetric with ``encode_args``: nulls nested
        inside user payloads (``output_schema``, ``request`` items) are the
        payload's own business.
        """
        return {key: value for key, value in arguments.items() if value is not None}

    def validation_error(self, arguments: Any) -> str | None:
        if isinstance(arguments, dict):
            arguments = self._without_nulls(arguments)
        return super().validation_error(arguments)

    def execute(self, runtime: ControlToolContext, arguments: dict) -> Any:
        spec = self._without_nulls(dict(arguments))
        spec.setdefault("wait", True)
        return runtime.invoke(self.host_method, spec)


class ListChildrenTool(Tool):
    name = "list_children"
    host_method = "children"
    description = "List direct sub-agent children and their current states."
    parameters = {"properties": {}, "required": []}
    requires_approval = False
    resource_key_prefix = "delegation"
    resource_target_default = "children"

    def execute(self, runtime: ControlToolContext, arguments: dict) -> list:
        del arguments
        return runtime.invoke(self.host_method)


class CollectChildrenTool(Tool):
    name = "collect_children"
    host_method = "collect"
    description = "Collect results from asynchronous sub-agent children."
    parameters = {
        "properties": {
            "child_ids": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "maxItems": 100,
            },
            "timeout": {"type": "number", "minimum": 0, "maximum": 3600},
        },
        "required": [],
    }
    requires_approval = False
    resource_key_prefix = "delegation"
    resource_target_default = "children"

    def execute(self, runtime: ControlToolContext, arguments: dict) -> Any:
        return runtime.invoke(self.host_method, dict(arguments))


class StopChildTool(Tool):
    name = "stop_child"
    host_method = "stop_child"
    description = "Cancel one running direct sub-agent child by exact ID."
    parameters = {
        "properties": {
            "child_id": {"type": "string", "minLength": 1},
        },
        "required": ["child_id"],
    }
    read_only = False
    requires_approval = False
    side_effect_class = "runtime_mutation"
    resource_key_prefix = "delegation"
    resource_target_key = "child_id"

    def execute(self, runtime: ControlToolContext, arguments: dict) -> Any:
        return runtime.invoke(self.host_method, arguments.get("child_id", ""))


class SendChildMessageTool(Tool):
    name = "send_child_message"
    host_method = "send_message"
    description = "Send steering context to one running direct sub-agent child."
    parameters = {
        "properties": {
            "child_id": {"type": "string", "minLength": 1},
            "message": {"type": "string", "minLength": 1, "maxLength": 20000},
        },
        "required": ["child_id", "message"],
    }
    read_only = False
    requires_approval = False
    side_effect_class = "runtime_mutation"
    resource_key_prefix = "delegation"
    resource_target_key = "child_id"

    def execute(self, runtime: ControlToolContext, arguments: dict) -> Any:
        return runtime.invoke(self.host_method, dict(arguments))


__all__ = [
    "CollectChildrenTool",
    "DelegateTaskTool",
    "ListChildrenTool",
    "SendChildMessageTool",
    "StopChildTool",
]
