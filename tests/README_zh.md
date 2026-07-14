# Tests（测试套件）

[English](README.md)

本目录是 OpenAI4S 的正确性门禁。默认 pytest suite 使用确定性 fake 覆盖供应商无关 Agent 引擎、Host service、持久 Python/R 内核协议、仓储、安全边界、Tool、Skill 与 Web 组合。它与 [`../harness/`](../harness/) 中可复用的场景/评测层分工不同。

## 离线契约

- `uv run pytest` 不得需要真实 LLM、API key、网络、GPU、SSH 主机、Docker daemon、浏览器或实验室系统。[`conftest.py`](conftest.py) 为每个测试把 `~/.openai4s` 重定向到临时目录，并安装 fake provider/key。
- 外部资源测试必须使用 `pyproject.toml` 注册的 marker 显式 opt-in；[`test_marker_policy.py`](test_marker_policy.py) 负责守护该策略。
- 捕获输入与字节敏感 sample 位于 `fixtures/`；测试不得静默重写它们。
- Network、subprocess、provider、clock、UUID 与 filesystem 边界均应 mock 或约束，除非独立调用的 smoke program 明确声明例外。
- 单模块可运行 `uv run pytest tests/test_kernel.py`，单用例可运行 `uv run pytest tests/test_agent.py::test_max_turns_stop`，完整门禁运行 `uv run pytest`。

## 支持与 Smoke 文件

| 文件 | 职责 |
| --- | --- |
| [`conftest.py`](conftest.py) | 建立 import path、隔离的逐测试数据目录、fake LLM 配置/key、Store 清理与共享 pytest fixture。 |
| [`browser_smoke.mjs`](browser_smoke.mjs) | 对运行中的 Gateway UI 与流式交互路径执行真实浏览器 smoke；本地需与 pytest 分开调用，普通 PR CI workflow 会自动运行。 |
| [`scientific_renderers_smoke.cjs`](scientific_renderers_smoke.cjs) | 对 Web UI 中 UMD 科学 Artifact parser 执行轻依赖 Node 契约检查。 |

## 测试模块

下面逐项列出全部 `test_*.py`。说明指出主要契约边界；单个模块通常还包含 failure、restart、redaction 与 concurrency 回归用例。

