# 单步逆合成 Skill

面向“一个产物到一步前体”的可执行 recipe。它复用已有的隔离
RetroChimera/Syntheseus adapter，并明确区分前体提案与完整多步路线。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 模型选择、隔离调用、候选归一化、输出约定、科学边界和失败处理。 |
| [`README.md`](README.md) | 英文目录索引。 |
| [`README_zh.md`](README_zh.md) | 中文目录索引。 |

可执行的 class-unknown Benchmark 协议复用
[`../retrosynthesis_planning/single_step_benchmark.py`](../retrosynthesis_planning/single_step_benchmark.py)，
使前体规范化和 evaluator 语义只有一个实现，不在两个 Skill 中复制。
