"""Session-scoped composition of built-in and sandboxed dynamic tools."""

from __future__ import annotations

import re
import threading
from typing import Any, Callable, Iterable, Mapping, Sequence

from openai4s.tools.base import Tool
from openai4s.tools.dynamic import DynamicToolManifest, DynamicToolRegistry
from openai4s.tools.native import ToolSpec, control_tool_specs
from openai4s.tools.registry import all_tools

_GROUPS: tuple[dict[str, Any], ...] = (
    {
        "id": "capabilities",
        "always": True,
        "description": "Active discovery for progressively disclosed tools.",
        "keywords": (),
    },
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
        "id": "data",
        "always": False,
        "description": "Read-only Store queries, frame history, and lineage.",
        "keywords": (
            "sql",
            "database",
            "query schema",
            "table schema",
            "frame history",
            "session history",
            "lineage",
            "provenance",
            "upstream artifact",
            "downstream artifact",
            "数据库",
            "数据查询",
            "数据表",
            "会话记录",
            "谱系",
            "血缘",
            "溯源",
        ),
    },
    {
        "id": "science",
        "always": False,
        "description": "Schema-normalized public scientific database search.",
        "keywords": (
            "scientific database",
            "uniprot",
            "protein database",
            "pdb",
            "ensembl",
            "gene database",
            "chembl",
            "pubchem",
            "compound database",
            "arxiv",
            "openalex",
            "paper search",
            "literature database",
            "科学数据库",
            "蛋白数据库",
            "基因数据库",
            "化合物数据库",
            "论文检索",
        ),
    },
    {
        "id": "background",
        "always": False,
        "description": "Long-running background execution and job control.",
        "keywords": (
            "background job",
            "background execution",
            "long-running",
            "asynchronous",
            "async job",
            "job status",
            "peek job",
            "interrupt job",
            "后台任务",
            "后台执行",
            "长时间运行",
            "异步任务",
            "任务状态",
            "中断任务",
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
        "id": "session",
        "always": False,
        "description": "Session status, checkpoints, branches, and approvals.",
        "keywords": (
            "session status",
            "checkpoint",
            "branch",
            "fork session",
            "revert preview",
            "pending permission",
            "human approval",
            "会话状态",
            "检查点",
            "分支",
            "派生会话",
            "回退预览",
            "待审批",
            "人工审批",
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
        "description": (
            "Remote GPU capability inspection, registration, and job lifecycle."
        ),
        "keywords": (
            "gpu",
            "remote",
            "ssh",
            "remote compute",
            "compute job",
            "job result",
            "cancel job",
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
    "search_capabilities": "capabilities",
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
    "skill_status": "skills",
    "skill_history": "skills",
    "rollback_skill_version": "skills",
    "list_artifacts": "artifacts",
    "get_artifact_metadata": "artifacts",
    "list_artifact_versions": "artifacts",
    "save_artifact": "artifacts",
    "restore_artifact_version": "artifacts",
    "query_schema": "data",
    "query": "data",
    "frames": "data",
    "lineage_get": "data",
    "lineage_graph": "data",
    "science_list_dbs": "science",
    "science_search": "science",
    "read_todos": "workflow",
    "write_todos": "workflow",
    "read_plan": "workflow",
    "review_status": "workflow",
    "update_plan_step": "workflow",
    "session_status": "session",
    "create_checkpoint": "session",
    "fork_session": "session",
    "revert_preview": "session",
    "pending_permissions": "session",
    "delegate_task": "delegation",
    "list_children": "delegation",
    "collect_children": "delegation",
    "stop_child": "delegation",
    "send_child_message": "delegation",
    "exec_background": "background",
    "exec_list": "background",
    "exec_peek": "background",
    "exec_interrupt": "background",
    "list_mcp_servers": "mcp",
    "list_mcp_tools": "mcp",
    "list_mcp_resources": "mcp",
    "read_mcp_resource": "mcp",
    "list_mcp_prompts": "mcp",
    "get_mcp_prompt": "mcp",
    "call_mcp_tool": "mcp",
    "request_network_access": "network",
    "remote_gpu_status": "remote",
    "register_remote_capability": "remote",
    "compute_submit": "remote",
    "compute_status": "remote",
    "compute_result": "remote",
    "compute_cancel": "remote",
    "compute_close": "remote",
    "define_dynamic_tool": "dynamic",
    "list_dynamic_tools": "dynamic",
    "promote_dynamic_tool": "dynamic",
    "list_dynamic_tool_versions": "dynamic",
    "activate_dynamic_tool_version": "dynamic",
    "rollback_dynamic_tool_version": "dynamic",
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
        tool_filter: Callable[[Tool], bool] | None = None,
    ) -> None:
        self.dynamic_registry = dynamic_registry
        self._builtins = tuple(all_tools() if builtins is None else builtins)
        self._tool_filter = tool_filter
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
            tools = (*self._builtins, *dynamic)
            if self._tool_filter is not None:
                tools = tuple(tool for tool in tools if self._tool_filter(tool))
            return tools

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
                "keywords": list(group["keywords"]),
                "tools": [
                    tool.name for tool in tools if self._group_for(tool) == group["id"]
                ],
            }
            for group in _GROUPS
        )

    def search_capabilities(self, query: str) -> dict[str, Any]:
        """Find and monotonically activate matching progressive groups."""

        normalized = str(query or "").strip().casefold()
        if not normalized:
            raise ValueError("capability query must be a non-empty string")
        terms = tuple(
            dict.fromkeys(
                part
                for part in re.findall(r"[\w-]+", normalized, flags=re.UNICODE)
                if part
            )
        )
        matches: list[tuple[int, dict[str, Any]]] = []
        with self._lock:
            tools = self.tools()
            for group in _GROUPS:
                group_tools = [
                    tool.name for tool in tools if self._group_for(tool) == group["id"]
                ]
                fields = (
                    str(group["id"]),
                    str(group["description"]),
                    *(str(keyword) for keyword in group["keywords"]),
                    *group_tools,
                )
                haystack = "\n".join(fields).casefold()
                term_hits = sum(term in haystack for term in terms)
                exact = normalized == str(group["id"]).casefold()
                phrase = normalized in haystack
                if not (exact or phrase or term_hits):
                    continue
                score = (100 if exact else 0) + (20 if phrase else 0) + term_hits
                matches.append(
                    (
                        score,
                        {
                            "id": group["id"],
                            "description": group["description"],
                            "keywords": list(group["keywords"]),
                            "tools": group_tools,
                        },
                    )
                )
            matches.sort(key=lambda item: (-item[0], str(item[1]["id"])))
            before = set(self._active_groups)
            self._active_groups.update(str(item[1]["id"]) for item in matches)
            active = [
                str(group["id"])
                for group in _GROUPS
                if str(group["id"]) in self._active_groups
            ]
            visible_tools = [
                tool.name
                for tool in tools
                if self._group_for(tool) in self._active_groups
            ]
        return {
            "query": normalized,
            "matched_groups": [item[1] for item in matches],
            "activated_group_ids": [
                item[1]["id"] for item in matches if item[1]["id"] not in before
            ],
            "active_group_ids": active,
            "visible_tools": visible_tools,
        }

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
        if any(tool.name == name for tool in self._builtins):
            raise ValueError(f"tool name conflicts with a built-in: {name!r}")
        if registry.session_manifest(name) is not None:
            raise ValueError(f"session dynamic tool already exists: {name!r}")
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
        return {
            **self._public_manifest(manifest),
            "audit": registry.last_audit_event,
        }

    def list_dynamic_versions(
        self,
        *,
        name: str | None = None,
        scope: str | None = None,
    ) -> dict[str, Any]:
        registry = self._required_dynamic_registry()
        with self._lock:
            result = registry.versions(name=name, scope=scope)
        return {
            "count": len(result["versions"]),
            "versions": result["versions"],
            "audit": result["events"],
        }

    def activate_dynamic_version(
        self,
        name: str,
        scope: str,
        manifest_id: str,
        *,
        approved: bool = False,
    ) -> dict[str, Any]:
        registry = self._required_dynamic_registry()
        if not approved:
            raise PermissionError("dynamic tool activation requires Host approval")
        with self._lock:
            manifest = registry.activate(
                name,
                scope,
                manifest_id,
                approved=True,
            )
        return {
            **self._public_manifest(manifest),
            "audit": registry.last_audit_event,
        }

    def rollback_dynamic_version(
        self,
        name: str,
        scope: str,
        *,
        approved: bool = False,
    ) -> dict[str, Any]:
        registry = self._required_dynamic_registry()
        if not approved:
            raise PermissionError("dynamic tool rollback requires Host approval")
        with self._lock:
            manifest = registry.rollback(name, scope, approved=True)
        return {
            **self._public_manifest(manifest),
            "audit": registry.last_audit_event,
        }

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
            "scope_id": manifest.scope_id,
            "session_id": manifest.session_id,
            "ttl_s": manifest.ttl_s,
            "created_at": manifest.created_at,
            "expires_at": manifest.expires_at,
            "manifest_id": manifest.manifest_id,
            "source_manifest_id": manifest.source_manifest_id,
            "source_project_id": manifest.source_project_id,
            "source_root_frame_id": manifest.source_root_frame_id,
        }


__all__ = ["SessionToolCatalog"]
