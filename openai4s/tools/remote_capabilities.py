"""Remote-science service registry control tools."""

from __future__ import annotations

from openai4s.tools.base import Tool
from openai4s.tools.contexts import ControlToolContext


class RemoteGPUStatusTool(Tool):
    name = "remote_gpu_status"
    host_method = "remote_gpu_status"
    description = "Inspect configured remote GPU hosts and verified capabilities."
    parameters = {"properties": {}, "required": []}
    requires_approval = False
    resource_key_prefix = "remote_gpu"
    resource_target_default = "catalog"

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        del arguments
        return runtime.invoke(self.host_method, {})


class RegisterRemoteCapabilityTool(Tool):
    name = "register_remote_capability"
    host_method = "register_remote_capability"
    description = "Register a remote service only after its structured probe succeeds."
    parameters = {
        "properties": {
            "alias": {"type": "string", "minLength": 1},
            "capability": {"type": "string", "minLength": 1},
            "script": {"type": "string"},
            "engine": {"type": "string"},
            "invoke": {"type": "string"},
            "markers": {"type": "object", "additionalProperties": True},
            "notes": {"type": "string", "maxLength": 10000},
            "probe": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["path_exists", "executable_exists"],
                    },
                    "path": {"type": "string", "minLength": 1},
                    "binary": {"type": "string", "minLength": 1},
                },
                "required": ["kind"],
                "additionalProperties": False,
            },
        },
        "required": ["alias", "capability", "probe"],
    }
    read_only = False
    dangerous = True
    side_effect_class = "external_write"
    permission_target_key = "alias"
    resource_key_prefix = "remote_gpu"
    resource_target_key = "alias"

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        return runtime.invoke(self.host_method, dict(arguments))


__all__ = ["RegisterRemoteCapabilityTool", "RemoteGPUStatusTool"]
