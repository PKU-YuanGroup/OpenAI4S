# frontend/src/features/customize

[English](README.md)

F-19 Customize 领域逻辑。Tab 状态机、定时器租约（unmount 清掉每一轮询）、同源 API 客户端、火山/DataPro/豆包辅助函数。Window 导出 `openCust` / `custTab` / `telemetryRow` 由本模块赋值，不写进 `compat/window-exports.ts`。能力判定走 `compat/stub.ts` 的 `isReady`。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`actions.ts`](actions.ts) | `openCust` / `custTab` / `closeCust`。递增 generation 让面板重新挂载。 |
| [`api.ts`](api.ts) | `api` / `ApiError` / `apiErrorText`。路径必须是单个前导斜杠。 |
| [`environment.ts`](environment.ts) | Skill readiness 文案；`sanitizeStandardProfileReadiness`。 |
| [`host.ts`](host.ts) | 经 `isReady` 调用 `hint` / `openViewer` / `loadModels`；`effProject`。 |
| [`index.ts`](index.ts) | `installCustomize` / `bootCustomize` 与对外 re-export。 |
| [`layout.ts`](layout.ts) | `os-layout` 密度。`setLayout` / `applyLayout`。 |
| [`memory.ts`](memory.ts) | Memory 作用域。绝不发送字面 `"default"`。 |
| [`models.ts`](models.ts) | 本机端点清洗、协议目录、capability-receipt 读取。 |
| [`state.ts`](state.ts) | `customizeOpen` / `customizeTab` / `customizeGeneration` / `nestedEditor`。 |
| [`tabs.ts`](tabs.ts) | 九个 tab id；`agents` → `specialists`。 |
| [`tabs.test.ts`](tabs.test.ts) | Tab 状态机。 |
| [`telemetry.ts`](telemetry.ts) | 同意开关 drain 循环；契约 `telemetryRow(host)`。 |
| [`timers.ts`](timers.ts) | 按挂载的定时器租约。unmount 即 dispose。 |
| [`timers.test.ts`](timers.test.ts) | unmount 后零残留；火山 key 轮询；vendor 辅助；window 导出。 |
| [`vendors.ts`](vendors.ts) | DataPro index-complete；豆包专用 source 检查。 |
| [`volcengine.ts`](volcengine.ts) | 额度计算；key 轮询 2500/5000×24 绑在租约上。 |
