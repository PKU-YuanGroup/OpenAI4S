"""Model Context Protocol external-service control tools."""

from __future__ import annotations

from typing import Any

from openai4s.tools.base import Tool
from openai4s.tools.contexts import ControlToolContext
from openai4s.tools.taxonomy import resource_key


class ListMCPServersTool(Tool):
    name = "list_mcp_servers"
    host_method = "mcp_list"
    description = "List enabled MCP connector servers."
    parameters = {"properties": {}, "required": []}
    requires_approval = False
    screen_untrusted_output = True
    resource_key_prefix = "mcp"
    resource_target_default = "catalog"

    def execute(self, runtime: ControlToolContext, arguments: dict) -> list:
        del arguments
        return runtime.invoke(self.host_method)


class ListMCPToolsTool(Tool):
    name = "list_mcp_tools"
    host_method = "mcp_tools"
    description = "List tools exposed by one enabled MCP server."
    parameters = {
        "properties": {
            "server": {"type": "string", "minLength": 1},
        },
        "required": ["server"],
    }
    requires_approval = False
    screen_untrusted_output = True
    resource_key_prefix = "mcp"
    resource_target_key = "server"

    def execute(self, runtime: ControlToolContext, arguments: dict) -> Any:
        return runtime.invoke(self.host_method, arguments.get("server", ""))


class CallMCPTool(Tool):
    name = "call_mcp_tool"
    host_method = "mcp_call"
    description = "Invoke one named tool on an enabled MCP connector server."
    parameters = {
        "properties": {
            "server": {"type": "string", "minLength": 1},
            "tool": {"type": "string", "minLength": 1},
            "args": {"type": "object", "additionalProperties": True},
        },
        "required": ["server", "tool"],
    }
    needs_network = True
    screen_untrusted_output = True
    read_only = False
    side_effect_class = "external_write"

    def permission_target(self, arguments: Any) -> str:
        if not isinstance(arguments, dict):
            return ""
        return f"{arguments.get('server', '')}/{arguments.get('tool', '')}"

    def resource_keys(self, arguments: Any) -> tuple[str, ...]:
        return (resource_key("mcp", self.permission_target(arguments)),)

    def execute(self, runtime: ControlToolContext, arguments: dict) -> Any:
        return runtime.invoke(
            self.host_method,
            {
                "server": arguments.get("server", ""),
                "tool": arguments.get("tool", ""),
                "args": dict(arguments.get("args") or {}),
            },
        )


__all__ = ["CallMCPTool", "ListMCPServersTool", "ListMCPToolsTool"]
