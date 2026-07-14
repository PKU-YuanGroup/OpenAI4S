# Host 服务

[English](README.md)

本目录包含聚焦的 Host 侧 capability service。它们由 [`HostDispatcher`](../host_dispatch.py) 组合；dispatcher 仍是共享 RPC envelope，负责参数校验、权限/审批、审计记录、不可信输出筛查、activity event 和软错误路由。这些 service 实现领域行为，并不是各自独立暴露的网络 endpoint。

## 在架构中的位置

Python worker 侧的 [`host` facade](../sdk/host.py) 发出同步 `host_call`。[`kernel/manager.py`](../kernel/manager.py) 将其交给 `HostDispatcher`；dispatcher 应用策略后调用下列某个 service。返回值通过匹配的 `host_response` 发回，使阻塞的 Cell 恢复执行。能力重叠时，native control tool 也使用 dispatcher，从而让控制平面与内核内调用遵循一致策略。

Service 可以返回单键结构 `{"error": message}` 表示软失败。Python worker 会把它转换为 `RuntimeError`；这不是成功的科学结果，也不代表任务完成。大多数 service 有意把权限、replay、audit 和 injection 策略留在 dispatcher，避免重复实现。

## 文件

| 文件 | 职责 |
|---|---|
| [`__init__.py`](__init__.py) | 重新导出组合代码使用的主要 service class。 |
| [`bash.py`](bash.py) | 授权内核本地 `host.bash`：分类/脱敏 proposal，签发与 generation、challenge、command、cwd 绑定的短时 capability，每个 token 只消费一次，并记录有界的 worker 上报结果；不导入 `subprocess`，也不执行命令。 |
| [`completion.py`](completion.py) | 校验唯一的 Cell 内成功契约（`output`、1–4 条已完成 action bullet 以及可选 schema），并为当前 dispatch context 保存一次 submission。 |
| [`credentials.py`](credentials.py) | 在内存中保存 session-local credential 及短时、action-bound、single-use lease；轮换 credential 会使旧 lease 失效，本模块不持久化原始值。 |
| [`data.py`](data.py) | 提供 Store-backed 只读 SQL/schema、限定作用域的 Artifact 元数据/版本/路径/保存/恢复、图片投影、frame 浏览，以及 provenance/lineage 读取与上报。 |
| [`delegation.py`](delegation.py) | 应用已存储 agent profile override、注入内建 specialist context，并把 delegate/children/collect/stop/message/stat 操作转发给 session delegation runtime。 |
| [`delegation_policy.py`](delegation_policy.py) | 解析并冻结子 Agent method/capability 策略，包括 alias、逐 method decision 和 tool visibility；显式 restricted 策略以 allowlist 控制操作，独立 unrestricted 模式也会明确出现在 projection 中。 |
| [`endpoints.py`](endpoints.py) | 分配 loopback 端口、保存 endpoint 元数据及 start/stop script，并探测 readiness；注册不会执行这些 lifecycle script，也不会新增独立 egress 策略。 |
| [`files.py`](files.py) | 解析后绑定的 session workspace、把相对路径限制在其中、拒绝 secret basename，并为具体 I/O 行为所在的 class-based file tool 提供兼容分派。 |
| [`llm.py`](llm.py) | 从运行中的 Cell 同步调用已配置模型，包括有界并发 batch fan-out，并投影当前/可用模型元数据。 |
| [`mcp.py`](mcp.py) | 解析持久化 MCP connector，并把 list/tools/call/resource/prompt 操作转发给 MCP manager；权限和不可信输出筛查仍由 dispatcher 负责。 |
| [`progress.py`](progress.py) | 维护 dispatcher 内瞬态 todo，并更新/读取持久化的已批准 plan step 与 reviewer progress。 |
| [`remote_capabilities.py`](remote_capabilities.py) | 规范化窄范围结构化 SSH 验证 probe、检查远程 capability 可用性，并在 remote-compute registry 中注册已验证的 service 元数据。 |
| [`remote_science.py`](remote_science.py) | 调用已注册的 SSH folding 和 mutation-scoring wrapper，解析显式结果 marker，并记录远程 provenance；服务缺失或失败时返回错误，不伪造科学结果。 |
| [`science.py`](science.py) | 通过共享 fetch 路径构造 allowlisted 公共科学数据库请求，并规范化 UniProt、PDB、Ensembl、ChEMBL、PubChem、arXiv 和 OpenAlex 响应。 |
| [`session.py`](session.py) | 把控制操作限制在当前 root session，读取持久化 branch/checkpoint/permission 状态，并把涉及文件系统的 checkpoint/fork/revert/recovery 操作委托给已挂接的 Web session-domain service。 |
| [`skills.py`](skills.py) | 搜索、读取、编辑、发布、版本化、回滚和删除限定作用域的 Code-as-Action Skill，同时保持 bundled skill 优先级与文件系统约束。 |

## 子目录

本包没有受跟踪的子目录。

## 控制、安全与失败边界

- 授权与审计边界是 [`HostDispatcher`](../host_dispatch.py)，而非单个 service。直接调用 service 属于受信任的进程内组合，会绕过该 envelope。
- Shell 通过 [`sdk/bash.py`](../sdk/bash.py) 留在科学 worker 内执行。本包只签发和消费一次性 capability；上报的 stdout/stderr 在持久化前会被限制长度并脱敏。
- [`credentials.py`](credentials.py) 中的 credential value 仅驻留内存，但任何收到 redeemed value 的消费者都具有相应能力。基于名称的脱敏不能证明任意输出中不存在 secret。
- [`files.py`](files.py) 约束路径，实际 Tool class 负责读写行为。Artifact snapshot 与 provenance 注册是独立、best-effort 的持久化步骤，并非全局文件系统/SQLite 事务。
- Endpoint start/stop script 仅为元数据。Readiness probe 成功并不能证明 tenant isolation、authentication 或公网暴露安全。
- 通用 `host.compute`、远程 capability provisioning、folding 和 mutation scoring 都是持续演进的集成面。存在 route 或 service class 不代表 provider credential、远程软件、GPU 容量或端到端 UI recovery 已配置完成。
- 公共数据库、MCP、LLM 和远程 SSH 调用可以独立失败或返回恶意内容；dispatcher screening 是额外防线，不是科学正确性验证。

## 相关文档

- [系统架构](../../docs/architecture.md)
- [安全模型](../../docs/security.md)
- [远程计算](../../docs/compute.md)
- [Skills](../../docs/skills.md)
