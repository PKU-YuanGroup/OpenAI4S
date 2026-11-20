# frontend/src/features/messages

[English](README.md)

F-10 消息流。分帧历史绘制（每 rAF 40 条 + 一次 fragment）、流式 Markdown 双节点（sealed 前缀 + live tail）、工具输出 `textNode.appendData(delta)`、跟随滚动合并进同一个 rAF。1MB 截断仍走 F-08 的 `appendLiveOutput`；本车道只把它变成增量。window 名字（`openConversation`、`fetch*Messages`、`down`）由本模块赋值，不再留给 F-05 占位。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`failure.ts`](failure.ts) | 实时与重开会话共用的失败提示；流式中断或重复动作停止后提供显式继续入口。“继续”发送的固定文本也在这里：这一行在实时和重开时都显示成一条弱化的平台提示，而不是用户气泡。 |
| [`approval.test.ts`](approval.test.ts) | 标题、状态行和会话行徽标随未作答卡片及其答复变化；卡片滚入视野且不被跳转按钮遮住；卡片不在视野时跳转按钮指向它，之后恢复「最新」。 |
| [`approval.ts`](approval.ts) | 等待批准是独立状态：当前会话里未作答的卡片决定输入框状态行、标签页标题前缀、会话行徽标（其他会话行用服务端的 `awaiting_approval`）和跳转按钮，并在卡片出现、会话打开时停在卡片上、以及从首页「去批准」进入时，把卡片的按钮滚到跳转按钮上方可见处。叶子模块：不引用 `sessions/` 或 `scroll.ts`。功能内文案。 |
| [`cardState.test.ts`](cardState.test.ts) | 单元结束后卡片保留其运行时长；不到两秒的单元不显示。 |
| [`cardState.ts`](cardState.ts) | 实时活动卡片的状态：新的单元卡片显示进度图标（而非成功对勾）并记录 `producing_cell_id`；`notebook_cell_finished`（由 Notebook 的处理器转发）把该卡片标为失败（x 图标、错误色边条、"失败 · N 行"）、成功或已停止；因 Host 调用被拒绝而结束的单元标为中性的「已拒绝」（锁图标，"你拒绝了 · 未执行"），等待批准时被停止的标为已停止；`turnDone` 把仍在运行的卡片收尾为中性的"已结束"。运行中的卡片显示已运行时间（满两秒起，共用一个 1 秒计时器），结束后保留为该单元的运行时长。单元结束后，生成的 "Running analysis · cell N" 标题改为"分析 · 单元 N"，单元自带标题保留。功能内文案。生成的运行中标题也按界面语言显示（正在分析 · 单元 N），单元编号记在卡片上。 |
| [`figures.test.ts`](figures.test.ts) | 只有指向本守护进程、且文件名是图片的制品链接才会附图；数量有上限，同一项不会重复附图。 |
| [`figures.ts`](figures.ts) | ART-8：消息渲染后，指向本守护进程图像制品（`/api/v1/artifacts/...`）的链接下方会显示该图，每条消息最多三张，让完成总结直接展示关键图，而不只是给链接。 |
| [`copy.ts`](copy.ts) | 当前UI所属的双语历史恢复与“会话不存在”文案；不改自动提取的语言文件。 |
| [`declined.ts`](declined.ts) | 拒绝不是失败：识别被拒绝的 Host 调用（步骤的 `output.error` "Permission denied: …"，或单元最后抛出的 "bash: / host.X error: Permission denied: …"，从不把系统 `PermissionError` 算进来），区分你拒绝的、规则拒绝的、等待批准时被「停止」取消的和审批超时的，并用界面语言给出状态与原因（功能内文案）。 |
| [`declined.test.ts`](declined.test.ts) | 步骤与单元的拒绝分类、卡片上填写的原因算作你的拒绝、系统 `PermissionError` 与被捕获后又失败的单元仍算失败、中英文文案。 |
| [`components.tsx`](components.tsx) | `HistoryLoadStatus`：可访问的历史状态/重试入口，由 Shell 挂在命令式消息宿主之外。 |
| [`cut.ts`](cut.ts) | 增量 `_mdStableCut` / `mdStableCut`（app.js:5378-5402）。 |
| [`cut.test.ts`](cut.test.ts) | 增量扫描与从零扫描同结果；围栏 / 120 字软尾。 |
| [`delta.ts`](delta.ts) | `liveOutputDelta`、`bindStreamingPre`（`appendData`；每块只检查该块本身，经 `liveOutputIncrement`）、`toolMetaLabel`（功能内文案：按界面语言显示 "N 行" / "完成"）。 |
| [`delta.test.ts`](delta.test.ts) | 1MB 截断幂等；换行只数增量；`append` 不搜索也不切片已有输出；元信息行的中英文。 |
| [`dom.ts`](dom.ts) | `$` / `el` / `#messages` / `ensureMessageDom`（Shell 渲染后是空操作；不得在 `render()` 之前调用）；`keepStripsLast` 在回合新增内容时让会话的两条文件条（“本会话输入 / 本会话产物”）始终排在转录末尾，两条遵循同一条位置规则。 |
| [`fetch.ts`](fetch.ts) | `fetchRecentMessages` / `fetchOlderMessages` / `fetchAllMessages`（6926-6961）。 |
| [`handlers.ts`](handlers.ts) | `text_reset` / `text_chunk` / `user_message` WS handler。 |
| [`handlers.test.ts`](handlers.test.ts) | mine / 陈旧 turn 守卫；重复注册幂等。 |
| [`identity.ts`](identity.ts) | 候选身份与 feed 边界上的 `storedCandidateOwnsChunk`。 |
| [`index.ts`](index.ts) | 对外导出；`installMessages` 用 `isReady` 往 window 赋值，不创建任何 DOM（它在 Shell 渲染之前运行）。 |
| [`install.test.ts`](install.test.ts) | 契约名字是真实现（`isReady`），不是 F-05 占位；安装时不会抢在 Shell 之前创建 `#messages`。 |
| [`list.ts`](list.ts) | 唯一的已存消息行实现，首屏、"加载更早"（经 `sessions/transcript.ts`）与实时回合共用：`renderStored`、`addMsgActions`（复制走 `copyText`、👍/👎 带已保存评价、编辑）、`renderMessageRefChips`、`renderEmptySession`、`insertMessageByTime`、`showLiveUserMessage`（`user_message` 事件带来的回合提问：认领本标签页的气泡、已渲染的行不重复，否则显示）、`renderMissingSession`（指向不存在会话的链接：说明与返回入口）；以及分帧批量绘制，其中 `interleaveHistory` 也按时间放置已存储的审批决定。计划模式的用户行渲染为用户自己的文字，计划执行种子渲染为计划标记（`planPrompt.ts`）。用户行里文本形式的 `@name#v-<id>` 显示为 `@name`，版本由下方的引用芯片给出（存储的行不变）。`@project:<路径>` 显示为 `@<路径>` 并在气泡下给出文件芯片（`renderProjectRefChips`，实时气泡同样调用）。 |
| [`list.test.ts`](list.test.ts) | 640 条 → 16 帧 × 40；按时间插入跳过 `#msgs-earlier`；首屏与更早页是同一实现：审阅徽章与候选身份、👍/👎 提交并显示已保存评价、复制只在确认写入后打勾、编辑与起始问题会撑高输入框、用户行显示 @ 引用芯片。 |
| [`messages.css`](messages.css) | `.md-sealed` / `.md-tail { display: contents }`；已停止回合标记与已停止卡片（弱化图标、中性色边条）；运行中与已结束的卡片（中性图标与边条，未要求减少动效时运行图标旋转）以及失败卡片（错误色图标与边条）；被拒绝的单元与步骤（中性图标）；重开后的计划种子行沿用已停止标记的样式；被拒审批卡片的原因行；历史状态提示位于转录内容列内；“会话不存在”面板的按钮。；用户行的 @ 引用芯片单独占一行，位于气泡下方。 |
| [`open.ts`](open.ts) | `openConversation` / `recoverConversation`：按代次读取、只读重试、保留已确认历史与原子分帧投影。会话级重置依据 `openedFrameId`（屏幕上状态所属的会话）而不只是 `currentId`，因此先发布 id 再打开的新会话、或从主页返回后打开的会话，Notebook 都从空开始。Notebook REPL 单元的步骤（`input.origin: "notebook"`）与实时时一样不进入转录，空会话欢迎页只在聊天中什么都没有时显示。列表中没有的会话会被查询（`GET /frames/{id}`）：属于其他项目的会话会把地址、项目名与侧栏移到该项目；根本不存在的会话显示“会话不存在”及返回入口（项目、首页），输入框禁用且不订阅。 |
| [`open.test.ts`](open.test.ts) | 指向不存在会话的链接会如实说明（不读历史、不订阅），项目 id 错误的链接会转到会话所属的项目；Notebook 单元的步骤不进入聊天（智能体步骤会取代欢迎页）；按打开代次保护历史失败、只读重试、REST/WS 交错与已展开旧页保留；新会话的 Notebook 只含自己的单元（从已打开会话新建、从主页返回后新建、上一会话的读取迟到）。 |
| [`planPrompt.ts`](planPrompt.ts) | 计划模式行重开后显示用户自己写的内容：`planModeRequestText` 从存储的用户行中去掉计划模式提示（工作台、旧版 app.js、修订种子），与 `openai4s/server/plans.py` 一致；`planSeed` / `planSeedMarker` 把批准或继续执行的种子渲染为一行弱化的"已批准计划：…"（功能内文案）。存储的行不变。 |
| [`raf.ts`](raf.ts) | 共用 `requestAnimationFrame` / setTimeout 回退。 |
| [`scroll.ts`](scroll.ts) | `down` / `updateJumpPill` 合并进一个 rAF；节流 scroll 监听，由 `bindMessageScroll` 在渲染后绑定。scroll 事件只测量并刷新跳转按钮，只有 `down()` 会滚动。只有读者向上滚动才会停止跟随；`ResizeObserver` 监视转录中的每一行，内容自行变高（图片加载、画廊填充、步骤卡展开）时让跟随中的视图保持在底部。有待批准卡片不在视野内时，跳转按钮指向它（「待批准」，其 `data-i18n` 暂存起来，切换语言的重绘不会改回「最新」），点击把卡片带回视野。 |
| [`scroll.test.ts`](scroll.test.ts) | scroll 事件从不滚动（位于 80px 容差内的读者不会被拉回底部）；`down()` 只在跟随时滚动；`down(true)` 与跳转按钮总会跳到底部；内容自行变高不会停止跟随（只有向上滚动会），变高的行让跟随中的视图留在底部。 |
| [`stopped.ts`](stopped.ts) | 已停止回合标记：带 `cancelled` 的 `text_chunk` 或存储行渲染为同一个标记（功能内文案），仍在运行的活动卡片经 `cardState.ts` 标为已停止（停止图标，生成的 "Running analysis · cell N" 标题改为"分析 · 单元 N"；已收到自身结果的卡片保持该结果），漏收标记块的 `cancelled` 终态也补上同一标记。 |
| [`stopped.test.ts`](stopped.test.ts) | 实时渲染为标记而非正文；已停止卡片（图标、替换生成标题、保留单元自带标题）与先完成的卡片区分；终态兜底不重复；两个存储渲染器重开一致；畸形元数据仍按正文渲染。经真实 `notebook_cell_finished` 处理器验证卡片结果：抛错的单元以失败收尾（无对勾、无运行中标题），成功保留对勾，被中断的单元只标一次已停止，只重绘所指单元的卡片，未收到结果的卡片不会在回合结束后仍显示运行中。经两个存储渲染器验证计划模式行：提示行的气泡是任务（中/英），修订行是修改意见，批准种子是计划标记，普通文字不受影响。 中文界面下，生成的运行中标题在单元运行时和结束后都是中文。 |
| [`stream.ts`](stream.ts) | `feed` / `flushRender` / `scheduleRender` / `startStream` / `sealText`。已稳定的文字只渲染一次，按块追加到 sealed 节点；每次只重渲染尾部（仍可能被后续列表项延续的列表留在尾部）；无法稳定的尾部（未闭合的代码块）越长，两次刷新的间隔越大，每秒的重渲染工作量保持不变。`startStream` 保留仍在页面上的步骤卡片登记，重开运行中的会话时，重放的回合会更新已存的卡片，而不是再添一张；`placeInLiveStream` 把卡片放在回合的当前位置（之前的正文封存在其上方），`adoptIntoLiveStream` 把这样的已存卡片移入重放回合中其事件所在的位置。 |
| [`stream.test.ts`](stream.test.ts) | 流式回答显示的内容与整段渲染完全一致，同时每个已稳定的段落只渲染一次（线性工作量）；松散列表不会被拆开；不断增长的未闭合代码块每秒只刷新几次而不是二十次；最终渲染是整段文字；被替换的回答在新节点里重新开始。 |
| [`sync.test.ts`](sync.test.ts) | 跨标签页、刷新与重连的同一份转录（文件内最小 DOM）：`user_message` 在其他标签页显示提问、发送页按 `X-Request-Id` 认领自己的乐观气泡、刷新已渲染的行不再重复；已存储的审批与消息、步骤按时间交错，并按结果渲染（拒绝及其原因、超时、仍可作答的待审批卡片）；出现 gap 的重连保留游标，重发的回合不会再追加一遍，新的守护进程 epoch 则清空游标；离线期间已处理的审批在重放结束后或点击过期的允许时收尾；重开运行中的会话会把已存的步骤与审批放回它们原本所在的正文之间，实时审批像步骤一样分隔正文；其他标签页的拒绝会显示在那里填写的原因；切换语言时已决定与待审批的卡片原位重新翻译；“会话不存在”面板提供返回项目与首页（项目也未知时只提供首页）。一轮回复在实时与重开后都是一个容器：同一轮分别经实时事件与历史绘制，子节点与复制的文字一致；被停止的一轮以停止标记收尾；运行中一轮的已存卡片没有操作栏，重放会把它们接管过去（并保持跟随）；更早的一页并入它所属的回合。 |
| [`turnStatus.ts`](turnStatus.ts) | 对话里紧跟实时回合的状态行：正在思考 / 正在编写单元（已 N 行）/ 正在运行单元 / 等待工具返回 / 等待你的审批，并显示该阶段已用时间。阶段由 `running`、Notebook 的实时单元、未处理的审批卡片和实时回合内仍在运行的步骤卡片推导；`syncTurnStatus` 负责重绘（显示期间一个 1 秒计时器，回合结束即停），阶段文字是 polite 的 live region，计数部分为 `aria-hidden`。`notePassingHint` 标记输入框下只描述某一时刻的提示（“正在恢复…”“开始处理你排队的消息”）；进入下一阶段或 8 秒后改回“运行中…”。文案在本模块内；由 `sync.test.ts` 覆盖。 |
| [`turnGroup.ts`](turnGroup.ts) | 一轮助手回复是一个 `.msg.assistant`：按发生顺序排列的正文块与卡片，加一条操作栏。`openTurnGroup` / `placeCardInTurn` 由实时流（`stream.ts`）和已存转录（`list.ts` 的 `historyTurns`，把已存的行归并成回合，首屏与经 `prependHistory` 的“加载更早”共用）调用；`dropEmptyProse`、`dropTurnGroupIfEmpty` 与 `turnTextKey`（评价所用、去掉空白差异的文字）负责收尾。`list.ts` 的 `finishTurnGroup` 给结束的一轮加上唯一的复制 / 评价栏，实时（`send/turn.ts`）与重开后相同。 |
