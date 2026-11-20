# frontend/src/features/ws

[中文说明](README_zh.md)

WebSocket transport for the workbench. Port of `connectWS` / `onEvent` from `openai4s/server/webui/app.js` (5157-5357). The if/else chain becomes a Map registry: exactly one handler per event type, duplicate register throws. The cursor (`_seqSeen`) advances only after `onEvent` returns, and a sequenced event at or below its root's cursor is dropped before dispatch (replay control frames and re-sent pending approvals excepted). Later F-series lanes register their own types via `registerWsHandler`; they must not re-register `replay_begin` / `replay_end` / `frame_update` / `artifact_created`.

## Files

| File | Responsibility |
| --- | --- |
| [`types.ts`](types.ts) | `WsMessage` / `WsHandler`. |
| [`registry.ts`](registry.ts) | Map registry, `registerWsHandler`, inner `onEvent`. |
| [`guards.ts`](guards.ts) | `mine`, `isStaleTurnEvent`, `eventFrameId`, `tryLane`. |
| [`connect.ts`](connect.ts) | `connectWS`, `sub`/`unsub`, `subDirectory`/`watchDirectory` (`view_directory` names the sidebar's project on open and on every project switch), ping with a pong deadline (`declareSocketDead`: a socket silent past `WS_PONG_TIMEOUT_MS`, or the browser's `offline` event, takes the close path at once -- banner, reconnect -- and `online` reconnects without waiting out the delay), reconnect, `handleIncomingMessage` (duplicate gate + cursor, written in place: sampled, never subscribed). |
| [`handlers.ts`](handlers.ts) | `replay_begin`/`replay_end` (a `gap` flags a history reload but keeps the cursor, which still marks what is on screen, so the next reconnect cannot re-append the turn; `setReplayEndSideEffects` lets F-11 re-read open approval cards); `frame_update` in-place patch + 300ms trailing load; `session_status` (another session in the sidebar's project changed) patches its row the same way and reloads the list, and a `deleted` one for the session on screen leaves it with a notice (`sessions/deleted.ts`, SYNC-17); `artifact_created` upsert (new list, `_artBust` and `_tbl` values, so subscribers see it) + 150ms trailing load. |
| [`index.ts`](index.ts) | `installWs` / `bootWs` (connects and starts the directory watch) and public re-exports. |
| [`registry.test.ts`](registry.test.ts) | Duplicate register throws; handler throw does not advance the cursor. |
| [`handlers.test.ts`](handlers.test.ts) | Epoch mismatch, gap reload (cursor kept), `session_status` for a session not viewed, mine / isStaleTurnEvent, session patch, artifact upsert, debounce. |
| [`connect.test.ts`](connect.test.ts) | URL, subscribe-on-open, the sidebar directory on open and on a project switch, ping, an unanswered ping shows the banner and reconnects (a late real close starts nothing more), offline/online events, reconnect, onmessage cursor order, duplicate/older drop, epoch reset, re-sent approval, non-finite seq. |
