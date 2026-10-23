# 计算任务观察：只读契约与验收准备

[English](compute-observation-design.md)

状态：**仅 D2 设计准备，本版不实现**。本文把对单个 `host.compute` 任务的只读观察，
与今天已有的显式结果取回，作为未来契约分离设计。本文不新增路由、响应字段、轮询器、
持久列、provider 操作或生产代码，也不改变 `ComputeManager.result()` 或任何默认开关。
**本版不实现其中任何一项。** 以下用例是未来实现的要求，不是已经通过的测试；今天的
列表、刷新、取消与结果读取语义保持不变。

## 当前边界

**探测就是取回。** [`ComputeManager.result()`](../openai4s/compute/manager.py) 是
询问某个 `host.compute` 任务进展的唯一路径。它探测远端，并在任务进入终态后把产物拉回
`<workspace>/hpc/<job_id>/`，提交终态、退出码、清单与摘要，然后关闭任务。
`POST /api/v1/frames/{fid}/compute/tasks/{job_id}/refresh` 正是这次调用外加产物
捕获，这也是它必须是显式 POST 而不是页面刷新的原因。它背后没有只读的 provider
探测。

**列表结构上接触不到 provider。** `GET /api/v1/frames/{fid}/compute/tasks` 调用
[`compute_tasks.owner_tasks`](../openai4s/server/compute_tasks.py)，它只拿到
`Store`，不 import `ComputeManager`；返回 `polled: false`，无论被调用多少次都无法
推进任何一行。`public_task` 只暴露一个有界投影：存储字段的允许列表（`job_id`、
`provider`、`status`、`reason`、`termination_reason`、`exit_code`、`created_at`、
`updated_at`、`submitted_at`、`terminal_at`、`integrity_sha256`），加上计算出的
`live`、`terminal` 与 `outputs` 清单摘要；其中 `updated_at` 是状态最后一次**被确立**
的时间，模块刻意不写读取时间，避免陈旧行看起来像刚被确认过。

**每条传输都把状态与字节绑在一起。** SSH 路径运行一条有界探测脚本
（`kill -0 <pid>` → `RUNNING`；否则 `.rc` → `RC <code> [TIMEOUT]`；否则 `NORC`），
随后在同一次 `result()` 调用内通过 SCP/tar 取回产物；主机不可达时返回
`status: unknown` 与 `error_kind: unknown_state`，且从不缓存。BYOC 路径派发
resident 的 `wait` 操作（有界 `.phase` 轮询，就绪后从沙箱流出 `out.tar.gz`），并在
同一次调用内发布取回结果；缺失终态证据同样记 `unknown`，不记 `failed`，也不缓存。
provider 契约本身（[`ByocProvider`](../openai4s_compute_provider/_protocol.py)）
只提供 `create_sandbox`、`exec`、`list_owned`、`read_owner`、`terminate` 以及可选的
`list_dir`/`list_volumes`/`read_file`，没有“读取任务状态”的操作。resident 里确实
有一条 probe-only 辅助路径（`_op_wait` 的 `probe_only`、`_op_probe_many`），本意是
供宿主状态轮询器使用，但今天没有任何宿主调用方：它是辅助管道，不是公开的观察
能力，而且它仍然要执行一次远端命令。

**另有一套控制面，本文刻意不涉及。**
[`openai4s/orchestration/`](../openai4s/orchestration/) 拥有 Team Server 的批处理
工作负载：`SlurmBackend.observe(allocation)` 返回归一化的 `Observation`
（`phase`、`reason`、`handle`、`diagnostics`），reconciler 把它转成持久事实，
[`orchestration_routes.py`](../openai4s/server/orchestration_routes.py) 对外暴露这些
工作负载。那套观察是分配生命周期内部的调度器读取，不是 `host.compute` 的任务状态，
从不出现在 `GET …/compute/tasks` 中，其 `Phase` 词汇也不是 `compute.states`。本文
既不把它扩展到 `ComputeManager` 任务，也不把它当作计算任务的观察契约。

