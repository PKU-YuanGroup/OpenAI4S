# `workflows/science-retrieval/`

**科学数据检索与来源证据** —— 检索到的记录必须能回答两个问题：什么时候为真、是不是同一批字节。

步骤：`science_query`, `connector_drift_check`
权限：`network:science`
声明产物：—

| 文件 | 用途 |
| --- | --- |
| `workflow.json` | 带版本的清单：步骤、权限、声明产物、失败条件，以及下面的用例。版本 `1.0.0`。用 JSON 而非 YAML，理由与内核一致；带版本是因为用例能被悄悄改动的基准，跨时间什么也衡量不了。 |

## 用例

| 用例 | 声明结果 | 钉住什么 |
| --- | --- | --- |
| `science-retrieval/provenance` | `provenance` | 哈希等于上游原始 body |
| `science-retrieval/drift` | `success` | required 字段被置 null 视为 drift |

## 清单声明的失败条件

- 哈希不是上游原始字节
- 记录的长度不是到达长度
