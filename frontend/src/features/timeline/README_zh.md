# frontend/src/features/timeline

[English](README.md)

F-15 Action Timeline 内核。`sanitize*` / `mergeActionTimelines` 是纯函数。虚拟化 ledger、SVG overview 和工作台面板在 `island.ts`，保留 `_timelineView` 对象同一性与 E2E 函数名。契约清单上的 window 名字由本模块自己赋值（不改 `window-exports.ts` 除标记区注释）。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`api.ts`](api.ts) | 同源 `/api/v1` fetch、`optionalApi`、`hint` / 后续车道 `isReady` 调用。 |
| [`copy.ts`](copy.ts) | C2 远程任务面板的 zh/en 文案（车道本地：生成的 `i18n` 词典来自冻结的 `app.js`）。 `workbenchT`：沙盒与权限面板、诊断区块的平实文案（NAV-6）。 |
| [`compute-records.test.ts`](compute-records.test.ts) | C2 行为测试（HTTP 替身）：503 为「任务记录暂不可用」（非「无任务」）且有重试；同会话失败保留过期页；切会话/筛选清空；仅成功空页显示无任务；缺 `total_live_count` 保持本页口径；加载更多按游标追加并按 job_id 去重；行显示记录自身更新时间。 |
| [`dom.ts`](dom.ts) | `el` / `iconEl` / `ghostIconBtn` / `svgElement` / `$` / `bytes`。 |
| [`execution-request.ts`](execution-request.ts) | `exactExecutionIdentity` / `scopedExecutionRequest`（app.js:3021-3041）：中断必须指名它要停的那一次执行。 |
| [`execution-request.test.ts`](execution-request.test.ts) | Vitest：中断必须指名具体执行；找不到属主时拒绝发送。 |
| [`index.ts`](index.ts) | `bootTimeline`：注册 WS handler 并把契约名字挂到 window。 |
| [`index.test.ts`](index.test.ts) | `installTimeline` 把 `loadWorkbenchState` 挂到后续车道的 `callWindow`。 |
| [`island.ts`](island.ts) | 命令式孤岛：46px ledger、overview SVG、五个侧栏面板、`renderActionTimeline`（外框常驻，各区块只在输入变化时重建；WS 触发的重绘经 `scheduleActionTimelineRender`，按住鼠标或 IME 组字期间暂缓），以及定时的工作台刷新（`scheduleWorkbenchRefresh` 只读事件影响到的那几个端点）。 侧栏用平实的话（NAV-6）：沙盒面板说明是否启用、自检结果，以及单元里的代码能否联网，原始值收在「技术细节」里；模型上下文的 token 分层放在默认收起的「诊断」里；概览坐标轴显示本地时间。 |
| [`island.test.ts`](island.test.ts) | 在假 DOM 上跑的 Vitest：排队条、视图销毁、WS 重绘保留的状态，以及远程任务面板的错误/过期/分页状态。 沙盒面板显示平实状态，原始自检信息在收起的详情里；上下文 token 分层在收起的诊断区块里；精确时间是带时区的本地时间。 |
| [`model.ts`](model.ts) | span / overview 几何、`actionTimelineEntryKey`、epoch 解析。 `timelineOverviewExactTime` / `timelineLocalClock` 给出带 UTC 偏移的本地时间，而不是不标时区的 UTC。 |
| [`queue.ts`](queue.ts) | `#queue-strip`：输入框上方排队的后续消息及其 ✕ 取消（每个执行只发一次 POST）。 |
| [`s.ts`](s.ts) | `createSProxy()` 别名，保证 `_timelineView` 嵌套写入保持同一性。 |
| [`sanitize.ts`](sanitize.ts) | `sanitize*` 家族、`mergeActionTimelines`（app.js:2795-3298）与 `mergeComputeTaskPages`（C2 翻页追加）。 |
| [`sanitize.test.ts`](sanitize.test.ts) | sanitize* 与 merge 的 Vitest。 |
| [`types.ts`](types.ts) | 白名单投影形状。 |
| [`ws.ts`](ws.ts) | `action_timeline` / execution / recovery / branch / delegation / sandbox handler。 |
