# frontend/src/features/send

[English](README.md)

F-11 发送全链与现场卡片。作曲框 `send()`、turn ticket、步骤 / 计划 / 权限 / 候选卡片、附件与 @-引用问题卡、admission 追踪器。window 名字（`send`、`buildStepCard`、`renderAttachmentProblems`、`renderRefProblems`、`searchResultHttpUrl`、`admissionSettled`、`forgetAdmission`、`outstandingAdmissions`、`reconcileLastAdmission`、`rememberAdmission`）由本模块赋值，不再留给 F-05 占位。`frame_update` 仍归 F-06；turn-ticket 体通过 `setFrameUpdateTurnHandler` 注入。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`admission.ts`](admission.ts) | Admission 追踪器。独立键 `openai4s.admission.{fid}.{id}`；legacy key 迁移；60 秒 grace。 |
| [`admission.test.ts`](admission.test.ts) | 前缀、legacy 迁移、grace 窗口、settled 状态。 |
| [`bind.test.ts`](bind.test.ts) | `bindComposer` 只绑一次；`installSend` 不碰 DOM；Enter 和点击 `#send-btn` 都派发文本；两者合计同一时刻只有一个 dispatch 在途。 |
| [`copy.ts`](copy.ts) | `sendCopy`：作曲框文案（发送按钮的标题），不写进生成的词典。 |
| [`candidate.ts`](candidate.ts) | Review 门控三态时序：`markCandidateReady` → `applyCandidateResolution` → `applyFinalReviewStatus`。 |
| [`candidate.test.ts`](candidate.test.ts) | 三态顺序、禁止把 verified 降级、durable 回执规则。 |
| [`first-send.test.ts`](first-send.test.ts) | 新会话的第一条消息只在共享创建流程打开对话之后才派发，`openConversation` 的重置不会落在回合中间；票据与运行态得以保留。 |
| [`refused-send.test.ts`](refused-send.test.ts) | 服务端在准入之前拒绝的发送（409 `model_profile_needs_key` / `model_revision_unavailable` / `model_profile_needs_active`）：文字回到输入框、移除乐观气泡（若输入框里已有新内容，则保留该气泡并标为未发送，被拒绝的文字不会丢失），提示保留服务端给出的原因而不是「本轮失败」；改绑成功后的提示说明实际绑定了什么（`rebindDoneText`）；改绑确认框依据服务端的 code 与消息说明真实原因（`rebindConfirmText`：配置已改指其他提供商或端点、缺少密钥、固定配置无法读取、匹配不唯一；只有服务端明确说"已不存在"时才这样说，其余情况用中性的"已不可用"）。拒绝在用户打开另一个会话之后才到达时，不往那个会话里放任何东西：不写入它的输入框、不显示提示、不打开设置、不弹改绑确认。 |
| [`environment.ts`](environment.ts) | `send()` / `turnDone` 用的 standard-profile 就绪横幅。 |
| [`handlers.ts`](handlers.ts) | cards / candidate / step / plan / permission 的 WS 类型；`handleFrameUpdateTurn`。 |
| [`host.ts`](host.ts) | 用 `isReady` 查 window（`callLane` / `hostFn`）；取消按钮显隐。 |
| [`icon.ts`](icon.ts) | 步骤 / 计划 / 权限多出来的图标（globe、list-check、lock 等）。 |
| [`index.ts`](index.ts) | `installSend` 往 window 赋值、注册 WS handler。不碰 DOM：作曲框由 `main.tsx` 在 render 之后绑定。 |
| [`install.test.ts`](install.test.ts) | 十个契约名字通过 `isReady`；不注册 `frame_update`。 |
| [`permission.ts`](permission.ts) | 权限门卡片。冻结 DOM 类名 `.perm-card` / `.resolved` / `.allowed` / `.denied`。202 `decision_resolving`（已受理、仍在落盘）不当作失败：按钮保持禁用并显示中性的状态行（功能内文案），直到 `permission_resolved` 收尾卡片。 |
| [`permission.test.ts`](permission.test.ts) | 202 `decision_resolving` 不是失败：不再提供重试、不显示失败提示，由 `permission_resolved` 收尾卡片；真正的拒绝（410 已过期）仍会重新启用按钮。 |
| [`plan.ts`](plan.ts) | 结构化计划卡、进度、批准 / 修订 / 丢弃 / 恢复。仍为 `in_progress` 的步骤只在计划执行中时才闪烁；`completed` 计划若仍带着这样的步骤（服务端开始拒绝这种组合之前写下的行），会用本功能自带的文案表标成「已结束、有步骤未确认完成」。 |
| [`plan.test.ts`](plan.test.ts) | 结束态计划卡：带着进行中步骤的已完成计划不显示为完成；执行中仍保留实时图标；步骤全部有结论的计划照常显示完成。草稿计划的修改意见未能派发（已有回合在运行、请求失败）时会放回输入框。丢弃计划的请求在用户打开另一个会话之后才返回时，不动那个会话的计划卡与计划状态。旧版审批卡只在计划模式回合正常结束后出现：被停止、被 guardian 拦截或失败的回合不弹卡，也不给下一回合留下待审批状态。 |
| [`problems.ts`](problems.ts) | 附件问题卡（客户端文案）与 @-引用问题卡（服务端文案）。 |
| [`send.test.ts`](send.test.ts) | 拒绝与首条消息之外的 `send()` 行为：程序化发送（权限卡的继续、批准计划）不动输入框里用户无关的草稿，而输入框自己的文字发送后会被清空；技能目录读取失败后，下一次发送的 `/skill` 仍会带上技能指令。 |
| [`send.ts`](send.ts) | 作曲框发送全链。计划模式 payload 走 F-07 `planModePayload`。`bindComposer`（由 `main.tsx` 在 `render` 之后调用）把 Enter 和发送按钮绑到同一个派发锁上。 |
| [`step.ts`](step.ts) | 语义活动步骤、`buildStepCard`、`searchResultHttpUrl`。从步骤里打开产物失败时用提示报告。 |
| [`step.test.ts`](step.test.ts) | 步骤卡片：查看器打不开产物时会报告，而不是留下未处理的 rejection。 |
| [`ticket.ts`](ticket.ts) | Turn ticket 世代、`acceptTurnTicket` / `activateTurnTicket`、`resumeWatch`。 |
| [`turn.ts`](turn.ts) | `turnDone` 收尾；调用 F-14 的 `notebookOnTurnDone()`；收尾仍显示运行中的活动卡片（`messages/cardState.ts`）；任何终态都清掉 `planPending`，只有正常结束的回合才弹旧版审批卡。 |
