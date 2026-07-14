# Figure Composer Skill

这个渐进披露 Skill 协调从 claim/data reference 到 panel fan-out、composition 与 adversarial review 的多面板 figure 工作流。Sidecar 负责创建 plan/task 并组合已有 panel image；它不会捏造缺失分析，也不保证论文接收。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 定义 12-column panel outline、每 panel 一个 Agent、强制加载 `figure-style`、视觉检查、two-tier composite feedback、有限 regeneration round 与 anti-pattern 的 recipe。 |
| [`kernel.py`](kernel.py) | 可选 sidecar：定义 outline/review schema 与 geometry helper；生成 `panel_task`/`composite_review_task`；用 `compose_figure` 拼接并标字；暴露 crop；分组 fix；应用 outline revision；并由 `derive_outline` 从已有图像提出可编辑 outline。 |

## 直属子目录

无。

LLM/vision review call 与图像工具依赖活动 Host/内核环境。派生 outline 和 review 都只是需人工检查的提案。
