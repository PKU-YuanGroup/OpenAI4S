# frontend/src/features/notebook

[English](README.md)

F-14 Notebook 面板。Cell 合并与 live 协议从 `app.js`（9765-9910）移植。渲染不再在每个 chunk 上清空 `#dock-notebook`：CellList 按 `producing_cell_id` 键控，chunk 只写对应 cell 的输出 signal（text node 追加），已完成 cell 做 memo。Kernel chips 与 REPL/状态条和列表分开渲染。

`_seenChunks` 重放去重、三处 `_kc` invalidate（`kernel_status` / `turnDone` / `nbSwitchEnv`）、以及 120px 滚动跟随与阅读延迟门控均逐字保留。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`types.ts`](types.ts) | `NotebookCell` / kernel 状态 / 滚动容器类型。 |
| [`labels.ts`](labels.ts) | `kernelLabel` / `kernelIdFromEnv`（app.js:10063-10075）。 |
| [`cells.ts`](cells.ts) | 合并、draft/start/chunk/finished、`_seenChunks`、每 cell 输出 signal、`loadExecutionLog`、`paintStreamedText`（新的 `<pre>` 从零开始）、`notebookViewEntries`（阅读门控下一次渲染可以绘制的列表）。未变化的已完成记录沿用同一个投影对象（执行日志重新加载后也是），memo 的 cell 视图因此可以跳过它们。 |
| [`kernel.ts`](kernel.ts) | `_kc` invalidate、kernel/REPL/env，供 F-11 调用的 `notebookOnTurnDone`。每个会话同时只有一个 kernel 读取在途；跨过一次 invalidate 的回答会再读一次。`kernelView` 是面板渲染 kernel 状态所用的唯一不可变值；`_kc` 只整体替换，不原地修改。`syncKernel` 只在 Notebook 可见时读取 `/kernel` 与 `/environments`。徽标状态和每行操作按钮的能力都是 computed 值，Timeline 刷新或 kernel 读取不会让没变的部分重渲染。REPL 同一时间只执行一个（`replBusy`），每个会话同一时间只有一个 fork（`forkPending`，复用 `execution/branch.ts` 的 `forkFromCell`），分支建好后给出提示。复制使用 `chrome/clipboard.ts` 的 `copyText`，失败时明确提示。 |
| [`scroll.ts`](scroll.ts) | 跟随 + `_nbReading` / `_nbDirty` / `_nbSched`（app.js:10339-10350, 9900-9908）。 |
| [`chrome.ts`](chrome.ts) | `highlightTraceback`、`notebookExportLink`、live 图片、行内表格。`looksBinary` 与 Files 共用 `artifacts/api.ts` 那一份，不再各留一份。 |
| [`Notebook.tsx`](Notebook.tsx) | CellList / chips / REPL / `renderNotebook` / `cellNode`。状态行和 REPL 头部从 `kernelView` 渲染，不再手动写 ref。CellList 只绘制渲染传给它的条目，所以阅读门控对所有调用方都生效。一个 cell 从第一个 chunk 到最终记录都是同一个组件，完成时不会重新挂载卡片。被判为二进制的输出显示带大小的省略提示。 |
| [`dock.test.tsx`](dock.test.tsx) | 在 node 里把面板组件当函数调用：二进制输出的省略提示、同一 cell key 在完成前后保持同一组件类型、CellList 不订阅任何 cell store、状态行按最近一次 kernel 读取渲染、不改变 REPL 模式的 kernel 读取不会让整个面板重渲染、操作行与 chips 只订阅自己显示的内容、请求在途时 Rerun 与 Fork 不可点。不叫 `Notebook.test.tsx`：在大小写不敏感的磁盘上它与 `notebook.test.ts` 同名，tsc 会漏掉其中一个。 |
| [`install.ts`](install.ts) | WS handler + window 上的 `highlightTraceback` / `notebookExportLink`。由于每种 WS 类型只有一个处理器，`notebook_cell_finished` 同时收尾聊天区的实时活动卡片（`messages/cardState.ts`）。 |
| [`index.ts`](index.ts) | 对外再导出。 |
| [`notebook.test.ts`](notebook.test.ts) | 合并、重放去重、invalidate 时机、滚动门控、traceback XSS、被省略后重新出现的流式 `<pre>` 完整重绘、已完成的记录按原文完整显示、阅读时保持已绘制的列表、未变化时复用投影与记录对象、kernel 读取按会话隔离并在 invalidate 后重读、Notebook 隐藏时不读取 kernel、REPL 提交与 fork 请求同一时间各只有一个、复制只在确认写入后才报成功。 |
