# ADMET Genetic 示例

提交在仓库里、可重新生成的一份演示，展示数据契约和报告链路。它不能证明 ADMET-AI 或 GA 已经在当前环境里跑过，里面的分子和分数也不构成对任何人的推荐。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`build_example.py`](build_example.py) | 重建脚本，尽量只用标准库。它读取仓库里提交的 CSV 和配置记录，重新校验血缘、过滤与打分是否自洽，挑出优于最佳祖先种子的合格子代，写出最终候选和报告，再回调父目录的 sidecar 重新生成 dashboard。它不会运行 GA，也不会调用 ADMET-AI。 |
| [`seed_molecules.csv`](seed_molecules.csv) | 十二个演示用的种子 ID 与 SMILES。这是第零代，也是其余记录一路回溯到的输入身份。 |
| [`config.yaml`](config.yaml) | 演示用的硬过滤条件、打分权重与变换、性质窗口、ADMET 风险阈值，以及正向/负向 endpoint 关键词。 |
| [`generation_log.csv`](generation_log.csv) | 录下来的候选账本，共 108 行：代数、父代、操作细节、状态、分子性质、原始 ADMET 结果与派生的风险标记、各项分数，以及每一行的过滤判定。 |
| [`generation_summary.csv`](generation_summary.csv) | 四代的逐代汇总（生成数、最佳与平均分、通过数、种群最优），供报告和 dashboard 使用。 |
| [`candidates_final.csv`](candidates_final.csv) | 通过选择的四个子代分子，每一个都严格优于自己的祖先种子，同时保留基线种子 ID、基线分数和差值。 |
| [`optimization_dashboard.html`](optimization_dashboard.html) | 生成出来的可视化：各代历史、分数、过滤、血缘和入选分子，装在一个自包含的 HTML 文件里。 |
| [`report.md`](report.md) | 生成出来的可读报告：运行概览、配置、逐代汇总、候选表格、结果解读与局限。 |

从提交的记录重建应当是确定性的。如果某个科学数值变了，那是提醒你回头去查这个数字的来源，而不是反复重建展示文件直到它们对上。
