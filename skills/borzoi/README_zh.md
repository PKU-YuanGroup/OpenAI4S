# Borzoi Skill

Borzoi 直接从 DNA 序列预测功能性覆盖 track——RNA-seq、CAGE、DNase、ChIP。想拿一个位点上的整段预测 track，或者想看变异在实验层面的后果而不是语言模型给的 likelihood，就用它比较 ref/alt 窗口；likelihood 那一半归 `evo2`，两者回答的是同一个变异问题的不同侧面。这里讲的是怎么驱动一个外部的 PyTorch 移植版，模型 runtime 和 checkpoint 都不在本目录里。

能不能跑起来取决于环境：依赖包是否兼容、权重是否已经下载、track 元数据是否就位，以及有没有足够大的 GPU 显存。预测出来的 track delta 是可以用来排优先级的模型证据，但它不是因果证明，也不构成临床验证。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 输入窗口固定为 524,288 bp 的 one-hot DNA，而模型本身没有任何属性会告诉你这一点，所以最先撞上的就是 shape 不匹配：要么补齐，要么裁掉。输出是 7,611 条人类 track 在 32 bp bin 上的张量；另一套 2,608 条的小鼠 head 默认关闭，要显式打开并选中才会用到。接下来是：track 元数据到底放在哪里（`TRACKS_DF`，而不是 base 模型根本没有的 `targets` 属性）、怎么和输出对上号，ref/alt 变异打分怎么做，显存下限是多少，以及移植版权重的 CC-BY-4.0 条款——它和随之而来的 Apache-2.0 代码许可并不一致。 |
