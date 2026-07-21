# Chai-1 Skill

Chai-1 的渐进披露 recipe。Chai-1 是全原子扩散式 co-folding 模型，蛋白质、RNA、DNA 与 SMILES 配体链在同一份 multi-entity FASTA 里都是一等实体。这份 recipe 带 Agent 走通外部的 `chai-lab` Python API，并解读返回的候选结构与分数；模型本身不随本目录分发。Chai-1 与 `boltz` 覆盖的范围大体重合，recipe 也正是这么用它的：两个模型都跑一遍，保留任一模型判过的设计，这是常见的 consensus 过滤；而 Chai 有 Python 入口，比 `boltz` 更容易嵌进设计循环里。

依赖包、权重、GPU 资源以及可选的外部 MSA 服务均须另行可用。把 Chai-1 和 Boltz-2 一起跑，换来的是第二个模型，不是第二次实验。两者同属全原子扩散式 co-folding，是同一类模型，所以它们都看好的复合物，也可能是它们一起看错的复合物，而它们一致给出的 ipTM，说的仍然只是模型自己。两边都过的设计，是一份更短的、值得拿去做实验的名单，仅此而已。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 大部分篇幅都在讲一个选择：是走 MSA，还是走 ESM embedding。不用 MSA 服务确实更快，但 ipTM 通常要低几个点，而且并不省显存——embedding 这条路会把一个 30 亿参数的 ESM2 和主干一起装进 GPU。围绕这个选择，还讲了 multi-entity FASTA 里 `>protein\|name=…` 的写法、`run_inference` 的各个参数、排好序的 `pred.model_idx_*.cif` 与配套的 `scores.*.npz`，以及不设 `CHAI_DOWNLOADS_DIR` 会怎样：要么每次冷启动重下 5 GB，要么跑到一半抛出 `PermissionError`。最后讲怎么拿 Chai-1 和另一个模型做 consensus，以及许可。 |
