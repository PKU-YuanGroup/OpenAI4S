"""Session-scoped composition of built-in and sandboxed dynamic tools."""

from __future__ import annotations

import threading
from typing import Any, Iterable, Mapping, Sequence

from openai4s.tools.base import Tool
from openai4s.tools.dynamic import DynamicToolManifest, DynamicToolRegistry
from openai4s.tools.native import ToolSpec, control_tool_specs
from openai4s.tools.registry import all_tools

_GROUPS: tuple[dict[str, Any], ...] = (
    {
        "id": "core",
        "always": True,
        "description": "Workspace, environment, and Web primitives.",
        "keywords": (),
    },
    {
        "id": "skills",
        "always": True,
        "description": "Progressive Skill discovery and loading.",
        "keywords": (),
    },
    {
        "id": "dynamic",
        "always": True,
        "description": "Session dynamic-tool lifecycle and active proxies.",
        "keywords": (),
    },
    {
        "id": "artifacts",
        "always": False,
        "description": "Versioned artifact discovery and registration.",
        "keywords": (
            "artifact",
            "output file",
            "result file",
            "report",
            "dataset",
            "table",
            "plot",
            "figure",
            "csv",
            "产物",
            "结果文件",
            "报告",
            "数据集",
            "表格",
            "图表",
        ),
    },
    {
        "id": "delegation",
        "always": False,
        "description": "Sub-agent delegation and live steering.",
        "keywords": (
            "delegate",
            "sub-agent",
            "subagent",
            "specialist",
            "parallel",
            "fan out",
            "research",
            "investigate",
            "子代理",
            "委派",
            "专家",
            "并行",
            "研究",
            "调查",
        ),
    },
    {
        "id": "workflow",
        "always": False,
        "description": "Todo and approved-plan progress.",
        "keywords": (
            "plan",
            "todo",
            "step",
            "progress",
            "workflow",
            "multi-step",
            "计划",
            "待办",
            "步骤",
            "进度",
            "工作流",
        ),
    },
    {
        "id": "mcp",
        "always": False,
        "description": "MCP connector discovery and calls.",
        "keywords": (
            "mcp",
            "connector",
            "external service",
            "slack",
            "github",
            "notion",
            "drive",
            "calendar",
            "database",
            "连接器",
            "外部服务",
            "日历",
            "数据库",
        ),
    },
    {
        "id": "network",
        "always": False,
        "description": "Human-approved egress policy changes.",
        "keywords": (
            "network access",
            "allowlist",
            "egress",
            "domain blocked",
            "permission denied",
            "网络访问",
            "白名单",
            "域名",
            "权限被拒",
        ),
    },
    {
        "id": "remote",
        "always": False,
        "description": "Remote GPU capability inspection and registration.",
        "keywords": (
            "gpu",
            "remote",
            "ssh",
            "protein structure",
            "protein",
            "sequence",
            "mutation",
            "fold",
            "远程",
            "显卡",
            "蛋白结构",
            "蛋白",
            "序列",
            "突变",
            "折叠",
        ),
    },
)
_GROUP_BY_ID = {group["id"]: group for group in _GROUPS}
_TOOL_GROUP = {
    **{
        name: "core"
        for name in (
            "list_dir",
            "read_text_file",
            "write_file",
            "glob_files",
            "content_search",
            "edit_file",
            "env_list",
            "env_use",
            "env_create",
            "web_search",
            "web_fetch",
        )
    },
    "search_skills": "skills",
    "load_skill": "skills",
    "list_artifacts": "artifacts",
    "save_artifact": "artifacts",
    "read_todos": "workflow",
    "write_todos": "workflow",
    "read_plan": "workflow",
    "update_plan_step": "workflow",
    "delegate_task": "delegation",
    "list_children": "delegation",
    "collect_children": "delegation",
    "stop_child": "delegation",
    "send_child_message": "delegation",
    "list_mcp_servers": "mcp",
    "list_mcp_tools": "mcp",
    "call_mcp_tool": "mcp",
    "request_network_access": "network",
    "remote_gpu_status": "remote",
    "register_remote_capability": "remote",
    "define_dynamic_tool": "dynamic",
    "list_dynamic_tools": "dynamic",
    "promote_dynamic_tool": "dynamic",
}


