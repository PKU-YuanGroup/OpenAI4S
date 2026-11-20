# frontend/src/features/onboarding

[English](README.md)

M-01 首次运行向导内核。四个必需决策步骤、skip/清单，以及 B-04 `capability_receipt` badge 行。`GET /onboarding` 脱敏且零出站；只有 Test 会探测供应商。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`api.test.ts`](api.test.ts) | 现有配置档激活先于引导状态刷新的回归覆盖；端点模型获取返回端点确实提供的协议默认模型（否则取第一个 id），失败时以抛出的 `ApiError` 呈现。 |
| [`api.ts`](api.ts) | `GET /onboarding`、`POST /onboarding/complete`、配置档保存/更新/激活/probe。 |
| [`badges.test.ts`](badges.test.ts) | 三态 badge 行，以及 `CapabilityBadges` 据此渲染的标签；unknown 原因原样保留。 |
| [`badges.ts`](badges.ts) | `capability_receipt` → `true` / `false` / `unknown` 行 + stale。 每行是界面语言的一句话（「支持工具调用 ✓」「不支持工具调用 ✗」「工具调用：未确定」）；`unknown` 永远不会写成不支持。 |
| [`boot.ts`](boot.ts) | `bootOnboarding()` 只在首次设置未完成且当前用户可以保存它（`can_configure`；团队成员永远不会看到向导）、或读不到其状态时，才加载并挂载向导（`#onboarding-root`，见 `startOnboarding`、`wizardWanted`）。 |
| [`boot.test.ts`](boot.test.ts) | 首次设置已完成时不加载向导代码块；未完成、或状态读取失败时才加载。 |
| [`copy.ts`](copy.ts) | 车道本地 zh/en 文案（`ot`）。不改生成的 i18n。`copyLookup(table)` 是 `ot` 与 `judgmentT` 共用的查表函数。 |
| [`copy.test.ts`](copy.test.ts) | `copyLookup`：先查当前语言，再查英文，最后返回 key；已加载的词典优先；按位置填充占位符。`ot` 与 `judgmentT` 都基于它。 |
| [`index.ts`](index.ts) | 对外 re-export。 |
| [`machine.test.ts`](machine.test.ts) | skip / 清单 / 带 request id 的错误；Test 前 providerRequests=0，获取模型列表会计入请求数但不算作 Test 决定；模型身份变化时清除回执和测试决定；为其他配置档测得的 probe 结果会被丢弃。 |
| [`machine.ts`](machine.ts) | 四步 reducer。模型身份改变时清除旧能力回执和测试决定；仅展示字段变化时保留。`testResult` 带上它实际测的配置档，若不是当前所选路径则忽略。凭据不进入向导状态。 |
| [`mount.ts`](mount.ts) | `mountOnboarding()`：把 `WizardHost` 渲染到 `#onboarding-root`。它是唯一导入向导 UI 的模块，构建时因此被拆出首屏包。 |
| [`presets.ts`](presets.ts) | 向导云端表单的服务商预设：`readProviderPresets` 读取 `GET /onboarding` 的 `provider_presets`（注册表里每个协议自己的地址和默认模型），`presetLabel` 给出服务商名称，`keyPage` 只在 `docs/startup-guide.md` 写明了的地方给出获取 Key 的页面。界面不自造任何 URL。 |
| [`status.ts`](status.ts) | 清洗 GET 载荷；丢掉凭据形状的键。 |
| [`wizard-integration.test.ts`](wizard-integration.test.ts) | 现有配置档的 Continue 会等待激活完成，并刷新已编辑配置的模型身份后再进入下一步。就绪状态原位刷新保留步骤/路径/回执/providerRequests，双击产生的两次本地读取只有最新响应会写入四个状态字段。 |
| [`wizard-cloud-form.test.ts`](wizard-cloud-form.test.ts) | FR-2：没有任何模型时向导直接进入云端表单；API Key 排在模型之前；服务商按名称列出，带注册表里的地址和文档写明的获取 Key 页面（文档没写的只给文字、不给链接）；“请输入名称”出现在名称字段下方；模态框里有语言切换，且不再有第二份步骤清单。 |
| [`wizard-save.test.ts`](wizard-save.test.ts) | 保存新配置档或激活已有配置档后，重新读取输入框旁的模型列表。已保存过的路径再点「继续」会更新那个配置档（key 输入框为空时保留原 key）；已有完全相同的配置档时直接复用；期间被删除的则重新保存。模型留空点「继续」时，保存端点确实提供的协议默认模型；获取失败或期间端点被改过时都不保存；获取进行中「继续」保持禁用，期间改过的名称会被保存。环境变量配置的模型会被列出（仅当没有对应的已保存配置档时），点「继续」时保存为不含 Key 的配置档并激活；没选别的模型时，「测试连接」测的就是它。 |
| [`wizard-skip.test.ts`](wizard-skip.test.ts) | Test 或就绪状态刷新还在等待时 Skip 仍然可用，并且能在 probe 返回前完成跳过；Skip 或 Continue 之后才到达的 probe 结果会被丢弃；用户等待期间 probe 失败，仍然会报告出来。旧模型迟到的结果/错误不能覆盖新选模型的测试（属刻画性测试：离开 test 步骤的各出口本就已作废该 probe）。Test 只探测所选路径，绝不以当前激活的配置档顶替：未保存的选择会提示先选配置档；完全没有路径时，先选中激活配置档再探测。已保存的 local 模型一旦改了模型名就重新变回未保存，Test 不会拿旧模型的结果挂在新名字下。 步骤条标出已完成的步骤，点击即转到对应步骤。 |
