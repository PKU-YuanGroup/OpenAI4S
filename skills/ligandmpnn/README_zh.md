# LigandMPNN Skill

这个渐进披露 recipe 覆盖具有配体、核酸与金属上下文的 inverse folding。它指导使用外部 LigandMPNN 仓库与 checkpoint；本目录不捆绑 executable 或 weights。

生成序列与 threaded structure 是设计候选，不是经实验验证的 binder。Chain、fixed residue、context atom 与 model type 选择必须结合实际输入结构核对。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 记录仓库 setup、model type、PDB/context 处理、design/fixed chain 与 residue、sampling、批量输出、threaded PDB、ligand-aware 限制与下游验证。 |

## 直属子目录

无。
