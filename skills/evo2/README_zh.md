# Evo 2 Skill

这个渐进披露 recipe 覆盖使用 Evo 2 进行 DNA likelihood scoring、embedding、generation 与变异比较。它只提供操作指导；模型代码、checkpoint 与加速 runtime 均在目录外。

必须在活动环境中验证 GPU 需求、支持的 context length、checkpoint 访问与生成质量。Score 来自模型，不能当作实验变异效应。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 说明 Evo 2 加载、byte/token 约定、forward likelihood、ref/alt scoring、embedding、受控生成、长上下文显存/chunking、输出 shape 与限制。 |

## 直属子目录

无。
