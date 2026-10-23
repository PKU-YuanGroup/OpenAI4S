# 委派继续操作准入：设计与验收准备

[English](delegation-continuation-design.md)

状态：**仅 P2-01 准备**。本文确定后续的只读可继续性投影、绑定身份的准入约定、
执行与观察边界，以及继续一个已完成或已停止子任务所须的验收用例；不新增路由、
响应字段、UI 控件、配置开关或生产代码，不改变任何默认行为。以下用例是未来实现
的要求，不是已经通过的测试；现有同步 `POST …/continue` 在本版本中保持原有语义。

## 当前边界

[`DelegationRunner.continue_child`](../openai4s/agent/delegation.py) 已经存在，
并且是显式、持久的继续操作，而不是自动重启：恢复路径从不调用它，daemon 重启会把
所有 `pending`/`running` 子任务标为 `stopped`、`stop_reason` 记为
`daemon_restart`，丢弃排队中的引导消息，并且必须显式继续才会产生新尝试。该方法
从已保存的请求载荷创建下一次尝试，`wait=True`（默认）时在调用者线程上运行，返回
的是这次尝试的运行信封，而不是子任务对象。

请求身份 `(root_frame_id, parent_action_group_id, native_call_id)` 加
`request_sha256`，以及尝试身份 `(request_id, attempt_no)`，已由编号 Store 迁移
持久化在 `delegation_requests` / `delegation_attempts` 中。
[`DelegationProjectionRepository.continue_request`](../openai4s/storage/delegation.py)
在最新尝试为 `pending`/`running` 时拒绝（`DelegationRequestConflict`），执行会话
派生上限，并用 `previous_attempt_id` 记录新子任务。因此继续操作总能绑定到请求与
尝试身份；今天不存在的，是**继续操作本身**的身份。

| 接口面 | 当前行为（须保留或区分） |
| --- | --- |
| 继续端点 | `POST /api/v1/frames/{fid}/delegations/{child_id}/continue` → `SessionRunner.continue_delegation_child`。未知子任务 404；根帧没有活跃 runner 时为 409 `delegation_record_stale`；`DelegationConflictError` 为 409 `delegation_conflict`；其余情况 409 `delegation_error`。 |
| 响应 | 通过 `project_browser_child` 返回已保存的子任务（与 `GET …/delegations` 同形），不是运行信封；没有存储行的子任务会被裁剪到浏览器允许列表。 |
| 阻塞形态 | `wait=True` 会在整个子任务运行期间占住 HTTP handler；该路由没有异步受理，也没有 202。 |
| 持久请求／尝试 | `delegation_requests` 与 `delegation_attempts`；尝试状态为 `pending/running/done/failed/stopped`。重复的**委派**调用已按请求身份去重。 |
| 尝试创建 | 只有 `continue_request` 会创建尝试，每次被受理的调用一份；恢复路径从不创建。最新尝试仍活跃时拒绝再次继续。 |
| `GET …/delegations` | 经 `workbench_state.delegation` 的只读持久投影。子任务带 `request_id` 与 `attempt_id`；不带 `attempt_no`、可继续标志和拒绝原因。 |
| Timeline | [`frontend/src/features/timeline/island.ts`](../frontend/src/features/timeline/island.ts) 的 `renderDelegationPanel` 只为 `running`/`pending` 子任务提供停止与引导；没有继续控件。 |
| 可信捕获 | 设置 `cell_hooks_factory` 后，`wait=False` 与 fanout > 1 均被拒绝，子任务的 `permissions.background` 被强制为 `deny`，捕获所有权由一把独占捕获门保护，同一时间只受理一条委派链。 |

CLI 路径没有持久存储：当预算未绑定根帧、store 与 runner 实例时，
[`DelegationBudget.continue_request`](../openai4s/agent/delegation.py) 抛出
`DelegationError("continue requires durable delegation storage")`。因此未来任何
面向 UI 的准入都只适用于 Web／会话路径，而不是所有委派表面。

相邻行为已有覆盖，可作为今天事实的凭证：
[`tests/test_delegation_idempotency.py`](../tests/test_delegation_idempotency.py)
证明重复的委派身份只创建一个子任务、摘要不符为 409、重启后在显式调用
`continue_child` 之前不会自动继续；
[`tests/test_delegation_control.py`](../tests/test_delegation_control.py) 证明该
路由的 404/409 区分，以及陈旧记录返回 409 `delegation_record_stale`，既不是 404
也不是编造的成功；[`tests/test_delegation_persistence.py`](../tests/test_delegation_persistence.py)
证明恢复会把子任务标为停止且不创建新尝试。它们都没有覆盖重复的**继续**请求，
因为该身份尚不存在。

