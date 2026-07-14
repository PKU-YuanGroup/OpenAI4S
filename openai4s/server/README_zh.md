# Server（服务端）

[English](README.md)

本包是 OpenAI4S 的 Web 组合层：它把供应商无关的 Agent 引擎、持久 Python/R 内核、Host 能力边界与 SQLite 仓储组合成一个纯标准库 HTTP/WebSocket 应用。领域逻辑被刻意放入聚焦的 service；[`gateway.py`](gateway.py) 则保留为兼容与传输门面。

## 在整体架构中的位置

```text
浏览器
  |  REST 请求 + WebSocket 事件
  v
gateway.py
  |-- 会话领域服务与只读投影
  |-- AgentEngine 适配器（agent_run.py）
  |-- FIFO 执行所有权（execution_coordinator.py）
  `-- 会话拥有、惰性启动且彼此独立的 Python/R 内核槽位
         |
         `-- HostDispatcher -> 权限、工具、Artifact、数据与委派
```

- **Gateway 组合。** [`gateway.py`](gateway.py) 创建并组合受支持的标准库 `ThreadingHTTPServer`、路由、REST handler、WebSocket 帧与续传、会话 runner、服务、存储和静态资源。[`daemon.py`](daemon.py) 是独立的 legacy 最小 `/`、`/health`、`/run` 兼容服务，不属于完整 Gateway 组合。新增算法通常应放在聚焦模块，而不是继续扩大门面。
- **REST 与 WebSocket。** REST 端点处理有界请求/响应操作并暴露会话领域读模型；WebSocket 流式发送 Agent 文本、Action/Cell 生命周期、审批、Notebook 更新和终止事件，并支持断线重连与缓冲续传。
- **会话服务与投影。** Mutation service 管理计划、审阅、Artifact、分支、恢复、会话包、Skill 与删除。Projection service 将规范 Ledger、执行、lineage、Context 和 Security 状态转成经脱敏、可安全交给浏览器的 DTO；投影本身不是底层终止信号或事务信号。
- **内核所有权。** 每个 Web 会话通过 `SessionRunner` 拥有相互独立且惰性启动的 Python/R 槽位。[`execution_coordinator.py`](execution_coordinator.py) 用 FIFO ticket 串行化 Agent、REPL、恢复和生命周期 writer，并且只允许精确 owner/lease 中断。Tool-only 路由不会启动前台 session slot；个别工具可以管理专用 worker。
- **持久化边界。** 持久事实经 `Store` 仓储写入；WebSocket 状态和活动内核 namespace 属于进程内状态。SQLite、工作区文件、内核进程与 socket 投递之间不存在一个覆盖全部边界的事务。

## 完成、Notebook 与恢复边界

