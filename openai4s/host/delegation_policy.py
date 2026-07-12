"""Fail-closed execution policy for delegated child Agents."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

_DECISIONS = frozenset({"allow", "ask", "deny"})
_ALWAYS = frozenset(
    {
        "submit_output",
        "prov_record",
        "prov_resolve_path",
        "search_capabilities",
        "capabilities",
    }
)
_ALIASES: dict[str, frozenset[str]] = {
    "web": frozenset({"web_search", "web_fetch", "egress_check"}),
    "network": frozenset(
        {"web_search", "web_fetch", "egress_check", "request_network_access"}
    ),
    "read_file": frozenset(
        {
            "read_file",
            "read_text_file",
            "list_dir",
            "list_directory",
            "glob",
            "glob_files",
            "grep",
            "content_search",
        }
    ),
    "write_file": frozenset({"write_file", "edit_file"}),
    "files": frozenset(
        {
            "read_file",
            "read_text_file",
            "list_dir",
            "list_directory",
            "glob",
            "glob_files",
            "grep",
            "content_search",
            "write_file",
            "edit_file",
        }
    ),
    "bash": frozenset(
        {"authorize_bash", "consume_bash_authorization", "record_bash_result"}
    ),
    "env": frozenset({"env_list", "env_use", "env_setup", "env_create"}),
    "skills": frozenset(
        {
            "search_skills",
            "load_skill",
            "skills_get",
            "skills_list",
            "skills_read",
            "skills_edit",
            "skills_delete",
            "skills_publish",
        }
    ),
    "artifacts": frozenset(
        {
            "artifacts",
            "artifact_marker",
            "artifact_path",
            "list_artifacts",
            "get_artifact_metadata",
            "list_artifact_versions",
            "save_artifact",
            "restore_artifact_version",
            "lineage_get",
            "lineage_graph",
        }
    ),
    "data": frozenset(
        {"query", "query_schema", "frames", "lineage_get", "lineage_graph"}
    ),
    "workflow": frozenset(
        {
            "todo_read",
            "todo_write",
            "plan_read",
            "plan_update",
            "review_status",
            "update_plan_step",
        }
    ),
    "session": frozenset(
        {
            "session_status",
            "session_create_checkpoint",
            "session_fork",
            "session_revert_preview",
            "session_pending_permissions",
            "create_checkpoint",
            "fork_session",
            "revert_preview",
            "pending_permissions",
        }
    ),
    "delegation": frozenset(
        {
            "delegate",
            "delegate_task",
            "children",
            "list_children",
            "collect",
            "collect_children",
            "stop_child",
            "send_message",
            "send_child_message",
            "delegation_stats",
        }
    ),
    "background": frozenset(
        {"exec_background", "exec_list", "exec_peek", "exec_interrupt"}
    ),
    "mcp": frozenset(
        {
            "mcp_list",
            "mcp_tools",
            "mcp_resources",
            "mcp_resource_read",
            "mcp_prompts",
            "mcp_prompt_get",
            "mcp_call",
            "list_mcp_servers",
            "list_mcp_tools",
            "list_mcp_resources",
            "read_mcp_resource",
            "list_mcp_prompts",
            "get_mcp_prompt",
            "call_mcp_tool",
        }
    ),
    "remote": frozenset(
        {
            "remote_gpu_status",
            "register_remote_capability",
            "fold",
            "score_mutations",
            "compute_submit",
            "compute_status",
            "compute_result",
            "compute_cancel",
            "compute_close",
            "compute_ssh",
            "compute_scp",
            "compute_set_concurrency",
        }
    ),
    "compute": frozenset(
        {
            "compute_submit",
            "compute_status",
            "compute_result",
            "compute_cancel",
            "compute_close",
            "compute_ssh",
            "compute_scp",
            "compute_set_concurrency",
        }
    ),
    "llm": frozenset({"llm", "current_model", "list_models"}),
    "credentials": frozenset(
        {
            "credentials_get",
            "credentials_issue",
            "credentials_redeem",
            "credentials_list",
            "credentials_set",
        }
    ),
    "memory": frozenset({"remember"}),
    "dynamic": frozenset(
        {
            "dynamic_tool_define",
            "dynamic_tool_list",
            "dynamic_tool_promote",
            "dynamic_tool_versions",
            "dynamic_tool_activate",
            "dynamic_tool_rollback",
        }
    ),
}


class DelegationPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class ChildExecutionPolicy:
    restricted: bool
    allowed: frozenset[str]
    permissions: Mapping[str, str]

    def allows(self, method: str, tool: Any | None = None) -> bool:
        method = _name(method)
        if method in _ALWAYS or not self.restricted:
            return True
        candidates = self._candidates(method, tool)
        for capability in self.allowed:
            if capability in candidates:
                return True
            if candidates & _ALIASES.get(capability, frozenset()):
                return True
            if capability == "dynamic" and method.startswith("dynamic:"):
                return True
        return False

    def decision(self, method: str, tool: Any | None = None) -> str | None:
        method = _name(method)
        if method in _ALWAYS:
            return "allow"
        candidates = self._candidates(method, tool)
        specific = [
            self.permissions[item] for item in candidates if item in self.permissions
        ]
        for wanted in ("deny", "ask", "allow"):
            if wanted in specific:
                return wanted
        alias_decisions = [
            self.permissions[alias]
            for alias, members in _ALIASES.items()
            if alias in self.permissions and candidates & members
        ]
        for wanted in ("deny", "ask", "allow"):
            if wanted in alias_decisions:
                return wanted
        return self.permissions.get("*")

    def visible(self, tool: Any) -> bool:
        method = str(getattr(tool, "host_method", "") or "")
        return self.allows(method, tool) and self.decision(method, tool) != "deny"

    def allows_alias(self, alias: str) -> bool:
        if not self.restricted:
            return True
        alias = _name(alias)
        return alias in self.allowed or any(
            self.allows(method) for method in _ALIASES.get(alias, ())
        )

    def permits_capability(self, capability: str) -> bool:
        capability = _name(capability)
        members = _ALIASES.get(capability)
        if members:
            return all(self.allows(method) for method in members)
        return self.allows(capability)

    def public(self) -> dict[str, Any]:
        return {
            "restricted": self.restricted,
            "capabilities": sorted(self.allowed),
            "permissions": dict(self.permissions),
        }

    @staticmethod
    def _candidates(method: str, tool: Any | None) -> frozenset[str]:
        values = {method}
        if tool is not None:
            values.add(_name(str(getattr(tool, "name", "") or "")))
            values.add(_name(str(getattr(tool, "host_method", "") or "")))
        return frozenset(value for value in values if value)


def child_execution_policy(spec: Mapping[str, Any]) -> ChildExecutionPolicy:
    raw_capabilities = spec.get("capabilities")
    unrestricted = spec.get("unrestricted")
    if unrestricted is not None and type(unrestricted) is not bool:
        raise DelegationPolicyError("unrestricted must be a boolean")
    if raw_capabilities is None:
        capabilities = frozenset()
        restricted = unrestricted is False
    else:
        if isinstance(raw_capabilities, str):
            raw_items: Sequence[Any] = (raw_capabilities,)
        elif isinstance(raw_capabilities, Sequence) and not isinstance(
            raw_capabilities, (bytes, bytearray, memoryview)
        ):
            raw_items = raw_capabilities
        else:
            raise DelegationPolicyError(
                "capabilities must be a string or list of capability names"
            )
        values = []
        for raw in raw_items:
            value = _name(str(raw or ""))
            if not value:
                raise DelegationPolicyError(
                    "capabilities must contain only non-empty names"
                )
            values.append(value)
        capabilities = frozenset(values)
        restricted = True

    raw_permissions = spec.get("permissions")
    if raw_permissions is None:
        permissions: dict[str, str] = {}
    elif not isinstance(raw_permissions, Mapping):
        raise DelegationPolicyError("permissions must be an object")
    else:
        permissions = {}
        for raw_key, raw_value in raw_permissions.items():
            key = _name(str(raw_key or ""))
            decision = _name(str(raw_value or ""))
            if not key:
                raise DelegationPolicyError("permission names must not be empty")
            if decision not in _DECISIONS:
                raise DelegationPolicyError(
                    f"permission {key!r} must be allow, ask, or deny"
                )
            permissions[key] = decision
    return ChildExecutionPolicy(
        restricted=restricted,
        allowed=capabilities,
        permissions=MappingProxyType(permissions),
    )


def _name(value: str) -> str:
    return value.strip().casefold().replace("-", "_")


__all__ = [
    "ChildExecutionPolicy",
    "DelegationPolicyError",
    "child_execution_policy",
]
