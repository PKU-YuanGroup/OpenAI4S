# frontend/src/features/onboarding

[English](README.md)

M-01 首次运行向导内核。四个必需决策步骤、skip/清单，以及 B-04 `capability_receipt` badge 行。`GET /onboarding` 脱敏且零出站；只有 Test 会探测供应商。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`api.test.ts`](api.test.ts) | 现有配置档激活先于引导状态刷新的回归覆盖。 |
| [`api.ts`](api.ts) | `GET /onboarding`、`POST /onboarding/complete`、配置档保存/更新/激活/probe。 |
| [`badges.test.ts`](badges.test.ts) | 三态 badge 标记；unknown 原因原样保留。 |
| [`badges.ts`](badges.ts) | `capability_receipt` → `true` / `false` / `unknown` 行 + stale。 |
| [`boot.ts`](boot.ts) | `bootOnboarding()` 挂载 `#onboarding-root`。 |
| [`copy.ts`](copy.ts) | 车道本地 zh/en 文案（`ot`）。不改生成的 i18n。`copyLookup(table)` 是 `ot` 与 `judgmentT` 共用的查表函数。 |
| [`copy.test.ts`](copy.test.ts) | `copyLookup`：先查当前语言，再查英文，最后返回 key；已加载的词典优先；按位置填充占位符。`ot` 与 `judgmentT` 都基于它。 |
| [`index.ts`](index.ts) | 对外 re-export。 |
| [`machine.test.ts`](machine.test.ts) | skip / 清单 / 带 request id 的错误；Test 前 providerRequests=0；模型身份变化时清除回执和测试决定；为其他配置档测得的 probe 结果会被丢弃。 |
| [`machine.ts`](machine.ts) | 四步 reducer。模型身份改变时清除旧能力回执和测试决定；仅展示字段变化时保留。`testResult` 带上它实际测的配置档，若不是当前所选路径则忽略。凭据不进入向导状态。 |
| [`status.ts`](status.ts) | 清洗 GET 载荷；丢掉凭据形状的键。 |
| [`wizard-integration.test.ts`](wizard-integration.test.ts) | 现有配置档的 Continue 会等待激活完成，并刷新已编辑配置的模型身份后再进入下一步。 |
| [`wizard-save.test.ts`](wizard-save.test.ts) | 保存新配置档或激活已有配置档后，重新读取输入框旁的模型列表。已保存过的路径再点「继续」会更新那个配置档（key 输入框为空时保留原 key）；已有完全相同的配置档时直接复用；期间被删除的则重新保存。 |
| [`wizard-skip.test.ts`](wizard-skip.test.ts) | Test 还在等待时 Skip 仍然可用，并且能在 probe 返回前完成跳过；Skip 或 Continue 之后才到达的 probe 结果会被丢弃；用户等待期间 probe 失败，仍然会报告出来。旧模型迟到的结果/错误不能覆盖新选模型的测试（属刻画性测试：离开 test 步骤的各出口本就已作废该 probe）。Test 只探测所选路径，绝不以当前激活的配置档顶替：未保存的选择会提示先选配置档；完全没有路径时，先选中激活配置档再探测。已保存的 local 模型一旦改了模型名就重新变回未保存，Test 不会拿旧模型的结果挂在新名字下。 |
