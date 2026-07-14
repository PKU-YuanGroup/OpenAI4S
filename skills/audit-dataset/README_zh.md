# Audit Dataset Skill

这个渐进披露 Skill 为 row-oriented 表格数据提供纯标准库结构审计 recipe。加载后，其 [`kernel.py`](kernel.py) sidecar 向持久 Python 内核加入可复用 helper；它不会自动读取数据集，也不会替用户决定如何处理领域特定异常。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 定义 schema drift、missingness、duplicate、target balance 与 split leakage 的分析前流程、解读规则和必需 machine-readable 输出。 |
| [`kernel.py`](kernel.py) | 以 `audit_rows` 为核心的可选 sidecar：校验 row/column 参数，建立确定性比较表示，汇总 missing/type/unique 值，检测重复 row/ID，统计 target，并在无 pandas/numpy 情况下报告跨 split entity overlap。 |

## 直属子目录

无。

结构检查通过不能证明 representativeness、label quality 或不存在 near-duplicate leakage；这些仍需领域审阅。
