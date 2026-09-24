# frontend/src/features/notebook

[中文说明](README_zh.md)

F-14 Notebook dock. Cell merge and the live cell protocol are ported from `app.js` (9765-9910). Rendering no longer clears `#dock-notebook` on every chunk: CellList is keyed by `producing_cell_id`, a chunk writes only that cell's output signal (text node append), and finished cells are memoized. Kernel chips and the REPL/status header render apart from the list.

`_seenChunks` replay dedup, the three `_kc` invalidations (`kernel_status` / `turnDone` / `nbSwitchEnv`), and the 120px scroll-follow / reading-delay gate are kept verbatim.

## Files

| File | Responsibility |
| --- | --- |
| [`types.ts`](types.ts) | `NotebookCell` / kernel status / scroll box types. |
| [`labels.ts`](labels.ts) | `kernelLabel` / `kernelIdFromEnv` (app.js:10063-10075). |
| [`cells.ts`](cells.ts) | Merge, draft/start/chunk/finished, `_seenChunks`, per-cell output signals, `loadExecutionLog`, `paintStreamedText` (a new `<pre>` starts from zero). |
| [`kernel.ts`](kernel.ts) | `_kc` invalidate, kernel/REPL/env, `notebookOnTurnDone` for F-11. |
| [`scroll.ts`](scroll.ts) | Follow + `_nbReading` / `_nbDirty` / `_nbSched` (app.js:10339-10350, 9900-9908). |
| [`chrome.ts`](chrome.ts) | `highlightTraceback`, `notebookExportLink`, live figures, inline tables. `looksBinary` is the one Files uses (`artifacts/api.ts`), not a copy. |
| [`Notebook.tsx`](Notebook.tsx) | CellList / chips / REPL / `renderNotebook` / `cellNode`. An output judged binary shows the elision notice with its size. |
| [`Notebook.test.tsx`](Notebook.test.tsx) | Dock components called as functions in node: the binary-output notice. |
| [`install.ts`](install.ts) | WS handlers + window `highlightTraceback` / `notebookExportLink`. `notebook_cell_finished` also settles the chat's live activity card (`messages/cardState.ts`), since each WS type has one handler. |
| [`index.ts`](index.ts) | Public re-exports. |
| [`notebook.test.ts`](notebook.test.ts) | Merge, replay dedup, invalidate timings, scroll gate, traceback XSS, a streaming `<pre>` repainted in full after it was elided. |
