# RL Runtime 合约

[English](README.md)

这个包是 OpenAI4S 执行运行时与外部 Agentic RL Trainer 之间的无依赖数据边界。
`models.py` 定义版本化的 Science Trajectory 记录；`recorder.py` 收集一个 Episode，
支持追加式幂等写入以及确定性的 JSON/JSONL 导出。Reward Annotation 和 Cost 是独立
记录，因此 Verifier 或训练数据快照不会改写执行事实。训练算法和特定 Provider 的
Policy Client 保持在 Harness 外部。

`segments.py` 统一校验阶段生命周期。启用 RL 记录的 Engine 为每个路由后的 Cell、
原生工具 batch 或 finalize 创建机械执行段；纯文本不创建阶段。
`metadata["openai4s.segment"]` 标注 `source: "inferred"` 和 `rule: "action-v1"`，
不冒充科学阶段声明。生命周期事件与状态原子提交，完全相同的重试返回原事件；
未关闭阶段在 Episode 终态事件前被标为 interrupted/failed。Host 调用默认继承 action
执行段，lexical 阶段可以提供更具体的归属；不计算或重复 Reward。

Host Segment 接口只记录 observation 和产物证据。Reward Annotation 必须由可信的
Verifier 或 Trainer 写入，Agent 没有 Host 自评分接口。

`verifier.py` 定义与 Provider 无关的 `RewardVerifier` 协议和 `apply_verifier()`。
它在追加带版本 Annotation 前校验 Verifier 身份、Episode 范围、decision/segment
关联、durable evidence 引用和执行事实不变。

`snapshot.py` 定义 `DatasetSnapshot`：它要求 Episode 终态、固定完整轨迹哈希和事件
序号边界，用于冻结训练导出视图；它不复制或改写轨迹记录。

`dataset.py` 定义 `RolloutDataset` 和 `TrainingSample`。这个适配器把一个明确的
Snapshot 与每个 PolicyDecision 的训练样本关联起来，分别保留 Decision、Segment 和
Episode Reward Annotation，并把按 Decision 关联的 immutable ExecutionEvent 快照
连同稳定的事件 ID 一起写入样本。确定性的 JSON 与流式 JSONL 在 Trainer 边界上自包含；
它不选择 Advantage Estimator。

`policy.py` 定义 `PolicyProvider` 和 `PolicyAdapter`。Adapter 把 behavior policy 身份
和采样配置写入 `ModelReply.extra`；缺失的 logprob 保持缺失，不在采样后估算。
现代 provider 还可以通过 `tools` 接收复制后的当前原生工具投影；`observe()` 之后，
`RolloutSession.current_tools` 会暴露同一组声明。只接收 observation 的旧 sampler
仍然受支持。
配置关键字可以使用 `config` 或 `sampling_config`。
`PolicyAdapter.sample_async()`、`RolloutSession.run_async()` 和
`RolloutWorker.run_async()` 同时接受同步或 awaitable sampler，因此推理服务器客户端
可以接入，而不需要把执行逻辑移出已有 Engine。
轨迹 Observation 会保留该工具投影；如果 sampler 提供了 prompt token IDs 和采样配置，
Decision 也会保留它们。

`environment.py` 定义 `RolloutEnvironment`，每次 Episode reset 都创建新的
Engine/Recorder 和 workspace。Reset 元数据记录初始 Artifact 引用、配置与预算，但不暴露
绝对 workspace 路径。

`RolloutEnvironmentAdapter` 是这个环境之上的 JSON 安全 Trainer 边界，提供
reset/observe/step/finish/release/export，以及只读的 reward/cost 投影。外部 Trainer
不需要导入 Engine 的 Action 类或直接查询 SQLite；它不会计算 Reward、暴露 workspace
路径，也不会复制第二套执行循环。

`replay.py` 定义 `TrajectoryReplayer`。Event replay 支持 cursor，Decision replay 将每次
采样和当时的 Observation 配对，Artifact replay 通过调用方提供的 checker 校验不可变版本
引用。Execution replay 不会隐式执行，必须由宿主显式提供受控 runner。

