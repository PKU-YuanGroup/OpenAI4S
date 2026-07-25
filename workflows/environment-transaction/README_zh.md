# `workflows/environment-transaction/`

**环境 plan → apply → rollback** —— 环境变更是事务：失败不得破坏当前环境，回滚只移动指针。

步骤：`environment_transaction`
权限：`environment:apply`
声明产物：—

| 文件 | 用途 |
| --- | --- |
| `workflow.json` | 带版本的清单：步骤、权限、声明产物、失败条件，以及下面的用例。版本 `1.0.0`。用 JSON 而非 YAML，理由与内核一致；带版本是因为用例能被悄悄改动的基准，跨时间什么也衡量不了。 |

## 用例

| 用例 | 声明结果 | 钉住什么 |
| --- | --- | --- |
| `environment-transaction/rollback` | `recovered` | 两代之后回滚到第一代 |
| `environment-transaction/failed-apply` | `success` | 构建失败后 current 必须不变 |

## 清单声明的失败条件

- 失败的构建改动了 current 指针
- 回滚需要重建
