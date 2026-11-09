# RL Runtime 合约

[English](README.md)

这个包是 OpenAI4S 执行运行时与外部 Agentic RL Trainer 之间的无依赖数据边界。
`models.py` 定义版本化的 Science Trajectory 记录；`recorder.py` 收集一个 Episode，
支持追加式幂等写入以及确定性的 JSON/JSONL 导出。Reward Annotation 和 Cost 是独立
记录，因此 Verifier 或训练数据快照不会改写执行事实。训练算法和特定 Provider 的
Policy Client 保持在 Harness 外部。

Host Segment 接口只记录 observation 和产物证据。Reward Annotation 必须由可信的
Verifier 或 Trainer 写入，Agent 没有 Host 自评分接口。

`verifier.py` 定义与 Provider 无关的 `RewardVerifier` 协议和 `apply_verifier()`。
它在追加带版本 Annotation 前校验 Verifier 身份、Episode 范围和执行事实不变。

`snapshot.py` 定义 `DatasetSnapshot`：它要求 Episode 终态、固定完整轨迹哈希和事件
序号边界，用于冻结训练导出视图；它不复制或改写轨迹记录。

`policy.py` 定义 `PolicyProvider` 和 `PolicyAdapter`。Adapter 把 behavior policy 身份
和采样配置写入 `ModelReply.extra`；缺失的 logprob 保持缺失，不在采样后估算。

`__init__.py` 导出公共记录和适配器；`agent_sink.py` 把现有 `AgentEngine` 事件流接入
这些记录，同时保留 Action Ledger 作为执行事实源。`rollout.py` 导出面向 Trainer 的
`RolloutSession`，提供 reset/observe/step/finish/export 生命周期。

`atif.py` 提供 `ATIFExporter`，将轨迹确定性地投影为 ATIF-v1.8 交换格式，并在 ATIF
扩展元数据中保留 Segment、Reward、Cost 和执行事件标识。

`host_events.py` 定义有界的 Host RPC 生命周期事实（`started`、`completed`、`failed`），
用于把嵌套执行关联到父 Decision 和 Segment，同时不复制私有参数或结果。
