# RFdiffusion Skill

RFdiffusion 用于 de novo binder、hotspot 条件生成与 motif scaffolding 的蛋白
骨架生成。本目录只提供调用外部 GPU 软件的操作配方，不内置 RFdiffusion
代码或权重，也不把生成出的骨架视为已经折叠或结合的证据。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | RFdiffusion 的可复现安装与推理指南，覆盖正确的 Hydra 引号和 contig 语义、残基与 `.trb` 溯源、分批执行、motif scaffolding，以及向 ProteinMPNN 和独立单体/复合物验证的必要交接。 |
