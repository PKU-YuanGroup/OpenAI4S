# frontend/src/features/sessions

[English](README.md)

F-13 仪表盘 / 项目 / 会话。分页与排序是纯函数。窗口契约名（`fetchAllMessages`、`fetchOlderMessages`、`fetchRecentMessages`、`openConversation`、`renderMessageRefChips`、`renderComposerRefChips`）由本模块赋值。能力判定走 `compat/stub.ts` 的 `isReady`——本目录不 import `window-exports.ts`。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`actions.ts`](actions.ts) | 会话菜单、导入导出、标题、取消。app.js:7411-7793。 |
| [`api.ts`](api.ts) | `API`、`ApiError`、`api()`、`apiErrorText`。app.js:84-119。 |
| [`binds.ts`](binds.ts) | 迟绑定，避免 dashboard 与 conversation 互相 import。 |
| [`boot.ts`](boot.ts) | window 导出、`setLoadSessionsImpl`、工作台点击接线。Shell 的事件立即绑定，但首个视图要等语言分块加载完成（或加载失败、或超过 `I18N_ROUTE_WAIT_MS`）才路由：仪表盘列表、侧栏和打开的会话经 `t()` 渲染，字典到达后不会重绘。若等待超时，这些列表（仪表盘、侧栏、空会话）会在字典真正到达时重绘一次。 |
| [`boot.i18n-gate.test.ts`](boot.i18n-gate.test.ts) | 字典加载前事件已绑定，首次路由等待字典；语言分块加载失败或卡住时仍会路由；等待超时后才到达的字典会重绘列表（仅此情形）。 |
| [`chrome.test.ts`](chrome.test.ts) | hint 错误前缀（`错误：` / `Error: `），不新增 i18n 键。 |
| [`chrome.ts`](chrome.ts) | `hint`、断连横幅、`openMenu` 的 Esc/`role=menu`、键盘激活。 |
| [`conversation.ts`](conversation.ts) | `newSession`、`routeInitialView`。`openConversation`（F-10）与 `resumeWatch`（F-11）改为 re-export，不再保留本车道的副本。 |
| [`conversation.identity.test.ts`](conversation.identity.test.ts) | 断言这些 re-export 与拥有车道装上的是同一个函数对象。 |
| [`conversation.newsession.test.ts`](conversation.newsession.test.ts) | `newSession` 在发布新 id 之前先释放上一个对话（取消订阅、Notebook 缓存）；共享路径只在对话真正打开后才 resolve。 |
| [`actions.directory.test.ts`](actions.directory.test.ts) | 会话菜单操作让侧栏目录保持真实：「新建文件夹并移入」建的文件夹会被列出，而不是被缓存的文件夹列表挡掉；删除当前会话后即使随后的列表刷新失败，也不会重新打开被删的会话。 |
| [`actions.cancel.test.ts`](actions.cancel.test.ts) | 取消回执只有在它命名的执行仍是本客户端正在运行的那个时，才切换到「正在停止…」。 |
| [`dashboard.ts`](dashboard.ts) | 首页列表、项目搜索 / 加载更多 / 重试、示例 CTA 轮询绑视图生命周期、仪表盘轮询。 |
| [`dashboard.sessions.test.ts`](dashboard.sessions.test.ts) | 仪表盘的会话列表与示例 CTA：`/frames` 读取失败时如实提示并提供重试，保留上次读到的行，而不是显示没有会话并推荐示例；无论有多少次重绘与它的首次状态读取竞争，CTA 的状态轮询都只跑一份，离开仪表盘之后也不会再启动。 |
| [`dom.ts`](dom.ts) | `$` / `el` / `ago` / `navURL` / composer 辅助；`FRAME_ROUTE` / `PROJECT_ROUTE` / `routesToWorkspace` 由 `routeInitialView` 与 Shell 首帧共用。`setTitle` 从静态 `data-i18n-val` 标签手中接管 `#conv-title`。 |
| [`icon.ts`](icon.ts) | 本车道菜单、行和 `[data-icon]` 标记用的 `icon` / `iconEl` / `paintIcons`。图形路径取自共享的 `icons/paths.ts` 表。 |
| [`index.ts`](index.ts) | 对外 re-export；import 时挂 window 名字。 |
| [`lane.ts`](lane.ts) | 用 `isReady` 包一层，调用后续车道的 window 名字。 |
| [`load.ts`](load.ts) | `loadSessions` 游标走页；`loadProjects` keyset 分页（不发 `offset`），项目目录与仪表盘搜索各自分页；文件夹、`renderSessions`。 |
| [`dashboard.projects.test.ts`](dashboard.projects.test.ts) | 非整页加载的重绘之后项目卡片显示什么，以及项目目录里存着什么。运行中徽标依据仪表盘最近取到的 frame 标注——包含 4 秒轮询取到的那批，否则重绘会在刚被该轮询清空的「运行中」卡片旁边画出「1 running」。搜索有自己的结果页，从不替换页眉、切换器和各处标签所读的目录，所以被搜索框筛掉的项目在「最近」里仍显示名称；目录刷新失败时保留原目录。 |
| [`load.replace.test.ts`](load.replace.test.ts) | 防抖搜索还在等回复时点击「加载更多」会被拒绝：此前它会拿到更新的代号却带着**旧**查询和旧游标发请求，搜索回复因此被当作过期丢弃，旧筛选的第二页落在了新输入的搜索框下面。 |
| [`load.projects.test.ts`](load.projects.test.ts) | 项目列表查询串不含 `offset`；合并/去重；空态 / 重试 / 加载更多的视图状态。 |
| [`messages.ts`](messages.ts) | `fetchRecentMessages` / `fetchOlderMessages` / `fetchAllMessages` / 更早消息条。 |
| [`paging.test.ts`](paging.test.ts) | 分页常量、会话排序、走页/去重、仪表盘过滤。 |
| [`paging.ts`](paging.ts) | `MESSAGE_PAGE_SIZE=300`、`SESSION_MAX_PAGES=50`、排序/走页/过滤。 |
| [`navigation.ts`](navigation.ts) | 视图代际与同步的目录清理。刻意不承担列表读取归属：列表读取以所属项目为界。 |
| [`copy.ts`](copy.ts) | 目录读取失败与重试的双语文案。 |
| [`load.navigation.test.ts`](load.navigation.test.ts) | 会话/文件夹/分页乱序、按项目划分的读取归属、读取失败及自动打开归属。 |
| [`actions.export.test.ts`](actions.export.test.ts) | Markdown 导出要求读取成功及结构有效，并固定会话标题。 |
| [`projects.navigation.test.ts`](projects.navigation.test.ts) | 已过期的项目导航不能覆盖当前会话及会话/文件夹列表；菜单筛选会取消待处理的项目打开（包含 A→B→A 重复筛选），该次打开随即把视图交还——重新加载被它在入口处退役了读取的那个会话；若工作区已显示而没有会话，则打开菜单所选的项目；当前导航将所有权交给其会话。列表读取以所属项目为界，而不是视图代际：同一项目的刷新即使被打开会话或回到 Home 抢先，也照样生效。 |
| [`share.test.ts`](share.test.ts) | 只有剪贴板确认写入后，复制才提示“已复制”；写入被拒绝时如实报告失败，并选中链接以便手动复制。 |
| [`share.ts`](share.ts) | 分享对话框：创建、复制、更新或撤销会话的只读链接。app.js:7560-7697。 |
| [`projects.ts`](projects.ts) | 项目菜单/模态/研究视图、`sanitizeProjectLineage`。`renderProjMenu` 从静态 `data-i18n` 标签手中接管 `#proj-current`。 |
| [`static-i18n-ownership.test.ts`](static-i18n-ownership.test.ts) | 代码写入会话标题或当前项目名之后，迟到的语言分块重绘和切换语言都不会把它改回“会话”/“项目”；标题输入框失焦即提交，那次重绘曾把服务端的会话名改掉。 |
| [`transcript.ts`](transcript.ts) | 输入框的 @ 引用芯片。已存消息行的名字（`renderStored`、`addMsgActions`、`insertMessageByTime`、`renderEmptySession`、`renderMessageRefChips`）从唯一实现 `messages/list.ts` 转导出。 |
