# frontend/src/features/timeline

[中文说明](README_zh.md)

F-15 Action Timeline kernel. `sanitize*` / `mergeActionTimelines` are pure. The virtualized ledger, SVG overview, and workbench panels live in `island.ts` and keep `_timelineView` identity plus the E2E function names. Window contract names are assigned here (not in `window-exports.ts`).

## Files

| File | Responsibility |
| --- | --- |
| [`api.ts`](api.ts) | Same-origin `/api/v1` fetch, `optionalApi`, `hint` / later-lane `isReady` calls. |
| [`copy.ts`](copy.ts) | Lane-local zh/en copy (the generated `i18n` dictionaries come from frozen `app.js`): C2 remote-task panel (`computeRecordsT`) and the queue strip (`queueT`). `workbenchT`: the sandbox/permission panel and the diagnostics section in plain words (NAV-6). `timelineT` (an `overrideLookup`) replaces the Timeline's English-in-Chinese labels (title, action kinds, panels, checkpoint words) and its jargon subtitle; `island.ts` uses it as its `t`. |
| [`compute-records.test.ts`](compute-records.test.ts) | C2 behaviour tests over an HTTP stand-in: 503 is "records unavailable" (not "no tasks") with retry, same-session failure keeps the page as stale, session/filter changes clear it, a successful empty page is the only "no tasks", absent `total_live_count` stays page-scoped, load-more appends by cursor and de-duplicates, and rows show the record's own update time. |
| [`dom.ts`](dom.ts) | `el` / `iconEl` / `ghostIconBtn` / `svgElement` / `$` / `bytes`. |
| [`execution-request.ts`](execution-request.ts) | `exactExecutionIdentity` / `scopedExecutionRequest` (app.js:3021-3041): an interrupt names the exact execution it stops. |
| [`execution-request.test.ts`](execution-request.test.ts) | Vitest: the interrupt names an exact execution, and refuses when it cannot. |
| [`index.ts`](index.ts) | `bootTimeline`: WS handlers + window contract assignments. |
| [`index.test.ts`](index.test.ts) | `installTimeline` publishes `loadWorkbenchState` for later-lane `callWindow`. |
| [`island.ts`](island.ts) | Imperative island: 46px ledger, overview SVG, five sidebar panels, `renderActionTimeline` (a persistent frame whose sections rebuild only when their inputs change; socket-driven repaints go through `scheduleActionTimelineRender`, held during a press or IME composition), and the scheduled workbench refresh (`scheduleWorkbenchRefresh` reads only the parts an event changed). The side panels speak plainly (NAV-6): the sandbox panel says on/off, self-test and what code in cells can do on the network, with raw values under Technical details; the model-context token layers sit in a closed Diagnostics section; the overview axis shows local times. The runtime strip shows the state, who is using the kernel and the queue in words; branch / generation / revision / job ids sit under a closed Technical details. The action ledger is the first thing in the panel; the runtime summary, the recovery card and the branch, sub-agent, remote-compute, diagnostics and sandbox panels sit under one `details.timeline-technical` disclosure, closed by default, whose state the viewer's own toggle stores in `localStorage` (`os-timeline-technical`); a recovery that is partial or failed opens it for that render without changing the stored choice. |
| [`island.test.ts`](island.test.ts) | Vitest on a fake DOM: queue strip, view teardown, what a WS-driven re-render keeps, and the remote-task panel's error/stale/paging states. The sandbox panel shows plain status with the raw self-test under closed details, the context token layers are in a closed diagnostics section, and exact times are local with their zone. The runtime strip says not started / exited like the Notebook badge. Timeline words are Chinese in a Chinese UI where the dictionary has English. |
| [`model.ts`](model.ts) | Span / overview geometry, `actionTimelineEntryKey`, epoch parser. `timelineOverviewExactTime` / `timelineLocalClock` give local times with the UTC offset named, not UTC without a zone. |
| [`queue.ts`](queue.ts) | `#queue-strip`: queued follow-ups above the composer, showing what the user wrote (never branch/execution ids), with Edit (withdraw the message back into the composer) and ✕ (drop it), one POST per execution. `noteQueuedTurnStarted` says when a queued message starts; `queuedFollowUpCount` tells Stop what runs next. |
| [`s.ts`](s.ts) | `createSProxy()` alias so nested `_timelineView` writes keep identity. |
| [`sanitize.ts`](sanitize.ts) | `sanitize*` family, `mergeActionTimelines` (app.js:2795-3298), and `mergeComputeTaskPages` (C2 page append). |
| [`sanitize.test.ts`](sanitize.test.ts) | Vitest for sanitize* and merge. |
| [`types.ts`](types.ts) | Allowlisted projection shapes. |
| [`ws.ts`](ws.ts) | `action_timeline` / execution / recovery / branch / delegation / sandbox handlers. |
