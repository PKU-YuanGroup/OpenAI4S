# frontend/src/features/onboarding

[English](README.md)

M-01 首次运行向导内核。四个必需决策步骤、skip/清单，以及 B-04 `capability_receipt` badge 行。`GET /onboarding` 脱敏且零出站；只有 Test 会探测供应商。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`api.ts`](api.ts) | `GET /onboarding`、`POST /onboarding/complete`、配置档保存/激活/probe。 |
| [`badges.test.ts`](badges.test.ts) | 三态 badge 标记；unknown 原因原样保留。 |
| [`badges.ts`](badges.ts) | `capability_receipt` → `true` / `false` / `unknown` 行 + stale。 |
| [`boot.ts`](boot.ts) | `bootOnboarding()` 挂载 `#onboarding-root`。 |
| [`copy.ts`](copy.ts) | 车道本地 zh/en 文案。不改生成的 i18n。 |
| [`index.ts`](index.ts) | 对外 re-export。 |
| [`machine.test.ts`](machine.test.ts) | skip / 清单 / 带 request id 的错误；Test 前 providerRequests=0。 |
| [`machine.ts`](machine.ts) | 四步 reducer。凭据不进入向导状态。 |
| [`status.ts`](status.ts) | 清洗 GET 载荷；丢掉凭据形状的键。 |
