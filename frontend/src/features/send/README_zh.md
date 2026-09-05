# frontend/src/features/send

[English](README.md)

F-11 发送全链与现场卡片。作曲框 `send()`、turn ticket、步骤 / 计划 / 权限 / 候选卡片、附件与 @-引用问题卡、admission 追踪器。window 名字（`send`、`buildStepCard`、`renderAttachmentProblems`、`renderRefProblems`、`searchResultHttpUrl`、`admissionSettled`、`forgetAdmission`、`outstandingAdmissions`、`reconcileLastAdmission`、`rememberAdmission`）由本模块赋值，不再留给 F-05 占位。`frame_update` 仍归 F-06；turn-ticket 体通过 `setFrameUpdateTurnHandler` 注入。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`admission.ts`](admission.ts) | Admission 追踪器。独立键 `openai4s.admission.{fid}.{id}`；legacy key 迁移；60 秒 grace。 |
| [`admission.test.ts`](admission.test.ts) | 前缀、legacy 迁移、grace 窗口、settled 状态。 |
| [`bind.test.ts`](bind.test.ts) | `bindComposer` 只绑一次；`installSend` 不碰 DOM；Enter 派发文本；同一时刻只有一个 dispatch 在途。 |
| [`candidate.ts`](candidate.ts) | Review 门控三态时序：`markCandidateReady` → `applyCandidateResolution` → `applyFinalReviewStatus`。 |
| [`candidate.test.ts`](candidate.test.ts) | 三态顺序、禁止把 verified 降级、durable 回执规则。 |
| [`environment.ts`](environment.ts) | `send()` / `turnDone` 用的 standard-profile 就绪横幅。 |
| [`handlers.ts`](handlers.ts) | cards / candidate / step / plan / permission 的 WS 类型；`handleFrameUpdateTurn`。 |
| [`host.ts`](host.ts) | 用 `isReady` 查 window（`callLane` / `hostFn`）；取消按钮显隐。 |
| [`icon.ts`](icon.ts) | 步骤 / 计划 / 权限多出来的图标（globe、list-check、lock 等）。 |
| [`index.ts`](index.ts) | `installSend` 往 window 赋值、注册 WS handler。不碰 DOM：作曲框由 `main.tsx` 在 render 之后绑定。 |
| [`install.test.ts`](install.test.ts) | 十个契约名字通过 `isReady`；不注册 `frame_update`。 |
| [`permission.ts`](permission.ts) | 权限门卡片。冻结 DOM 类名 `.perm-card` / `.resolved` / `.allowed` / `.denied`。 |
| [`plan.ts`](plan.ts) | 结构化计划卡、进度、批准 / 修订 / 丢弃 / 恢复。 |
| [`problems.ts`](problems.ts) | 附件问题卡（客户端文案）与 @-引用问题卡（服务端文案）。 |
| [`send.ts`](send.ts) | 作曲框发送全链。计划模式 payload 走 F-07 `planModePayload`。`bindComposer`（由 `main.tsx` 在 `render` 之后调用）。 |
| [`step.ts`](step.ts) | 语义活动步骤、`buildStepCard`、`searchResultHttpUrl`。 |
| [`ticket.ts`](ticket.ts) | Turn ticket 世代、`acceptTurnTicket` / `activateTurnTicket`、`resumeWatch`。 |
| [`turn.ts`](turn.ts) | `turnDone` 收尾；调用 F-14 的 `notebookOnTurnDone()`。 |