**身份按所有者隔离，且部分信息在远端。** 任务行带有 `job_id`、`provider`
（`ssh:<alias>` 或 `byoc:<id>`）、提交会话的 `owner_key`（其工作区路径），以及远端
句柄 `receipt`/`alias`/`workdir`/`pid`/`sandbox_id`。`_rehydrate` 只加载本所有者
的活跃行，`_jobs` 也按所有者隔离，因此另一个会话的任务 id 解析为 `not_found`——
刻意与“从未存在”不可区分。`host.compute.status()` 回答
`{live, limit, daemon_live, provider_caps}`，来自本地计数而非远端真值；
`reconcile()` 只列出 daemon 上次停止时仍活跃的任务且不重提任何东西；
`job_history()` 读取只追加的事件流。原生 `compute_result` 工具是
`read_only = False`、`writes_files = True`；取消与提交同样是写操作。

| 接口面 | 当前行为（须保留或区分） |
| --- | --- |
| `ComputeManager.result()` | 一次调用内完成探测、取回与终态提交。对已终态任务返回带 `cached: True` 的已记录证据，且不接触 provider。 |
| `POST …/compute/tasks/{job_id}/refresh` | 显式写路径：`result()` 加 `ArtifactManager.capture`、该阶段启用时的 Stage 11 收据绑定，响应里 `polled: true`。 |
| `GET …/compute/tasks` | 只读持久记录；按所有者隔离；`polled: false`；零远端动作。 |
| SSH 传输 | 同一次调用里先有界探测、后取回；主机不可达或缺少退出码记为 `unknown`，不是 `failed`。 |
| BYOC 传输 | 同一次调用里先走 resident 的 `wait`、再取回沙箱内归档；缺失终态证据记为 `unknown`。 |
| provider 契约 | 没有状态操作；状态存在于宿主的包装文件（`.phase`、`.rc`、`.timeout`），读取需要一次远端 `exec`。 |
| 所有者范围 | `owner_key` 是会话工作区；另一个会话的 id 为 `not_found`，不确认其存在。 |
| SDK `status()` / `reconcile()` / `job_history()` | 本地 daemon 计数；上次停止时仍活跃的行且不重提；只追加事件读取。 |
| `compute_result` 工具与 SDK `.result()` | 具备写能力：取回文件、可能提交终态；不是观察调用。 |

## 可观察与不支持观察的接口面（待设计）

未来的观察必须是独立的只读能力、自己的契约，而不是 `result()` 的包装，也不是把
已保存的行重新贴上“新鲜”标签。是否可观察由 provider 边界决定；不具备时回答
“不支持观察”，而不是猜测。

| 拟议接口面 | 拟议观察契约 |
| --- | --- |
| 持久记录（`status`、时间戳） | 本地可读，零远端调用。它是已记录证据，并如实按此报告，绝不呈现为当前远端真值。 |
| SSH 进程存活（`pid`、`.rc`、`.timeout`） | 原则上可用一次不返回字节、不写文件的探针观察；今天该探针只存在于 `result()` 内部，且与取回耦合。 |
| BYOC 沙箱任务状态 | 取决于 provider 与 resident 辅助进程。probe-only 路径存在但未接线；每次探测仍是一次远端 `exec`。provider 无法回答时给出 `unsupported`。 |
| `host.compute` 任务的调度器或队列位置 | 未建模：SSH 传输把包装进程跑在 `nohup` 下，也没有 provider 契约暴露队列。另有的 `openai4s/orchestration/` 控制面观察它自己的调度器分配——那是不同的子系统，不是计算任务的观察。 |
| 远端文件清单或摘要 | 不经传输不可观察。观察不得暗示看到过文件内容；只有显式取回才会取回并计算摘要。 |
| 终态结果 | 只有能读到包装证据的探测才能确立。已保存的终态结果是已记录结果，可带 `cached` 标记重放，但重放不是新观察。 |
| 远端不可达 | 只能观察为“无法观察”：`unknown` 加有界原因。任务可能仍在运行并计费。 |
| 另一个会话的任务 | 永不可观察。保留按所有者隔离的 not-found 回答，观察不得确认其存在。 |

## 身份绑定（待设计）

观察结果必须命名它所针对的确切身份，且响应绝不能被归属到身份不符的任务。这在
BYOC 上尤其重要：沙箱是热的且会被复用，提交任务 B 会清掉 `/work`，所以只有
sandbox id 并不构成任务身份。

