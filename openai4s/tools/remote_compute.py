"""Native orchestration tools for the remote-compute job lifecycle.

Scientific work still runs in the remote job's command environment.  These
tools expose only the small control-plane surface needed to create and manage
that work; provider-specific configuration, credentials, SSH/SCP, shell
helpers, and scientific completion deliberately stay outside this catalogue.
"""

from __future__ import annotations

from typing import Any

from openai4s.tools.base import Tool
from openai4s.tools.contexts import ControlToolContext
from openai4s.tools.taxonomy import EXTERNAL_WRITE, RUNTIME_MUTATION, resource_key


def _provider_and_job_resources(arguments: Any) -> tuple[str, ...]:
    """Return stable provider/job identities without leaking command text."""

    spec = arguments if isinstance(arguments, dict) else {}
    keys = [resource_key("remote_compute_provider", spec.get("provider"))]
    job_id = spec.get("job_id")
    if job_id:
        keys.append(resource_key("remote_compute_job", job_id))
    return tuple(keys)


class SubmitRemoteComputeJobTool(Tool):
    """Create one approval-gated job on an existing compute provider."""

    name = "compute_submit"
    host_method = "compute_submit"
    description = (
        "Submit one bounded remote-compute job to an existing provider. "
        "Use the returned job_id with compute_result or compute_cancel."
    )
    parameters = {
        "properties": {
            "provider": {
                "type": "string",
                "minLength": 1,
                "maxLength": 256,
                "description": "Registered target such as ssh:lab or byoc:nvidia.",
            },
            "command": {
                "type": "string",
                "minLength": 1,
                "maxLength": 200000,
                "description": "Remote job script executed by the selected provider.",
            },
            "intent": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2000,
                "description": "Short human-readable purpose shown for approval.",
            },
            "timeout_seconds": {
                "type": "integer",
                "minimum": 1,
                "maximum": 604800,
                "description": "Optional remote runtime limit (at most seven days).",
            },
        },
        "required": ["provider", "command", "intent"],
    }
    read_only = False
    dangerous = True
    needs_network = True
    screen_untrusted_output = True
    side_effect_class = EXTERNAL_WRITE
    permission_target_key = "provider"
    resource_key_prefix = "remote_compute_provider"
    resource_target_key = "provider"

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        # Model-native execution validates against the bounded schema before
        # reaching this method.  A Python Cell's existing host.compute SDK also
        # routes through the same Host method and may carry its richer trusted
        # compatibility payload (staged inputs, credential *names*, provider
        # parameters, reuse handle); preserve that Code-as-Action contract.
        return runtime.invoke(self.host_method, dict(arguments))


class RemoteComputeStatusTool(Tool):
    """Read the session-wide compute capacity and live-job count."""

    name = "compute_status"
    host_method = "compute_status"
    description = (
        "Inspect remote-compute capacity and live-job counts for this session."
    )
    parameters = {"properties": {}, "required": []}
    requires_approval = False
    resource_key_prefix = "remote_compute"
    resource_target_default = "session"

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        del arguments
        return runtime.invoke(self.host_method, {})


class GetRemoteComputeJobResultTool(Tool):
    """Poll and, when ready, harvest one exact submitted job."""

    name = "compute_result"
    host_method = "compute_result"
    description = (
        "Read one remote job's current or terminal result and harvest ready outputs."
    )
    parameters = {
        "properties": {
            "provider": {
                "type": "string",
                "minLength": 1,
                "maxLength": 256,
            },
            "job_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 256,
            },
        },
        "required": ["provider", "job_id"],
    }
    # Result polling can change the manager's terminal state and harvest files
    # already authorized by submit, so it is a runtime mutation but does not
    # ask for a second approval.
    read_only = False
    requires_approval = False
    needs_network = True
    screen_untrusted_output = True
    side_effect_class = RUNTIME_MUTATION
    resource_key_prefix = "remote_compute_job"
    resource_target_key = "job_id"

    def resource_keys(self, arguments: Any) -> tuple[str, ...]:
        return _provider_and_job_resources(arguments)

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        return runtime.invoke(
            self.host_method,
            {"provider": arguments["provider"], "job_id": arguments["job_id"]},
        )


class CancelRemoteComputeJobTool(Tool):
    """Stop one exact job without creating or widening authority."""

    name = "compute_cancel"
    host_method = "compute_cancel"
    description = "Cancel one exact running remote-compute job."
    parameters = {
        "properties": {
            "provider": {
                "type": "string",
                "minLength": 1,
                "maxLength": 256,
            },
            "job_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 256,
            },
        },
        "required": ["provider", "job_id"],
    }
    read_only = False
    dangerous = True
    requires_approval = False
    needs_network = True
    screen_untrusted_output = True
    side_effect_class = EXTERNAL_WRITE
    resource_key_prefix = "remote_compute_job"
    resource_target_key = "job_id"

    def resource_keys(self, arguments: Any) -> tuple[str, ...]:
        return _provider_and_job_resources(arguments)

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        return runtime.invoke(
            self.host_method,
            {"provider": arguments["provider"], "job_id": arguments["job_id"]},
        )


class CloseRemoteComputeTool(Tool):
    """Release one provider handle and its explicitly named job handles."""

    name = "compute_close"
    host_method = "compute_close"
    description = (
        "Release a remote-compute provider handle and close its listed job handles."
    )
    parameters = {
        "properties": {
            "provider": {
                "type": "string",
                "minLength": 1,
                "maxLength": 256,
            },
            "job_ids": {
                "type": "array",
                "items": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 256,
                },
                "maxItems": 100,
                "description": "Exact job handles owned by this provider handle.",
            },
        },
        "required": ["provider", "job_ids"],
    }
    read_only = False
    dangerous = True
    requires_approval = False
    needs_network = True
    screen_untrusted_output = True
    side_effect_class = EXTERNAL_WRITE
    resource_key_prefix = "remote_compute_provider"
    resource_target_key = "provider"

    def resource_keys(self, arguments: Any) -> tuple[str, ...]:
        spec = arguments if isinstance(arguments, dict) else {}
        keys = [resource_key("remote_compute_provider", spec.get("provider"))]
        keys.extend(
            resource_key("remote_compute_job", job_id)
            for job_id in (spec.get("job_ids") or [])
        )
        return tuple(keys)

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        return runtime.invoke(
            self.host_method,
            {
                "provider": arguments["provider"],
                "job_ids": list(arguments["job_ids"]),
            },
        )


__all__ = [
    "CancelRemoteComputeJobTool",
    "CloseRemoteComputeTool",
    "GetRemoteComputeJobResultTool",
    "RemoteComputeStatusTool",
    "SubmitRemoteComputeJobTool",
]
