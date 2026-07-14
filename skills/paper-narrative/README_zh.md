# Paper Narrative Skill

这个渐进披露 Skill 审阅 manuscript 与 figure deck 讲述的故事：hook、claim arc、panel 归属、缺失分析与可删除材料。它生成编辑提案，而不是科学证据或接收概率预测。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 定义何时加载，以及从 abstract/caption 到 brief、整套图 review、figure move/missing panel 和移交 composer 的工作流。 |
| [`kernel.py`](kernel.py) | 可选 sidecar：提供可重新绑定 SDK；paper brief 与 narrative review 的 JSON schema；用 `derive_paper_brief` 提取 pitch/vision/figure claim；用 `narrative_review_task` 构造 handling-editor review prompt。 |

## 直属子目录

无。

模型生成的 missing-panel 建议只是待考虑分析，不能算作已完成分析。
