"""Class-based native tools for current-session orchestration."""

from __future__ import annotations

from typing import Any

from openai4s.tools.base import Tool
from openai4s.tools.contexts import ControlToolContext
from openai4s.tools.taxonomy import RUNTIME_MUTATION, resource_key


class SessionStatusTool(Tool):
    """Inspect checkpoints, branches, recovery, and pending approval count."""

    name = "session_status"
    host_method = "session_status"
    description = (
        "Inspect the current session's branch, checkpoint, and recovery state."
    )
    parameters = {
        "properties": {
            "checkpoint_limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
            }
        },
        "required": [],
    }
    requires_approval = False
    resource_key_prefix = "session"
    resource_target_default = "current"

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        return runtime.invoke(self.host_method, dict(arguments))


class CreateCheckpointTool(Tool):
    """Append an immutable checkpoint without changing the live workspace."""

    name = "create_checkpoint"
    host_method = "session_create_checkpoint"
    description = "Create an immutable checkpoint of the current scientific session."
    parameters = {
        "properties": {
            "reason": {"type": "string", "minLength": 1, "maxLength": 200},
            "expected_head": {
                "type": "string",
                "minLength": 1,
                "maxLength": 256,
                "description": "Optional compare-and-swap checkpoint head.",
            },
        },
        "required": [],
    }
    read_only = False
    # Append-only and reversible: it creates no external work and does not
    # alter the live workspace, so it remains approval-free but still forms a
    # mutating batch barrier and a durable audit event.
    requires_approval = False
    side_effect_class = RUNTIME_MUTATION
    resource_key_prefix = "session"
    resource_target_default = "current"

    def resource_keys(self, arguments: Any) -> tuple[str, ...]:
        spec = arguments if isinstance(arguments, dict) else {}
        return (
            resource_key("session", "current"),
            resource_key("checkpoint", spec.get("expected_head") or "head"),
        )

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        return runtime.invoke(self.host_method, dict(arguments))


class ForkSessionTool(Tool):
    """Fork one exact immutable cursor into an isolated, view-only branch."""

    name = "fork_session"
    host_method = "session_fork"
    description = (
        "Fork the current session from exactly one checkpoint, Cell, or message "
        "cursor into an isolated view-only branch."
    )
    parameters = {
        "properties": {
            "from_checkpoint_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 256,
            },
            "from_cell_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 256,
            },
            "from_message_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 256,
            },
            "name": {"type": "string", "minLength": 1, "maxLength": 200},
        },
        "required": [],
    }
    read_only = False
    requires_approval = False
    side_effect_class = RUNTIME_MUTATION
    resource_key_prefix = "session"
    resource_target_default = "current"

    def native_precheck(self, arguments: dict) -> str | None:
        sources = [
            arguments.get("from_checkpoint_id"),
            arguments.get("from_cell_id"),
            arguments.get("from_message_id"),
        ]
        if sum(value not in (None, "") for value in sources) != 1:
            return (
                "provide exactly one of from_checkpoint_id, from_cell_id, "
                "or from_message_id"
            )
        return None

    def permission_target(self, arguments: Any) -> str:
        spec = arguments if isinstance(arguments, dict) else {}
        return str(
            spec.get("from_checkpoint_id")
            or spec.get("from_cell_id")
            or spec.get("from_message_id")
            or "current"
        )

    def resource_keys(self, arguments: Any) -> tuple[str, ...]:
        spec = arguments if isinstance(arguments, dict) else {}
        kind = "checkpoint"
        target = spec.get("from_checkpoint_id")
        if spec.get("from_cell_id"):
            kind, target = "cell", spec["from_cell_id"]
        elif spec.get("from_message_id"):
            kind, target = "message", spec["from_message_id"]
        return (
            resource_key("session", "current"),
            resource_key(kind, target or "required"),
            resource_key("branch", "new"),
        )

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        return runtime.invoke(self.host_method, dict(arguments))


class RevertPreviewTool(Tool):
    """Compute a conflict-aware revert plan without changing session state."""

    name = "revert_preview"
    host_method = "session_revert_preview"
    description = (
        "Preview the exact workspace, Notebook, Artifact, and state changes for "
        "reverting to a checkpoint; this never applies the revert."
    )
    parameters = {
        "properties": {
            "checkpoint_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 256,
            }
        },
        "required": ["checkpoint_id"],
    }
    requires_approval = False
    resource_key_prefix = "checkpoint"
    resource_target_key = "checkpoint_id"

    def resource_keys(self, arguments: Any) -> tuple[str, ...]:
        spec = arguments if isinstance(arguments, dict) else {}
        return (
            resource_key("session", "current"),
            resource_key("checkpoint", spec.get("checkpoint_id")),
        )

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        return runtime.invoke(self.host_method, dict(arguments))


class PendingPermissionsTool(Tool):
    """Inspect durable approval identities without exposing stored inputs."""

    name = "pending_permissions"
    host_method = "session_pending_permissions"
    description = "List unresolved human-approval requests for the current session."
    parameters = {
        "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}},
        "required": [],
    }
    requires_approval = False
    resource_key_prefix = "permission"
    resource_target_default = "pending"

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        return runtime.invoke(self.host_method, dict(arguments))


__all__ = [
    "CreateCheckpointTool",
    "ForkSessionTool",
    "PendingPermissionsTool",
    "RevertPreviewTool",
    "SessionStatusTool",
]
