# RL Runtime 合约

[English](README.md)

这个包是 OpenAI4S 执行运行时与外部 Agentic RL Trainer 之间的无依赖数据边界。
`models.py` 定义版本化的 Science Trajectory 记录；`recorder.py` 收集一个 Episode，
支持追加式幂等写入以及确定性的 JSON/JSONL 导出。Reward Annotation 和 Cost 是独立
记录，因此 Verifier 或训练数据快照不会改写执行事实。训练算法和特定 Provider 的
Policy Client 保持在 Harness 外部。

`__init__.py` 导出公共记录和适配器；`agent_sink.py` 把现有 `AgentEngine` 事件流接入
这些记录，同时保留 Action Ledger 作为执行事实源。`rollout.py` 导出面向 Trainer 的
`RolloutSession`，提供 reset/observe/step/finish/export 生命周期。
