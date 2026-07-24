# `workflows/evidence-package/`

**证据包导出与验证** —— 一次科研运行的可交付物：带 manifest、hash 与复现说明的会话包，且在干净环境可验证。

步骤：`open_session`, `save_artifact`, `export_session_package`
权限：`workspace:read`
声明产物：`result.csv`, `session.zip`

| 文件 | 用途 |
| --- | --- |
| `workflow.json` | 带版本的清单：步骤、权限、声明产物、失败条件，以及下面的用例。版本 `1.0.0`。用 JSON 而非 YAML，理由与内核一致；带版本是因为用例能被悄悄改动的基准，跨时间什么也衡量不了。 |

## 用例

| 用例 | 声明结果 | 钉住什么 |
| --- | --- | --- |
| `evidence-package/verifies` | `success` | 导出的包能通过验证器 |
| `evidence-package/tamper` | `provenance` | 改一个字节验证器必须拒绝 |

## 清单声明的失败条件

- 包无法通过自身校验
- manifest 未覆盖全部成员
