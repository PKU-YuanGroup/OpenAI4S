# ADMET Genetic 示例

本目录是 data contract 与报告路径的 committed、可重新生成演示。它不能证明 ADMET-AI 或 GA 已在当前环境运行，其中 molecule/score 也不是面向用户的推荐。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`build_example.py`](build_example.py) | 以标准库为主的重建脚本：读取并校验 committed CSV/config record，检查 lineage/filter/scoring 一致性，选择优于最佳祖先 seed 的 passing child，写 final candidate/report，并通过父目录 sidecar 重建 dashboard。 |
| [`seed_molecules.csv`](seed_molecules.csv) | 十二个演示 seed ID 与 SMILES，组成 generation zero/输入 identity。 |
| [`config.yaml`](config.yaml) | 演示 hard filter、score weight/transform、property window、ADMET risk threshold 与正/负 endpoint keyword。 |
| [`generation_log.csv`](generation_log.csv) | 录制的 108-row candidate ledger，含 generation、parent、operation detail、status、molecular property、ADMET payload/flag、score 与 filter decision。 |
| [`generation_summary.csv`](generation_summary.csv) | 报告/dashboard 使用的四代 aggregate count、best/mean score、pass count 与 population best。 |
| [`candidates_final.csv`](candidates_final.csv) | 四个派生 passing child candidate，按优于祖先 seed 选择，并保存 baseline ID/score 与 delta。 |
| [`optimization_dashboard.html`](optimization_dashboard.html) | Generation history、score、filter、lineage 与所选 molecule 的 self-contained 生成可视化。 |
| [`report.md`](report.md) | 生成的人类可读 run overview、配置、generation summary、candidate table、解读与限制。 |

## 直属子目录

无。

重建应由 committed record 确定性完成；科学数值变化需要审阅 provenance，而不能只重建展示文件。
