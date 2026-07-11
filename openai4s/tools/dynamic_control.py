"""Human-governed lifecycle tools for session-scoped dynamic tools.

These classes expose only lifecycle control.  A defined dynamic capability is
represented by ``ProxyDynamicTool`` in the session catalog and its code still
runs only inside the fail-closed one-shot sandbox worker.
"""

from __future__ import annotations

from openai4s.tools.base import Tool
from openai4s.tools.contexts import ControlToolContext


class DefineDynamicTool(Tool):
    name = "define_dynamic_tool"
    host_method = "dynamic_tool_define"
    description = "Define and smoke-test a sandboxed tool for this session."
    parameters = {
        "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": 64},
            "description": {
                "type": "string",
                "minLength": 1,
                "maxLength": 4000,
            },
            "input_schema": {"type": "object", "additionalProperties": True},
            "output_schema": {"type": "object", "additionalProperties": True},
            "implementation": {
                "type": "string",
                "minLength": 1,
                "maxLength": 100000,
                "description": "Python defining exactly execute(args).",
            },
            "smoke_args": {"type": "object", "additionalProperties": True},
            "ttl_s": {"type": "number", "minimum": 0.001, "maximum": 86400},
        },
        "required": [
            "name",
            "description",
            "input_schema",
            "output_schema",
            "implementation",
        ],
    }
    read_only = False
    dangerous = True
    side_effect_class = "high_risk"
    permission_target_key = "name"
    resource_key_prefix = "dynamic_tool"
    resource_target_key = "name"

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        return runtime.invoke(self.host_method, dict(arguments))


class ListDynamicTools(Tool):
    name = "list_dynamic_tools"
    host_method = "dynamic_tool_list"
    description = "List active session dynamic-tool manifests without source code."
    parameters = {"properties": {}, "required": []}
    requires_approval = False
    resource_key_prefix = "dynamic_tool"
    resource_target_default = "catalog"

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        del arguments
        return runtime.invoke(self.host_method)


class PromoteDynamicTool(Tool):
    name = "promote_dynamic_tool"
    host_method = "dynamic_tool_promote"
    description = "Promote a session dynamic tool after explicit human approval."
    parameters = {
        "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": 64},
            "scope": {"type": "string", "enum": ["project", "global"]},
        },
        "required": ["name", "scope"],
    }
    read_only = False
    dangerous = True
    side_effect_class = "high_risk"
    permission_target_key = "name"
    resource_key_prefix = "dynamic_tool"
    resource_target_key = "name"

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        return runtime.invoke(self.host_method, dict(arguments))


__all__ = ["DefineDynamicTool", "ListDynamicTools", "PromoteDynamicTool"]
