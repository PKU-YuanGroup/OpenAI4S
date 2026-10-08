# Simulation Lab

[中文用户指南](#中文用户指南) · [Configuration](configuration.md#lab-simulation-providers) · [REST API](webapp-api.md)

## User guide

Lab is a **simulation-only** bench in the default Web workbench. Its first
virtual device, `chemgym.extractor.01`, exposes the ChemGymRL extraction profiles
`WaterOilExtract-v0` and `GenWurtzExtract-v2`. It neither discovers nor controls
physical equipment. Reaction/distillation benches, transfers between benches,
and reinforcement-learning training are outside this feature.

### Install and open

Install an optional provider with an available CPython 3.10 interpreter. The
setup command downloads the pinned upstream source and locked dependencies
into a separate environment; it does not add them to the stdlib core:

```bash
openai4s lab setup chemgymrl --python /path/to/python3.10 --dry-run
openai4s lab setup chemgymrl --python /path/to/python3.10
openai4s lab status
openai4s lab smoke --profile WaterOilExtract-v0
openai4s serve
```

Use the authenticated URL printed by `serve` (or `openai4s url`), open a
session, and select **Lab** in the right dock. Without an installed provider,
the ChemGymRL device remains listed as unavailable with an installation hint.
`status` only reads installation state; `smoke` starts a real simulation
provider, takes one step and closes it. Neither operates on a session's Lab
ledger. First creation can take minutes because each run has its own process
and a cold Numba cache. See [provider configuration](configuration.md#lab-simulation-providers)
for rollback, interpreter overrides and sandbox settings.

### Use the bench

1. Select the device and profile, optionally enter a seed (an integer from 0
   through 4294967295), then choose **Create experiment**. The device/profile and
   generated or supplied seed remain fixed for that run.
2. Read the run state, revision, step count, latest observation time, sensor
   view and channel quality. Select an advertised operation and one of its
   discrete parameter settings, then choose **Run one step**. The form sends the
   revision on which the decision was based; a stale revision is refused.
3. Check **Actions** for the request, receipt, command state and linked
   observation. `rejected` means the host refused before dispatch; `failed`
   means the provider returned an unapplied failure. A delivered response alone
   is not proof that a command succeeded.
4. If the result is unknown, choose **Query result** for that command. A
   transport failure offers **Retry same request**, retaining its original
   idempotency key. Never replace an uncertain command with a new key: it may
   already have been applied. Quarantine prevents further steps until the
   unknown command is reconciled.
5. To stop the run, use the Lab experiment control or `host.lab.stop(run_id)`.
   This closes the provider session. The agent's **Stop** controls its agent
   execution and is separate from stopping the experiment. Conversely, stopping
   a Lab run does not cancel the agent. The normal terminal capability,
   `end_experiment`, is an executed command and is distinct from this stop.

Switching sessions or reloading the page reads the saved ledger; reconnecting
refreshes confirmed state. A disconnected pane displays its last confirmed
state, not a live measurement. A daemon restart cannot recover the simulation
process: previously opened runs left unfinished end with `provider_lost`; an
interrupted creation becomes `failed` with `create_failed`. Uncertain dispatched
commands stay `outcome_unknown`, and no commands are automatically replayed.
Start a new run when a new simulation is needed.

### Agent tools and Python

Session Lab operations are available only in the Web daemon. Native Lab tools
and `host.lab` in `openai4s run` return `provider_unavailable`; delegated child
agents cannot use Lab. The installation/status/smoke CLI commands above remain
available independently. Python Cells in a Web session can use the SDK;
manual Notebook input additionally requires `OPENAI4S_NOTEBOOK_REPL=1`.

| Native tool | Python SDK | Purpose / 用途 | Approval / 批准 |
| --- | --- | --- | --- |
| `lab_list` | `host.lab.list()` | Devices and session runs / 设备与当前会话的实验 | No / 无需 |
| `lab_describe` | `host.lab.describe(device_id, profile)` | Capabilities and sensors / 能力与传感器清单 | No / 无需 |
| `lab_create` | `host.lab.create(device_id, profile, ...)` | Create a run / 创建实验 | Default ask / 默认询问 |
| `lab_observe` | `host.lab.observe(run_id)` | Latest observation / 最近观测 | No / 无需 |
| `lab_execute` | `host.lab.execute(run_id, operation, ..., expected_revision=revision)` | Execute one command / 执行一条命令 | Default ask / 默认询问 |
| `lab_status` | `host.lab.status(run_id, command_id)` | Read and reconcile the same command / 读取并核实原命令 | No / 无需 |
| `lab_stop` | `host.lab.stop(run_id)` | End the provider session / 停止 provider 会话 | No / 无需 |
| `lab_commands` | `host.lab.commands(run_id, ...)` | Paged command records / 分页命令记录 | No / 无需 |
| `lab_observations` | `host.lab.observations(run_id, ...)` | Paged observation records / 分页观测记录 | No / 无需 |

The default `ask` rules apply to agent and SDK create/execute calls; existing
permission rules and remembered approvals still apply. Inspect the device/run,
operation and parameters on the approval card before **Allow** or **Deny**.
Manual Lab controls express the user's action directly and do not open another
approval card; team-mode writes still require the session owner.
`observe(..., full=True)` and `observations(..., full=True)` return complete
sensor arrays inside Python. Native tools summarize arrays above 256 elements.
Create/execute accept an optional `idempotency_key`; preserve the returned key
for a retry. `execute` requires the current run's `expected_revision`. SDK
errors raise `RuntimeError` with `error_kind` and optional `details`; ordinary
command refusals remain results whose `command.state` and `command.error` must
be checked. Budgets may only tighten the [server limits](configuration.md#lab-simulation-providers).

### Report only supported conclusions

Label every result **simulation**. Sensor colors show layer intensity, not
material identities or exact composition. A missing/unmodeled quantity is
unavailable, never zero; model time is not physical seconds. Do not invent
uncertainty bars or infer reproducibility from a seed alone. Read the run's
recorded assumptions and reproducibility evidence.

When reporting a run, distinguish an applied command, a terminal run and an
achieved scientific goal. Neither a successful tool call nor Gym termination
proves goal attainment. Cite the run, relevant commands and observations, and
state what the available sensors cannot establish. Keep `outcome_unknown`
explicit. Reward and exact material composition belong only to separate
private evaluation records; they are excluded from ordinary agent/UI views,
logs and errors. The interface does not turn those hidden values into observed
measurements.

### Source and license

The optional provider installs `chemistrygym==2.0.0` from ChemGymRL commit
`ab8227b6b33f13617b7e551bdf6b894df7eec68d`. Upstream uses GPL-3.0-or-later;
OpenAI4S ships its adapter and metadata, not the upstream source. Running it
in a separate process does not change the upstream license. See the committed
[source, license and reproducibility record](../openai4s_lab_provider/chemgymrl/SOURCE.md).

## 中文用户指南

Lab 是默认 Web 工作台中的**仿真实验台**。首个虚拟设备
`chemgym.extractor.01` 支持 `WaterOilExtract-v0` 和 `GenWurtzExtract-v2`
两个 ChemGymRL 萃取 profile。它不发现、不控制真实设备；反应台、蒸馏台、
跨台转移和强化学习训练不在本功能范围内。

### 安装与打开

先准备 CPython 3.10，按上面的命令依次执行安装预览、安装、`lab status`、
`lab smoke` 和 `serve`，把 `/path/to/python3.10` 换成解释器路径。安装会下载
固定提交的上游源码与锁定依赖，放入独立 provider 环境，不给标准库核心加依赖。
使用 `serve` 或 `openai4s url` 打印的带认证 URL，打开会话，在右侧面板选择
**Lab**。未安装时 ChemGymRL 仍在设备列表中，但显示不可用及安装提示。
`status` 只读取安装状态；`smoke` 实际启动仿真 provider、执行一步并关闭，
两者都不操作会话的 Lab 账本。每个 run 有独立进程与冷 Numba 缓存，首次创建
可能需要数分钟。回滚、解释器覆盖和沙箱设置见[配置说明](configuration.md#lab-simulation-providers)。

### 使用实验台

1. 选择设备和 profile，可选填 0 到 4294967295 的整数 seed，再点**创建实验**。
   设备、profile 和自动生成或指定的 seed 在本次 run 中固定。
2. 查看状态、revision、步数、最近观测时间、传感器视图和通道质量。选择已公布的
   操作与离散参数档位，再点**执行单步**。界面发送决策依据的 revision；版本过期会被拒绝。
3. 在**动作记录**检查请求、回执、命令状态和对应观测。`rejected` 是派发前的宿主拒绝；
   `failed` 是 provider 返回的未应用失败。收到响应本身不能证明命令成功。
4. 遇到未知结果，对原命令点**查询结果**。传输失败时，**重试原请求**保留原有幂等键。
   不要换新 key 重发未知命令，它可能已经执行。未知命令核实前，隔离状态会阻止新步骤。
5. 停止实验用 Lab 的实验控制或 `host.lab.stop(run_id)`，这会关闭 provider 会话。
   agent 的 **Stop** 控制 agent 执行，两者不能替代；停止 Lab 也不取消 agent。
   正常终止能力 `end_experiment` 是一条执行命令，与停止会话不同。

切换会话或重载页面会读取已保存的账本，重连会刷新确认状态。断线时显示的是最近确认
状态，不是实时测量。daemon 重启不能恢复仿真进程：已打开但未结束的 run 以
`provider_lost` 结束，创建中断的 run 记为 `failed`/`create_failed`。已派发的未知命令
保持 `outcome_unknown`，系统不会自动重放；需要新仿真时创建新 run。

### Agent、Python 与批准

会话 Lab 操作只在 Web daemon 中可用：`openai4s run` 内的原生 Lab 工具和
`host.lab` 返回 `provider_unavailable`，委派子代理也不能使用 Lab。上述安装、
状态和 smoke CLI 独立可用。Web 会话的 Python Cell 可以调用 SDK；手动 Notebook
输入还需 `OPENAI4S_NOTEBOOK_REPL=1`。完整工具与 SDK 对照见上表。

agent 与 SDK 的创建、执行默认询问批准；已有权限规则和记住的批准仍然生效。
在批准卡检查设备/实验、操作与参数后再选 **Allow** 或 **Deny**。手动 Lab 操作直接
表达用户意图，不再弹第二次批准；团队模式的写操作仍要求会话所有者。
`observe(..., full=True)`、`observations(..., full=True)` 在 Python 内返回完整传感器
数组；原生工具对超过 256 个元素的数组返回摘要。创建/执行可传 `idempotency_key`，
重试时保留返回的 key；执行必须提供当前 run 的 `expected_revision`。SDK 错误抛出
带 `error_kind` 和可选 `details` 的 `RuntimeError`；一般命令拒绝仍正常返回，必须检查
`command.state` 与 `command.error`。预算只能收紧[后端限额](configuration.md#lab-simulation-providers)。

### 如实报告与许可

所有结果都要标明**仿真**。液层颜色表示传感器强度，不代表材料种类或精确组成；
缺失或未建模的量是不可用，不是零；模型时间不是实际秒数。不能编造误差条，也不能仅凭
相同 seed 宣称可复现，应查看该 run 记录的模型假设和可复现证据。

报告应区分“命令已应用”“run 已结束”和“科学目标已达到”。工具调用成功或 Gym
终止都不能单独证明目标达成。引用 run、相关命令和观测，并说明传感器不能证明的部分；
`outcome_unknown` 必须明确保留。奖励和精确组成只保存在独立的私有评价记录中，
不进入普通 agent/UI 视图、日志或错误，也不应被当成已观测的测量值。

可选 provider 安装固定提交 `ab8227b6b33f13617b7e551bdf6b894df7eec68d` 的
`chemistrygym==2.0.0`。上游采用 GPL-3.0-or-later；OpenAI4S 分发自身适配器与元数据，
不附带上游源码。独立进程不改变上游许可，详见
[来源、许可与可复现记录](../openai4s_lab_provider/chemgymrl/SOURCE.md)。

## Internal simulation Lab contract

`openai4s.lab` implements OpenAI4S's own `openai4s.lab/v1-draft` vocabulary.
It is organized around public MHS design ideas; it does **not** claim official
MHS API compatibility. This package is standard-library-only and supports only
`simulation`. It does not connect to physical hardware. Importing it neither
registers devices nor starts providers.

## Objects and identity

Frozen dataclasses define quantities, discrete parameter specifications,
capabilities, resources, sensor channel specifications, device descriptors,
requests, normalized commands, dispatches, receipts, observations, evaluations,
session opening, stop results, budgets and caller context. List-shaped typed
fields are tuples; JSON serializers emit arrays. `from_dict` rejects unknown
keys, missing required fields, invalid types and nonfinite numbers; booleans
are not numeric values. Nullable fields are still required unless the value
class explicitly supplies a default. Unavailable channel specifications have
a reason; unavailable/unknown readings have a null value. An array channel may
declare `axes`, one per dimension, so a reader can tell what each index means:
ChemGymRL's `layers` channel labels its rows with the vessels they show and
leaves the pixel axis unlabelled.

A `DeviceDescriptor` fixes device, backend, profile, simulation mode, versions,
resources, capabilities, sensor channels and limits. Its time unit is
`model_time` and `wall_clock_equivalent` is null: model time is not seconds.
Reproducibility is `unverified`, `verified_for_profile`, or `not_reproducible`;
a verified claim requires evidence for that profile. Assumptions describe
additional model behavior without claiming it belongs to the upstream model.

`load_descriptor` validates unique identities, resource and channel references,
unambiguous operation/source/target matches and strictly increasing nonempty
settings. A supplied capability revision must match the recomputed digest.
When omitted at this loading boundary it is computed; the resulting value
always carries it.

Run, command, observation and evaluation IDs have prefixes `labrun`, `labcmd`,
`labobs`, `labeval`, followed by a hyphen and 12 random hexadecimal characters.
Provider command identity is the durable command identity. A simulation
resource key is `lab:<device_id>#<run_id>:<resource_id>`.

Canonical JSON sorts dictionary keys, uses compact separators, preserves
Unicode and refuses NaN. SHA-256 hashes its UTF-8 bytes. Capability revision
sorts capabilities by `capability_id`; request hash excludes idempotency key
and expected revision after unit normalization; config hash includes device,
profile, seed, budgets and options. An idempotency key contains 1–128 printable
ASCII characters. A new mode or profile requires a new run.

## State and failure semantics

The immutable tables in `models.py` are the transition authority.

```text
creating -> ready <-> busy
creating -> failed
ready/busy -> quarantined -> ready
creating/ready/busy/quarantined -> ended
```

`ended` and `failed` are terminal. Any nonterminal run can end (stop, budget,
idle timeout, provider loss, deletion); only a run that is still being created
can fail. Quarantine forbids new commands while an execution outcome remains
uncertain, and lifts only when reconciliation has resolved the run's last
unknown command. `run_sources_for` derives the ledger's CAS sources.

```text
created -> awaiting_approval -> admitted -> dispatching -> running
created -> admitted
created/awaiting_approval/admitted -> rejected | not_dispatched
dispatching/running -> succeeded | failed | outcome_unknown | stop_requested
stop_requested -> stopped | succeeded | failed | outcome_unknown
dispatching/outcome_unknown -> not_dispatched     (proof of non-receipt)
outcome_unknown -> succeeded | failed             (a queried receipt)
```

Command terminal states are `succeeded`, `failed`, `rejected`, `not_dispatched`
and `stopped`. `outcome_unknown` remains reconcilable; it is never automatic
permission to resend. It leaves only through reconciliation: a queried receipt,
or `not_dispatched` when the device proves it never received the command. A
device proves that by remembering every command id a session accepted, so
`query` returns `None` only for an id it never saw and raises
`LabError(OUTCOME_UNKNOWN)` for one whose receipt it no longer retains; it
refuses to execute such an id again. A provider receipt that was not applied
(`failed` or `rejected`) is recorded as a `failed` command; the command state
`rejected` is reserved for host refusals before dispatch. Once a command is
dispatched its exits also free leases and move the run, so only the ledger's
`record_receipt`, `mark_outcome_unknown` and `mark_not_dispatched` may take
them. `command_sources_for` derives CAS sources from the same table. A stop
request becomes `stopped` only after provider confirmation.
Run end reasons are `end_action`, `max_steps`, `env_terminated`, `stopped`,
`budget_exhausted`, `provider_lost`, `idle_timeout`, `create_failed`, `deleted`.

A receipt preserves raw `terminated` and `truncated` flags. Provider receipt
status is `succeeded`, `failed` or `rejected`; the latter two cannot be applied.
Its end reason is null, `end_action`, `max_steps` or `env_terminated`.
Provider observation payloads contain `channels` and `sim_time`; evaluation
payloads contain `reward`, `ground_truth` and optional `metrics`. The host adds
ledger IDs, sequence, ownership and timestamps when persisting them. Initial
observation sequence is zero; subsequent applied commands advance it.

## Errors and units

`LabError` carries a code, a safe human message and optional details. Messages
must not contain simulator truth. All 21 error codes are fixed:

| Code | Meaning |
| --- | --- |
| `invalid_parameters` | Wrong shape, type, key or numeric value. |
| `unsupported_action` | Missing capability or unavailable exact setting. |
| `unit_mismatch` | No permitted unit conversion. |
| `precondition_failed` | Provider refused without changing state. |
| `stale_revision` | Caller revision differs from the current state. |
| `idempotency_conflict` | Same key, different request. |
| `run_not_found` | Run absent or outside caller ownership. |
| `device_not_found` | Device absent, or the device has no such profile. |
| `run_ended` | Run is terminal. |
| `resource_busy` | Resource occupied or stale fencing token. |
| `resource_quarantined` | Resource has an unknown outcome. |
| `budget_exhausted` | A forced limit was exhausted. |
| `approval_denied` | Approval refused. |
| `persistence_unavailable` | Durable write failed. |
| `provider_unavailable` | Provider/session absent or unavailable. |
| `provider_timeout` | Receipt did not arrive. |
| `provider_protocol_error` | Invalid, oversized or incompatible frame. |
| `outcome_unknown` | Execution may have happened without a receipt. |
| `adapter_mismatch` | Runtime capabilities differ from expected revision. |
| `replay_forbidden` | Side effect requested from recovery. |
| `mode_mismatch` | Request and run modes differ. |

Matching first selects exact operation/source/target, then checks the exact
parameter-name set, then converts units and matches a setting. Units are `L`,
`mL`, `layer_px`, `model_time` and `dimensionless`. Only L/mL conversion is
allowed (factor 1000). Settings use `math.isclose` with both tolerances `1e-9`;
the normalized value is the advertised setting, never rounded, clipped or
chosen by nearest neighbor. Unsupported-setting details carry the parameter,
allowed values and unit. The standalone quantity normalizer calls the unnamed
parameter `value`; command matching replaces that with the actual name.
Source capacity and target overflow are provider preconditions.

Budgets default to 50 steps (override with the chosen profile's limit), 200
commands, 30 minutes wall time, 3 consecutive failures and 60 minutes idle time.
The process manager enforces these budgets and the four-live-provider daemon
limit through durable admission.

## Ports and registration

`DevicePort` supplies describe, open, execute, query, stop, close and alive.
Explicit refusals return unapplied receipts. Transport failures are `LabError`,
never bare `TimeoutError`/`ConnectionError`. A timeout can leave a live session:
query its command ID before drawing conclusions. Unavailable/protocol-error
sessions are dead; failed capability matching at open creates no live session.

`LabLedgerPort` defines atomic admission/dispatch, fencing, receipt persistence,
unknown-outcome quarantine, initial observations and ordered reads/events.
Its dictionaries decode JSON columns and remove their `_json` suffix. Missing
rows are null, timestamps are integer milliseconds. It neither opens a database
nor implements migrations here. `LabManagerPort` carries caller context into
every method. Caller origins are `agent_tool`, `host_sdk`, `manual_ui`, `system`;
execution ownership is `agent`, `user_repl`, `lifecycle`, `recovery` or null.
Mutating manager operations must reject recovery contexts.

`DeviceRegistry` is explicit and thread safe, lists sorted device IDs, rejects
duplicate IDs and checks profile membership before calling a descriptor loader.
`fake_registration()` creates a registration without installing it.

## Projections and honesty

Persistence serializers include evaluation where the contract requires it;
they are not agent views. `project_observation` exposes simulated sensors only.
An array larger than 256 elements becomes a shape, min/max/mean summary and a
truncation flag inside its value. `full=True` preserves the complete array.
Unavailable/unknown channels always carry null, even if an in-memory stale
value was supplied. Categories cannot carry arbitrary dictionaries.
`project_descriptor` selects the public descriptor fields, strips private
provider/evaluation fields recursively and validates the remaining schema.

Evaluation is separate: reward and exact material composition never belong in
observations, default projections, logs or error messages. Missing pressure
is not a measurement of zero. Unknown execution is not success. No time
conversion, uncertainty or reproducibility claim is invented.

`FakeExtractorDevice` is an unrelated deterministic toy model for offline
integration tests. It provides transfers, layer draining, mixing, settling and
ending, sensor-shaped outputs, per-command deduplication and per-resource
fencing. Fault hooks simulate explicit refusal, response loss with a queryable
receipt, process death and capability mismatch. It provides no evidence about
ChemGymRL accuracy or physical hardware behavior.

## Process manager

`build_lab_manager(ledger_provider=..., registry=...)` composes one manager per
process and reconciles older daemon instances before returning. The provider
callback resolves the current Store generation on every ledger call. Adapters
share this manager: W3 connects manual UI, native tools and `host.lab` to it.
Only the daemon builds one. Startup reconciliation treats every other
`daemon_instance` as gone, so a second manager over the same database (a CLI
process, say) would end the daemon's live runs; `openai4s lab status|smoke`
deliberately use no database.
`LabLimits` defaults to four live providers (including openings and closing
sessions) and five seconds waiting for an in-flight receipt during stop.
No provider is registered or started by importing the package.

Creation persists the seed, capped step budget and registered descriptor before
opening a provider. Omitted seeds are generated once; create-key replays reuse
the durable seed, including after a restart. Opening reservations enforce the
process limit and a stop/deletion during open cannot register a late session.
Once the session opens, the run's pinned descriptor adopts what that session
declares about itself: its assumptions (wrapper parameters, the provider
sandbox posture) and its runtime reproducibility claim. Capabilities and
channels stay the registered allowlist. Requested budgets can only tighten the
defaults; an omitted budget is its ceiling and `max_steps` obeys the profile.

Commands are scoped to the caller's root session and owner. Normalized requests
are hashed before ledger admission. Duplicate keys return the original command
in every state and never call the device again. Semantic refusals are also
recorded, with the public details that let a caller correct the request (the
allowed levels, which budget ran out). The manager enforces step, command,
wall-time and consecutive-failure budgets. The ledger's `consecutive_failures`
counts host rejections and failed receipts alike, so admission and the
published run read the same number. Every budget is monotone: exhausting any
one rejects that command, ends the run with `budget_exhausted` and releases its
provider. A nonblocking per-run lock
and the ledger's atomic resource leases prevent concurrent dispatch. The
provider is called only after `begin_dispatch` commits the intent and fencing
token. The manager never opens a transaction around a ledger method.

Whenever a port call fails while the session is alive, the same command is
queried once. A receipt completes the durable command; only authoritative
non-receipt permits `not_dispatched`. Unavailable evidence leaves
`outcome_unknown` and quarantines the run. A dead session, or a port raising
outside the CONTRACT §9 vocabulary, ends the run as `provider_lost`. `status` may query this same
command later; it never resends it. Provider death ends the run with
`provider_lost`, retaining unknown commands. A failure to persist an already
received result blocks further execution until restart reconciliation, and an
error after dispatch says the device may have executed and names the command.
Ending a run for any reason (stop, idle, budget, shutdown) first queries each
unknown command once while the session can still answer. Shutdown persists
unknown intent before ending a run; if that write fails, the run stays
nonterminal so startup can still find it, and the provider is released anyway.

Stop bypasses the command lock, requests stop with CAS, waits the configured
receipt interval, then closes the session and ends the run. A timely receipt
remains succeeded/failed; an unresolved dispatch remains unknown. Late receipts
cannot reopen a stopped run. Close preempts a request still in flight
(CONTRACT §9), so stop and shutdown finish within the receipt wait plus a kill,
not the 60 s execute timeout. The manager holds no global lock while calling
provider methods.

Idle cleanup runs opportunistically on public calls and skips active or blocked
runs. `observe` is the deliberate exception: it reads and projects the ledger
only, even after the idle interval, and never invokes a provider. Recovery
callers cannot create, execute or stop; read paths also skip cleanup and query
side effects. `describe_run`, `commands` and `observations` are ledger-only
read views in the same way (paged, scoped, projected). Public run, command,
descriptor and observation values use the contract projection functions.
Device summaries publish only `available` and a fixed `availability_detail`
(never interpreter paths). `events` pages over the rows the caller may see and
returns `next_after_seq`.

## Provider environment

`openai4s lab setup chemgymrl [--python P] [--dry-run] [--rollback]` builds a
fresh CPython 3.10 generation under `<data_dir>/lab/providers/chemgymrl/`:
hash-locked dependencies (including the setuptools/wheel that build upstream's
legacy `setup.py` without build isolation), upstream pinned by commit and
checked through `direct_url.json`, and both portable manifests byte-equal to the
committed ones. A single JSON `current` pointer switches atomically only after
every check passes; a failure leaves it untouched and keeps the failed
generation and its `setup.log`. A generation built from another lock is no
longer verified. The ChemGymRL backend runs only with that generation or an
explicit `OPENAI4S_LAB_CHEMGYMRL_PYTHON`, never the daemon interpreter; the toy
backend (daemon interpreter, stdlib only) is registered only with
`OPENAI4S_LAB_ENABLE_TOY=1`.

Each session is its own sandboxed process (`-I -B`, raw network denied, a
private run directory, protocol input on a private descriptor). Exit is
observed without reaping through `os.waitid`, or kqueue on macOS interpreters
that lack it, so the process group is always disposed of before its leader's
PID is released. `openai4s lab status` starts nothing; `openai4s lab smoke`
runs one real step and prints only the projected receipt and the sandbox
posture; `openai4s doctor` reports a `lab` row.

## Evaluation and baseline policies

`lab.evaluation` is a private, simulator-ground-truth evaluation surface. It
accepts fully paginated, decoded ledger rows, not public projections. Its
`Goal` explicitly names the profile, target, collection vessel, minimum mole
amount and minimum purity. Reward, Gym termination, successful tool delivery,
and `goal_met` are independent. Missing final/initial evidence, unbound
revisions or an unresolved command yield `goal_met: "unknown"`. Absent species
in a complete `moles` map count as zero; absent vessels/maps do not.

The defaults below target `beaker_1`, require at least **0.5 mol** and **0.9
purity**, and are **benchmark choices, not upstream success thresholds**.
Evidence is pinned to ChemGymRL SHA
`ab8227b6b33f13617b7e551bdf6b894df7eec68d`:

- `WaterOilExtract-v0`: salt equivalents `q = NaCl + min(Na, Cl)` in the
  collection vessel. Purity is `q / (sum(moles except H2O) - min(Na, Cl))`.
  Oil remains contamination. Upstream instead sums salt-minus-oil across both
  work vessels; it supplies no Boolean success criterion. See upstream
  `chemistrylab/benches/extract_bench.py:115-124,202-231` and
  `chemistrylab/util/reward.py:16-39,74-95`. The initial salt inventory is
  1 mol, so the quantity threshold requires at least half of it.
- `GenWurtzExtract-v2`: the target is randomly selected at reset; pass the
  initial public `targets` channel value to `default_goal(profile, target=...)`.
  The factory refuses an omitted target. Purity excludes `C6H14` and
  `diethyl ether`; for NaCl only, it folds paired Na/Cl into equivalents in
  both numerator and denominator. For a hydrocarbon target it leaves impurity
  ions separate, matching upstream's target-specific denominator. Upstream
  sums `q * purity` across three work vessels. See
  `chemistrylab/benches/extract_bench.py:69-86,158-165`,
  `chemistrylab/benches/general_bench.py:235-240`, and
  `chemistrylab/util/reward.py:74-95`. Solvent identities are valid for these
  pinned profiles, not an arbitrary chemistry model.

Initial evaluation sequence 0 is the reset **baseline**, reported separately
from the sum of step rewards (sequences >0). Upstream subtracts the baseline
at episode end; see `chemistrylab/benches/general_bench.py:211-233`. Material
consumption is positive **net depletion of feed vessels** (transfer sources
that are never targets), in L and mol; it is neither total transferred material
nor chemical destruction. Missing stock evidence yields null. Budget use
reports steps, command attempts, terminal wall duration and consecutive
failures. Evidence completeness binds each successful command to its own
observation id, command id and applied revision; no successes gives null,
not a fictitious 100%.

A final command snapshot cannot attest the number of actual dispatch calls or
recover `outcome_unknown` states overwritten by reconciliation. Those historic
metrics are null with reasons; current unresolved commands are counted.
A future event-history input is required to prove zero duplicate dispatches
and report subsequent reconciliation outcomes. `compare` refuses numeric
comparisons unless episodes are terminal and complete paired configuration,
backend/adapter versions, wrapper assumptions, explicit goal and initial-state
fingerprints match. Wrapper assumptions reach the fingerprint because the run's
descriptor records what its session declared on open. Pass the
`run_episode` trace as `evaluate_run(..., episode=...)`: an episode the harness
aborted is incomplete evidence, not a policy result. Identical seeds alone do
not establish comparable initial states.

`ManagerPolicyEnv` adapts an already-created run to `LabManagerPort`. The
production fixed and seeded random policies therefore pass through the same
manager admission, budgets, leases and reconciliation boundary as other
callers. They receive projected sensors and capabilities only, read in full
(as `host.lab.observe(full=True)`) even where the agent view summarizes arrays
over 256 elements. `run_episode` leaves the run as it found it; stop it when the
episode ends, or it holds one of the four provider slots. Random sampling
is uniform over capabilities and then over each advertised parameter level;
physical preconditions can still fail. The fixed 13-action sequence is a
readable WaterOil simulation baseline (with toy and GenWurtz compatibility),
not a physical experiment recipe or a guarantee of goal attainment.
`run_episode` stops on unknown outcomes and counts refused commands against
its attempt bound. It never automatically retries an uncertain command.

`LatencyWrapper`, `ObservationNoiseWrapper`, `FaultInjectionWrapper` and
`BusyWrapper` declare their parameters in descriptor `assumptions` on both
`describe` and `open`. Latency uses injected `sleep(seconds)` and affects
open/execute/query observation delivery without converting model time to real
time. Noise currently supports `{kind: "gaussian", sigma: ...}` on a declared
numeric scalar/array channel, is additive and unclipped, and leaves evaluation
untouched. Its seed/profile/session-seed/provider-step key gives retries the
same sensor sample. The wrapper seed is not published: with the run seed,
profile and step public, it would let a policy subtract the noise exactly. Fault schedules and inclusive busy windows use one-based
**new-command attempt ordinals** per session, not successful model steps;
replays do not consume ordinals. A dropped request remains queryable as absent;
a dropped response leaves the underlying receipt queryable. Refusal receipts
retain tombstones after eviction so a seen id never executes twice.

`scripts/lab_evaluate.py` is a development-only device-contract probe and emits
private evaluation JSON. Its local adapter is deliberately labelled
`device_contract_probe` in the configuration; it does not claim the manager's
approval/lease/durability guarantees and cannot establish LLM comparison
fairness. Use `ManagerPolicyEnv` with the integrated manager for that comparison.
The probe runs fake, subprocess toy, or an explicitly provided ChemGymRL
interpreter; all provider scratch files belong under its isolated work-dir.
