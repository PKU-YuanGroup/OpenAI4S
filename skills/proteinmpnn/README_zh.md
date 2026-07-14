# ProteinMPNN Skill

这个渐进披露 recipe 覆盖利用外部 ProteinMPNN 仓库对蛋白质 backbone 做 inverse folding。它是操作 runbook，不是捆绑的模型实现。

生成序列仍需 folding、功能、developability 与实验验证。小型运行可能使用 CPU；实际耗时及 GPU 收益取决于 campaign 规模和环境。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 说明仓库 setup、PDB/chain 选择、fixed/tied position、sampling-temperature 语法、批量 JSONL helper、输出 FASTA 解读、资源选择与验证流程。 |

## 直属子目录

无。
