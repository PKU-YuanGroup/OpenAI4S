# frontend/src/features/messages

[English](README.md)

F-10 消息流。分帧历史绘制（每 rAF 40 条 + 一次 fragment）、流式 Markdown 双节点（sealed 前缀 + live tail）、工具输出 `textNode.appendData(delta)`、跟随滚动合并进同一个 rAF。1MB 截断仍走 F-08 的 `appendLiveOutput`；本车道只把它变成增量。window 名字（`openConversation`、`fetch*Messages`、`down`）由本模块赋值，不再留给 F-05 占位。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`cardState.ts`](cardState.ts) | 实时活动卡片的状态：新的单元卡片显示进度图标（而非成功对勾）并记录 `producing_cell_id`；`notebook_cell_finished`（由 Notebook 的处理器转发）把该卡片标为失败（x 图标、错误色边条、"失败 · N 行"）、成功或已停止；`turnDone` 把仍在运行的卡片收尾为中性的"已结束"。单元结束后，生成的 "Running analysis · cell N" 标题改为"分析 · 单元 N"，单元自带标题保留。功能内文案。 |
| [`copy.ts`](copy.ts) | 当前UI所属的双语历史恢复文案；不改自动提取的语言文件。 |
| [`components.tsx`](components.tsx) | `MessageList`（`#messages` / `#jump-pill`）、消息宿主外的可访问历史状态/重试入口，以及 `StreamingPre`。 |
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
| [`list.ts`](list.ts) | `renderStored`、`insertMessageByTime`、分帧批量绘制。计划模式的用户行渲染为用户自己的文字，计划执行种子渲染为计划标记（`planPrompt.ts`）。 |
| [`list.test.ts`](list.test.ts) | 640 条 → 16 帧 × 40；按时间插入跳过 `#msgs-earlier`。 |
| [`messages.css`](messages.css) | `.md-sealed` / `.md-tail { display: contents }`；已停止回合标记与已停止卡片（弱化图标、中性色边条）；运行中与已结束的卡片（中性图标与边条，未要求减少动效时运行图标旋转）以及失败卡片（错误色图标与边条）；重开后的计划种子行沿用已停止标记的样式。 |
| [`open.ts`](open.ts) | `openConversation` / `recoverConversation`：按代次读取、只读重试、保留已确认历史与原子分帧投影。会话级重置依据 `openedFrameId`（屏幕上状态所属的会话）而不只是 `currentId`，因此先发布 id 再打开的新会话、或从主页返回后打开的会话，Notebook 都从空开始。 |
| [`open.test.ts`](open.test.ts) | 按打开代次保护历史失败、只读重试、REST/WS 交错与已展开旧页保留；新会话的 Notebook 只含自己的单元（从已打开会话新建、从主页返回后新建、上一会话的读取迟到）。 |
| [`planPrompt.ts`](planPrompt.ts) | 计划模式行重开后显示用户自己写的内容：`planModeRequestText` 从存储的用户行中去掉计划模式提示（工作台、旧版 app.js、修订种子），与 `openai4s/server/plans.py` 一致；`planSeed` / `planSeedMarker` 把批准或继续执行的种子渲染为一行弱化的"已批准计划：…"（功能内文案）。存储的行不变。 |
| [`raf.ts`](raf.ts) | 共用 `requestAnimationFrame` / setTimeout 回退。 |
| [`scroll.ts`](scroll.ts) | `down` / `updateJumpPill` 合并进一个 rAF；节流 scroll 监听。 |
| [`stopped.ts`](stopped.ts) | 已停止回合标记：带 `cancelled` 的 `text_chunk` 或存储行渲染为同一个标记（功能内文案），仍在运行的活动卡片经 `cardState.ts` 标为已停止（停止图标，生成的 "Running analysis · cell N" 标题改为"分析 · 单元 N"；已收到自身结果的卡片保持该结果），漏收标记块的 `cancelled` 终态也补上同一标记。 |
| [`stopped.test.ts`](stopped.test.ts) | 实时渲染为标记而非正文；已停止卡片（图标、替换生成标题、保留单元自带标题）与先完成的卡片区分；终态兜底不重复；两个存储渲染器重开一致；畸形元数据仍按正文渲染。经真实 `notebook_cell_finished` 处理器验证卡片结果：抛错的单元以失败收尾（无对勾、无运行中标题），成功保留对勾，被中断的单元只标一次已停止，只重绘所指单元的卡片，未收到结果的卡片不会在回合结束后仍显示运行中。经两个存储渲染器验证计划模式行：提示行的气泡是任务（中/英），修订行是修改意见，批准种子是计划标记，普通文字不受影响。 |
| [`stream.ts`](stream.ts) | `feed` / `flushRender` / `scheduleRender` / `startStream` / `sealText`。 |
