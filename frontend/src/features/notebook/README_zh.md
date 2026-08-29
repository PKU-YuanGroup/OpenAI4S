# frontend/src/features/notebook

[English](README.md)

F-14 Notebook 面板。Cell 合并与 live 协议从 `app.js`（9765-9910）移植。渲染不再在每个 chunk 上清空 `#dock-notebook`：CellList 按 `producing_cell_id` 键控，chunk 只写对应 cell 的输出 signal（text node 追加），已完成 cell 做 memo。Kernel chips 与 REPL/状态条和列表分开渲染。

`_seenChunks` 重放去重、三处 `_kc` invalidate（`kernel_status` / `turnDone` / `nbSwitchEnv`）、以及 120px 滚动跟随与阅读延迟门控均逐字保留。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`types.ts`](types.ts) | `NotebookCell` / kernel 状态 / 滚动容器类型。 |
| [`labels.ts`](labels.ts) | `kernelLabel` / `kernelIdFromEnv`（app.js:10063-10075）。 |
| [`cells.ts`](cells.ts) | 合并、draft/start/chunk/finished、`_seenChunks`、每 cell 输出 signal、`loadExecutionLog`。 |
| [`kernel.ts`](kernel.ts) | `_kc` invalidate、kernel/REPL/env，供 F-11 调用的 `notebookOnTurnDone`。 |
| [`scroll.ts`](scroll.ts) | 跟随 + `_nbReading` / `_nbDirty` / `_nbSched`（app.js:10339-10350, 9900-9908）。 |
| [`chrome.ts`](chrome.ts) | `highlightTraceback`、`notebookExportLink`、live 图片、行内表格。 |
| [`Notebook.tsx`](Notebook.tsx) | CellList / chips / REPL / `renderNotebook` / `cellNode`。 |
| [`install.ts`](install.ts) | WS handler + window 上的 `highlightTraceback` / `notebookExportLink`。 |
| [`index.ts`](index.ts) | 对外再导出。 |
| [`notebook.test.ts`](notebook.test.ts) | 合并、重放去重、invalidate 时机、滚动门控、traceback XSS。 |
