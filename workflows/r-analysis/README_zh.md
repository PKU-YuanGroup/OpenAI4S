# `workflows/r-analysis/`

**R 通道独立执行** —— R 是独立的分析通道，不是 Python 的包装。这个 workflow 证明 R 单元格真的在 R 里跑。

步骤：`open_session`, `run_r_cell`
权限：`kernel:r`
声明产物：—

| 文件 | 用途 |
| --- | --- |
| `workflow.json` | 带版本的清单：步骤、权限、声明产物、失败条件，以及下面的用例。版本 `1.0.0`。用 JSON 而非 YAML，理由与内核一致；带版本是因为用例能被悄悄改动的基准，跨时间什么也衡量不了。 |

## 用例

| 用例 | 声明结果 | 钉住什么 |
| --- | --- | --- |
| `r-analysis/happy` | `success` | R 单元格返回 R 自己的版本串 |
| `r-analysis/error` | `failure` | R 报错必须作为失败上报 |

## 清单声明的失败条件

- 解析不到 Rscript
- R 单元格报错
- 输出来自 Python 而不是 R
