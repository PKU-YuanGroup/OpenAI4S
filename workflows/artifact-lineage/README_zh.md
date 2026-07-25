# `workflows/artifact-lineage/`

**派生产物携带 lineage** —— B 由 A 派生时，Store 必须记录这条边；否则证据链在第二步就断了。

步骤：`open_session`, `save_raw`, `save_derived`, `assert_lineage`
权限：`workspace:write`
声明产物：`raw.csv`, `derived.csv`

| 文件 | 用途 |
| --- | --- |
| `workflow.json` | 带版本的清单：步骤、权限、声明产物、失败条件，以及下面的用例。版本 `1.0.0`。用 JSON 而非 YAML，理由与内核一致；带版本是因为用例能被悄悄改动的基准，跨时间什么也衡量不了。 |

## 用例

| 用例 | 声明结果 | 钉住什么 |
| --- | --- | --- |
| `artifact-lineage/derived` | `provenance` | 派生产物记录输入版本 |
| `artifact-lineage/missing-input` | `failure` | 声明的输入不存在时不得伪造边 |

## 清单声明的失败条件

- lineage 边缺失
- 派生产物指向错误的输入版本
