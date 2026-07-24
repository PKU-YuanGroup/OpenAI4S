# `workflows/environment-provenance/`

**产物环境 provenance** —— 产物必须记录**实际产生它的内核代际**，而不是 daemon 自己的解释器。

步骤：`open_session`, `register_kernel_generation`, `capture_environment`
权限：`kernel:python`
声明产物：—

| 文件 | 用途 |
| --- | --- |
| `workflow.json` | 带版本的清单：步骤、权限、声明产物、失败条件，以及下面的用例。版本 `1.0.0`。用 JSON 而非 YAML，理由与内核一致；带版本是因为用例能被悄悄改动的基准，跨时间什么也衡量不了。 |

## 用例

| 用例 | 声明结果 | 钉住什么 |
| --- | --- | --- |
| `environment-provenance/measured` | `provenance` | 有内核代际时快照标记为 verified |
| `environment-provenance/assumed` | `provenance` | 无内核代际时必须明说是假定的 |

## 清单声明的失败条件

- 快照记录了 daemon 而不是内核
- 代际归属不可验证却未标注
