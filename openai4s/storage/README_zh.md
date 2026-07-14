# 存储 Repository

[English](README.md)

本目录包含 [`Store`](../store.py) 背后的领域 repository。`Store` 管理唯一 SQLite connection、schema/migration、query guard、re-entrant lock、缓存 facade generation 和兼容 API；各 repository 共享该 connection 与 lock，而不会自行打开数据库。

## 在架构中的位置

外层循环把 canonical action ledger 和 execution attempt 写入这里。Web/CLI projection 也通过同一 `Store` facade 持久化 frame、message、Cell、Artifact、permission、plan、delegation、kernel-generation identity、checkpoint 和 recovery event。Host service 则使用窄化的 repository projection 来访问数据、策略、session control、Skill、connector 和 progress。

SQLite transaction 可以让明确定义的一组 row 原子化，但不会在 SQLite、workspace 文件、content-addressed blob、运行中的 Python/R namespace、远程计算和 WebSocket event 之间形成统一事务。因此 repository 会明确区分 append-only history、可变 materialized projection 和 best-effort 外部文件绑定。

## 文件

| 文件 | 职责 |
|---|---|
| [`__init__.py`](__init__.py) | 重新导出 `Store` 组合 facade 使用的 repository class。 |
| [`actions.py`](actions.py) | 保存用于 provider history 和 tool/Cell observation 的不可变有序 action group/event；在工作开始前分配 execution attempt，并保证每个 lifecycle milestone 只填写一次。 |
| [`activation.py`](activation.py) | 原子激活一个 checkpoint branch 的 conversation-scoped SQLite projection：所选 branch、session capability、conversation permission rule、可见 Artifact head、checkpoint state 和所选 Python 环境。 |
| [`agents.py`](agents.py) | 持久化具名 specialist Agent profile 及其 JSON skill/connector override。 |
| [`annotations.py`](annotations.py) | 保存单个 session/Artifact context 中规范化的 image-review pin、body、ordinal、状态转换和删除。 |
| [`artifacts.py`](artifacts.py) | 管理 Artifact identity/version、文件路径解析、environment snapshot、restore record、priority/latest head、producing Cell 及版本级 lineage edge。 |
| [`branch_projection.py`](branch_projection.py) | 根据不可变 checkpoint cursor 与 head 之后的本地 row 重建 branch-aware 逻辑历史，而不删除物理 append-only history。 |
| [`capabilities.py`](capabilities.py) | 按 session → project → global 优先级持久化 capability enablement、append-only state event 和 bootstrap manifest。 |
| [`checkpoint_state.py`](checkpoint_state.py) | 为 plan、reviewer activity/settings/annotation 和 project memory 捕获带 integrity digest 的 checkpoint state；校验/隔离导入状态、重映射 identity，并仅恢复已验证作用域。 |
| [`connectors.py`](connectors.py) | 持久化并解码 MCP connector command、argument、environment、enabled state 和 display metadata。 |
| [`delegation.py`](delegation.py) | 保存有界 sub-Agent tree、session budget/lease、child lifecycle/result 和 steering message，以支持 restart-safe projection。 |
| [`deletion.py`](deletion.py) | 在单一事务中删除一个 session 或 project 明确拥有的全部 SQLite aggregate，并返回文件系统清理候选而不自行 unlink。 |
| [`frames.py`](frames.py) | 持久化 project、frame hierarchy/scope、可见 message、activity step、token counter、可搜索 frame detail 和带 replay/visibility 元数据的 Cell execution log。 |
| [`kernels.py`](kernels.py) | 保存 Python/R kernel generation 的持久 UUID identity、manifest、owner/process 元数据、ordinal、activity 和 terminal state；绝不声称序列化 namespace。 |
| [`memories.py`](memories.py) | 提供 project-scoped 长期 memory CRUD 和 category/block projection。 |
| [`metadata.py`](metadata.py) | 集中小型 repository：project note、folder、managed endpoint 元数据、compaction archive 和 Host-call audit record，并对可推导/含 secret argument 的 RPC 做特殊处理。 |
| [`permissions.py`](permissions.py) | 解析 scoped allow/ask/deny rule，写入本地默认值，持久化 approval request/event，使 decision 过期，并原子消费窄绑定的 restart-continuation grant。 |
| [`plans.py`](plans.py) | 持久化 frame 的结构化 plan 及逐 step status/note。 |
| [`recovery.py`](recovery.py) | 追加有序 recovery-attempt 与 repair journal entry，使失败或 partial recovery 在重启后仍可检查。 |
| [`settings.py`](settings.py) | 保存 key/value setting 及 model profile、message feedback 的结构化 projection。 |
| [`skills.py`](skills.py) | 保存不可变 content-addressed Skill file/manifest、active installation pointer、乐观并发 activation/deactivation 和 append-only version history。 |
| [`snapshots.py`](snapshots.py) | 实现 stdlib workspace content-addressed store 与 session branch/checkpoint envelope、restore preview/conflict、fork、operation journal 和 retained-tree discovery，且不修改用户 Git repository。 |

## 子目录

本包没有受跟踪的子目录。

## 持久化模型

- **Canonical history：** action group/event、capability event、recovery journal entry、Skill version 和 checkpoint operation record 以追加为主。Execution attempt 与 kernel generation 具有受控生命周期字段，可以推进，但不能重写已完成历史。
- **UI/session projection：** frame/message/step、active branch、Artifact head、setting、plan、annotation 和 profile 都是可变视图，不能被误认为 terminal signal 或完整 audit record。
- **Workspace state：** `WorkspaceCAS` 在 OpenAI4S 数据目录下保存不可变 blob/tree manifest，排除已识别 secret path、限制文件大小，并且从不使用或修改研究者的 Git index/branch。
- **Kernel state：** checkpoint envelope 保存 environment/generation reference 和 replay recipe，而不是 pickle 后的 Python/R memory。[`kernel/recovery.py`](../kernel/recovery.py) 决定哪些内容可安全重建。

## 一致性与安全边界

- Artifact version 只有在 snapshot binding 成功时才完全不可变。Snapshot capture 失败时，row 可能保留 live/path-backed reference；调用方必须检查元数据，不能假定每个 version 都有冻结字节。
- Workspace restore 能感知 conflict，并对每个文件使用 atomic replacement，但不是全文件系统事务。Restore 中途失败可能留下部分恢复的 tree，需要 operation/recovery record 来诊断。
- Checkpoint activation 只保证所列 conversation-scoped **数据库 projection** 原子。Project/global policy 仍保持实时，文件系统和 kernel recovery 分别协调。
- Agent 只读 SQL 由 `Store` query guard 强制，而不是通过直接数据库权限实现。Repository method 本身属于受信任进程内代码。
- Permission decision 持久化的是权限元数据，不是可恢复的 Python stack 或已存 execution argument。重启后必须由匹配的新 action 消费窄 continuation grant。
- Connector 配置及其他 JSON 元数据可能包含敏感运维输入；audit redaction rule 只覆盖特定 Host call，而不是任意 stored field。部署备份必须相应保护。
- Deletion 先提交 SQLite 所有权变更，只返回候选路径。Server-side cleanup 必须在 unlink 前重新校验路径；数据库成功并不证明字节清理成功。

## 相关文档

- [系统架构](../../docs/architecture.md)
- [Web 运行时](../../docs/webapp.md)
- [安全模型](../../docs/security.md)
- [Store facade](../store.py)
