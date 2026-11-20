# frontend/src/features/sessions

[English](README.md)

F-13 仪表盘 / 项目 / 会话。分页与排序是纯函数。窗口契约名（`fetchAllMessages`、`fetchOlderMessages`、`fetchRecentMessages`、`openConversation`、`renderMessageRefChips`、`renderComposerRefChips`）由本模块赋值。能力判定走 `compat/stub.ts` 的 `isReady`——本目录不 import `window-exports.ts`。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`actions.ts`](actions.ts) | 会话菜单、导入导出、标题、取消。app.js:7411-7793。会话选项菜单里有运行环境选择（`notebook/envmenu.ts`）。 |
| [`api.ts`](api.ts) | `API`、`ApiError`、`api()`、`apiErrorText`。app.js:84-119。 |
| [`binds.ts`](binds.ts) | 迟绑定，避免 dashboard 与 conversation 互相 import。 |
| [`boot.ts`](boot.ts) | window 导出、`setLoadSessionsImpl`、工作台点击接线。Shell 的事件立即绑定，但首个视图要等语言分块加载完成（或加载失败、或超过 `I18N_ROUTE_WAIT_MS`）才路由：仪表盘列表、侧栏和打开的会话经 `t()` 渲染，字典到达后不会重绘。若等待超时，这些列表（仪表盘、侧栏、空会话）会在字典真正到达时重绘一次。切换语言时（app.js `rerenderI18n`）用已有数据重绘仪表盘列表、项目菜单、侧栏、未命名标题、空会话、输入框占位符（运行中的回合保留排队文案）、排队条、转录中的文件条和右侧面板，不发新请求。 |
| [`boot.i18n-gate.test.ts`](boot.i18n-gate.test.ts) | 字典加载前事件已绑定，首次路由等待字典；语言分块加载失败或卡住时仍会路由；等待超时后才到达的字典会重绘列表（仅此情形）。只有真正切换语言时，才会用已有数据重绘列表；回合运行中切换语言时输入框保留排队占位符。 |
| [`chrome.test.ts`](chrome.test.ts) | hint 错误前缀（`错误：` / `Error: `），不新增 i18n 键；`reportFailure` 把失败的「发起即不管」操作显示为错误提示；`watchActivateKeys` 只观察右侧标签栏、文件面板和对话里的产物卡片条，而不是整个 body。 |
| [`chrome.ts`](chrome.ts) | `hint`、`reportFailure`（「发起即不管」操作的失败处理）、断连横幅、`openMenu` 的 Esc/`role=menu`、键盘激活（只观察会插入可激活节点的容器）。 |
| [`compute.ts`](compute.ts) | 会话在哪里运行（M3b-6）：会话标题栏的徽标（写明尚未满足的就绪条件）、内核状态丢失横幅（INV-11），以及「运行位置」对话框。app.js:8375-8517。 |
| [`compute.test.ts`](compute.test.ts) | 集群会话的徽标写明它在等什么，本地会话没有徽标；内核状态丢失会一直提示到用户确认，再次丢失会再次提示；已切走的会话不会被绘制；「运行位置」列出本机与已配置的规格，并请求所选的那一个。 |
| [`conversation.ts`](conversation.ts) | `newSession`、`routeInitialView`。`openConversation`（F-10）与 `resumeWatch`（F-11）改为 re-export，不再保留本车道的副本。 |
| [`conversation.identity.test.ts`](conversation.identity.test.ts) | 断言这些 re-export 与拥有车道装上的是同一个函数对象。 |
| [`conversation.newsession.test.ts`](conversation.newsession.test.ts) | `newSession` 在发布新 id 之前先释放上一个对话（取消订阅、Notebook 缓存）；共享路径只在对话真正打开后才 resolve；只有 `reuseEmpty` 请求才会让服务器复用未使用的会话。 |
| [`actions.menu.test.ts`](actions.menu.test.ts) | 菜单项都接到真实实现：「存为技能」以当前对话为种子打开设置里的技能编辑器，「运行位置」打开运行位置对话框，项目研究视图的时间线把各会话的动作组画成卡片，上下文用量卡片用当前语言标注。 |
| [`actions.directory.test.ts`](actions.directory.test.ts) | 会话菜单操作让侧栏目录保持真实：「新建文件夹并移入」建的文件夹会被列出，而不是被缓存的文件夹列表挡掉；删除当前会话后即使随后的列表刷新失败，也不会重新打开被删的会话。 |
| [`actions.cancel.test.ts`](actions.cancel.test.ts) | 取消回执只有在它命名的执行仍是本客户端正在运行的那个时，才切换到「正在停止…」。 |
| [`dashboard.ts`](dashboard.ts) | 首页列表、项目搜索 / 加载更多 / 重试、示例 CTA 轮询绑视图生命周期，以及仪表盘唯一的 4 秒轮询，其他功能经 `onDashPoll` 搭载。 每个会话只列一次：等你决定的只在「需要处理」里，运行中的只在「运行中」卡片里，其余在「最近会话」（其他会话都在上方时会说明，而不是显示「还没有会话」）；运行中卡片的副标题是项目名，任务摘要只在比标题多说了内容时才显示。示例分析会保留一条状态提示（运行中，之后是已完成或「6 个单元中有 N 个没有完成」，来自 `/example/session` 的 `result`），带打开会话的按钮；示例按钮是有样式的主按钮。 |
| [`dashboard.sessions.test.ts`](dashboard.sessions.test.ts) | 仪表盘轮询用一个定时器驱动「运行中」卡片和所有搭载者，启动时立即读取一次。仪表盘的会话列表与示例 CTA：`/frames` 读取失败时如实提示并提供重试，保留上次读到的行，而不是显示没有会话并推荐示例；无论有多少次重绘与它的首次状态读取竞争，CTA 的状态轮询都只跑一份，离开仪表盘之后也不会再启动。 等待审批的会话只出现在「需要处理」，运行中的只出现在「运行中」；「最近会话」会说明会话都在上方，而不是提供示例；运行中卡片的副标题不重复标题；示例分析的状态提示跟随运行，并说明有几个单元没有完成。 种子示例项目的名称和标签跟随界面语言。 |
| [`dom.ts`](dom.ts) | `$` / `el` / `ago` / `navURL` / composer 辅助；`FRAME_ROUTE` / `PROJECT_ROUTE` / `routesToWorkspace` 由 `routeInitialView` 与 Shell 首帧共用。`setTitle` 从静态 `data-i18n-val` 标签手中接管 `#conv-title`。 `ago` 使用共享的、随界面语言变化的相对时间（`chrome/dom.ts`）。 |
| [`icon.ts`](icon.ts) | 本车道菜单、行和 `[data-icon]` 标记用的 `icon` / `iconEl` / `paintIcons`。图形路径取自共享的 `icons/paths.ts` 表。 |
| [`index.ts`](index.ts) | 对外 re-export；import 时挂 window 名字。 |
| [`lane.ts`](lane.ts) | 用 `isReady` 包一层，调用后续车道的 window 名字。 |
| [`load.ts`](load.ts) | `loadSessions` 游标走页；`loadProjects` keyset 分页（不发 `offset`），项目目录与仪表盘搜索各自分页；文件夹、`renderSessions`。 |
| [`dashboard.projects.test.ts`](dashboard.projects.test.ts) | 非整页加载的重绘之后项目卡片显示什么，以及项目目录里存着什么。运行中徽标依据仪表盘最近取到的 frame 标注——包含 4 秒轮询取到的那批，否则重绘会在刚被该轮询清空的「运行中」卡片旁边画出「1 running」。搜索有自己的结果页，从不替换页眉、切换器和各处标签所读的目录，所以被搜索框筛掉的项目在「最近」里仍显示名称；目录刷新失败时保留原目录；从项目行打开项目失败时会提示。 |
| [`load.folders.test.ts`](load.folders.test.ts) | 服务端拒绝的文件夹重命名或删除（通常是 `404`，即文件夹已在另一个标签页被删除）会被报告出来，并且两个列表照样重新读取，让过期的那一行从侧栏消失。此前这个拒绝在重新读取之前就被吞掉，于是那一行一直留着，菜单项点了也没有任何反应。 |
| [`load.render.test.ts`](load.render.test.ts) | 一次会话读取只重建侧栏两次（加载状态，以及两个列表都就绪之后），不会再因它带动的文件夹读取多重建一次。 |
| [`load.replace.test.ts`](load.replace.test.ts) | 防抖搜索还在等回复时点击「加载更多」会被拒绝：此前它会拿到更新的代号却带着**旧**查询和旧游标发请求，搜索回复因此被当作过期丢弃，旧筛选的第二页落在了新输入的搜索框下面。 |
| [`load.projects.test.ts`](load.projects.test.ts) | 项目列表查询串不含 `offset`；合并/去重；空态 / 重试 / 加载更多的视图状态。 |
| [`messages.ts`](messages.ts) | `fetchRecentMessages` / `fetchOlderMessages` / `fetchAllMessages` / 更早消息条。 |
| [`package.ts`](package.ts) | 会话包导入对话框（文件、校验、校验结果、导入，然后打开会话或给出导入器拒绝的原因；TEAM-20），以及会话包 / 产物 zip 的导出：先取回字节，被拒绝时显示原因，完成时说明保存了什么（TEAM-13）。`flash` 是会自动消失的状态行。 |
| [`package.test.ts`](package.test.ts) | 导入被拒绝时在对话框里显示（而不只写进隐藏的输入框提示），校验失败不会导入，校验通过则打开会话；导出会话包报告大小，409 会被报告而不是被下载。 |
| [`markdown-export.ts`](markdown-export.ts) | “导出为 Markdown”（TEAM-12）：每个回合一节，按执行顺序附上每个单元的源码和截断后的输出，图片产物以 data URI 内嵌在产生它的单元下，站内链接只保留文字。 |
| [`markdown-export.test.ts`](markdown-export.test.ts) | 每个回合一个标题、代码与输出按顺序且不含完成调用、图片内嵌在其单元下且不留 `/api/` 链接、长输出被截断、只在预算内内嵌图片。 |
| [`paging.test.ts`](paging.test.ts) | 分页常量、会话排序、走页/去重、仪表盘过滤。 |
| [`paging.ts`](paging.ts) | `MESSAGE_PAGE_SIZE=300`、`SESSION_MAX_PAGES=50`、排序/走页/过滤。 |
| [`navigation.ts`](navigation.ts) | 视图代际与同步的目录清理。刻意不承担列表读取归属：列表读取以所属项目为界。 |
| [`copy.ts`](copy.ts) | 目录读取失败与重试、操作失败提示、输入框菜单标签（上下文用量、审阅中、开/关）、分享对话框、本地项目文件夹和本地历史标签的双语文案。 `dashCopy` 存放首页示例分析状态提示和「会话都在上方」这一行。 `displayProjectName`：种子示例项目在保持原名「Example project」时按界面语言显示。 |
| [`load.navigation.test.ts`](load.navigation.test.ts) | 会话/文件夹/分页乱序、按项目划分的读取归属、读取失败及自动打开归属。 |
| [`actions.export.test.ts`](actions.export.test.ts) | Markdown 导出要求读取成功及结构有效，并固定会话标题。 |
| [`projects.navigation.test.ts`](projects.navigation.test.ts) | 已过期的项目导航不能覆盖当前会话及会话/文件夹列表；菜单筛选会取消待处理的项目打开（包含 A→B→A 重复筛选），该次打开随即把视图交还——重新加载被它在入口处退役了读取的那个会话；若工作区已显示而没有会话，则打开菜单所选的项目；当前导航将所有权交给其会话。列表读取以所属项目为界，而不是视图代际：同一项目的刷新即使被打开会话或回到 Home 抢先，也照样生效。没有列出任何会话的项目会请求复用未使用的会话。 |
| [`share.test.ts`](share.test.ts) | 只有剪贴板确认写入后，复制才提示“已复制”；写入被拒绝时如实报告失败，并选中链接以便手动复制。中转服务器未连接时，在链接上方（以及创建之前）说明并附最近的错误；毫秒时间戳的 `expires_at` 按日期显示；显示快照的条目数；撤销在对话框内确认，不用 `confirm()`。 |
| [`share.ts`](share.ts) | 分享对话框：创建、复制、更新或撤销会话的只读链接。app.js:7560-7697。显示中转隧道是否连接（打开期间每 4 秒重读）、快照发布了什么，更新后对话框保持打开。 |
| [`rejections.test.ts`](rejections.test.ts) | 「发起即不管」的操作失败时会提示，而不是留下未处理的 rejection：会话行菜单、项目菜单的导入与下载项，以及会话刷新触发的仪表盘重载。 |
| [`projects.ts`](projects.ts) | 项目菜单/模态/研究视图（其时间线卡片：`projectTimelineCard`）、`sanitizeProjectLineage`。`renderProjMenu` 从静态 `data-i18n` 标签手中接管 `#proj-current`。打开没有列出任何会话的项目时进入其最新的未使用会话（`reuseEmpty`），而不是每次进入都再建一个空会话。 |
| [`project-files.ts`](project-files.ts) | 本地源文件夹选择器与只读项目文件浏览器：子文件夹浏览、“显示隐藏文件夹”开关、限长文本预览、重试，以及避免过期读取覆盖界面的请求序号。`projectFolderErrorText` 按错误码把被拒绝的文件夹（保存或选择器）以界面语言说明。项目文件弹窗有自己的带内边距的面板，可直接在某个文件夹或文件处打开（UP-5）。 |
| [`project-history.ts`](project-history.ts) | 本地 `.openai4s/` 保存状态、立即保存、历史对话与版本、纯文本对话/代码/设置视图、文件版本下载和遗漏文件提示。继续对话前重新检查当前数据库归属，不恢复内核内存。 |
| [`project-history.test.ts`](project-history.test.ts) | 真实保存状态与失败、重试、仅有存档的对话、继续前重新检查、版本切换、安全文本/下载链接，以及过期或已关闭视图的响应。 |
| [`project-files.test.ts`](project-files.test.ts) | 显式选择文件夹、新建/编辑/解除绑定持久化、嵌套文件导航、安全文本预览、读取失败与过期响应。项目切换同时打开该项目的会话；目录读取或首个会话 POST 失败时恢复原项目、源文件夹和侧栏范围，也覆盖连续切换多个项目的情况。 |
| [`static-i18n-ownership.test.ts`](static-i18n-ownership.test.ts) | 代码写入会话标题或当前项目名之后，迟到的语言分块重绘和切换语言都不会把它改回“会话”/“项目”；标题输入框失焦即提交，那次重绘曾把服务端的会话名改掉。 |
| [`transcript.ts`](transcript.ts) | 输入框的 @ 引用芯片：通过 `@` 菜单钉选的文件（`autocomplete/mentions.ts`）显示为已解析并带版本号，来自其他会话也一样；其余 `@name` 按本会话的文件匹配。已存消息行的名字（`renderStored`、`addMsgActions`、`insertMessageByTime`、`renderEmptySession`、`renderMessageRefChips`）从唯一实现 `messages/list.ts` 转导出。 |
| [`transcript.test.ts`](transcript.test.ts) | 输入框引用芯片：`@` 钉选显示为已解析并给出版本号（以及来源的其他会话）；含空格的文件名只算一个芯片；钉选只在版本一致时才显示本会话那一行的校验和；未钉选的 `@name` 按本会话文件解析，解析不到则标为未解析；token 被删掉后不再显示芯片。 |
