# Agentic RL 轨迹契约

[English](README.md)

这个目录记录 OpenAI4S 执行运行时与外部 Agentic RL Trainer 之间的版本化数据边界。
运行时记录 policy、执行、成本和生命周期事实；Verifier 或 Trainer 可以追加 Reward
Annotation，训练算法仍然在本仓库之外。

## 文件

| 文件 | 职责与状态 |
| --- | --- |
| `trajectory-contract-v1.md` | `openai4s.science-trajectory.v1` JSON/JSONL 包络及其关联键的英文规范。 |
| `trajectory-contract-v1_zh.md` | 轨迹契约的中文正文。 |

契约描述 `openai4s/rl/` 产生的记录、`openai4s/storage/rl.py` 所有的持久表、
Action Ledger 关联方式，以及当前 Rollout Adapter 的边界。它是数据契约，不表示本仓库
已经包含 Trainer、Verifier 或 Reward 算法。
