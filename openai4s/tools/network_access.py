"""Human-approved network-policy widening control tool."""

from __future__ import annotations

from openai4s.tools.base import Tool
from openai4s.tools.contexts import ControlToolContext


class RequestNetworkAccessTool(Tool):
    """Request one durable egress decision; never grants without Host policy."""

    name = "request_network_access"
    host_method = "request_network_access"
    description = "Request human approval for outbound access to one domain."
    parameters = {
        "properties": {
            "domain": {
                "type": "string",
                "minLength": 1,
                "maxLength": 255,
                "description": "Domain only, for example example.org.",
            },
            "reason": {"type": "string", "minLength": 1, "maxLength": 2000},
        },
        "required": ["domain"],
    }
    read_only = False
    dangerous = True
    side_effect_class = "high_risk"
    permission_target_key = "domain"
    resource_key_prefix = "network_policy"
    resource_target_key = "domain"

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        return runtime.invoke(self.host_method, dict(arguments))


__all__ = ["RequestNetworkAccessTool"]
