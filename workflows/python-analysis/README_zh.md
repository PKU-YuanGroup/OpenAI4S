# `workflows/python-analysis/`

**Python 分析产出可追溯产物** —— 在持久 Python 内核中执行分析代码，把结果注册为带校验和的 artifact。这是本系统最常见的一次科研运行。

步骤：`open_session`, `run_python_cell`, `save_artifact`
权限：`kernel:python`, `workspace:write`
声明产物：`scores.csv`

| 文件 | 用途 |
| --- | --- |
| `workflow.json` | 带版本的清单：步骤、权限、声明产物、失败条件，以及下面的用例。版本 `1.0.0`。用 JSON 而非 YAML，理由与内核一致；带版本是因为用例能被悄悄改动的基准，跨时间什么也衡量不了。 |

## 用例

| 用例 | 声明结果 | 钉住什么 |
| --- | --- | --- |
| `python-analysis/happy` | `success` | 计算并写出 CSV |
| `python-analysis/cell-error` | `failure` | 分析代码抛异常时必须失败而不是静默产出 |

## 清单声明的失败条件

- cell 抛出异常
- 声明的产物未被写出
- artifact 校验和缺失