| 身份事实 | 拟议规则 |
| --- | --- |
| 所有者（`owner_key`、根帧） | 每次观察都必须携带；它限定可见性、持久行与任何远端调用。不符即拒绝，且不透露任务是否存在。 |
| `job_id` | 必须与持久行和 provider 响应一致。命名了不同 id 的响应被丢弃，不记录。 |
| provider 目标（`ssh:<alias>` / `byoc:<id>`） | 必须与行一致；同一 job id 在另一个 provider 下是另一个任务。 |
| 远端句柄（`receipt`、`sandbox_id`、`alias`/`workdir`/`pid`） | 必须是该尝试保存的句柄。被后续工作复用的句柄应拒绝，而不是映射到旧任务。 |
| 观察时间 | `observed_at` 只属于本次观察；它绝不改写 `updated_at`、`terminal_at` 或已保存的摘要。 |
| 已记录结果 | 仅允许带显式的 cached/recorded 标记重放已保存证据，并绑定同一身份与摘要。 |

## 单任务观察的 provider 契约（待设计）

若将来新增观察，它是一次拥有自己契约的 provider 操作，不是 `result()` 的变体，也不是
本版注册的新方法。以下要求说明该操作必须保证什么。SSH 传输可以用一条有界的只读
探针满足；BYOC provider 只有在 resident 辅助进程能不流出归档就回答时才满足，否则
回答 `unsupported`。

| 拟议要素 | 要求 |
| --- | --- |
| 范围 | 每次调用恰好针对一个任务身份，且属于调用者所有者。没有批量、没有列表、没有“观察所有活跃任务”的扇出。 |
| 输入身份 | 持久记录自身的事实——`owner_key`／根帧、`job_id`、`provider`、远端句柄。绝不接受调用者提供的远端路径、命令或进程 id。 |
| 输出 | 归一化答案：观察 `state`、`observed_at`、有界原因，以及至多包装进程的状态证据。没有文件字节、没有清单、没有摘要，也不写任务行。 |
| 只读传输 | 探针可以执行一次有界远端读取（例如 `kill -0`、读 `.rc` 或 `.phase`），但绝不流出 `out.tar.gz`、不解包归档、不写工作区／数据目录。 |
| 不重提 | 观察不能提交、重试、取消或终止；那些仍是独立的显式动作，各有自己的权限。 |
| 能力声明 | provider 或传输要么声明用只读探针支持观察，要么回答 `unsupported`。未声明或无法验证的能力即 `unsupported`，绝不是尽力而为的取回。 |
| 界限与超时 | 每次观察一个有时限的超时（数值留给后续实现决定）。到期即 `unknown`；没有后台重试，重复观察是相互独立的有界调用。 |

## 观察状态、时间与超时（待设计）

响应是附加式的：观察 `state`、`observed_at` 时间与有界 `reason`。`updated_at` 保持
原义（记录最后一次变化的时间），未知绝不被渲染成运行中、成功或失败。

| 拟议状态 | 含义 |
| --- | --- |
| `observed` | 在 `observed_at` 时刻，provider 往返针对上述确切身份给出了答案。 |
| `recorded` | 重放已保存的终态结果；作为历史是权威的，但不是新观察，也不盖新的观察时间戳。 |
| `unobserved` | 尚未尝试观察；与“没有在跑”和“没有这个任务”都不同。 |
| `unsupported` | provider 或传输无法在不取回的情况下回答、能力缺失，或该特性被关闭。手动取回仍然可用。 |
| `unknown` | 已尝试但没有证据：主机不可达、认证失败、远端超时或包装状态不可读。任务可能仍然活跃。 |
| `refused` | 调用者无权观察该身份（所有者／可见性）或请求畸形；除今天的 `not_found` 外不泄露存在性。 |
| `disabled` | 观察能力被关闭（其默认值）。旧的列表、刷新、取消与结果路径不受影响。 |

未来的观察超时只约束观察调用，不约束任务：超时返回带原因的 `unknown`，观察方退出，
不取消任务、也不在后台重试。调用方离开后迟到的答案不更新任何缓存状态——观察不是
订阅。允许重复观察，但有界且无副作用；没有 daemon 轮询器、没有自动刷新、没有调度。

