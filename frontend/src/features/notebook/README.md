# frontend/src/features/notebook

[中文说明](README_zh.md)

F-14 Notebook dock. Cell merge and the live cell protocol are ported from `app.js` (9765-9910). Rendering no longer clears `#dock-notebook` on every chunk: CellList is keyed by `producing_cell_id`, a chunk writes only that cell's output signal (text node append), and finished cells are memoized. Kernel chips and the REPL/status header render apart from the list.

`_seenChunks` replay dedup, the three `_kc` invalidations (`kernel_status` / `turnDone` / `nbSwitchEnv`), and the 120px scroll-follow / reading-delay gate are kept verbatim.

## Files

| File | Responsibility |
| --- | --- |
| [`types.ts`](types.ts) | `NotebookCell` / kernel status / scroll box types. |
| [`labels.ts`](labels.ts) | `kernelLabel` / `kernelIdFromEnv` (app.js:10063-10075). |
| [`cells.ts`](cells.ts) | Merge, draft/start/chunk/finished, `_seenChunks`, per-cell output signals, `loadExecutionLog`, `paintStreamedText` (a new `<pre>` starts from zero), `notebookViewEntries` (the list a render may paint under the reading gate). Unchanged finished records keep their projected object, across execution-log reloads too, so the memoized cell view skips them. |
| [`kernel.ts`](kernel.ts) | `_kc` invalidate, kernel/REPL/env, `notebookOnTurnDone` for F-11. One kernel read in flight per session; an answer that crossed an invalidation is read again. `kernelView` is the one immutable value the dock renders kernel state from; `_kc` is replaced, never edited in place. `syncKernel` reads `/kernel` and `/environments` only while the Notebook is on screen. The badge mode and each action row's capabilities are computed values, so a Timeline refresh or kernel read re-renders nothing that did not change. One REPL execution at a time (`replBusy`) and one fork per session (`forkPending`, reusing `execution/branch.ts` `forkFromCell`), with a hint when the branch exists. |
| [`scroll.ts`](scroll.ts) | Follow + `_nbReading` / `_nbDirty` / `_nbSched` (app.js:10339-10350, 9900-9908). |
| [`chrome.ts`](chrome.ts) | `highlightTraceback`, `notebookExportLink`, live figures, inline tables. `looksBinary` is the one Files uses (`artifacts/api.ts`), not a copy. |
| [`Notebook.tsx`](Notebook.tsx) | CellList / chips / REPL / `renderNotebook` / `cellNode`. The status line and REPL header render from `kernelView`, not refs painted by hand. CellList paints only the entries a render hands it, so the reading gate holds for every caller. One cell component from first chunk to final record, so completion never remounts a card. An output judged binary shows the elision notice with its size. |
| [`dock.test.tsx`](dock.test.tsx) | Dock components called as functions in node: the binary-output notice, one component type per cell key across completion, a CellList that subscribes to no cell store, the status line rendered from the last kernel read, a kernel read that leaves the REPL mode alone not re-rendering the dock, action rows and chips subscribed only to what they show, Rerun and Fork disabled while their request is out. Not `Notebook.test.tsx`: on a case-insensitive disk tsc would drop it beside `notebook.test.ts`. |
| [`install.ts`](install.ts) | WS handlers + window `highlightTraceback` / `notebookExportLink`. `notebook_cell_finished` also settles the chat's live activity card (`messages/cardState.ts`), since each WS type has one handler. |
| [`index.ts`](index.ts) | Public re-exports. |
| [`notebook.test.ts`](notebook.test.ts) | Merge, replay dedup, invalidate timings, scroll gate, traceback XSS, a streaming `<pre>` repainted in full after it was elided, a finished record shown exactly, the painted list held while reading, projections and records reused when unchanged, kernel reads per session and across an invalidation, no kernel reads while the Notebook is hidden, one REPL submission and one fork request at a time. |
