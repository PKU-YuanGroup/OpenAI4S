# ADMET Genetic 参考资料

按需加载的补充 recipe 材料（例如通过 `host.skills.read` 读取），好让主 Skill 保持渐进披露。这些文件只负责写清契约和设计选择：它们不安装任何依赖，也不提供实验验证。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`admet.md`](admet.md) | 怎么安装和调用 ADMET-AI，各个 endpoint 的方向如何、该怎么聚合，运行时会是什么表现，以及出问题时怎么排查。 |
| [`data_contracts.md`](data_contracts.md) | 整条流程要遵守的契约：分子身份、每条候选与逐代记录必须带的列、父代字段、规范的 `operation_detail` JSON、血缘不变式，以及可视化对这些内容的预期。 |
| [`ga.md`](ga.md) | 一套可以直接往上搭的起步 GA：整体结构、变异与交叉的选项、化学过滤、SA 打分、综合打分，以及如何按多样性做选择。 |
