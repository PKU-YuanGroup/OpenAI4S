# Catalyst SAR Screening Skill

这个渐进披露 Skill 定义 hard-locked 单原子催化剂 SAR pipeline：graphene M–N4 结构只能用 FAIRChem UMA `uma-s-1p1`/`oc20` 评估，再排序并报告。当 UMA、Hugging Face 访问或凭据不可用时，hard lock 禁止静默替换为 table、heuristic 或其他 MLIP。

Python sidecar 包含真实 pipeline 代码，但成功生成用户结果仍需要兼容科学 package、model/weights、按需提供的 `HF_TOKEN` 或可达 hub、compute 与全新 workdir。Committed `metal_center_dissolution_*` 文件是刻意清除数值的 developer demo shell，绝不能作为 live result 返回。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`.gitignore`](.gitignore) | 排除 Python build/cache 与 Skill 根目录全部 image format，避免生成的 live figure 被误认为 committed 用户 deliverable。 |
| [`SKILL.md`](SKILL.md) | 主 hard-lock recipe：必需 UMA backend/environment、readiness stop-and-ask、fresh-workdir `run_pipeline`、固定阶段、deliverable 规则、developer-demo 警告与 analyst checklist。 |
| [`kernel.py`](kernel.py) | 可选 sidecar：加载结构 catalog；解析 description 并构造/替换 POSCAR；提供 UMA-only `CalculationTools`；检查 dependency/hub/model readiness；评估 dissolution/ORR metric；排序并分析 SAR；解析结构；渲染 figure/dashboard/report；组合端到端 `run_pipeline` 与受约束 dissolution case helper。 |
| [`contcar_catalog.json`](contcar_catalog.json) | Version-2 synthetic fixture/catalog，包含 28 个嵌入 graphene/pyridineN M–N4 slab POSCAR text，用作 exact/nearest structure template；明确不是实验数据发布。 |
| [`build_example.py`](build_example.py) | 只重建 text/HTML developer demo shell，清除 numerical result/backend field 与 image path，写入 disclaimer，且不运行 UMA。 |
| [`metal_center_dissolution_descriptions.json`](metal_center_dissolution_descriptions.json) | Mn-N4、Fe-N4、Cu-N4 三个演示 structure request，用于展示输入 shape。 |
| [`metal_center_dissolution_summary.json`](metal_center_dissolution_summary.json) | 经净化的三行 dissolution-mode demo metadata，无 converged numerical prediction，标记 `demo: true`，不是用户 deliverable。 |
| [`metal_center_dissolution_dashboard.html`](metal_center_dissolution_dashboard.html) | 生成的 self-contained demo-shell dashboard，含 disclaimer，不含 live UMA figure/metric。 |
| [`metal_center_dissolution_report.md`](metal_center_dissolution_report.md) | 生成的 demo-shell method/report 文本，明确不含已计算 candidate result。 |

## 直属子目录

无。
