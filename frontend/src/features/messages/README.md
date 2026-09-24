# frontend/src/features/messages

[中文说明](README_zh.md)

F-10 message stream. Framed history paint (40 rows per rAF + one fragment), dual-node streaming markdown (sealed prefix + live tail), tool output `textNode.appendData(delta)`, follow-scroll coalesced on rAF. 1MB live-output cap is F-08 `appendLiveOutput`; this lane only turns it into a delta. Window names (`openConversation`, `fetch*Messages`, `down`) are assigned here, not left as F-05 stubs.

## Files

| File | Responsibility |
| --- | --- |
| [`failure.ts`](failure.ts) | Shared live and restored failure hints, with explicit continuation for stream interruption and repeated-action stops. |
| [`cardState.ts`](cardState.ts) | The live activity card's state: a new Cell card shows a progress glyph (not the success check) and records its `producing_cell_id`; `notebook_cell_finished` (forwarded by the Notebook's handler) marks that card failed (x glyph, error bar, "Failed · N lines"), succeeded or stopped; `turnDone` settles any card still running to a neutral "ended". The generated "Running analysis · cell N" title becomes "Analysis · cell N" once the cell ends; a cell's own title stays. Feature-local copy. |
| [`copy.ts`](copy.ts) | Source-owned bilingual history-recovery text; generated locale extracts stay unchanged. |
| [`components.tsx`](components.tsx) | `HistoryLoadStatus`: accessible history status/retry, mounted by the Shell outside the imperative message host. |
| [`cut.ts`](cut.ts) | Incremental `_mdStableCut` / `mdStableCut` (app.js:5378-5402). |
| [`cut.test.ts`](cut.test.ts) | Incremental scan matches the original from-scratch cut; fence / 120-char tail. |
| [`delta.ts`](delta.ts) | `liveOutputDelta`, `bindStreamingPre` (`appendData`; each chunk is examined alone, via `liveOutputIncrement`), `toolMetaLabel` (feature-local copy: "N lines" / "done" in the UI language). |
| [`delta.test.ts`](delta.test.ts) | 1MB truncation idempotent; newlines counted on the increment only; `append` neither searches nor slices the output it holds; the meta line in both languages. |
| [`dom.ts`](dom.ts) | `$` / `el` / `#messages` / `ensureMessageDom` (a no-op once the Shell has rendered; never before `render()`). |
| [`fetch.ts`](fetch.ts) | `fetchRecentMessages` / `fetchOlderMessages` / `fetchAllMessages` (6926-6961). |
| [`handlers.ts`](handlers.ts) | `text_reset` / `text_chunk` WS handlers. |
| [`handlers.test.ts`](handlers.test.ts) | mine / stale-turn guards; idempotent register. |
| [`identity.ts`](identity.ts) | Candidate identity + `storedCandidateOwnsChunk` at the feed boundary. |
| [`index.ts`](index.ts) | Public exports; `installMessages` assigns window names via `isReady` and creates no DOM (it runs before the Shell renders). |
| [`install.test.ts`](install.test.ts) | Contract names are real (`isReady`), not F-05 stubs; install creates no `#messages` ahead of the Shell. |
| [`list.ts`](list.ts) | The one stored-row implementation, shared by the first page, "load earlier" (through `sessions/transcript.ts`) and the live turn: `renderStored`, `addMsgActions` (Copy through `copyText`, 👍/👎 with the saved rating, Edit), `renderMessageRefChips`, `renderEmptySession`, `insertMessageByTime`; plus framed batch paint. A plan-mode user row renders as the user's own text and a plan execution seed as a plan marker (`planPrompt.ts`). |
| [`list.test.ts`](list.test.ts) | 640 rows → 16 frames of 40; insert-by-time skips `#msgs-earlier`; first page and older page are one implementation: review badge and candidate identity, 👍/👎 post and show the saved rating, Copy ticks only for a confirmed write, Edit and starter chips grow the composer, user rows show their @-ref chips. |
| [`messages.css`](messages.css) | `.md-sealed` / `.md-tail { display: contents }`; the stopped-turn marker and stopped card (muted glyph, neutral bar); running and ended cards (neutral glyph and bar, the running glyph spins unless reduced motion is preferred) and failed cards (error glyph colour and bar); the reopened plan-seed line shares the stopped marker's style. |
| [`open.ts`](open.ts) | `openConversation` / `recoverConversation`: generation-scoped read results, GET-only retries, confirmed-history retention and atomic framed paint. The per-session reset keys on `openedFrameId` (the frame whose state is on screen), not only on `currentId`, so a new session published before it is opened, or opened after Home, starts with an empty Notebook. |
| [`open.test.ts`](open.test.ts) | Generation-scoped history failures, GET-only recovery, REST/WS races and retained older pages; a new session's Notebook holds only its own cells (adopted from an open session, after Home, with a late read of the previous session). |
| [`planPrompt.ts`](planPrompt.ts) | Plan-mode rows reopened as what the user wrote: `planModeRequestText` cuts the plan-mode prompt (workbench, legacy app.js, revision seed) from a stored user row, mirroring `openai4s/server/plans.py`; `planSeed` / `planSeedMarker` render an approval or resume seed as one muted "Plan approved: …" line (feature-local copy). Stored rows are unchanged. |
| [`raf.ts`](raf.ts) | Shared `requestAnimationFrame` / setTimeout fallback. |
| [`scroll.ts`](scroll.ts) | `down` / `updateJumpPill` on one rAF; throttled scroll listener, bound after render by `bindMessageScroll`. A scroll event only measures and paints the pill; only `down()` scrolls. |
| [`scroll.test.ts`](scroll.test.ts) | A scroll event never scrolls (a reader inside the 80px pad is not snapped back); `down()` follows only while following; `down(true)` and the pill always jump. |
| [`stopped.ts`](stopped.ts) | The stopped-turn marker: a `text_chunk` or stored row carrying `cancelled` renders as one marker (feature-local copy), the still-running activity card is marked stopped through `cardState.ts` (stop glyph, and the generated "Running analysis · cell N" title becomes "Analysis · cell N"; a card whose own outcome already arrived keeps it), and a `cancelled` terminal whose chunk was missed gets the same marker. |
| [`stopped.test.ts`](stopped.test.ts) | Live marker instead of prose, stopped card (glyph, generated title replaced, the cell's own title kept) vs. a card that finished first, terminal fallback without duplicates, reopen via both stored renderers, malformed metadata stays prose. The card outcome through the real `notebook_cell_finished` handler: a cell that raised ends failed (no check, no running title), success keeps the check, an interrupted cell is stopped once, only the named cell's card is repainted, and a card with no outcome does not outlive its turn as running. Stored plan-mode rows through both renderers: the prompt's bubble is the task (en/zh), a revision's is the change request, an approval seed is a plan marker, ordinary text is untouched. |
| [`stream.ts`](stream.ts) | `feed` / `flushRender` / `scheduleRender` / `startStream` / `sealText`. `startStream` keeps the step cards still on screen registered, so a replayed turn updates the stored cards of a reopened running session instead of adding second ones. |
