# frontend/src/features/timeline

[English](README.md)

F-15 Action Timeline 内核。`sanitize*` / `mergeActionTimelines` 是纯函数。虚拟化 ledger、SVG overview 和工作台面板在 `island.ts`，保留 `_timelineView` 对象同一性与 E2E 函数名。契约清单上的 window 名字由本模块自己赋值（不改 `window-exports.ts` 除标记区注释）。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`api.ts`](api.ts) | 同源 `/api/v1` fetch、`optionalApi`、`hint` / 后续车道 `isReady` 调用。 |
| [`dom.ts`](dom.ts) | `el` / `iconEl` / `ghostIconBtn` / `svgElement` / `$` / `bytes`。 |
| [`index.ts`](index.ts) | `bootTimeline`：注册 WS handler 并把契约名字挂到 window。 |
| [`index.test.ts`](index.test.ts) | `installTimeline` 把 `loadWorkbenchState` 挂到后续车道的 `callWindow`。 |
| [`island.ts`](island.ts) | 命令式孤岛：46px ledger、overview SVG、五个侧栏面板、`renderActionTimeline`。 |
| [`model.ts`](model.ts) | span / overview 几何、`actionTimelineEntryKey`、epoch 解析。 |
| [`s.ts`](s.ts) | `createSProxy()` 别名，保证 `_timelineView` 嵌套写入保持同一性。 |
| [`sanitize.ts`](sanitize.ts) | `sanitize*` 家族与 `mergeActionTimelines`（app.js:2795-3298）。 |
| [`sanitize.test.ts`](sanitize.test.ts) | sanitize* 与 merge 的 Vitest。 |
| [`types.ts`](types.ts) | 白名单投影形状。 |
| [`ws.ts`](ws.ts) | `action_timeline` / execution / recovery / branch / delegation / sandbox handler。 |