## 可继续性只读投影（待设计）

该投影只用持久记录回答一个问题：此刻是否可以受理一次显式继续？它必须由已保存的
请求、尝试与子任务行，加上当前 runner 租约算出；不得写入、预留预算、创建子任务或
复活运行。它是一次带时间戳的读取，不是对远端或进程存活性的声明。

| 拟议字段 | 拟议含义 |
| --- | --- |
| `root_frame_id` | 限定委派、预算与可见性的帧树。 |
| `child_id` | 用户正在看的子任务；与 stop/steer/continue 路由使用的 id 相同。 |
| `depth`、`parent_child_id` | 树中位置。位于 `MAX_DEPTH` 的子任务是叶子；继续它只是重跑该叶子，不会获得新的委派能力。 |
| `request_id` | 跨尝试稳定的持久逻辑请求。 |
| `attempt_id`、`attempt_no`、`previous_attempt_id` | 确切尝试及其前驱；客户端受理时必须回显的一组身份。 |
| `status`、`task_status`、`stop_reason` | 持久子任务状态，含重启后的 `daemon_restart`。 |
| `continuable` | 仅当持久记录与活跃所有权同时支持新尝试时为 `true`；证据缺失即 `false`，绝不做乐观的 `true`。 |
| `reason` | `continuable` 为 `false` 时取下表中的一个稳定代码；为 `true` 时省略或为 `ok`。 |
| `evaluated_at` | 本次读取的 UTC 时间，供客户端区分旧评估与新评估；不是远端观察时间。 |

| 拟议拒绝原因 | 它指向的证据 |
| --- | --- |
| `ok` | 活跃 runner 拥有根帧、最新尝试已终态、会话上限仍有空位。 |
| `not_found` | 该根帧的持久记录中没有这个 id 的子任务。 |
| `no_durable_request` | 子任务行没有请求／尝试身份（遗留或非持久工作）；不能从未记录的请求创建尝试。 |
| `attempt_in_flight` | 最新尝试为 `pending`/`running`；即 `continue_request` 现在抛出的存储冲突。 |
| `root_inactive` | 根帧没有活跃委派 runner；即 `delegation_record_stale` 背后的重启场景。 |
| `session_cap_reached` | `spawned` 已达到会话 `budget_limit`；累计派生从不退还。 |
| `trusted_capture_unavailable` | 捕获准入／租约／门会拒绝，或请求要求捕获上限禁止的并行工作。 |
| `evidence_unavailable` | 持久行或 runner 租约读不到；未知不得显示为可继续。 |

这些代码是拟议词汇，不是现有响应。把它们加入 GET 载荷须走常规的契约与响应
schema 门禁，且字段缺失时旧客户端必须继续可用。

## 绑定身份的幂等准入（待设计）

委派时的身份已幂等，继续时还没有。未来的受理请求必须携带客户端期望的
`(root_frame_id, child_id, request_id, attempt_no)`，以及针对该操作本身的幂等键。
服务端必须做到：

1. 每个幂等键至多受理一次尝试。双击、重试的 HTTP 请求或两个标签页提交同一操作，
   都返回同一个已受理的尝试身份，而不是再创建一行子任务。
2. 与持久记录不符的身份——不同的根帧、子任务或请求，或比最新更旧的
   `attempt_no`——以 409 拒绝并回传当前尝试身份。陈旧的视图不得静默创建第二次
   尝试。
3. 保持现有摘要纪律：同键不同摘要即冲突，与 `DelegationRequestConflict` 对齐。
4. 每次操作只计一次尝试、一次派生，绝不计两次；被拒绝的受理不产生任何消耗。
5. 不得抬高任何上限、不得绕过可信捕获准入，也不得重放上一次尝试的内存状态
   （见下）。

今天没有用于继续操作身份的表或列，本版本也不新增。后续实现必须决定：该记录是新
持久行（需要编号迁移），还是可由现有尝试行推导；该决定不在本文范围内。

## 执行协调与观察超时（待设计）

- 同步路由是兼容面，不是未来 UI 的形态。子任务可能运行很多轮，未来入口可以优先
  返回命名了尝试身份的“已受理”（异步）响应。若新增，它必须是附加路由或显式分版
  形状；不得改变今天 `wait=True` 响应的含义。
- 存储已经保证每个持久请求至多一个活跃尝试。继续操作必须继续使用这道守卫，而不
  是 UI 侧检查；同步子任务运行期间必须保持父 Cell 阻塞：捕获门存在的前提是，两个
  子任务同时执行 Cell 时无法诚实归属各自写入的字节。
