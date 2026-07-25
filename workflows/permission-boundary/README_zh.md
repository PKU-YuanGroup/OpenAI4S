# `workflows/permission-boundary/`

**workspace 边界拒绝越界写** —— agent 选择写入路径，所以边界必须是代码而不是约定。

步骤：`open_session`, `host_file_write`
权限：`workspace:write`
声明产物：—

| 文件 | 用途 |
| --- | --- |
| `workflow.json` | 带版本的清单：步骤、权限、声明产物、失败条件，以及下面的用例。版本 `1.0.0`。用 JSON 而非 YAML，理由与内核一致；带版本是因为用例能被悄悄改动的基准，跨时间什么也衡量不了。 |

## 用例

| 用例 | 声明结果 | 钉住什么 |
| --- | --- | --- |
| `permission-boundary/inside` | `success` | workspace 内正常写入 |
| `permission-boundary/escape` | `permission_denied` | 越界写入必须被拒绝 |

## 清单声明的失败条件

- 越界路径被接受
- 符号链接绕过边界
