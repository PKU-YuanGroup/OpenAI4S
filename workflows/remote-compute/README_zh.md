# `workflows/remote-compute/`

**远程任务 submit → poll → harvest** —— 把重活送出去，并把结果与证据带回来。远端脚本在真实 shell 中执行。

步骤：`open_session`, `remote_job`
权限：`compute:ssh`
声明产物：`scores.csv`

| 文件 | 用途 |
| --- | --- |
| `workflow.json` | 带版本的清单：步骤、权限、声明产物、失败条件，以及下面的用例。版本 `1.0.0`。用 JSON 而非 YAML，理由与内核一致；带版本是因为用例能被悄悄改动的基准，跨时间什么也衡量不了。 |

## 用例

| 用例 | 声明结果 | 钉住什么 |
| --- | --- | --- |
| `remote-compute/harvest` | `success` | 声明的产物真的被回收回来 |
| `remote-compute/unwritten-output` | `success` | 承诺了却没产出必须判失败 |

## 清单声明的失败条件

- 声明的产物未被回收
- 任务被取消却报告成功
- 退出码丢失
