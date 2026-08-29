# frontend/src/features/timeline

[中文说明](README_zh.md)

F-15 Action Timeline kernel. `sanitize*` / `mergeActionTimelines` are pure. The virtualized ledger, SVG overview, and workbench panels live in `island.ts` and keep `_timelineView` identity plus the E2E function names. Window contract names are assigned here (not in `window-exports.ts`).

## Files

| File | Responsibility |
| --- | --- |
| [`api.ts`](api.ts) | Same-origin `/api/v1` fetch, `optionalApi`, `hint` / later-lane `isReady` calls. |
| [`dom.ts`](dom.ts) | `el` / `iconEl` / `ghostIconBtn` / `svgElement` / `$` / `bytes`. |
| [`index.ts`](index.ts) | `bootTimeline`: WS handlers + window contract assignments. |
| [`island.ts`](island.ts) | Imperative island: 46px ledger, overview SVG, five sidebar panels, `renderActionTimeline`. |
| [`model.ts`](model.ts) | Span / overview geometry, `actionTimelineEntryKey`, epoch parser. |
| [`s.ts`](s.ts) | `createSProxy()` alias so nested `_timelineView` writes keep identity. |
| [`sanitize.ts`](sanitize.ts) | `sanitize*` family and `mergeActionTimelines` (app.js:2795-3298). |
| [`sanitize.test.ts`](sanitize.test.ts) | Vitest for sanitize* and merge. |
| [`types.ts`](types.ts) | Allowlisted projection shapes. |
| [`ws.ts`](ws.ts) | `action_timeline` / execution / recovery / branch / delegation / sandbox handlers. |
