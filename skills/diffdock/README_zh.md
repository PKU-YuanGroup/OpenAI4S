# DiffDock Skill

这个渐进披露 recipe 指导使用外部 DiffDock-L 做 blind 小分子 pose prediction。它不捆绑 DiffDock 仓库、weights、receptor preparation 或 GPU 环境。

DiffDock confidence 排序的是 pose plausibility，不是 binding affinity。Pose 需要化学检查，通常还要下游 scoring/refinement；sequence-only receptor folding 会引入另一层模型不确定性。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 单 complex CLI 输入、SMILES/SDF/PDB 处理、ranked pose/confidence 输出、解读、资源需求及区分 geometry 与 affinity 的主 runbook。 |

## 直属子目录

| 目录 | 职责 |
| --- | --- |
| [`references/`](references/) | 按需读取的 batch/library 与 sequence-only receptor 工作流。 |
