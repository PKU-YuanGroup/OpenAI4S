# `workflows/telemetry-identity/`

**遥测身份随撤销一同销毁** —— 撤销销毁权限与身份；重新授权铸造新身份，两段参与期不可关联。

步骤：`telemetry_identity_cycle`
权限：`telemetry:consent`
声明产物：—

| 文件 | 用途 |
| --- | --- |
| `workflow.json` | 带版本的清单：步骤、权限、声明产物、失败条件，以及下面的用例。版本 `1.0.0`。用 JSON 而非 YAML，理由与内核一致；带版本是因为用例能被悄悄改动的基准，跨时间什么也衡量不了。 |

## 用例

| 用例 | 声明结果 | 钉住什么 |
| --- | --- | --- |
| `telemetry-identity/revoked` | `success` | 撤销后旧 payload 不得发送 |
| `telemetry-identity/current` | `success` | 当前身份的 payload 不被本检查阻断 |

## 清单声明的失败条件

- 撤销后旧身份仍被发送
- 重新授权复用旧身份
