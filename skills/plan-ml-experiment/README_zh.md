# Plan ML Experiment Skill

在开训之前，把一个 ML 问题写成可复现、把泄漏考虑在内的实验计划。有一个选择统摄其余所有选择：哪个单元必须保持独立——病人、分子骨架、中心、文档，还是时间点。纯标准库的 sidecar 用调用方给出的元数据生成确定性的 split 与 manifest。它不训练模型，也没法证明某个 split 在科学上是合理的。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 先把分析单元定下来，split 就是从它读出来的：病人、分子骨架、中心、文档或重复测量，用 grouped；上线之后要预测未来，用 chronological；只有当行确实彼此独立时，random 才是诚实的。其余一切都挂在这上面——假设、干预、基线、主指标和决策规则都要在看到测试集表现之前写死，然后是固定的 seed 与配置、单因素消融，以及那套能让别人把这场比较重跑一遍的产出（配置 fingerprint、校验和、split 下标、逐样本预测）。确定性不等于有效性：把一个有偏的 split 重跑一遍，只会把偏差原样复现。 |
| [`kernel.py`](kernel.py) | 可选 sidecar。`random_split` 在给定 seed 下打乱行下标，`chronological_split` 按时间戳稳定排序而不打乱，`grouped_split` 保证同一个分组只落进一个划分。此外还有：配置的规范 fingerprint、文件的 SHA-256，以及一份 JSON experiment manifest，它只记录调用方给出的内容，不虚构环境状态。 |

分组标识和时间顺序必须来自领域知识。实验单元如果一开始就定错了，这些辅助函数的输出里是看不出来的。
