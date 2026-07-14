# Example Stats Skill

一个小型的渐进披露 Skill，用零依赖的描述性统计演示 `SKILL.md` 加 Python sidecar 这个模式。sidecar 只在 Skill 被选中时才加载，处理的也只是调用方传进来的数值序列。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 汇总、分位数、z-score 与 Pearson 相关系数的 import 示例和简短用法。 |
| [`kernel.py`](kernel.py) | 可选 sidecar，作用在普通的 Python 数字列表上：`mean`、样本或总体 `std`、`median`、线性插值的 `quantile`、`zscore`、`correlation`，以及把它们合到一起的 `summary`。每个函数都会先检查输入，空序列直接报错。 |

这些都是教学和通用场景下的普通计算。它们不会替你挑统计设计，也不能让一个推断变得成立。
