# frontend/src/features/ws

[中文说明](README_zh.md)

WebSocket transport for the workbench. Port of `connectWS` / `onEvent` from `openai4s/server/webui/app.js` (5157-5357). The if/else chain becomes a Map registry: exactly one handler per event type, duplicate register throws. The cursor (`_seqSeen`) advances only after `onEvent` returns. Later F-series lanes register their own types via `registerWsHandler`; they must not re-register `replay_begin` / `replay_end` / `frame_update` / `artifact_created`.

## Files

| File | Responsibility |
| --- | --- |
| [`types.ts`](types.ts) | `WsMessage` / `WsHandler`. |
| [`registry.ts`](registry.ts) | Map registry, `registerWsHandler`, inner `onEvent`. |
| [`guards.ts`](guards.ts) | `mine`, `isStaleTurnEvent`, `eventFrameId`, `tryLane`. |
| [`connect.ts`](connect.ts) | `connectWS`, `sub`/`unsub`, ping, reconnect, `handleIncomingMessage` (cursor). |
| [`handlers.ts`](handlers.ts) | `replay_begin`/`replay_end`; `frame_update` in-place patch + 300ms trailing load; `artifact_created` upsert + 150ms trailing load. |
| [`index.ts`](index.ts) | `installWs` / `bootWs` and public re-exports. |
| [`registry.test.ts`](registry.test.ts) | Duplicate register throws; handler throw does not advance the cursor. |
| [`handlers.test.ts`](handlers.test.ts) | Epoch mismatch, gap reload, mine / isStaleTurnEvent, session patch, artifact upsert, debounce. |
| [`connect.test.ts`](connect.test.ts) | URL, subscribe-on-open, ping, reconnect, onmessage cursor order. |