## 与 C1 任务列表字段的关系（待设计）

C1（计划 §6 与附录 B.1）给任务列表自己的持久记录契约。C1 的符合性整改可能仍在调整
内部实现，因此下面的关系按计划契约陈述——`limit`/`state`/`cursor`、
`tasks`/`live_count`/`polled`，以及新增的 `total_live_count`/`has_more`/`next_cursor`
——而不是绑定某一个实现细节。

| C1 列表事实 | 与观察的关系 |
| --- | --- |
| `GET …/compute/tasks` 是带 `polled: false` 的持久记录读取，零远端动作。 | 观察绝不改变该响应。它是独立的显式动作，从 GET 路径无法触达。 |
| `live_count` 是**本页**活跃数；`total_live_count` 是同所有者跨全部状态的活跃总数。 | 两者都是持久记录计数，绝不从观察计算，也绝不呈现为远端真值。 |
| 排序为 `created_at DESC, job_id DESC`；`has_more` 与 `next_cursor` 沿该顺序翻页。 | 观察不重排、不插入、不删除、不消耗游标；分页不受影响。 |
| `public_task.updated_at` 是状态最后一次被确立的时间。 | 观察的 `observed_at` 是独立的任务级时间；本版不把它写入 `updated_at`。 |
| 记录本身可能已经是 `unknown` 这个活跃状态。 | 列表继续把记录的 `unknown` 显示为活跃而非失败；观察返回 `unknown` 不改变记录。 |
| 记录读取失败是 503 `compute_record_unavailable`（C1）。 | 观察失败是远端读取的 `unknown`／`unsupported`／超时。客户端不得把“记录读不到”与“远端未知”互相折叠。 |
| C1 的新字段对旧客户端是附加且可选的。 | 观察在实现时加入自己独立的结果；不替换、不扩展、不重新解释 C1 的响应形状。 |

## 副作用约定

- 观察不写工作区或数据目录中的任何文件，不创建 `hpc/<job_id>/` 目录树，不登记产物
  版本，也不运行 `ArtifactManager.capture`。
- 它不提交终态、退出码、清单、摘要或 `terminal_at`，也不追加任务历史事件。若后续
  设计需要观察日志，那是独立的存储决策，需要自己的迁移与保留审查，不属于本契约。
- 它不提交、不取消、不终止沙箱，也不触碰热 BYOC 沙箱的 `/work`；未来实现必须证明
  其探针命令只能读取包装进程的状态文件。
- 它不改变 `result()` 语义。`result()` 仍是探测加取回；`_stored_terminal_result`
  继续返回带 `cached: True` 的已记录证据；刷新 POST 仍是显式写路径。
- 它不取代取消。取消仍是独立的、需确认的动作，具有 `cancel_confirmed`、
  `cancel_indeterminate`（202）与 `already_terminal`（409）结果，观察绝不改变它们。

## 显式取回保持分离

`POST …/refresh` 保留当前链路：按所有者隔离的 manager、`result()`、产物捕获、
Stage 11 收据绑定、`polled: true`，以及现有的 `no_provider`、`refresh_failed` 与
`harvest_provenance_invalid` 失败形态。原生 `compute_result` 工具
（`writes_files = True`）与 SDK 的 `.result()` 保持写能力（取回与终态提交）。SDK 的
既有约定——从后续 Cell 轮询到终态、没有任何东西会自动取回——也不变。认证、会话
可见性与所有者隔离保留在每条路径上；观察不引入新的权限绕过，也不引入新的审批面。
关闭未来的观察能力后，以上全部必须不受影响。

## 能力不足（待设计）

- 稳定的原因词汇应能区分：未配置 provider、provider 没有观察操作、远端不可达、
  调用者不是所有者、观察超时，以及观察被关闭。
- 不支持观察的 UI 显示“此处不支持观察”，并保留今天的显式刷新，而不是编造状态。
- 任何回退路径都不得隐式调用 `result()`；要付出一次取回代价的状态必须由用户显式
  触发。
- 以后才获得观察能力的 provider，不得要求旧路径随之改变。

## 兼容性与非目标

- **本版不实现该契约。** 本版不交付路由、响应字段、轮询器、持久列、provider 操作、
  配置开关或迁移；本文只是准备记录。
