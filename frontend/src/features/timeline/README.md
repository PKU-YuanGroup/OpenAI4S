# frontend/src/features/timeline

[中文说明](README_zh.md)

F-15 Action Timeline kernel. `sanitize*` / `mergeActionTimelines` are pure. The virtualized ledger, SVG overview, and workbench panels live in `island.ts` and keep `_timelineView` identity plus the E2E function names. Window contract names are assigned here (not in `window-exports.ts`).

## Files

| File | Responsibility |
| --- | --- |
| [`api.ts`](api.ts) | Same-origin `/api/v1` fetch, `optionalApi`, `hint` / later-lane `isReady` calls. |
| [`compute-copy.ts`](compute-copy.ts) | C2 zh/en copy for the remote-task panel (lane-local: the generated `i18n` dictionaries come from frozen `app.js`). |
| [`compute-records.ts`](compute-records.ts) | C2 remote-task read ownership: session + filter + monotonic request token, stale snapshot on failure, cursor paging; the GET builder adds `state=live` / `cursor`. |
| [`compute-records.test.ts`](compute-records.test.ts) | Vitest: failure keeps the last page as explicitly stale; another session/filter or a late page never lands; paging appends from the cursor. |
| [`dom.ts`](dom.ts) | `el` / `iconEl` / `ghostIconBtn` / `svgElement` / `$` / `bytes`. |
| [`execution-request.ts`](execution-request.ts) | `exactExecutionIdentity` / `scopedExecutionRequest` (app.js:3021-3041): an interrupt names the exact execution it stops. |
| [`execution-request.test.ts`](execution-request.test.ts) | Vitest: the interrupt names an exact execution, and refuses when it cannot. |
| [`index.ts`](index.ts) | `bootTimeline`: WS handlers + window contract assignments. |
| [`index.test.ts`](index.test.ts) | `installTimeline` publishes `loadWorkbenchState` for later-lane `callWindow`. |
| [`island.ts`](island.ts) | Imperative island: 46px ledger, overview SVG, five sidebar panels, `renderActionTimeline` (a persistent frame whose sections rebuild only when their inputs change; socket-driven repaints go through `scheduleActionTimelineRender`, held during a press or IME composition), and the scheduled workbench refresh (`scheduleWorkbenchRefresh` reads only the parts an event changed). |
| [`island.test.ts`](island.test.ts) | Vitest on a fake DOM: queue strip, view teardown, what a WS-driven re-render keeps, and the remote-task panel's error/stale/paging states. |
| [`model.ts`](model.ts) | Span / overview geometry, `actionTimelineEntryKey`, epoch parser. |
| [`queue.ts`](queue.ts) | `#queue-strip`: queued follow-ups above the composer and their ✕ cancel (one POST per execution). |
| [`s.ts`](s.ts) | `createSProxy()` alias so nested `_timelineView` writes keep identity. |
| [`sanitize.ts`](sanitize.ts) | `sanitize*` family, `mergeActionTimelines` (app.js:2795-3298), and `mergeComputeTaskPages` (C2 page append). |
| [`sanitize.test.ts`](sanitize.test.ts) | Vitest for sanitize* and merge. |
| [`types.ts`](types.ts) | Allowlisted projection shapes. |
| [`ws.ts`](ws.ts) | `action_timeline` / execution / recovery / branch / delegation / sandbox handlers. |