`__init__.py` 导出公共记录和适配器；`agent_sink.py` 把现有 `AgentEngine` 事件流接入
这些记录，同时保留 Action Ledger 作为执行事实源。`rollout.py` 导出面向 Trainer 的
`RolloutSession`，提供 reset/observe/step/finish/export 生命周期。

`worker.py` 提供一个 Episode 的 `RolloutWorker` 边界：`run()` 是同步路径，
`run_async()` 接受 awaitable sampler；它负责隔离环境 reset/run/release，对 durable
receipt 中断状态只报告 `recovery_required`，不重放副作用；
并返回带 schema version 和精确 payload 字节 SHA-256 的 JSON/JSONL/ATIF 导出。
`export("atif")` 是 Harbor 交换投影；完整 Science Trajectory 保留在 ATIF 的
`extra.openai4s` 扩展中。

`rpc.py` 提供纯标准库的 JSON-lines 外部 Trainer 传输层。`RolloutRPC` 校验
`reset`/`observe`/`step`/`finish`/`export`/`release`/`rewards`/`costs` 请求并委托给
`RolloutEnvironmentAdapter`；`serve_rollout_rpc` 对每一行输出结构化响应，遇到格式错误
或超长请求仍保持流同步，也不会启动第二套执行循环。

`atif.py` 提供 `ATIFExporter`，将轨迹确定性地投影为 ATIF-v1.8 交换格式，并在 ATIF
扩展元数据中保留 Segment、Reward、Cost 和执行事件标识。`completion_contract()` 校验
Harbor 风格的正向终态证据：非 running Episode 必须有且只有一个位于末尾、且 stop
reason 匹配的 `episode.finished` 事件。失败、取消、中断、`max_turns` 和普通 `done`
仍是合法终态记录，但永远不会标为正向 `completion_evidence`；该字段只表示 Runtime
收到终态 completion payload，科学质量仍由外部 Verifier 写入 Reward Annotation。
`ATIFExporter(..., children=[...])` 接受调用方显式提供的委派 child trajectory，并为每个
child 输出独立 ATIF 文档，同时从 parent decision 建立引用；它不会查询 Store，也不会
臆造 child trajectory。

`host_events.py` 定义有界的 Host RPC 生命周期事实（`started`、`completed`、`failed`），
用于把嵌套执行关联到父 Decision 和 Segment，同时不复制私有参数或结果。

`projector.py` 在导出边界只读投影持久化事实。它只跟随执行事件已经明确引用的
Artifact version 和 Compute job ID，带出 checksum、producing cell、receipt、输入
版本、manifest 以及追加式 job events；不会解析 Artifact head、复制源表或估算 GPU/
Provider 成本。

`cost.py` 只接受明确声明已认证、且绑定到精确 compute job 与 receipt 的 Provider
成本证据。缺失的 Provider 测量保持未知；持久化 job 的耗时只作为 metadata 保存，
不会被改称为 GPU 时间或账单金额。

`admission.py` 提供与 Provider 无关的资源请求和幂等 allocation 边界。它要求在
启动前绑定 Provider receipt，并分别报告准入等待时间和实际计算时间；它不替换
持久化 Compute job 仓储。

`DurableResourceAdmission` 通过 Store 和 revision 检查持久化容量预留及生命周期，
因此守护进程重启后未知的远端 allocation 仍会占用容量。

`budget.py` 将预计预留与实际用量分开，并明确 hard/soft 限制。绑定 receipt 的
allocation 在可能启动 Provider 工作后不能乐观释放；未知终态会保持预留，直到
显式 reconcile。

`DurableBudgetLedger` 通过 Store 的 RL repository 以带 revision 检查的方式持久化
预算快照和绑定 receipt 的 allocation，因此守护进程重启不会重新创建或重复扣除
同一项预留。