| 文件 | 主要覆盖范围 |
| --- | --- |
| [`test_action_ledger_repository.py`](test_action_ledger_repository.py) | Append-only 规范 Action Ledger group/event、Tool group 原子性、execution attempt、migration 与不可变终止状态。 |
| [`test_action_ledger_runtime.py`](test_action_ledger_runtime.py) | Runtime Ledger 写入、参数脱敏、分支前缀继承、中断归约与重启重建。 |
| [`test_action_routing_eval.py`](test_action_routing_eval.py) | 带计分的离线 Tool/Code/Finalize routing fixture 与可审阅 failure/confusion 报告。 |
| [`test_action_timeline_service.py`](test_action_timeline_service.py) | Action、attempt、权限、failure 与 branch cursor 的有界脱敏公开 Timeline 投影。 |
| [`test_actions.py`](test_actions.py) | 原生 call、Python/R fence、不完整/不支持 fence、首 Cell 选择与 finalization 的解析及优先级。 |
| [`test_admet_genetic.py`](test_admet_genetic.py) | Bundled ADMET genetic Skill 的发现及确定性 helper/output 契约。 |
| [`test_agent.py`](test_agent.py) | 离线 outer loop、Code-as-Action cycle、委派、compaction、Artifact 路径、完成规则与 max-turn stop。 |
| [`test_agent_control.py`](test_agent_control.py) | 有序原生 Tool batch、schema failure、barrier、并行读取、取消及完整规范结果闭合。 |
| [`test_agent_engine.py`](test_agent_engine.py) | 通过 fake port 验证纯 `AgentEngine` 编排、routing 优先级、replay-safe 历史、取消与完成原因。 |
| [`test_agent_hybrid.py`](test_agent_hybrid.py) | Hybrid `Agent` 门面的最小集成及复用任务间状态重置。 |
| [`test_agent_profile_repository.py`](test_agent_profile_repository.py) | 具名 Agent profile CRUD、序列化边缘、排序、commit 与并发更新语义。 |
| [`test_agent_runtime.py`](test_agent_runtime.py) | 本地 model/action/cell adapter、动态 Tool catalog、规范 observation、校验、limit 与仅 submit 完成。 |
| [`test_analysis_skills.py`](test_analysis_skills.py) | 可执行 bundled analysis Skill：发现、编译、数据审计、分类/回归指标与确定性 bootstrap。 |
| [`test_annotation_repository.py`](test_annotation_repository.py) | 图像 annotation 持久化、原子 ordinal 分配、transaction/cascade 行为与 Store 门面对等。 |
| [`test_artifact_control_tools.py`](test_artifact_control_tools.py) | 原生 Artifact 生命周期 schema/policy、scoped metadata、经验证不可变恢复、审批、审计与回滚安全。 |
| [`test_artifact_manager.py`](test_artifact_manager.py) | 工作区 Artifact 捕获/版本化、provisional 合并、snapshot、provenance 合并、保护、恢复与广播。 |
| [`test_artifact_mutation_service.py`](test_artifact_mutation_service.py) | 交互式 edit/rename/upload mutation、事件 shape、文本资格、版本化与工作区逃逸拒绝。 |
| [`test_artifact_repository.py`](test_artifact_repository.py) | Artifact/version/environment/lineage 仓储、精确 scope、事务回滚、snapshot binding、列表与恢复 metadata。 |
| [`test_artifact_scope.py`](test_artifact_scope.py) | Frame、root session 与 project scope 之间的 Artifact 所有权继承和冲突规则。 |
| [`test_backend_import_contract.py`](test_backend_import_contract.py) | 模块化过程中声明的 backend facade import/export 与兼容边界。 |
| [`test_background_cleanup.py`](test_background_cleanup.py) | 会话关闭时中断/杀死独立后台内核，防止 worker 泄漏。 |
| [`test_bash_authorization.py`](test_bash_authorization.py) | 绑定 command、cwd、generation、challenge、expiry 与路径约束的一次性 `host.bash` capability。 |
| [`test_capability_state.py`](test_capability_state.py) | 持久 Skill/Specialist enablement、scope 优先级、loader rebinding、sidecar version、event 与重启。 |
| [`test_catalyst_sar_screening.py`](test_catalyst_sar_screening.py) | Catalyst SAR Skill 的发现、文档、安全示例与 helper 契约。 |
| [`test_cell_dependencies.py`](test_cell_dependencies.py) | 保守 Python/R 静态依赖分析、namespace mutation、不确定性与传递 stale 投影。 |
| [`test_cell_execution_service.py`](test_cell_execution_service.py) | Web Cell 事务顺序、generation identity、有界实时输出、日志、捕获、中断与 protocol-only 完成可见性。 |
| [`test_cell_watchdog.py`](test_cell_watchdog.py) | Timeout policy、审批暂停预算、精确取消、SIGINT、hard-kill respawn 与 bootstrap 恢复。 |
| [`test_checkpoint_state_snapshots.py`](test_checkpoint_state_snapshots.py) | Plan/review/memory 状态与不可变 checkpoint 绑定、migration、revert/undo 恢复、legacy partial state 与原子性。 |
| [`test_cli_contract.py`](test_cli_contract.py) | 支持的 CLI 入口、子命令、option、环境 setup 选择、help 文本与无效调用行为。 |
| [`test_compute_nvidia.py`](test_compute_nvidia.py) | 离线 NVIDIA BYOC provider 发现、hosted/self-hosted 创建、命令构造、参数与 secret scrubbing。 |
| [`test_config.py`](test_config.py) | 分层配置、placeholder key 过滤、provider/generic fallback、环境解析与 Notebook REPL flag。 |
| [`test_connector_repository.py`](test_connector_repository.py) | MCP connector CRUD、JSON 规范化、排序、enable/disable、commit 与 Host service 集成。 |
| [`test_context_policy_web.py`](test_context_policy_web.py) | 持久 Web Context Policy、compaction 历史及大输出 Artifact 去重/链接。 |
| [`test_data_background_tools.py`](test_data_background_tools.py) | Class-based 原生 data/background Tool、schema、policy、Host forwarding、只读 query、submit 审批与精确 interrupt。 |
| [`test_delegation_persistence.py`](test_delegation_persistence.py) | 重启安全 child state、budget、stale lease、steering delivery、级联取消与删除清理。 |
| [`test_delegation_policy.py`](test_delegation_policy.py) | 强制 child capability/permission policy、无效 policy 拒绝与阻止嵌套策略放宽。 |
| [`test_delegation_runtime.py`](test_delegation_runtime.py) | 整棵委派树 budget、深度限制、规范 child ledger、取消、lineage、并发与实时 steering。 |
| [`test_dynamic_tool_scopes.py`](test_dynamic_tool_scopes.py) | Project/global/session Dynamic Tool resolution、version activation、promotion、rollback、隔离、审计与防篡改。 |
| [`test_dynamic_tools.py`](test_dynamic_tools.py) | 一次性隔离 Dynamic Tool worker、source gate、无秘密环境、强制 sandbox、schema/TTL/权限检查与 timeout。 |
| [`test_e2e.py`](test_e2e.py) | 经 Skill 使用及内核错误 observation 的离线端到端 Code-as-Action 流程。 |
| [`test_egress.py`](test_egress.py) | 出站 domain allowlist mode、科学/包域名、后缀匹配、仿冒拒绝与 URL 解析。 |
| [`test_environments.py`](test_environments.py) | Prebuilt environment 发现、默认选择、hidden/R-only 过滤、可执行文件解析与 override。 |
| [`test_execution_coordinator.py`](test_execution_coordinator.py) | 核心逐会话 FIFO writer 所有权、会话并行、精确 cancel/interrupt、异常释放与 close。 |
| [`test_execution_view_service.py`](test_execution_view_service.py) | Execution/Notebook DTO 排序、重试、runtime 边界、protocol-only Cell、依赖、stale 状态与 lineage。 |
| [`test_frame_repository.py`](test_frame_repository.py) | Project/frame/message/step/cell-log 仓储、层级、sequence、browse、search、metadata、JSON fallback 与 commit。 |
| [`test_gateway.py`](test_gateway.py) | WebSocket hub 续传/coalescing/trim 顺序、静态启动行为及有界实时 Notebook/activity 状态。 |
| [`test_gateway_engine.py`](test_gateway_engine.py) | `AgentEngine` Web runner 集成：原生 Tool、Artifact、streaming、plan 限制、环境切换与取消。 |
| [`test_gateway_kernel_lifecycle.py`](test_gateway_kernel_lifecycle.py) | 受监督惰性 Python/R 槽位、stop/start 竞态、dead worker 替换、bootstrap lock、环境替换与 R soft failure。 |
| [`test_gateway_lazy_runtime.py`](test_gateway_lazy_runtime.py) | Tool-only 不启动内核、首 Cell/REPL 惰性 spawn、session runtime 复用、结构化完成与持久 attempt 顺序。 |
| [`test_gateway_session_domain_routes.py`](test_gateway_session_domain_routes.py) | 删除、checkpoint/fork、promotion、branch activation、revert/undo 及共享 session-domain 组合的 Gateway 路由。 |
| [`test_gateway_session_lifecycle.py`](test_gateway_session_lifecycle.py) | 持久 generation/attempt ID、TTL sweep、启动协调、project 删除准入、清理与竞态。 |
| [`test_global_research_views.py`](test_global_research_views.py) | 跨会话 project Timeline/lineage 读模型、scope、排序、有界输出与脱敏。 |
| [`test_governance.py`](test_governance.py) | Workflow 固定、权限、发布纪律与源码策略等仓库治理/安全自动化契约。 |
| [`test_harness_characterization.py`](test_harness_characterization.py) | 确定性 r5 生产探针、规范化 golden 比较、known-bug 标记与显式重新生成。 |
| [`test_harness_contract.py`](test_harness_contract.py) | Harness schema、scripted provider、fault、normalization、runner invariant、CLI selection/error 与必需 PR 场景。 |
| [`test_host_completion_service.py`](test_host_completion_service.py) | `host.submit_output` 校验、唯一 Cell 内完成语义、Artifact/公开字段及重复/failure 处理。 |
| [`test_host_contract.py`](test_host_contract.py) | Worker-to-Host API wire surface、注入 facade、soft error、audit context 与兼容契约。 |
| [`test_host_credentials_service.py`](test_host_credentials_service.py) | 会话本地 credential reference、provider scope、秘密不披露、lookup 与 audit。 |
| [`test_host_data_service.py`](test_host_data_service.py) | Store-backed `host.query`、数据注册、lineage/version lookup、只读 SQL 强制与公开结果 shape。 |
| [`test_host_delegation_service.py`](test_host_delegation_service.py) | Host 委派、steering、取消、树 policy/budget 转发、progress event 与 soft failure。 |
| [`test_host_endpoint_service.py`](test_host_endpoint_service.py) | Managed endpoint create/status/request/close 生命周期、校验、授权与 secret-safe 投影。 |
| [`test_host_llm_service.py`](test_host_llm_service.py) | 内核内 `host.llm` 模型配置、message/schema 校验、usage/audit、取消与 error 契约。 |
| [`test_host_mcp_service.py`](test_host_mcp_service.py) | MCP connector enablement、Tool discovery/call routing、配置、error、audit 与 disabled state。 |
| [`test_host_progress_service.py`](test_host_progress_service.py) | 会话 todo 与已批准 plan progress 更新、scope/status 校验、持久化与事件。 |
| [`test_host_remote_capability_service.py`](test_host_remote_capability_service.py) | 经验证 remote capability probe、结构化输入、SSH 边界拒绝、activity 投影与 SDK wire。 |
| [`test_host_remote_science_service.py`](test_host_remote_science_service.py) | Remote fold/mutation scoring 编排、provider 契约、no-fabrication 检查、Artifact 与 failure 投影。 |
| [`test_host_skill_service.py`](test_host_skill_service.py) | Host Skill search/read/execute/version、policy、sidecar、scoped capability state 与 audit。 |
| [`test_host_workspace_service.py`](test_host_workspace_service.py) | 受限 Host workspace read/write/list、version capture、路径安全与兼容 shape。 |
| [`test_jupyter_adapter.py`](test_jupyter_adapter.py) | 可选 Jupyter KernelSpec 发现和 wire adapter framing、执行、timeout、interrupt 与缺失处理。 |
| [`test_kernel.py`](test_kernel.py) | 持久 Python worker namespace、stdout/stderr、错误定位、Host mid-cell RPC、lock、background、provenance 与防死锁。 |
| [`test_kernel_generation_storage.py`](test_kernel_generation_storage.py) | 持久 kernel generation/attempt allocation、lease binding、终止 transition、activity、migration 与重启状态。 |
| [`test_kernel_generation_supervisor.py`](test_kernel_generation_supervisor.py) | Supervisor ABA counter 之上的持久 UUID generation identity、替换 fencing、lease 与 status 投影。 |
| [`test_kernel_recovery.py`](test_kernel_recovery.py) | Recovery candidate 隔离、精确 generation commit/rollback、sidecar/environment replay、failure cleanup 与 namespace 恢复。 |
| [`test_kernel_sandbox.py`](test_kernel_sandbox.py) | Python/R OS sandbox 命令构造、自检、auto/enforce/off、工作区约束、网络拒绝与无秘密 child env。 |
| [`test_kernel_supervisor.py`](test_kernel_supervisor.py) | 协议无关 worker 生命周期、lazy start、lease、restart、精确 interrupt、并发 caller 与 shutdown。 |
| [`test_lazy_kernel.py`](test_lazy_kernel.py) | 线程安全的一次性 CLI kernel ownership、惰性启动、context cleanup 与 Tool/Finalize 不 spawn。 |
| [`test_llm_anthropic_tool_calls.py`](test_llm_anthropic_tool_calls.py) | Anthropic Messages 原生 Tool 的无损 encode/decode、ID、排序、参数、历史、streaming 与 malformed input。 |
| [`test_llm_capabilities.py`](test_llm_capabilities.py) | Provider capability catalog、model feature resolution、token/usage 规范化与 canonical accounting。 |
| [`test_llm_gemini_tool_calls.py`](test_llm_gemini_tool_calls.py) | Gemini `generateContent` 原生 Tool 无损 encode/decode、thought/content part、历史、streaming 与 malformed call。 |
| [`test_llm_openai_tool_calls.py`](test_llm_openai_tool_calls.py) | OpenAI Chat 原生 Tool 无损 encode/decode、排序、参数保留、历史、streaming 与 error。 |
| [`test_llm_providers.py`](test_llm_providers.py) | 多 provider/multimodal 标准库 LLM transport、配置解析、payload、SSE、retry/error、usage 与图像。 |
| [`test_llm_responses_tool_calls.py`](test_llm_responses_tool_calls.py) | OpenAI Responses 原生 Tool wire assembly/parsing、item 顺序、ID、streaming delta、usage 与错误。 |
| [`test_local_model_discovery.py`](test_local_model_discovery.py) | 仅 loopback 模型端点发现、redirect 拒绝、有界 probe、去重与无 mutation 公开结果。 |
| [`test_marker_policy.py`](test_marker_policy.py) | External/network/live-LLM/GPU/SSH/Docker/browser/lab marker 的注册与显式 opt-in 规则。 |
| [`test_mcp_client.py`](test_mcp_client.py) | 离线 MCP JSON-RPC framing、stdio 生命周期、request correlation、timeout/error 与严格 child env allowlist。 |
| [`test_mcp_control_tools.py`](test_mcp_control_tools.py) | Class-owned MCP 原生 Tool schema、policy/resource、connector listing、Tool list/call 与 Host forwarding。 |
| [`test_memory_repository.py`](test_memory_repository.py) | 长期 memory CRUD、scope、去重、search/ranking、无效数据 fallback 与 Store facade。 |
| [`test_metadata_repositories.py`](test_metadata_repositories.py) | 小型 metadata/settings 类仓储、序列化、upsert/delete、排序、事务与 facade parity。 |
| [`test_methodology_skills.py`](test_methodology_skills.py) | 六个纯 methodology bundled Skill 的发现/内容契约。 |
| [`test_mineral_spectra_analysis.py`](test_mineral_spectra_analysis.py) | Mineral analysis Skill 的离线发现及确定性 spectrum parsing/matching/report helper。 |
| [`test_model_catalog.py`](test_model_catalog.py) | 可扩展 provider/model catalog、alias/default、profile composition、capability metadata 与 migration。 |
| [`test_native_tools.py`](test_native_tools.py) | Provider-neutral 原生 Tool declaration、独立 schema copy、命名限制、progressive group 及排除 shell/completion。 |
| [`test_notebook_export.py`](test_notebook_export.py) | 分离且确定性的 Python/R 只读 Notebook export、bundle manifest/checksum 与 language 校验。 |
| [`test_onboarding.py`](test_onboarding.py) | 确定性首次 provider setup、校验、default、provider switch、key clearing 与无秘密 response。 |
| [`test_orchestration_skills.py`](test_orchestration_skills.py) | Kernel Host facade、customization、self-awareness、endpoint、compute/env setup 与 audit 的端到端编排 Skill。 |
| [`test_permission_repository.py`](test_permission_repository.py) | 持久 permission rule 规范化、scope/specificity resolution、absolute deny、default、upgrade、原子 seed 与并发 upsert。 |
| [`test_permissions.py`](test_permissions.py) | Global/project/conversation rule、pattern、secret env deny、CRUD 与 fallback 的 Tool-call permission gate 优先级。 |
| [`test_plan.py`](test_plan.py) | Plan JSON/prose 提取、规范化、公开 step merge、持久化、review 与 auto-execute 基础。 |
| [`test_plan_repository.py`](test_plan_repository.py) | 经 `Store` 的 `PlanRepository` parity、malformed JSON fallback、status merge、update 与 delete。 |
| [`test_plan_service.py`](test_plan_service.py) | Plan draft/finalize/Artifact 生命周期、公开状态、discard、normal-turn 执行、guard、revision 与 event failure。 |
| [`test_protein_mutation_enhancement_skill.py`](test_protein_mutation_enhancement_skill.py) | 确定性 mutation enumeration、application、scoring/ranking、selection round、threshold 与 next-position 建议。 |
| [`test_provenance_paths.py`](test_provenance_paths.py) | 跨 cwd 变化和真实内核执行的 worker filesystem canonicalization 与 object provenance read/write identity。 |
| [`test_public_api_contract.py`](test_public_api_contract.py) | 支持的 public import、package version、constructor/signature、`run_task`、Host facade 与 server facade 兼容。 |
| [`test_r_kernel.py`](test_r_kernel.py) | R worker FD 协议、持久性、输出隔离、变量检查、child env、interrupt、error 与防死锁。 |
| [`test_recovery_recipe.py`](test_recovery_recipe.py) | 保守 dependency-closed Python/R recovery recipe、external/manual state、精确 source hash、环境与 sidecar bootstrap。 |
| [`test_release_gates.py`](test_release_gates.py) | 源码 secret scan 与 release archive 验证，覆盖必需资源、纯净依赖与安全 synthetic fixture。 |
| [`test_remote_capability_probe.py`](test_remote_capability_probe.py) | 结构化 remote probe 校验/quoting、SSH 前 shell 拒绝、activity 可见性与 SDK forwarding。 |
| [`test_remote_compute_control_tools.py`](test_remote_compute_control_tools.py) | Class-based remote compute submit/result/cancel/close schema、policy/resource、审批边界与 SDK forwarding。 |
| [`test_renderer_registry.py`](test_renderer_registry.py) | 确定性 Artifact renderer 选择、重复 ID 拒绝及保留 version/provenance 的公开 descriptor。 |
| [`test_retrosynthesis_planning.py`](test_retrosynthesis_planning.py) | Retrosynthesis Skill 发现、route 规范化/排序、solved metadata、示例、HTML rendering 与报告。 |
| [`test_revert_projection.py`](test_revert_projection.py) | 重启后的 append-only Revert/Undo 投影、branch message 隔离、state/policy 恢复与 legacy backfill。 |
| [`test_review.py`](test_review.py) | 受约束 Reviewer JSON 提取/规范化、有界 evidence packet、verdict 与 omission fail-closed。 |
| [`test_review_service.py`](test_review_service.py) | Review 编排、证据 excerpt、模型 late binding、取消、持久化、usage/event 与非致命 provider failure。 |
| [`test_science_connectors.py`](test_science_connectors.py) | Schema-normalized 科学数据库 connector、catalog coverage、request encoding、pagination、结果规范化与离线 HTTP fake。 |
| [`test_science_control_tools.py`](test_science_control_tools.py) | 原生及内核内 science search surface、flat schema、registry、catalog operation 与 Host wire encoding。 |
| [`test_sdk_compute.py`](test_sdk_compute.py) | Worker 侧 `host.compute` namespace、legacy export、provider normalization、路径规则、并发、instance、transfer、attach 与 cleanup。 |
| [`test_security.py`](test_security.py) | Defense-in-depth 代码分类、safety mode、heuristic、LLM classifier、biosecurity/injection 检查与 fast path。 |
| [`test_server_agent_run.py`](test_server_agent_run.py) | Prose/code draft streaming、fence hiding、event usage、取消、throttling 与 legacy Tool fence 的 Web engine adapter。 |
| [`test_server_completions.py`](test_server_completions.py) | 本地化公开进度/completion rendering、Artifact delta、科学字段、去重、有界 fallback、error 与脱敏。 |
| [`test_server_execution_coordinator.py`](test_server_execution_coordinator.py) | Web admission/event 投影、精确 ticket/lease 取消、REPL/Agent 串行、queued cancel 与 Gateway concurrency。 |
| [`test_session_branching.py`](test_session_branching.py) | Checkpoint/fork 不可变性、revert preview/mutation、外部编辑冲突、append-only undo 与 untracked file 保留。 |
| [`test_session_control_tools.py`](test_session_control_tools.py) | 原生 current-session/capability Tool、schema、resource、精确 scope、progressive activation、脱敏与 fail-closed mutation。 |
| [`test_session_deletion.py`](test_session_deletion.py) | 聚合 session/project 清理、共享 CAS locking/GC、symlink 安全、scoped Dynamic Tool 与 feedback delete。 |
| [`test_session_domain_service.py`](test_session_domain_service.py) | Snapshot、checkpoint、cursor fork、branch、Timeline、export、renderer 与 recovery 的 Store/session-domain 组合。 |
| [`test_session_package.py`](test_session_package.py) | 确定性 session export/import、checksum、graph 校验、secret/path/symlink/size 过滤、quarantine 与 round trip。 |
| [`test_session_recovery.py`](test_session_recovery.py) | TTL 解析、严格 idle sweeping blocker、持久 activity、recovery occupancy、启动协调与 sweeper lifecycle。 |
| [`test_session_snapshots.py`](test_session_snapshots.py) | Workspace CAS snapshot/restore、排除项、冲突/delete、append-only branch、精确 cursor binding 与 migration。 |
| [`test_session_title_service.py`](test_session_title_service.py) | 后台 title prompt/cleanup、late-bound model、placeholder、竞态保护、failure、持久化与广播。 |
| [`test_session_tool_catalog.py`](test_session_tool_catalog.py) | Class-based session Tool 端到端组合、受 gate 的 Dynamic Tool 生命周期、exact proxy 与 progressive catalog group。 |
| [`test_settings_repository.py`](test_settings_repository.py) | Settings、model profile、feedback 仓储、JSON fallback、并发 mutation、Store facade 与 permission seed。 |
| [`test_skill_customization_service.py`](test_skill_customization_service.py) | Web Customize Skill create/read/update/delete/import、校验、builtin collision、root、优先级、enablement 与 route。 |
| [`test_skill_product_surface.py`](test_skill_product_surface.py) | 版本化 personal/project Skill control Tool、SDK call、scoped rollback/history、dispatcher audit 与 HTTP route。 |
| [`test_skill_sidecar_recovery.py`](test_skill_sidecar_recovery.py) | 真实 worker Skill sidecar 捕获进 generation manifest/checkpoint 及精确、防篡改恢复 replay。 |
| [`test_skill_versions.py`](test_skill_versions.py) | Content-addressed Skill install、upgrade、publish、history、delete、activation/rollback、scope isolation 与 Store reopen。 |
| [`test_skills.py`](test_skills.py) | Skill loader discovery/frontmatter/progressive context、bootstrap path、sidecar compilation/function 与 error。 |
| [`test_store.py`](test_store.py) | Frame、execution dependency、Artifact version、coalescing 与 lineage transaction 的 Store schema/migration/serializer。 |
| [`test_structured_finalize.py`](test_structured_finalize.py) | Engine-owned `finalize_response` schema/校验、sole-call routing、canonical result、mixed-batch 不完成与 CLI 执行。 |
| [`test_tool_schema.py`](test_tool_schema.py) | 支持 JSON Schema 子集的纯标准库校验/规范化，包括 nested path、constraint 与 unknown field。 |
| [`test_tools.py`](test_tools.py) | 具名 class-based control Tool、registry/handler coverage、隔离 schema、禁止 eager singleton 与自有安全 policy。 |
| [`test_variable_inspector_service.py`](test_variable_inspector_service.py) | 窄 idle-kernel variable inspection、language/lease state 检查、净化有界结果与协议 failure。 |
| [`test_webtools.py`](test_webtools.py) | HTML-to-Markdown、arXiv metadata/abstract 保留、block/inline 处理及使用离线 fixture 的 search backend。 |
| [`test_webui_static_contract.py`](test_webui_static_contract.py) | 零依赖 UI asset 存在性、DOM ID 唯一/稳定、control、event wiring、icon definition 与静态安全 invariant。 |
| [`test_workbench_state_service.py`](test_workbench_state_service.py) | 安全 Context/Security 投影、compaction metadata、禁止消息泄露及如实聚合 Python/R sandbox 状态。 |
| [`test_worker_runtime_alias.py`](test_worker_runtime_alias.py) | `openai4s_worker_runtime` 纯 re-export 兼容、symbol identity、public `__all__` 及无 shadow submodule/entrypoint。 |

## 直属子目录

| 目录 | 职责 |
| --- | --- |
| `fixtures/` | 字节敏感的捕获 HTML、fake interpreter helper 与 renderer sample；此子树刻意不生成目录 README，也不参与自动格式化。 |

## 如何选择位置

聚焦的回归断言放在这里；可复用 scripted scenario、fake provider、规范化 trajectory、计分 eval 与经审阅 golden 放入 [`../harness/`](../harness/) 层。两个层的默认检查均保持离线，只有明确标记并单独调用的入口才可作为 opt-in smoke。
