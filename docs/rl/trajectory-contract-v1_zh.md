# OpenAI4S science trajectory v1 契约

状态：**本文列出的记录已经实现；外部 Verifier 与 Trainer 接口仍是后续工作**。

本文规范 `Trajectory.schema_version` 为
`openai4s.science-trajectory.v1` 时 `TrajectoryRecorder` 产生的 JSON。它记录可观察的
rollout 事实，不从 action 推断 reward，不把执行事件变成 policy decision，也不取代
Action Ledger。

## 包络

规范 JSON 对象包含 `schema_version`、`episode`、`observations`、`decisions`、
`segments`、`execution_events`、`rewards` 和 `costs`。规范 JSON 使用 UTF-8、
`ensure_ascii=false`、按键排序且不含无意义空白；`Trajectory.sha256()` 对这些字节做
哈希。JSONL 第一行是 `type: "trajectory"` 的包络，后面按 observations、decisions、
segments、execution_events、rewards、costs 顺序写出，每行使用单数 `type`。

## 身份与追加规则

每条记录都带 `episode_id`，Recorder 拒绝属于其他 Episode 的记录。公共 ID 是幂等键：
相同 JSON 的重试会被接受，使用同一 ID 写入不同规范 JSON 会产生冲突；Store 持久写入
遵守相同规则。Execution Event 还带每个 Episode 内单调递增的 `sequence`，由 SQLite
仓储在写事务中分配。Episode 只能从 `running` 进入一个终态。

运行时不会因 Verifier 改变而删除或重写执行事件。Reward Annotation 与 Cost 是独立的
追加记录；消费者必须保留原始执行事实，再计算新的 reward 或投影。

## 记录

`Episode` 表示一个 Trainer 可见的 rollout，包含任务、会话、创建时间、policy 身份、
状态、元数据及可选的 `parent_episode_id`。RL 上下文中的委派 worker 使用自己的 Episode
和 ID 命名空间，并显式记录父 Episode。

`Observation` 是一次 decision 前实际提供的、脱离运行状态的消息上下文，包含
`observation_id`、`decision_id`、消息、时间、来源及截断标记。

`PolicyDecision` 是一次 policy sampling 边界，包含 observation 关联、prompt hash、
规范化 action、policy 身份和可选的 `parent_decision_id`、`frame_id`、
`action_group_id`。外部 sampler 提供时，`token_ids`、`token_logprobs`、`loss_mask` 和
`action_logprob` 会被保留并校验长度与有限值；运行时不会重新计算这些 telemetry。

可选的 `PolicyAdapter` 是外部 sampler 的 provider-neutral 边界。它把配置的 behavior
`policy_name`、`policy_version` 和 `sampling_config` 写入 `ModelReply.extra`，并覆盖
provider 自带的身份字段。没有 logprob 证据时保持缺失，并标记
`behavior_logprobs_available: false`。

`RolloutEnvironment.reset()` 通过调用方提供的 Engine factory 创建新的 Episode、Reset
元数据和 workspace。存在 active Episode 时会拒绝第二次 reset；只有 rollout 发出终态事件
后 `release()` 才会删除 workspace。绝对 workspace 路径属于运行时状态，不进入轨迹元数据。

`TrajectoryReplayer` 是持久轨迹上的只读投影。Event replay 校验每个 Episode 内连续的事件
序号，并支持有界的 `after_sequence` cursor；Decision replay 将每个精确 Observation 与
采样 Decision 并列输出；Artifact replay 发现不可变版本引用，并通过宿主提供的 checker
报告 checksum 是否仍可验证。Execution replay 只有调用方显式提供 runner 才会启用，因此不会
静默重跑 scientific cell 或重复提交远程 job。

`Segment` 是显式 scientific phase，包含名称、目标、父 Segment、状态和元数据。
Host API 接受显式的 `parent_segment_id`，不会从相邻调用顺序推断层级。
`host.segment()` 只产生 Segment 生命周期、observation 和 output 事实，不计算 reward。

`ExecutionEvent` 是有界执行事实，例如 action dispatch、Cell 结果、Segment 生命周期或
Host RPC 生命周期。Host RPC 只记录参数/结果哈希与错误类别，不复制私有参数或结果。

