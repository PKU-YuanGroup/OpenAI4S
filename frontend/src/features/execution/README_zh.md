# frontend/src/features/execution

[English](README.md)

F-16 执行视图。Executed-code 历史、变量检查器、产物 Provenance tab，以及 fork 无 checkpoint 时的 409 呈现。recovery/branch 载荷清洗仍在 F-15 `features/timeline/sanitize.ts`；本车道接线 REST 变更，并把缺失 cursor checkpoint 如实显示为 HTTP 409，不重试、不改写。

F-14 已用 `isReady` 门控 `toggleExecutedCode` / `buildExecutedCodeView`。F-17 Viewer 在 `provMode` 时调用 `window.renderProvenanceInto`。本车道挂上这些名字，并把 F-14 `renderNotebook` 与检查器组合。

不改 `stores/`，也不改 `compat/window-exports.ts` 标记线以上的内容。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`types.ts`](types.ts) | Executed-code / lineage / 环境快照记录。 |
| [`api.ts`](api.ts) | 同源 fetch，失败时 `ApiError` 保留 HTTP status（409 与其它错误可区分）。 |
| [`conflict.ts`](conflict.ts) | Fork 409 呈现：不重试，保留服务端原句。 |
| [`lineage.ts`](lineage.ts) | Provenance 链变换（cell / captures / 环境诚实性三态）。记录中的包列表从未读取（有 `packages_unavailable` 且无包）时，`envPackageCount` 返回 null。 |
| [`exec.ts`](exec.ts) | `execSourcesState` / `toggleExecutedCode` / `buildExecutedCodeView`（app.js:10148-10229）。 |
| [`inspector.ts`](inspector.ts) | 变量检查器（app.js:10265-10332）。 |
| [`provenance.ts`](provenance.ts) | Provenance tab（app.js:10631-10833）。未读取的包列表显示为"Packages 未知"（非 Python kernel 为"不适用"），不显示"没有可报告的包"；原因仍作为警告说明保留。 |
| [`branch.ts`](branch.ts) | Fork / recovery REST 与 409 呈现。 |
| [`boot.ts`](boot.ts) | window 名 + notebook/viewer 组合。 |
| [`index.ts`](index.ts) | 对外再导出。 |
| [`lineage.test.ts`](lineage.test.ts) | Provenance 链数据变换；针对 0.2.x 遗留快照、确实为空的 Python 列表和 R kernel 渲染 Environment 面板。 |
| [`conflict.test.ts`](conflict.test.ts) | 409 呈现；`forkOnce` 只打一次；分支错误横幅以新对象发布。 |
| [`exec.test.ts`](exec.test.ts) | Notebook 侧栏外壳：打开「已执行代码」时移除变量检查器；每次打开、每个 cell 完成后都重新读取快照。 |
| [`inspector.test.ts`](inspector.test.ts) | 变量检查器刷新时，加载中与结果都以新的状态对象发布。 |

- [`copy.ts`](copy.ts): 溯源读取状态与证据边界的双语文案。
- [`validation.ts`](validation.ts): 溯源／环境网络响应严格校验，保留历史可空字段。
- [`provenance.test.ts`](provenance.test.ts): 只读重试、乱序响应、产物归属与精确 Cell 跳转测试。
