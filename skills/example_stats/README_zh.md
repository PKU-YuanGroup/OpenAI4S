# Example Stats Skill

这个小型渐进披露 Skill 用零依赖 descriptive statistics 演示 `SKILL.md` 加 Python sidecar 的模式。Sidecar 只在 Skill 被选中时加载，并只处理 caller 提供的数值序列。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 提供 summary、quantile、z-score 与 Pearson correlation 的 import 示例和 recipe。 |
| [`kernel.py`](kernel.py) | 可选 sidecar，在普通 Python number list 上实现输入检查、`mean`、sample/population `std`、`median`、插值 `quantile`、`zscore`、`correlation` 与组合 `summary`。 |

## 直属子目录

无。

这些 helper 属于教学/通用计算；不会选择统计设计，也不能证明推断有效性。