导出时 durable projector 可以只读追加 `artifact.version`、`compute.job` 和
`compute.job_event` 引用。Artifact 引用携带精确 `version_id`、checksum、producing cell
和 source event；Compute 引用携带 durable job ID、provider、status、receipt、输入
version、artifact manifest 和源 job events。它只跟随轨迹中已经存在的 ID，不解析可变
Artifact head，也不伪造成本。

`RewardAnnotation` 是独立产生的分数，包含 verifier 与版本、数值、组件、元数据、
`evidence_refs`、可选的 `[0, 1]` `confidence` 和 `source`。同一 Episode 可以有多个
verifier 版本。`apply_verifier()` 校验 verifier 身份与 Episode 范围，然后追加 Annotation，
不改变执行事件。

`CostRecord` 保存测得的 token、wall/GPU 时间及可选价格。未知价格保持未知，不把缺失计数
伪造为零。

Agent 和 Rollout adapter 可以启用 wall-time meter。它用 monotonic clock 测量
TurnStarted 到 OutcomeProduced 的间隔，并追加 `source: "turn_wall"` 记录。GPU 时间与
provider 价格在有可信测量前保持为空。

## Action Ledger 关联

Action Ledger 仍是执行事实源。若本地 Agent runtime 有 `RuntimeActionLedger`，它在处理
`ActionRouted` 时创建 action group；RL sink 随后把同一个 `group_id` 写入
`PolicyDecision.action_group_id`。RL 层不会再创建一个 group，也不会用 turn 编造 ID。
没有 ledger 的 provider-neutral `AgentEngine` 运行可以让该字段为空。

决策上下文还会向 Host dispatch 传递 `rl_episode_id` 与 `rl_decision_id`。Host call 和
Segment 是该 decision 下的执行事件，不是额外的 policy decision。

## 委派与投影

在 RL 上下文中创建的 delegated worker 使用新的 Episode 和轨迹命名空间，Episode 保存
`parent_episode_id`，首个 decision 保存父 decision。`episode-id:decision-0` 这类 ID 属于
本地命名空间，不能靠字符串启发式和父轨迹合并。ATIF-v1.8 是交换投影，完整 Science
Trajectory 仍在 `extra.openai4s` 中，是训练输入的权威来源。

`RLRepository.load_trajectory(episode_id)` 按稳定身份顺序从 SQLite 投影重建记录。训练
数据接受 Snapshot 前，重建后的规范哈希必须等于 `DatasetSnapshot.trajectory_sha256`。

## 当前生命周期边界

配置了持久仓储时，`RolloutSession.reset()` 受理幂等 Run Receipt；`observe()` 准备下一次
上下文；`step()` 接收外部 `ModelReply` 或 mapping；`finish()`/`run()` 导出轨迹。既有
`AgentEngine` 仍负责 action routing、权限、kernel 执行、completion、取消和 ledger 回调。
此 runtime 不包含 PPO、GRPO、advantage、模型服务或 Verifier 实现。

`RolloutWorker` 是围绕这个生命周期的同步单 Episode 适配器。`run()` 在一次有界调用中
完成 reset、外部 sampler、release；需要显式所有权时也可直接使用 `reset()` 和 `release()`。
Recovery 只检查 durable receipt：`running` receipt 投影为 `recovery_required`，不会自动重试。
Worker 导出携带轨迹 schema version，以及精确 JSON/JSONL payload 字节的 SHA-256。

`DatasetSnapshot` 是当前固定数据集边界：只接受终态 Episode、没有 open Segment 且所有
Execution Event 都有 sequence；它保存规范轨迹哈希、最大事件序号和记录计数。
`matches()` 会拒绝已经改变的候选轨迹。
配置 Store 仓储时，这些元数据由 migration 38 追加到 `rl_dataset_snapshots`，可按
snapshot ID 读取；显式 Segment parent 由 migration 39 增加，持久化 RL 预算账本和
绑定 receipt 的 allocation 由 migration 40 增加。如果之后追加新的
Verifier Annotation 使轨迹改变，旧 Snapshot 会拒绝匹配，Trainer 不会静默读取已改变的
reward view。
