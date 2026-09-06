# frontend/src/features/messages

[中文说明](README_zh.md)

F-10 message stream. Framed history paint (40 rows per rAF + one fragment), dual-node streaming markdown (sealed prefix + live tail), tool output `textNode.appendData(delta)`, follow-scroll coalesced on rAF. 1MB live-output cap is F-08 `appendLiveOutput`; this lane only turns it into a delta. Window names (`openConversation`, `fetch*Messages`, `down`) are assigned here, not left as F-05 stubs.

## Files

| File | Responsibility |
| --- | --- |
| [`components.tsx`](components.tsx) | `MessageList` (`#messages` / `#jump-pill`) and `StreamingPre`. |
| [`cut.ts`](cut.ts) | Incremental `_mdStableCut` / `mdStableCut` (app.js:5378-5402). |
| [`cut.test.ts`](cut.test.ts) | Incremental scan matches the original from-scratch cut; fence / 120-char tail. |
| [`delta.ts`](delta.ts) | `liveOutputDelta`, `bindStreamingPre` (`appendData`), `toolMetaLabel`. |
| [`delta.test.ts`](delta.test.ts) | 1MB truncation idempotent; newlines counted on the increment only. |
| [`dom.ts`](dom.ts) | `$` / `el` / `#messages` / `ensureMessageDom`. |
| [`fetch.ts`](fetch.ts) | `fetchRecentMessages` / `fetchOlderMessages` / `fetchAllMessages` (6926-6961). |
| [`handlers.ts`](handlers.ts) | `text_reset` / `text_chunk` WS handlers. |
| [`handlers.test.ts`](handlers.test.ts) | mine / stale-turn guards; idempotent register. |
| [`identity.ts`](identity.ts) | Candidate identity + `storedCandidateOwnsChunk` at the feed boundary. |
| [`index.ts`](index.ts) | Public exports; `installMessages` assigns window names via `isReady`. |
| [`install.test.ts`](install.test.ts) | Contract names are real (`isReady`), not F-05 stubs. |
| [`list.ts`](list.ts) | `renderStored`, `insertMessageByTime`, framed batch paint. |
| [`list.test.ts`](list.test.ts) | 640 rows → 16 frames of 40; insert-by-time skips `#msgs-earlier`. |
| [`messages.css`](messages.css) | `.md-sealed` / `.md-tail { display: contents }`. |
| [`open.ts`](open.ts) | `openConversation`: store reset + framed history + `isReady` lane calls. |
| [`raf.ts`](raf.ts) | Shared `requestAnimationFrame` / setTimeout fallback. |
| [`scroll.ts`](scroll.ts) | `down` / `updateJumpPill` on one rAF; throttled scroll listener. |
| [`stream.ts`](stream.ts) | `feed` / `flushRender` / `scheduleRender` / `startStream` / `sealText`. |