class SessionToolCatalog:
    """One non-global tool view used by model declaration and execution.

    Built-ins remain the immutable instances created only by ``registry.py``.
    Dynamic proxies are derived from this session's isolated manifest registry
    and never mutate the process-global registry.
    """

    def __init__(
        self,
        dynamic_registry: DynamicToolRegistry | None = None,
        *,
        builtins: Iterable[Tool] | None = None,
    ) -> None:
        self.dynamic_registry = dynamic_registry
        self._builtins = tuple(all_tools() if builtins is None else builtins)
        self._lock = threading.RLock()
        self._active_groups = {
            str(group["id"]) for group in _GROUPS if bool(group["always"])
        }
        names = [tool.name for tool in self._builtins]
        methods = [tool.host_method for tool in self._builtins]
        if len(set(names)) != len(names) or len(set(methods)) != len(methods):
            raise ValueError("session tool catalog contains duplicate built-ins")
        if dynamic_registry is not None:
            dynamic_names = {tool.name for tool in dynamic_registry.tools()}
            collisions = sorted(dynamic_names & set(names))
            if collisions:
                raise ValueError(
                    "dynamic manifests collide with built-in tools: "
                    + ", ".join(collisions)
                )

    def tools(self) -> tuple[Tool, ...]:
        with self._lock:
            dynamic = (
                self.dynamic_registry.tools()
                if self.dynamic_registry is not None
                else ()
            )
            return (*self._builtins, *dynamic)

    def specs(self) -> tuple[ToolSpec, ...]:
        return control_tool_specs(self.tools())

    def specs_for(
        self,
        messages: Sequence[Mapping[str, Any]],
    ) -> tuple[ToolSpec, ...]:
        """Return a monotonic, task-aware progressive tool projection."""

        text = self._message_text(messages)
        with self._lock:
            for group in _GROUPS:
                if group["always"]:
                    continue
                if any(keyword in text for keyword in group["keywords"]):
                    self._active_groups.add(str(group["id"]))
            selected = tuple(
                tool
                for tool in self.tools()
                if self._group_for(tool) in self._active_groups
            )
        return control_tool_specs(selected)

    def activate_groups(self, *group_ids: str) -> None:
        """Explicit runtime seam for a router/UI to enable known groups."""

        unknown = sorted(set(group_ids) - set(_GROUP_BY_ID))
        if unknown:
            raise ValueError("unknown tool groups: " + ", ".join(unknown))
        with self._lock:
            self._active_groups.update(group_ids)

    def group_metadata(self) -> tuple[dict[str, Any], ...]:
        """Stable group metadata for diagnostics and future capability search."""

        tools = self.tools()
        return tuple(
            {
                "id": group["id"],
                "always": bool(group["always"]),
                "active": group["id"] in self._active_groups,
                "description": group["description"],
                "tools": [
                    tool.name for tool in tools if self._group_for(tool) == group["id"]
                ],
            }
            for group in _GROUPS
        )

    def get(self, name: str) -> Tool | None:
        return next((tool for tool in self.tools() if tool.name == name), None)

    def get_by_host_method(self, host_method: str) -> Tool | None:
        return next(
            (tool for tool in self.tools() if tool.host_method == host_method),
            None,
        )

    def define(
        self,
        spec: Mapping[str, Any],
        *,
        approved: bool = False,
    ) -> dict[str, Any]:
        registry = self._required_dynamic_registry()
        if not approved:
            raise PermissionError("dynamic tool definition requires Host approval")
        name = str(spec.get("name") or "")
        if self.get(name) is not None:
            raise ValueError(f"tool name already exists in this session: {name!r}")
        with self._lock:
            manifest = registry.define(spec)
        return self._public_manifest(manifest)

    def list_dynamic(self) -> list[dict[str, Any]]:
        registry = self._required_dynamic_registry()
        with self._lock:
            manifests = [tool.manifest for tool in registry.tools()]
        return [self._public_manifest(manifest) for manifest in manifests]

    def promote(
        self,
        name: str,
        scope: str,
        *,
        approved: bool = False,
    ) -> dict[str, Any]:
        registry = self._required_dynamic_registry()
        if not approved:
            raise PermissionError("dynamic tool promotion requires Host approval")
        with self._lock:
            manifest = registry.promote(name, scope, approved=True)
        return self._public_manifest(manifest)

    def execute(self, name: str, context: Any, arguments: Mapping[str, Any]) -> Any:
        """Execute one resolved tool after the caller's Host policy envelope."""

        tool = self.get(name)
        if tool is None:
            raise KeyError(f"unknown session tool {name!r}")
        spec = dict(arguments)
        error = tool.validation_error(spec)
        if error is not None:
            raise ValueError(error)
        precheck = tool.native_precheck(spec)
        if precheck:
            raise ValueError(precheck)
        return tool.execute(context, spec)

    def _required_dynamic_registry(self) -> DynamicToolRegistry:
        if self.dynamic_registry is None:
            raise RuntimeError("dynamic tools are unavailable in this session")
        return self.dynamic_registry

    @staticmethod
    def _group_for(tool: Tool) -> str:
        if tool.name in _TOOL_GROUP:
            return _TOOL_GROUP[tool.name]
        if tool.host_method.startswith("dynamic:"):
            return "dynamic"
        # A future trusted built-in must stay reachable until it declares a
        # stable group here; omission must not silently remove capability.
        return "core"

    @staticmethod
    def _message_text(messages: Sequence[Mapping[str, Any]]) -> str:
        values: list[str] = []
        for message in messages[-24:]:
            if str(message.get("role") or "") == "system":
                continue
            content = message.get("content")
            if isinstance(content, str):
                values.append(content.casefold())
        return "\n".join(values)

    @staticmethod
    def _public_manifest(manifest: DynamicToolManifest) -> dict[str, Any]:
        return {
            "name": manifest.name,
            "description": manifest.description,
            "input_schema": dict(manifest.input_schema),
            "output_schema": dict(manifest.output_schema),
            "imports": list(manifest.imports),
            "scope": manifest.scope,
            "session_id": manifest.session_id,
            "ttl_s": manifest.ttl_s,
            "created_at": manifest.created_at,
            "expires_at": manifest.expires_at,
            "manifest_id": manifest.manifest_id,
        }


__all__ = ["SessionToolCatalog"]
