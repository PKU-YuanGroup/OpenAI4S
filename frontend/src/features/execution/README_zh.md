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
| [`lineage.ts`](lineage.ts) | Provenance 链变换（cell / captures / 环境诚实性三态）。 |
| [`exec.ts`](exec.ts) | `execSourcesState` / `toggleExecutedCode` / `buildExecutedCodeView`（app.js:10148-10229）。 |
| [`inspector.ts`](inspector.ts) | 变量检查器（app.js:10265-10332）。 |
| [`provenance.ts`](provenance.ts) | Provenance tab（app.js:10631-10833）。 |
| [`branch.ts`](branch.ts) | Fork / recovery REST 与 409 呈现。 |
| [`boot.ts`](boot.ts) | window 名 + notebook/viewer 组合。 |
| [`index.ts`](index.ts) | 对外再导出。 |
| [`lineage.test.ts`](lineage.test.ts) | Provenance 链数据变换。 |
| [`conflict.test.ts`](conflict.test.ts) | 409 呈现；`forkOnce` 只打一次。 |
