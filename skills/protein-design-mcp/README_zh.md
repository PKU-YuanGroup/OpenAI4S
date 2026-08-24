# Protein-design MCP Skill

[English](README.md)

这个 Skill 指导 agent 组合使用内置 protein-design MCP 工具，完成通用的蛋白质设计与
重设计任务。覆盖 target-conditioned binder backbone、带约束的序列设计、单体与复合物
结构预测、物理打分与 relaxation、sequence naturalness 打分、minimization，以及可复现
的候选比较。

文档也明确说明当前科学能力边界：RFdiffusion 工具要求提供 target hotspot，尚不能表达
无表位约束、motif scaffolding、unconditional 或 membrane-aware backbone generation。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 通用工具选择工作流、可复现控制、当前能力缺口和模型证据边界。 |
| [`README.md`](README.md) | 英文目录边界和文件清单。 |
| [`README_zh.md`](README_zh.md) | 中文目录边界和文件清单。 |

本 Skill 不内置模型包、权重或 GPU 环境；connector 及其外部 backend 需要单独配置。
