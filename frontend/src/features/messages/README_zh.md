# frontend/src/features/messages

[English](README.md)

F-10 消息流。分帧历史绘制（每 rAF 40 条 + 一次 fragment）、流式 Markdown 双节点（sealed 前缀 + live tail）、工具输出 `textNode.appendData(delta)`、跟随滚动合并进同一个 rAF。1MB 截断仍走 F-08 的 `appendLiveOutput`；本车道只把它变成增量。window 名字（`openConversation`、`fetch*Messages`、`down`）由本模块赋值，不再留给 F-05 占位。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`components.tsx`](components.tsx) | `MessageList`（`#messages` / `#jump-pill`）和 `StreamingPre`。 |
| [`cut.ts`](cut.ts) | 增量 `_mdStableCut` / `mdStableCut`（app.js:5378-5402）。 |
| [`cut.test.ts`](cut.test.ts) | 增量扫描与从零扫描同结果；围栏 / 120 字软尾。 |
| [`delta.ts`](delta.ts) | `liveOutputDelta`、`bindStreamingPre`（`appendData`）、`toolMetaLabel`。 |
| [`delta.test.ts`](delta.test.ts) | 1MB 截断幂等；换行只数增量。 |
| [`dom.ts`](dom.ts) | `$` / `el` / `#messages` / `ensureMessageDom`。 |
| [`fetch.ts`](fetch.ts) | `fetchRecentMessages` / `fetchOlderMessages` / `fetchAllMessages`（6926-6961）。 |
| [`handlers.ts`](handlers.ts) | `text_reset` / `text_chunk` WS handler。 |
| [`handlers.test.ts`](handlers.test.ts) | mine / 陈旧 turn 守卫；重复注册幂等。 |
| [`identity.ts`](identity.ts) | 候选身份与 feed 边界上的 `storedCandidateOwnsChunk`。 |
| [`index.ts`](index.ts) | 对外导出；`installMessages` 用 `isReady` 往 window 赋值。 |
| [`install.test.ts`](install.test.ts) | 契约名字是真实现（`isReady`），不是 F-05 占位。 |
| [`list.ts`](list.ts) | `renderStored`、`insertMessageByTime`、分帧批量绘制。 |
| [`list.test.ts`](list.test.ts) | 640 条 → 16 帧 × 40；按时间插入跳过 `#msgs-earlier`。 |
| [`messages.css`](messages.css) | `.md-sealed` / `.md-tail { display: contents }`。 |
| [`open.ts`](open.ts) | `openConversation`：store 重置 + 分帧历史 + `isReady` 跨车道调用。 |
| [`raf.ts`](raf.ts) | 共用 `requestAnimationFrame` / setTimeout 回退。 |
| [`scroll.ts`](scroll.ts) | `down` / `updateJumpPill` 合并进一个 rAF；节流 scroll 监听。 |
| [`stream.ts`](stream.ts) | `feed` / `flushRender` / `scheduleRender` / `startStream` / `sealText`。 |
