"""Simulation Lab tools. Real equipment must use NEW tools classified as
high_risk/external_write; these simulation permissions must never authorize it.
"""

from __future__ import annotations

import json
from typing import Any

from openai4s.tools.base import Tool
from openai4s.tools.contexts import ControlToolContext

_ID = {"type": "string", "minLength": 1, "maxLength": 256}
_KEY = {"type": "string", "minLength": 1, "maxLength": 128}
_RUN = {"run_id": _ID}


class _LabTool(Tool):
    requires_approval = False
    resource_key_prefix = "lab"
    resource_target_key = "run_id"
    permission_target_key = "run_id"

    def invoke(self, dispatcher: Any, arguments: dict) -> Any:
        # Trusted native entry, separate from SDK wire arguments. The same
        # dispatcher and Tool.execute still enforce all permission policies.
        return dispatcher.invoke_lab_tool(self.host_method, arguments)

    def render_observation(self, result: Any) -> str | None:
        # The generic formatter shows only error text, losing the command_id
        # required to reconcile an unknown outcome without resending.
        if isinstance(result, dict) and "error" in result:
            return json.dumps(result, ensure_ascii=False)
        return None

    def _arguments(self, arguments: Any) -> Any:
        # The SDK codec omits optional None values. Native callers may spell
        # the same absence explicitly (notably source/target in CONTRACT §3.5).
        # Keep unknown and required nulls so validation still refuses them.
        if not isinstance(arguments, dict):
            return arguments
        properties = self.parameters["properties"]
        required = self.parameters["required"]
        return {
            key: value
            for key, value in arguments.items()
            if not (value is None and key in properties and key not in required)
        }

    def validation_error(self, arguments: Any) -> str | None:
        return super().validation_error(self._arguments(arguments))

    def execute(self, context: ControlToolContext, arguments: dict) -> dict:
        arguments = self._arguments(arguments)
        # SDK calls do not go through registry.execute_tool_call's validator.
        if self.validation_error(arguments):
            return {
                "error": "Invalid Lab arguments",
                "error_kind": "invalid_parameters",
            }
        return context.invoke(self.host_method, arguments)


class LabListTool(_LabTool):
    name = host_method = "lab_list"
    description = "List simulation-only Lab devices and this session's runs."
    parameters = {"properties": {}, "required": []}
    resource_target_key = None
    resource_target_default = "devices"


class LabDescribeTool(_LabTool):
    name = host_method = "lab_describe"
    description = "Describe a simulation-only Lab device's capabilities, exact parameter levels and units."
    parameters = {
        "properties": {"device_id": _ID, "profile": _ID},
        "required": ["device_id"],
    }
    permission_target_key = resource_target_key = "device_id"


class LabCreateTool(_LabTool):
    """Simulation only. Real devices require new high_risk/external_write tools."""

    name = host_method = "lab_create"
    description = "Create an approved simulation-only Lab run. Budgets only tighten backend limits; reuse the returned idempotency_key for retries."
    parameters = {
        "properties": {
            "device_id": _ID,
            "profile": _ID,
            "seed": {"type": "integer", "minimum": 0, "maximum": 4294967295},
            "budgets": {
                "type": "object",
                "properties": {
                    name: {"type": "integer", "minimum": 1}
                    for name in (
                        "max_steps",
                        "max_commands",
                        "max_wall_ms",
                        "max_consecutive_failures",
                        "idle_timeout_ms",
                    )
                },
                "additionalProperties": False,
            },
            "idempotency_key": _KEY,
        },
        "required": ["device_id", "profile"],
    }
    read_only = False
    requires_approval = True
    side_effect_class = "runtime_mutation"
    permission_target_key = resource_target_key = "device_id"


class LabObserveTool(_LabTool):
    name = host_method = "lab_observe"
    description = "Read the latest simulated sensor observation without advancing the experiment. Arrays over 256 elements are summarized; full arrays are available only in Python via host.lab.observe(full=True)."
    parameters = {"properties": _RUN, "required": ["run_id"]}


class LabExecuteTool(_LabTool):
    """Simulation only. Real devices require new high_risk/external_write tools."""

    name = host_method = "lab_execute"
    description = "Execute an approved simulation-only operation using expected_revision from the latest observation/status. Rejection is a normal result: inspect command.state and command.error (including available parameter levels). For outcome_unknown, query lab_status(run_id, command_id) for the SAME command; never resend with a new key."
    parameters = {
        "properties": {
            **_RUN,
            "operation": _ID,
            "source": _ID,
            "target": _ID,
            "parameters": {
                "type": "object",
                "additionalProperties": {
                    "type": "object",
                    "properties": {"value": {"type": "number"}, "unit": _ID},
                    "required": ["value", "unit"],
                    "additionalProperties": False,
                },
            },
            "expected_revision": {
                "type": "integer",
                "minimum": 0,
                "maximum": 9223372036854775807,
            },
            "idempotency_key": _KEY,
        },
        "required": ["run_id", "operation", "expected_revision"],
    }
    read_only = False
    requires_approval = True
    side_effect_class = "runtime_mutation"


class LabStatusTool(_LabTool):
    name = host_method = "lab_status"
    description = "Query simulation status and reconcile the SAME unknown command using command_id; never resend with a new idempotency key. This may record reconciliation, but never dispatches a new command."
    parameters = {"properties": {**_RUN, "command_id": _ID}, "required": ["run_id"]}


class LabStopTool(_LabTool):
    """Approval-free safety stop, limited to this session's run.

    Cross-session attempts return run_not_found, never stopping another run.
    """

    name = host_method = "lab_stop"
    description = "Stop this session's simulation run safely without waiting for approval. Runs belonging to other sessions return run_not_found."
    parameters = {
        "properties": {**_RUN, "reason": {"type": "string", "maxLength": 256}},
        "required": ["run_id"],
    }
    read_only = False
    side_effect_class = "runtime_mutation"


class LabCommandsTool(_LabTool):
    name = host_method = "lab_commands"
    description = "Read this simulation run's projected command ledger, including rejected commands and their errors."
    parameters = {
        "properties": {
            **_RUN,
            "after_seq": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1},
        },
        "required": ["run_id"],
    }


class LabObservationsTool(_LabTool):
    name = host_method = "lab_observations"
    description = "Read this simulation run's sensor history without advancing it. Arrays over 256 elements are summarized; Python host.lab.observations(full=True) can read full arrays."
    parameters = {
        "properties": {
            **_RUN,
            "after_sequence": {"type": "integer", "minimum": -1},
            "limit": {"type": "integer", "minimum": 1},
        },
        "required": ["run_id"],
    }
