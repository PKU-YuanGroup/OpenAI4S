# frontend/src/features/ws

[English](README.md)

工作台的 WebSocket 传输层。移植 `openai4s/server/webui/app.js` 的 `connectWS` / `onEvent`（5157-5357）。原 if/else 链改成 Map 注册表：每种事件恰好一个 handler，重复注册 throw。游标（`_seqSeen`）只在 `onEvent` 返回之后推进。后续 F 系列车道用 `registerWsHandler` 注册自己的类型；不得再注册 `replay_begin` / `replay_end` / `frame_update` / `artifact_created`。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`types.ts`](types.ts) | `WsMessage` / `WsHandler`。 |
| [`registry.ts`](registry.ts) | Map 注册表、`registerWsHandler`、内层 `onEvent`。 |
| [`guards.ts`](guards.ts) | `mine`、`isStaleTurnEvent`、`eventFrameId`、`tryLane`。 |
| [`connect.ts`](connect.ts) | `connectWS`、`sub`/`unsub`、ping、重连、`handleIncomingMessage`（游标）。 |
| [`handlers.ts`](handlers.ts) | `replay_begin`/`replay_end`；`frame_update` 原位 patch + 300ms 尾沿加载；`artifact_created` upsert + 150ms 尾沿加载。 |
| [`index.ts`](index.ts) | `installWs` / `bootWs` 与对外 re-export。 |
| [`registry.test.ts`](registry.test.ts) | 重复注册 throw；handler 抛异常时游标不推进。 |
| [`handlers.test.ts`](handlers.test.ts) | epoch 失配、gap 重载、mine / isStaleTurnEvent、会话 patch、产物 upsert、防抖。 |
| [`connect.test.ts`](connect.test.ts) | URL、open 时订阅、ping、重连、onmessage 游标顺序。 |
