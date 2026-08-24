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

    # `mcp_tools` predates this control tool: the kernel SDK's
    # `host.mcp.tools(server)` sends a bare positional string, and the
    # dispatcher hands that straight to `execute`/`resource_keys` as the
    # arguments. Same dual-shape contract as LineageGetTool.
    def resource_keys(self, arguments: Any) -> tuple[str, ...]:
        server = (
            arguments if isinstance(arguments, str) else (arguments or {}).get("server")
        )
        return (resource_key("mcp", server or "*"),)

    def execute(
        self,
        runtime: ControlToolContext,
        arguments: dict | str,
    ) -> Any:
        server = (
            arguments
            if isinstance(arguments, str)
            else (arguments or {}).get("server", "")
        )
        return runtime.invoke(self.host_method, server)


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


class ListMCPResourcesTool(Tool):
    name = "list_mcp_resources"
    host_method = "mcp_resources"
    description = "List resources exposed by one configured MCP server."
    parameters = {
        "properties": {
            "server": {"type": "string", "minLength": 1},
            "cursor": {
                "type": "string",
                "minLength": 1,
                "description": "Opaque nextCursor returned by the previous page.",
            },
        },
        "required": ["server"],
    }
    requires_approval = False
    screen_untrusted_output = True
    resource_key_prefix = "mcp"

    def resource_keys(self, arguments: Any) -> tuple[str, ...]:
        server = arguments.get("server", "") if isinstance(arguments, dict) else ""
        return (resource_key("mcp", f"{server}/resources"),)

    def execute(self, runtime: ControlToolContext, arguments: dict) -> Any:
        return runtime.invoke(
            self.host_method,
            {
                "server": arguments.get("server", ""),
                "cursor": arguments.get("cursor"),
            },
        )


class ReadMCPResourceTool(Tool):
    name = "read_mcp_resource"
    host_method = "mcp_resource_read"
    description = "Read one URI-addressed resource from an enabled MCP server."
    parameters = {
        "properties": {
            "server": {"type": "string", "minLength": 1},
            "uri": {"type": "string", "minLength": 1},
        },
        "required": ["server", "uri"],
    }
    needs_network = True
    screen_untrusted_output = True
    resource_key_prefix = "mcp"

    def permission_target(self, arguments: Any) -> str:
        if not isinstance(arguments, dict):
            return ""
        return f"{arguments.get('server', '')}/{arguments.get('uri', '')}"

    def resource_keys(self, arguments: Any) -> tuple[str, ...]:
        return (resource_key("mcp", self.permission_target(arguments)),)

    def execute(self, runtime: ControlToolContext, arguments: dict) -> Any:
        return runtime.invoke(
            self.host_method,
            {
                "server": arguments.get("server", ""),
                "uri": arguments.get("uri", ""),
            },
        )


class ListMCPPromptsTool(Tool):
    name = "list_mcp_prompts"
    host_method = "mcp_prompts"
    description = "List reusable prompts exposed by one configured MCP server."
    parameters = {
        "properties": {
            "server": {"type": "string", "minLength": 1},
            "cursor": {
                "type": "string",
                "minLength": 1,
                "description": "Opaque nextCursor returned by the previous page.",
            },
        },
        "required": ["server"],
    }
    requires_approval = False
    screen_untrusted_output = True
    resource_key_prefix = "mcp"

    def resource_keys(self, arguments: Any) -> tuple[str, ...]:
        server = arguments.get("server", "") if isinstance(arguments, dict) else ""
        return (resource_key("mcp", f"{server}/prompts"),)

    def execute(self, runtime: ControlToolContext, arguments: dict) -> Any:
        return runtime.invoke(
            self.host_method,
            {
                "server": arguments.get("server", ""),
                "cursor": arguments.get("cursor"),
            },
        )


class GetMCPPromptTool(Tool):
    name = "get_mcp_prompt"
    host_method = "mcp_prompt_get"
    description = "Render one named prompt from an enabled MCP server."
    parameters = {
        "properties": {
            "server": {"type": "string", "minLength": 1},
            "name": {"type": "string", "minLength": 1},
            "arguments": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
        },
        "required": ["server", "name"],
    }
    needs_network = True
    screen_untrusted_output = True
    resource_key_prefix = "mcp"

    def permission_target(self, arguments: Any) -> str:
        if not isinstance(arguments, dict):
            return ""
        return f"{arguments.get('server', '')}/{arguments.get('name', '')}"

    def resource_keys(self, arguments: Any) -> tuple[str, ...]:
        return (resource_key("mcp", self.permission_target(arguments)),)

    def execute(self, runtime: ControlToolContext, arguments: dict) -> Any:
        return runtime.invoke(
            self.host_method,
            {
                "server": arguments.get("server", ""),
                "name": arguments.get("name", ""),
                "arguments": dict(arguments.get("arguments") or {}),
            },
        )


__all__ = [
    "CallMCPTool",
    "GetMCPPromptTool",
    "ListMCPPromptsTool",
    "ListMCPResourcesTool",
    "ListMCPServersTool",
    "ListMCPToolsTool",
    "ReadMCPResourceTool",
]
