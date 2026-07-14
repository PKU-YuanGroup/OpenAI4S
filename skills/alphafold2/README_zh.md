# AlphaFold2 Skill

AlphaFold2 与 AlphaFold2-Multimer，走的是 ColabFold 的 `colabfold_batch`，而不是 DeepMind 自己的流水线——正因如此，一次预测只需要一条命令加一个 FASTA，而不必挂载 2 TB 的本地数据库。加载器一开始只暴露 Skill 名称与摘要；只有当任务确实需要基于 MSA 的单体或多聚体折叠，或者需要对设计序列做 confidence 与 self-consistency 审阅时，Agent 才会去读 [`SKILL.md`](SKILL.md)。AF2 与旁边三个 co-folding 模型的区别在覆盖范围：它只折叠蛋白质链，配体和核酸要转给 `boltz`、`chai1` 或 `openfold3`。

本目录不捆绑 AlphaFold 代码、权重、环境，也没有跑着的服务。要真跑一次预测，ColabFold、模型参数和算力都得自己备好；走公共 MSA 路径时，序列还会被发到外部的 ColabFold MMseqs2 服务。Skill 元数据里的 GPU 要求只是声明这份 recipe 需要 GPU，并不能证明机器上真有一块。

用来排名的那几个分数，衡量的是模型对自己刚画出的坐标有多确信，而不是这些坐标对不对。pLDDT 高，说明 AF2 对自己折出来的结构很有把握。ipTM 过了常用的 0.5 这条宽松线，说明 Multimer 模型对自己造出来的界面很有把握——至于这两条链在细胞里究竟会不会碰面，它一个字也没讲。根本不存在的异源二聚体，AF2 照样折得出来，而且折得信心十足。哪怕 rank-1 的模型分数漂亮，它也只是一个待验证的假设，不是结论。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Agent 决定要折叠之后才会读到的内容：`colabfold_batch` 怎么调用、FASTA 里链与链之间的冒号才是真正把 AF2 切到 Multimer 模型的开关、以及在信任任何一个模型之前怎么按 pLDDT 和 ipTM 给输出的五个模型排名。两个运行时陷阱各占一节——ColabFold 在 import 时设下的 unified memory 默认值，会让受限 GPU 沙箱里的第一次折叠永远卡住且不报错；公共 MMseqs2 服务器是共享且限流的，短任务的墙钟时间大半花在它身上。序列会发往那台服务器、以及 AF2 权重的 CC-BY-4.0 条款，都写在开头。 |