- 超时结束的是观察，不是尝试。尝试保留自己的帧、执行所有权与产物身份；停止等待
  的客户端通过读取持久的子任务／尝试行来对账。它绝不能通过再次 POST 继续来消解
  未知结果。
- 受理之后丢失的响应属于未知结果。用同一幂等键重新受理是唯一安全的补试；换新键
  就是新尝试，必须是用户显式决定。
- 被受理但不能在 daemon 重启后存活的尝试必须如实报告，不得重放。若后续的异步受理
  被持久化，恢复必须像子任务行那样把它标为停止并要求显式受理；若未被持久化，受理
  就不得宣称自己不拥有的持久性。
- 等待预算、重试提示与任何 202 形状都是未来实现决定。本文不固定任何数值观察超时。

## 兼容性与非目标

- 保持现有路由含义：`POST …/continue` 仍然显式、默认同步，并以浏览器子任务投影
  应答。现有错误码 `not_found`、`delegation_record_stale`、`delegation_conflict`、
  `delegation_error` 与 `bad_request` 的状态码与语义不变。
- 无自动继续、无重启重放、无新执行系统、无后台调度器、无 daemon 级轮询。
- 不放开并行：`FANOUT_CAP` 48、`SESSION_CAP` 1000、`MAX_DEPTH` 4 不变；深度 4 的
  子任务仍是叶子；可信捕获保留其单子任务上限与强制的 `background: deny`。
- 不恢复内存。继续是在新的子任务帧中重跑已保存请求：内核状态、在途工具调用、未
  保存变量与上一次尝试的产物版本都不会被带入；旧证据保持历史，绝不被改写。
- 本版本不新增 UI 入口。后续入口必须默认关闭且可独立关闭；投影或路由不存在时，
  Timeline 保持今天的停止／引导行为，不展示自己无法支撑的东西。
- 本版本不迁移数据库、不新增默认开关、不改变恢复行为。

## 后续本地验收矩阵

以下全部为**待实现的计划用例**，必须用真实 `DelegationRunner` 与真实的 store／重启
路径证明，而不是 stub runner 或源码字符串断言。扩展现有三个委派测试文件与 Timeline
测试；只用合成数据与临时目录，不调用真实模型或提供方。

| ID | 可控场景 | 必须观察到的结果 |
| --- | --- | --- |
| D01 | 对一个终态子任务发起两个并发、重复或重试的继续请求 | 恰好创建一个新尝试和一行新子任务；两个响应命名同一尝试身份；会话只计一次派生。 |
| D02 | 携带比最新更旧的 `attempt_no` 继续 | 409 并回传当前尝试；不建第二行子任务，也不静默受理。 |
| D03 | 继续另一个会话帧树中的子任务 | 保持现有所有者／可见性拒绝（404/403 且不确认存在）；外部 id 零行、零尝试。 |
| D04 | 子任务活跃时 daemon 重启，随后继续 | 恢复把子任务标为 `stopped`/`daemon_restart` 且不创建尝试；不自动重放；之后的显式继续恰好启动一个新尝试。 |
| D05 | 根帧的 runner 已不在时继续 | GET 投影报告不可继续且原因为 `root_inactive`；POST 仍返回 409 `delegation_record_stale`，绝不是编造的 200。 |
| D06 | 最新尝试仍为 `pending`/`running` 时继续 | 冲突响应；持久尝试集合与子任务数量不变。 |
| D07 | `spawned` 等于会话上限时继续 | 拒绝；预算计数与尝试历史不变，派生额度不会被二次退还。 |
| D08 | 可信产物捕获开启时继续 | `wait=False`／并行请求被拒绝，`background` 保持 `deny`，捕获准入拒绝以可理解的拒绝抵达调用者，而不是产生绕过捕获的子任务。 |
| D09 | 受理后响应丢失，客户端用同一身份重试；另测观察方超时 | 重试返回已受理的尝试而不新增尝试；超时的观察方不取消尝试，尝试自行运行到自己的终态。 |
| D10 | 没有持久请求身份的子任务行 | 投影返回 `no_durable_request`；不虚构 request id，不创建尝试。 |
| D11 | 反复 GET 该投影 | 零行、零尝试、零引导写入；不消耗预算；没有活跃 runner 时也可读，且不复活子任务。 |
| D12 | 一次完成的继续操作 | 新尝试拥有自己的子任务帧与产物版本；上一次尝试的证据不变；`FANOUT_CAP`、`SESSION_CAP` 与 `MAX_DEPTH` 不变。 |

本次准备完成意味着投影、拒绝词汇、准入约定与上述用例已达成一致，且现有委派测试
在无行为变化的前提下仍然通过。后续运行时完成还须针对真实 runner、store 与重启
路径执行 D01–D12 并记录结果；本文不能代替那一轮验收。