- Cell 结果是外层循环 observation，本身不是任务完成。成功必须来自单独且有效的 Engine 自有 `finalize_response`，或 Python Cell 内的 `host.submit_output(...)`；R Cell 不能完成任务。
- Protocol-only `host.submit_output` Cell 仍保留在原始执行/审计历史中，但会从实时和重新打开的 Notebook 投影中过滤。当前 `.ipynb` exporter 读取未套用该过滤的不可变执行历史，因此它是 raw/audit export，可能包含该 system Cell。
- 恢复执行已接入 REST/UI、FIFO 所有权和 Python/R 候选流水线，但仍为 **Partial**：不安全或非确定性 Cell 被分类为 `never`，历史 namespace 不会被任意序列化，无法成为 active 的语言候选可以显式 Partial 结果终止整体恢复。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | 稳定包门面，导出 `build_server` 与 `serve`。 |
| [`action_timeline.py`](action_timeline.py) | 从规范 Action Ledger 的 group、event、attempt、权限、usage 与 Artifact 引用生成有界且脱敏的 Timeline 投影。 |
| [`agent_run.py`](agent_run.py) | 将 `AgentEngine` 适配为 Web 契约：流式输出安全文本/代码草稿，发送 Web 事件，处理取消，并通过注入端口执行原生 Action 或 Cell。 |
| [`artifacts.py`](artifacts.py) | 捕获、版本化、编辑、重命名、上传、恢复与提升工作区 Artifact，同时协调 snapshot、provenance 与广播。 |
| [`cell_run.py`](cell_run.py) | 编排单个 Python/R Cell：从执行准入，经内核运行、实时输出、Artifact 捕获、执行日志，直到终止投影。 |
| [`completions.py`](completions.py) | 生成本地化的公开进度/结果叙述，并根据结构化 completion 与真实 Artifact 增量渲染结果，不暴露隐藏推理。 |
| [`daemon.py`](daemon.py) | Legacy 最小线程 HTTP 兼容服务，暴露 `/`、`/health` 和 `/run`；它不是完整 Gateway，也不拥有 Gateway 的 WebSocket、Origin/认证或单例生命周期。 |
| [`execution_coordinator.py`](execution_coordinator.py) | 会话级 FIFO 执行所有权的 Web 适配层，管理精确 ticket/lease 取消、准入状态与清理。 |
| [`execution_views.py`](execution_views.py) | 将不可变 Cell 历史、runtime generation、依赖、stale 状态、重试与 lineage 投影为 Notebook/执行 DTO。 |
| [`gateway.py`](gateway.py) | HTTP/WebSocket 主组合门面：协议帧、hub/续传缓冲、`SessionState`/`SessionRunner`、REST 路由、静态资源、安全检查与服务装配。 |
| [`global_views.py`](global_views.py) | 跨会话生成项目级研究 Timeline 与 Artifact lineage 视图。 |
| [`model_discovery.py`](model_discovery.py) | 仅在 loopback 上对 OpenAI-compatible 模型端点做有界、拒绝重定向的发现。 |
| [`model_profiles.py`](model_profiles.py) | 校验、迁移、持久化、选择与删除模型供应商 profile，并从公开结果中清理凭据。 |
| [`notebook_export.py`](notebook_export.py) | 将原始不可变 Python 或 R 执行历史确定性导出为只读 `.ipynb` 及带 checksum 描述的 bundle；与 Notebook 投影不同，它当前可能包含 protocol-only completion Cell。 |
| [`plans.py`](plans.py) | 管理结构化计划解析、规范化、草稿/定稿生命周期、审阅转换、执行、公开投影与计划 Artifact。 |
| [`recovery_control.py`](recovery_control.py) | 投影恢复 journal/generation 状态，并组合经校验、脱敏的恢复 Action 计划。 |
| [`recovery_execution.py`](recovery_execution.py) | 在精确执行所有权和经验证的 Python/R 恢复流水线中执行一次恢复 mutation。 |
| [`recovery_recipe.py`](recovery_recipe.py) | 根据不可变 Cell 事实、依赖闭包、环境需求、sidecar 与确定性检查，保守地编译恢复 recipe。 |
| [`recovery_runtime.py`](recovery_runtime.py) | 为会话恢复提供具体 Python/R 候选内核、环境探针、bootstrap、验证、提交与回滚。 |
| [`renderers.py`](renderers.py) | 定义从 Artifact kind/content-type/扩展名到安全科学 renderer 的 registry 与公开描述。 |
| [`reviews.py`](reviews.py) | 构建有界证据包，编排可取消的科学审阅、持久化、usage 与公开审阅事件。 |
| [`session_branching.py`](session_branching.py) | 协调 checkpoint、隔离 fork、revert preview、append-only revert/undo 历史、工作区冲突检查与分支激活。 |
| [`session_deletion.py`](session_deletion.py) | 清理持久会话聚合、工作区、snapshot/CAS 引用和进程内状态，同时不越过所有权 scope。 |
| [`session_domain.py`](session_domain.py) | checkpoint、cursor checkpoint、branch、timeline、导出、renderer、会话包操作与恢复的高层会话领域组合。 |
| [`session_package.py`](session_package.py) | 通过 quarantine 创建/导入确定性、校验 checksum、过滤秘密并防路径穿越的会话 ZIP 包。 |
| [`session_recovery.py`](session_recovery.py) | 启动时协调陈旧 runtime 状态，并在 activity/recovery blocker 约束下确定性回收空闲内核。 |
| [`session_runtime.py`](session_runtime.py) | 保存委派树、动态能力等会话级控制平面对象，使其独立于语言 worker。 |
| [`skill_sidecars.py`](skill_sidecars.py) | 将成功加载的 Skill sidecar 记录到精确内核 generation，使恢复能重放实际观察到的不可变 manifest。 |
| [`skills.py`](skills.py) | 实现 Web Customize 中经校验的用户 Skill 文档生命周期与能力启用。 |
| [`titles.py`](titles.py) | 使用延迟绑定模型配置生成安全后台会话标题，并以防竞态方式持久化和广播。 |
| [`variable_inspector.py`](variable_inspector.py) | 通过窄协议读取活动且空闲的 Python/R namespace，返回有界、净化后的变量预览。 |
| [`workbench_state.py`](workbench_state.py) | 根据持久与实时状态投影 Context/Security 面板，不泄露消息内容，也不夸大 sandbox 保证。 |

## 直属子目录

| 目录 | 职责 |
| --- | --- |
| [`webui/`](webui/) | Gateway 直接提供的零依赖浏览器客户端与科学 Artifact renderer。 |

## 修改注意事项

- 将 [`gateway.py`](gateway.py) 保持为谨慎修改的组合/兼容门面；新领域行为放到对应 service。
- 修改内核生命周期、WebSocket 流、执行所有权或 Artifact 捕获时，除聚焦测试外还需进行真实浏览器端到端验证。
- 浏览器 DTO 必须保持有界且脱敏；原始供应商 payload、工具参数、凭据和不受限文件系统路径不能进入投影。

另见仓库[架构指南](../../docs/architecture.md)、[Web 应用指南](../../docs/webapp.md)与 [`webui/` README](webui/README_zh.md)。