- `result()` 保持现有语义：它仍是探测加取回的写路径，`_stored_terminal_result` 仍
  重放已记录证据，没有显式动作就不会发生任何取回。
- 没有后台轮询、没有定时器驱动的网络请求、没有自动刷新、没有订阅：观察将是用户
  触发的一次有界调用（INV-4）。
- 不新增审批或权限面。未来的观察必须复用现有会话认证、会话可见性与所有者隔离，
  不得扩大 provider 的权限。
- 未来能力缺失或被关闭时，列表、刷新、取消、`result()` 与 `job_history()` 均不受
  影响；旧客户端继续读取旧形状，另有的 `openai4s/orchestration/` 观察也不会被扩展
  进本契约。

## 后续本地验收矩阵

以下全部为**待实现的计划用例**，扩展现有的
[`tests/test_compute_durability.py`](../tests/test_compute_durability.py)、
[`tests/test_compute_task_centre.py`](../tests/test_compute_task_centre.py) 与
[`tests/test_compute_owner_and_harvest.py`](../tests/test_compute_owner_and_harvest.py)。
使用合成数据、临时数据目录，并在 provider 边界使桩或本地传输；真实 SSH／BYOC
证据必须单独记录，绝不从桩推断。

| ID | 可控场景 | 必须观察到的结果 |
| --- | --- | --- |
| R01 | 观察期间断网或主机不可达 | 状态 `unknown` 加有界原因；工作区／数据目录零写入；持久行与列表不变；之后的观察可以解出结果。 |
| R02 | 远端探测找不到退出码证据（`NORC`）或 phase 不可读 | `unknown`，与 `failed` 区分；不提交终态、不创建缓存终态结果，仍可重试。 |
| R03 | 观察另一个会话的任务 id | 按所有者隔离的 not-found 拒绝；对外部 id 零远端调用；不确认存在、不泄露数据。 |
| R04 | 观察与取消或提交并发；取消返回不确定 | 观察不改变任何状态、不覆盖任何结果；取消的 confirmed／indeterminate／already-terminal 语义保持不变。 |
| R05 | 观察前后发生 daemon 重启 | 恢复只绑定本所有者的活跃行；不重提任何东西；远端句柄丢失时给出 `unsupported`/`unknown`；已保存终态结果以 `recorded` 重放。 |
| R06 | 单次单任务观察前后的副作用扫描 | 工作区、数据目录、产物版本、任务行与任务事件均不变；provider 至多收到一次针对确切任务的有界探测，无取回、无终态写入。 |
| R07 | provider 不具备观察能力，以及该特性被关闭 | 带稳定原因的 `unsupported`/`disabled`；显式刷新与 `result()` 仍可用；不发生任何隐式取回。 |
| R08 | 身份漂移——复用 BYOC 沙箱、陈旧收据、provider 不匹配 | 回答为拒绝或 `unknown`；绝不归属到更早的任务，不写清单或摘要。 |
| R09 | 观察前后对照 C1 列表 | `GET …/compute/tasks` 仍报告 `polled: false`，`updated_at` 不变，`live_count`/`total_live_count`/`has_more`/`next_cursor` 不变；观察时间不出现在已保存的更新里，也不改变游标。 |
| R10 | 观察之后的显式取回 | 刷新仍然取回并捕获产物版本；`result()` 返回同样的已记录终态证据与 `cached: true`；观察没有创建任何产物。 |
| R11 | 观察期限到期，而远端持续缓慢应答 | 有界 `unknown` 加原因；没有滞留的 handler／worker，不取消任务；第二次观察是新的有界调用。 |
| R12 | 关闭能力与旧客户端兼容 | 旧字段形状与含义不变；新字段可选；观察关闭时列表、刷新、取消与结果读取不变，且不需要迁移。 |

本次准备完成意味着可观察／不支持观察的划分、身份绑定、provider 契约、观察状态词汇、
与 C1 的关系、副作用约定与上述用例已达成一致，且现有计算相关测试在无行为变化的前提下
仍然通过。后续运行时完成须在真实实现上执行 R01–R12，并在环境存在时使用真实 SSH／
BYOC 观察；无法执行的运行记为 `not_run`，绝不记为通过。本文不能代替那一轮验收。
