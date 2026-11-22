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
| [`kernel.ts`](kernel.ts) | `_kc` invalidate、kernel/REPL/env，供 F-11 调用的 `notebookOnTurnDone`。每个会话同时只有一个 kernel 读取在途；跨过一次 invalidate 的回答会再读一次。`kernelView` 是面板渲染 kernel 状态所用的唯一不可变值；`_kc` 只整体替换，不原地修改。`syncKernel` 只在 Notebook 可见时读取 `/kernel` 与 `/environments`。徽标状态和每行操作按钮的能力都是 computed 值，Timeline 刷新或 kernel 读取不会让没变的部分重渲染。REPL 同一时间只执行一个（`replBusy`），每个会话同一时间只有一个 fork（`forkPending`，复用 `execution/branch.ts` 的 `forkFromCell`），分支建好后给出提示。复制使用 `chrome/clipboard.ts` 的 `copyText`，失败时明确提示。 `runtimeStatusText` 是运行状态徽标和行动时间线运行条共用的一套说法：未启动 / 已退出 / 已停止 / 已结束，以及按界面语言显示的就绪 / 忙碌 / 恢复中。`shownKernelSlot` 决定这些说法描述哪个槽位（SCI-35）：任一槽位存活即视为会话存活；只运行过 R 的会话按 R 槽位及其代次描述，而不是说 Python 内核从未启动。 |
| [`scroll.ts`](scroll.ts) | 跟随 + `_nbReading` / `_nbDirty` / `_nbSched`（app.js:10339-10350, 9900-9908）。`_nbReading` 只跟随 Notebook 自己的滚动：dock body 由所有面板共用。 Notebook 出现在屏幕上（或换到另一个会话）时打开在最新的单元；读者没有向上滚动时，运行中输出增长也会一直跟到底部；我们自己滚到底部不会被当成读者滚开（M-54、M-57）。 |
| [`chrome.ts`](chrome.ts) | `highlightTraceback`、`notebookExportLink`、live 图片、行内表格。`looksBinary` 与 Files 共用 `artifacts/api.ts` 那一份，不再各留一份。行内表格按 A1 严格预算读取确切版本；读取失败显示分类原因、重试与同版本下载，已 dispose 的 Cell 静默忽略被取消的读取。 导出拆分按钮的文字会随界面语言实时切换（`relabelNotebookExports`，页面上所有按钮共用一个 `onLanguageChange` 钩子）。 |
| [`Notebook.tsx`](Notebook.tsx) | CellList / chips / REPL / `renderNotebook` / `cellNode`。状态行和 REPL 头部从 `kernelView` 渲染，不再手动写 ref。CellList 只绘制渲染传给它的条目，所以阅读门控对所有调用方都生效。一个 cell 从第一个 chunk 到最终记录都是同一个组件，完成时不会重新挂载卡片。被判为二进制的输出显示带大小的省略提示。 单元状态词、分叉 / 保存为产物操作、所有者标签和单元状态都按界面语言显示（`nbT` / `nbLabelT`）。 结果仍然有效的单元不显示状态徽标；已过期的单元在徽标旁用文字说明原因，单元写成 `Python [3]`（R 单元为 `R [2]`，SCI-35）（`staleReasonWords`，M-54 / SCI-23）；变灰的「作为新单元运行 / 编辑为新单元」会说明是手动单元开关未开，或另一个手动单元正在运行。 |
| [`dock.test.tsx`](dock.test.tsx) | 在 node 里把面板组件当函数调用：二进制输出的省略提示、同一 cell key 在完成前后保持同一组件类型、CellList 不订阅任何 cell store、状态行按最近一次 kernel 读取渲染、不改变 REPL 模式的 kernel 读取不会让整个面板重渲染、操作行与 chips 只订阅自己显示的内容、请求在途时 Rerun 与 Fork 不可点。不叫 `Notebook.test.tsx`：在大小写不敏感的磁盘上它与 `notebook.test.ts` 同名，tsc 会漏掉其中一个。 徽标和行动时间线用同样的说法描述运行状态，不再出现「仅供查看」。 中文界面下所有者标签、单元状态和变量查看器显示中文。只运行 R 的会话在其 R 内核存活时显示为就绪，状态行、徽标和摘要写明 R 及其代次；Python 会话不受影响。 |
| [`install.ts`](install.ts) | WS handler + window 上的 `highlightTraceback` / `notebookExportLink`。由于每种 WS 类型只有一个处理器，`notebook_cell_finished` 同时收尾聊天区的实时活动卡片（`messages/cardState.ts`）。 智能体回合结束，或回合之外的手动单元完成时，读者加载过的变量列表会重新读取（M-77）。 |
| [`envmenu.ts`](envmenu.ts) | 输入框「会话选项」菜单里的运行环境选择：分 Python 和 R 两组并勾出正在用的，每行标「内置」或「本机环境」（`builtin: false`）；R 环境可以选。 R 分组有自己的「默认」一行：没有选 R 环境时打勾，写明是哪个 R 以及缺少哪个关键包；选过本机 R 之后可以由它切回默认（`nbUseDefaultR`，SCI-20）。 |
| [`editor.ts`](editor.ts) | REPL 输入框的编辑按键，写成作用于 textarea 值和选区的纯函数：Tab/Shift+Tab 缩进与取消缩进，光标在首行/末行时 ↑/↓ 调出历史（`ReplHistory`）。 |
| [`copy.ts`](copy.ts) | `nbT`：生成的词典里没有的 Notebook 文案（中英文）：内核崩溃提示、新内核分隔线、输出标签、带文字的「中断」按钮和进行中的内核操作。 `nb.runtime.*`：运行状态徽标的文案（生成的中文词典里这些是英文）。 `nbLabelT`（一个 `overrideLookup`）：所有者标签、单元状态和变量查看器的中文文案。 |
| [`notebook.css`](notebook.css) | 移植之后新增的 Notebook 样式（内核崩溃提示、新内核分隔线、「中断」按钮），只用 `style.css` 已有的 token。 |
| [`index.ts`](index.ts) | 对外再导出。 |
| [`notebook.test.ts`](notebook.test.ts) | 合并、重放去重、invalidate 时机、滚动门控、traceback XSS、被省略后重新出现的流式 `<pre>` 完整重绘、已完成的记录按原文完整显示、阅读时保持已绘制的列表（滚动其他面板不算）、未变化时复用投影与记录对象、kernel 读取按会话隔离并在 invalidate 后重读、Notebook 隐藏时不读取 kernel、REPL 提交与 fork 请求同一时间各只有一个、复制只在确认写入后才报成功。 已构建的导出按钮在切换语言后会重新标注。 |
