# frontend/src/features/customize

[English](README.md)

F-19 Customize 领域逻辑。Tab 状态机、定时器租约（unmount 清掉每一轮询）、同源 API 客户端、火山/DataPro/豆包辅助函数。Window 导出 `openCust` / `custTab` / `telemetryRow` 由本模块赋值，不写进 `compat/window-exports.ts`。能力判定走 `compat/stub.ts` 的 `isReady`。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`actions.ts`](actions.ts) | `openCust` / `custTab` / `closeCust`。递增 generation 让面板重新挂载。 |
| [`load.ts`](load.ts) | 每个 tab generation 的有界首次加载：`beginCustomizeLoad` / `markCustomizeLoaded` / `markCustomizeFailed` / `markCustomizeTimedOut`，`CUST_LOAD_TIMEOUT_MS`（30 秒，与 app.js 一致）。 |
| [`load.test.ts`](load.test.ts) | `custTab()` 开始一次待定加载；标记只结算一次；超时只对仍在等待的 generation 生效。 |
| [`dismiss.ts`](dismiss.ts) | Customize 如何关闭。打开状态只归 `customizeOpen` 一个来源：`#cust` 在最上层时，chrome 的焦点陷阱把 Esc 让给 Customize（先关嵌套编辑器，再关模态）；其他途径给 `#cust` 加上 `.hidden` 时，随后调用 `closeCust()`。只有按下不在对话框内开始的点击，遮罩才会关闭它。 |
| [`dismiss.test.ts`](dismiss.test.ts) | 嵌套编辑器里按 Esc 只关编辑器；Customize 之上的模态自己处理 Esc；输入法组字的 Esc 不处理；`#cust` 被别的途径隐藏时 Customize 随之关闭；在对话框内按下、在遮罩上松开的点击不关闭。 |
| [`api.ts`](api.ts) | `api` / `ApiError` / `apiErrorText`。路径必须是单个前导斜杠。 |
| [`environment.ts`](environment.ts) | Skill readiness 文案；`sanitizeStandardProfileReadiness`。 |
| [`host.ts`](host.ts) | 经 `isReady` 调用 `hint` / `openViewer`；直接 re-export `models.ts` 里真正的 `loadModels`（不经 window 桥）；`effProject`。 |
| [`index.ts`](index.ts) | `installCustomize` / `bootCustomize` 与对外 re-export。 |
| [`layout.ts`](layout.ts) | `os-layout` 密度。`setLayout` / `applyLayout`。 |
| [`memory.ts`](memory.ts) | Memory 作用域。绝不发送字面 `"default"`。 |
| [`models.ts`](models.ts) | 本机端点清洗、协议目录、capability-receipt 读取；`loadModels` / `chooseComposerModel` 用 `GET /models` 与 `PUT /models/default` 填充 composer `#model-select` 的 store（由 `bootCustomize` 调用）。 |
| [`models.test.ts`](models.test.ts) | `loadModels` 请求 `/models` 并填充 `models` / `defaultModel` / `defaultModelName`（配置档条目用模型名命名，id 原样保留）；`bootCustomize` 接好了它。 |
| [`state.ts`](state.ts) | `customizeOpen` / `customizeTab` / `customizeGeneration` / `nestedEditor`。 |
| [`tabs.ts`](tabs.ts) | 九个 tab id；`agents` → `specialists`。 |
| [`tabs.test.ts`](tabs.test.ts) | Tab 状态机。 |
| [`telemetry.ts`](telemetry.ts) | 同意开关 drain 循环；契约 `telemetryRow(host)`。 |
| [`timers.ts`](timers.ts) | 按挂载的定时器租约。unmount 即 dispose。 |
| [`timers.test.ts`](timers.test.ts) | unmount 后零残留；火山 key 轮询；vendor 辅助；window 导出。 |
| [`vendors.ts`](vendors.ts) | DataPro index-complete；豆包专用 source 检查。 |
| [`volcengine.ts`](volcengine.ts) | 额度计算；key 轮询 2500/5000×24 绑在租约上。 |
