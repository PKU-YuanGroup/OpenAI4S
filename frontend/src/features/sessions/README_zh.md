# frontend/src/features/sessions

[English](README.md)

F-13 仪表盘 / 项目 / 会话。分页与排序是纯函数。窗口契约名（`fetchAllMessages`、`fetchOlderMessages`、`fetchRecentMessages`、`openConversation`、`renderMessageRefChips`、`renderComposerRefChips`）由本模块赋值。能力判定走 `compat/stub.ts` 的 `isReady`——本目录不 import `window-exports.ts`。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`actions.ts`](actions.ts) | 会话菜单、分享对话框、导入导出、标题、取消。app.js:7411-7793。 |
| [`api.ts`](api.ts) | `API`、`ApiError`、`api()`、`apiErrorText`。app.js:84-119。 |
| [`binds.ts`](binds.ts) | 迟绑定，避免 dashboard 与 conversation 互相 import。 |
| [`boot.ts`](boot.ts) | window 导出、`setLoadSessionsImpl`、工作台点击接线。 |
| [`chrome.test.ts`](chrome.test.ts) | hint 错误前缀（`错误：` / `Error: `），不新增 i18n 键。 |
| [`chrome.ts`](chrome.ts) | `hint`、断连横幅、`openMenu` 的 Esc/`role=menu`、键盘激活。 |
| [`conversation.ts`](conversation.ts) | `newSession`、`routeInitialView`。`openConversation`（F-10）与 `resumeWatch`（F-11）改为 re-export，不再保留本车道的副本。 |
| [`conversation.identity.test.ts`](conversation.identity.test.ts) | 断言这些 re-export 与拥有车道装上的是同一个函数对象。 |
| [`dashboard.ts`](dashboard.ts) | 首页列表、项目搜索 / 加载更多 / 重试、示例 CTA 轮询绑视图生命周期、仪表盘轮询。 |
| [`dom.ts`](dom.ts) | `$` / `el` / `ago` / `navURL` / composer 辅助。 |
| [`icon.ts`](icon.ts) | 本车道菜单和行用到的线性图标。 |
| [`index.ts`](index.ts) | 对外 re-export；import 时挂 window 名字。 |
| [`lane.ts`](lane.ts) | 用 `isReady` 包一层，调用后续车道的 window 名字。 |
| [`load.ts`](load.ts) | `loadSessions` 游标走页、`loadProjects` keyset 分页（不发 `offset`）、文件夹、`renderSessions`。 |
| [`load.projects.test.ts`](load.projects.test.ts) | 项目列表查询串不含 `offset`；合并/去重；空态 / 重试 / 加载更多的视图状态。 |
| [`messages.ts`](messages.ts) | `fetchRecentMessages` / `fetchOlderMessages` / `fetchAllMessages` / 更早消息条。 |
| [`paging.test.ts`](paging.test.ts) | 分页常量、会话排序、走页/去重、仪表盘过滤。 |
| [`paging.ts`](paging.ts) | `MESSAGE_PAGE_SIZE=300`、`SESSION_MAX_PAGES=50`、排序/走页/过滤。 |
| [`projects.ts`](projects.ts) | 项目菜单/模态/研究视图、`sanitizeProjectLineage`。 |
| [`transcript.ts`](transcript.ts) | `renderStored`、引用芯片、空会话 starter、消息动作。 |
