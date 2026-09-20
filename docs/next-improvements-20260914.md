# Next improvements — 2026-09-14 / next 分支改进记录

This ledger covers the approved sequential T0–T9 plan, starting at
`8127907657d21a4cbb514bd1c21096801fd6cf6a`. It does not change the version,
database schema, release policy or `main`. Each implementation is reviewed and validated
before pushing `next`; the following item waits for that commit's CI.

本记录跟踪本轮 T0–T9 顺序执行。原有 `TODO_zh.md` 与
`next-version-progress.md` 的未提交修改保留在工作区，不纳入本轮提交。
验证凭据只由进程环境或继承管道提供，不进入交付代码、保留日志或 Git。

| Stage / 阶段 | Status / 状态 | Evidence / 证据 |
|---|---|---|
| T0 Baseline / 基线 | Completed / 完成 | Locked Python 3.12 science + chemistry, frontend and three Playwright browsers installed; 90 LLM baseline tests passed. Baseline [CI 34819967908](https://github.com/PKU-YuanGroup/OpenAI4S/actions/runs/34819967908) succeeded at the starting SHA. |
| T1 P0-01 Retry and compatibility / 重试与降级 | Completed / 完成 | Shared three-send state, one compatibility POST, structured stream refusal only, cancellation before sends, semantic replay veto. Initial regression run reproduced 11 failures. Independent review passed after five findings were fixed; 144 targeted tests passed. |
| T2 P0-03 Editing / 编辑保护 | Completed / 完成 | Conditional writes, immutable editor baseline, bounded recoverable drafts and read-only reconciliation; backend, frontend and dist together. |
| T3 P0-02 Resource bounds / 资源边界 | Completed / 完成 | Shared total deadline, bounded input, backpressure and original usage evidence; independent review passed. |
| T4 P1-01 Files | Completed / 完成 | Shared owned filtering, pagination and refresh; 705 frontend tests passed. |
| T5 P1-03 Navigation / 导航 | Completed / 完成 | `2f53bf9c`; all 25 applicable CI jobs passed. |
| T6 P1-02 Provenance and export / 溯源与导出 | Completed / 完成 | `14947d25`; all 25 applicable CI jobs passed; fixed read states, validation and version identity. |
| T7 P2-01 Snapshot design / 快照准备 | Completed / 完成 | `1216c986`; bilingual retention/restore preparation, 25 applicable CI jobs passed; no migration changes. |
| T8 P2-02 Slow connection design / 慢连接准备 | Completed / 完成 | `cf68ef11`; bilingual phase/admission/release contract, 25 applicable CI jobs passed; 14 future cases, no new runtime quotas. |
| T9 Final validation / 最终验收 | Local acceptance passed / 本地验收通过 | Final real Ark, three-engine artifact editing/filter/export and independent wheel/sdist installations passed. The delivery reply records this final documentation commit and its own CI result. / 最终回执记录本轮文档提交及其对应 CI。 |

## Live Ark evidence / Ark 实测

Endpoint: `https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions`.
Requested model: `doubao-seed-2.0-pro`. Values below are provider-returned usage,
not zero-filled estimates; missing usage is recorded as unknown.

| Stage / 阶段 | Request ID / 请求编号 | Returned model / 返回模型 | Time / 耗时 | Terminal / 终态 | Raw usage / 实际用量 |
|---|---|---|---|---|---|
| T0 short text | `021789376424524e2e425e7482a92db97bab17cb602abe8d89403` | `doubao-seed-2-1-turbo-260628` | 4.045 s | HTTP 200, stop | prompt 53; completion 24 (reasoning 21); total 77; cached 0 |
| T1 streaming text | `02178937694642171d94858e1bfbff6308a33b0bd3e33b8ea50f2` | `doubao-seed-2-1-turbo-260628` | 1.811 s | HTTP 200, stop; 3 deltas | prompt 53; completion 34 (reasoning 31); total 87; cached 0 |
| T1 native tool | `021789376948232c5962d2ff0c11c118c6b5d934e5fbaa186dcb7` | `doubao-seed-2-1-turbo-260628` | 5.032 s | HTTP 200, tool_calls; echo_value(value=7) | prompt 406; completion 88 (reasoning 55); total 494; cached 0 |
| T2 named-session artifact | `021789384515327427deff93a3e6360e32202b6d931cb308af2bf` | `doubao-seed-2-1-turbo-260628` | 6.816 s | HTTP 200, stop; one call; agent completed | prompt 15,648; completion 150 (reasoning 66); total 15,798; cached 2,360 |
| T3 initial streaming probe | `021789389419432fb9ad9ccefd1cf94f2af1554e5d9f81220d9f6` | `doubao-seed-2-1-turbo-260628` | 90.003 s | HTTP 200, deadline; no completed usage | Unknown / 未知 |

| T3 visible-delta Stop / 可见文本后停止 | `021789389644031a94a092d6e1517e56ac52cca715566be31a51a` | `doubao-seed-2-1-turbo-260628` | 4.092 s | HTTP 200, stop | prompt 57; completion 42 (reasoning 33); total 99; cached 0 |
| T3 next-call recovery / 下一调用恢复 | `021789389648026bfe35b6525b6a24c7ddbea7b420bd558f419dd` | `doubao-seed-2-1-turbo-260628` | 4.572 s | HTTP 200, stop | prompt 51; completion 24 (reasoning 23); total 75; cached 0 |
| T3 browser recovery tool 1 | `02178939012235529a69b00e37f9b7feeeb938de1ddf732b07129` | `doubao-seed-2-1-turbo-260628` | 6.164 s | HTTP 200, tool_calls | prompt 15,805; completion 84 (reasoning 26); total 15,889; cached 2,360 |
| T3 browser recovery tool 2 | `0217893901286014ab1b857f955f8c19d0609a2b480a31ea5d9e7` | `doubao-seed-2-1-turbo-260628` | 4.939 s | HTTP 200, tool_calls | prompt 15,911; completion 79 (reasoning 19); total 15,990; cached 15,672 |

The gateway returned a different model ID than requested; both are retained.
端点返回的模型编号与请求模型不同，记录保留两者，不假定它们相同。

## T1 validation / T1 验证

- Full offline suite through `capture_response_schemas.py --check`: **8,850
  passed, 25 skipped**, 1,165 captured route/status shapes, 212/212 routes,
  no breaking shape drift. `/kernel/packages` had an additive environment
  shape observation in this local environment; this item does not change that route or regenerate its contract.
- Full pre-commit (including mypy) passed in a complete candidate copy that
  excludes the two pre-existing document edits. Directory documentation and
  source secret scan passed. The PR harness passed all 38 scenarios.
- Skills installer: 16 selftests passed; npm pack verified 2,283 files and
  604 Skills (6.5 MB).
- Additional explicit kernel/agent/gateway regression: 254 passed and one
  15-second first-cell timeout under `--dist load`. The failed test,
  `test_kernel_survives_the_request_thread_that_created_it`, does not invoke
  the LLM and passed alone in 14.30 seconds (14.12-second test body). The full
  suite above used CI's `--dist loadfile` and already passed this test.
  The same 255 tests then passed together using CI's `--dist loadfile`
  (434.39 seconds). The timeout's cause was not proven by the available log.
  No kernel timeout or production behavior was changed to hide the result.
- Commit `c2cb02dde4f8752861fb1a093c73db9c986965e0` passed all 25 applicable jobs in [CI 34830046563](https://github.com/PKU-YuanGroup/OpenAI4S/actions/runs/34830046563). Temporary detailed
  logs are in `/private/tmp/openai4s-next-improvements-20260914`; this ledger
  retains the non-secret results for repository readers.

完整离线套件与 schema 门禁、独立复核、静态检查及 Ark 实测均已完成。
额外并行核心回归出现一次首次 Cell 的 15 秒超时；该用例不经过模型调用，
单独重跑通过，同一组 255 项按 CI 分配方式重跑也全部通过。保留这次观察，
不放宽超时或改动内核来消除测试结果。本项提交的 25 项适用 CI 门禁已全部通过。

## T2 validation / T2 验证

- Loading disables Save both in the DOM and callback. Saves compare the expected
  head inside the existing execution/write locks, before unchanged-content or
  snapshot handling. One of two writers sharing the same baseline succeeds;
  a conflict changes no bytes, versions or events. Omitted versions retain the
  legacy API behavior; explicit invalid values fail with 400.
- Editor baselines are immutable, checksum-verified version reads. Drafts retain
  original and modified text within 10 entries / 8 MiB, survive in-page refresh
  and navigation, and remain accessible after source deletion. Unknown save
  results trigger reads only, without replay or an unsupported success claim.
  Browser reload is guarded: cancelling it retains page memory; accepting it
  destroys memory, as documented. Historical tabs remain read-only.
- Independent read-only review passed after five findings were fixed: clipboard
  fallback, freeing draft capacity, missing historical snapshots, nullable old
  metadata and recovering drafts whose source was deleted. A second independent
  evidence audit found no remaining substantive acceptance gap.
- Backend targeted tests: **56 passed**, plus a real HTTP 400/409 projection test.
  Frontend: **690 passed** across 75 files; typecheck and production build passed.
  The full Chromium/Firefox/WebKit matrix passed **33/33** checks. Both original
  and final real Ark artifacts passed the conditional-editor scenarios in all
  three engines, including a response lost after the server committed the save.
- Final full offline suite and schema check: **8,878 passed, 26 skipped** in
  814.15 seconds, **1,167** captured route/status shapes and **212/212** routes,
  without breaking drift. Two newly covered PUT/PATCH edit success shapes were
  then captured with the existing recorder from real route tests and added to
  the frozen artifact; their fields match the existing POST success contract.
  All 76 schema/crosswalk metadata tests passed after that capture.
- The first full run had 8,876 passes, 25 skips and two failures. One was the
  pre-existing 15-second kernel first-cell timeout; it passed alone without any
  timeout change, then passed in the full run above. The available log does not
  establish its cause. The other was the R6/O4S-03 evidence digest: adding CAS
  tests changed one cited file. Independent re-audit confirmed the existing
  claim and old test ASTs were unchanged, and only that digest was refreshed.
- Full pre-commit including mypy passed on a clean candidate excluding the two
  original document edits. The PR harness passed all 38 scenarios; directory
  documentation, secret scan, route contract, 16 Skills installer selftests
  and the 2,283-file / 604-Skill npm package check passed. Wheel and sdist were
  built and verified without publishing. Source and committed Vite assets match.
- The named-session Ark probe above used a thread-owned observer and produced
  `ark-edit-notes.txt` as artifact `a-e173b9ee7826` in session `f-914c87c4d080`.
  An earlier completed probe retained request
  `02178938144336688a480d877f824556f2559e19636edcdfd3765` and a captured usage
  payload (prompt 15,568; completion 187, including 76 reasoning; total 15,755;
  cached 14,648), but its global observer mixed main and background title calls.
  Auxiliary usage and per-call elapsed attribution remain unknown; the captured
  usage is not the total cost of T2. The corrected probe does not erase that
  limitation or the earlier record.

T2 已完成实现、独立复核、本地完整验收及远端 CI。提交
`f558fc5225c7d45fc1c2ffba22b0a20d564a4ccc` 的 [CI 34837765300](https://github.com/PKU-YuanGroup/OpenAI4S/actions/runs/34837765300)
全部 25 个适用门禁通过（另有 4 个非适用作业跳过），包括 Python 3.10/3.12/3.13/3.14、
Linux、Docker、三浏览器及安装验证。Python 3.14 本轮运行 26 分 29 秒并通过；
无取消或重跑。独立无依赖 wheel 安装 smoke 通过 13 个模块及 604 个 Skills。
保留首次测试失败和实测记录缺口，不把未知用量当成零。
后端、前端及 dist 配套提交，不修改数据库 schema、迁移、版本号或发布流程。


## T3 validation / T3 验证

- A logical model call shares its three-send budget and absolute deadline across
  compatibility fallback, backoff, headers, body reads and cancellation drain.
  The default total timeout is 600 seconds (finite 1–3600); idle timeouts retain
  their meaning. The shared HTTP helper leaves its new idle option disabled for
  existing callers. DNS that is still resolving retains its detached slot, and
  returning after cancellation or expiry cannot open or send a new connection.
- JSON, error bodies, SSE lines/events/total input and queued UTF-8 text have
  enforced read-time bounds. Heartbeats count toward total input. Provider
  terminal events close promptly; OpenAI finish reasons still permit trailing
  usage. Oversized or incomplete tool arguments cannot become executable calls.
- Raw usage evidence remains separate from the compatible eight-field public
  projection. Missing, malformed or non-final counters are unknown; genuine
  measured zero stays zero. Cancelled calls keep their original accounting
  identity and detached capacity until accounting completes. Existing team
  quota entry points and automatic mode preserve unknown reservations rather
  than treating them as free calls. This item does not introduce new team quota
  entry points for previously unwired capabilities or change the database schema.
- Independent read-only implementation review found no remaining blockers after
  fixes to cancellation accounting, provider-error usage, malformed cache
  evidence, automatic-mode admission and late settlement branches. Targeted
  regression batches passed 268 tests and a final 82-test review acceptance run;
  strict mypy passed all eight configured source files. Full candidate pre-commit,
  38 PR harness scenarios, directory coverage and source secret scan passed.
- Real Ark short streaming requests confirm Stop after visible text, exactly one
  late callback for the old call (99 tokens), and successful next-call recovery
  (75 tokens). The initial 90-second probe reached HTTP 200 but no final usage;
  its usage remains unknown and is excluded from known usage subtotals.
- Chromium drove the real Stop control and then completed the same session via
  two native tool calls. Their returned usage (31,879 total tokens) matches the
  frame's 31,716 input + 163 output. The cancelled first browser call had no
  recorded response and unknown usage: it proves neither zero sends nor a
  cancellation after visible streaming. The separate short streaming test above
  covers that boundary. The UI issued two user-message POSTs and one cancel POST,
  reached done, and reported no uncaught page errors.
- The first live test failure exposed the supplied credential in a local pytest
  traceback through the configuration repr. That temporary log was immediately
  scrubbed. LLMConfig now omits its API key from repr, a regression test covers
  this, and subsequent live-test output is scrubbed before writing. No credential
  entered a source file, fixture, commit or CI configuration; retained evidence
  is checked before delivery.

T3 的慢头、滴流、心跳、长行、背压和协议终态由本地可控服务验证；
Ark 实测覆盖流式停止、旧调用回记和下一调用恢复。浏览器取消发生于
可见文本之前，记录明确保留该边界；不把未知计量或失败验证写成成功。
三浏览器矩阵 33/33 已通过，wheel/sdist 验证及独立无依赖安装 smoke
（13 个模块、604 个 Skills）已通过。完整离线套件及提交 CI 均已通过。


The first full T3 run reported three failures. Two characterization assertions
used an obsolete urllib monkeypatch and therefore injected zero attempts into
the new deadline-aware transport. The fixture now uses the transport injection
point and a finite, size-readable BytesIO response. Independent review confirmed
the same two-attempt recovery and byte-identical existing golden; no golden or
audit digest changed. The third failure was the unchanged first-cell 15-second
kernel timeout also observed in T1/T2. All three passed together after the fixture
fix (22.75 seconds; kernel test body 8.74 seconds), with the original timeout.
The full suite passed after this correction; its earlier failure is retained.

首轮完整测试的三处失败已如实保留：两处为旧 HTTP 夹具入口失效，
修复后原 golden 逐字节一致；另一处为原有首次 Cell 超时，未放宽阈值。
三项复测及随后完整套件均通过。


Final local T3 verification: **8,957 passed, 26 skipped**, zero failures/errors,
in 823.09 seconds. Response capture assembled all workers and checked **1,167
route/status shapes**, **212/212 routes**, with no breaking drift. The recorded
suite includes 209 kernel, 119 agent, 380 gateway, 81 MCP, 38 Doubao and 274 LLM
cases with no failures. Full clean-candidate pre-commit and all 38 PR harness
scenarios passed after the fixture correction. The final wheel/sdist is built
from that candidate so the two original user document edits are excluded from
both the commit and the distribution. Publishing, version changes, database
migration and merging to main remain outside this work.

最终本地验证通过：8,957 项通过、26 项跳过，耗时 823.09 秒；全部 worker 的
响应捕获完整合并，1,167 种响应形状无破坏性变化，212 条路由全部覆盖。
修复夹具后的全量 pre-commit 与 38 个 PR 场景均通过。
最终分发包从干净候选构建，排除两份原有文档修改；远端 CI 通过后进入 T4。


T3 commit `cc801345ccb10cecd14910f8ca177675258ab212` passed all 25 applicable
jobs (four not applicable jobs skipped) in [CI 34848725299](https://github.com/PKU-YuanGroup/OpenAI4S/actions/runs/34848725299).
The first local CI watcher exited on a TLS handshake timeout; a timestamped
read-only API monitor recovered and confirmed completion. No CI jobs were
cancelled or restarted.

T3 提交的 25 项适用 CI 作业全部通过；四项不适用作业跳过。首次本地监视进程
因 TLS 握手超时退出，随后恢复只读监视并核实最终状态，未取消或重跑 CI 作业。

## T4 Files validation / Files 验证

The initial regression run reproduced five failures: frame cards bypassed the
paged result, closed-dock loads did not populate filters, refresh lost requested
capacity, partial final pages did not grow correctly, and session switches could
paint previous cards. Cards, count, empty state and load-more now read the same
owned result. Frame data has a separate session/generation identity; project
refresh walks the existing artifact-index while retaining requested capacity.
Filters reset to 50, in-place WS updates re-slice current data, hidden rows stay
excluded, and conversation artifact visibility and retained drafts are unchanged.

Independent read-only review found no remaining implementation blockers. Its
project refresh and ownership probes were added to the fixed tests, including
second-page interleavings, repeated cursors and zero-progress failure. The full
frontend suite passed 705 tests; browser checks and full offline validation passed.

首轮复现五处失败。现在卡片、数量、空状态及加载更多共用具备会话归属的结果；
初始读取和 WS 更新都会重算，刷新保留已请求的容量，筛选变更回到第一页。
独立只读复核暂无阻断问题，补充的项目多页刷新与迟到响应测试已通过；
全量前端 705 项、浏览器检查和完整离线验证通过。


The real Chromium fixture found an additional server boundary: the project index
applied its limit before priority-hidden rows were removed by the client, so a
50-row response painted 49 cards. The index repository now excludes priority-hidden
rows before LIMIT; ordering, cursors, response shape and the legacy array endpoint
are preserved. Independent read-only review confirmed this scope. A real-route
regression covers 60 newer hidden rows in front of 125 visible rows, 50/50/25
pages, combined filters, all-hidden empty state and the unchanged legacy array.
The old in-progress full run was stopped after 3,801 passes and ten skips so the
updated candidate can receive a complete response-capture run; that partial run
is not final verification.

真实 Chromium 另发现项目索引的隐藏过滤发生于分页后，导致 50 行响应只显示
49 张卡片。现于 LIMIT 前排除 priority 隐藏行，排序、游标、响应结构及旧数组
接口保持；独立只读复核认可范围。新增真实路由回归覆盖 60 个隐藏新记录和
125 个可见记录的分页、组合筛选、全隐藏空状态及旧数组兼容性。
正在运行的旧候选完整测试在 3,801 项通过、10 项跳过后主动停止，后续以新候选
重新完成完整响应捕获，不把部分运行作为最终验证。


The three-engine matrix first passed 36/36. After strengthening the delayed-read
and REST-refresh assertions, all three Files scenes passed again, while the
existing WebKit editor scene timed out once waiting for ready (35/36 overall).
Its standalone recheck and the entire WebKit matrix then passed with unchanged
timeouts (12/12). Independent review found no T4 editor-state dependency that
explains this observation; its cause remains unproven and the failure is retained.
The final Files checks also compare both real DOM IDs for equal names in two
sessions, rather than relying only on intermediate arrays.

The existing Ark artifact `a-e173b9ee7826` / `ark-edit-notes.txt` passed Generated
versus Uploaded filtering in Chromium, Firefox and WebKit, with no new model
requests. The observed version was `v-37083588ebbd`, the head after T2 editor
validation; the original generation receipt records `v-4d33b4cdb21b`. These are
separate observations, not an assertion that editing left the original head intact.

三引擎矩阵首次 36/36 通过。加强迟到读取与 REST 刷新断言后，Files 三引擎再次
通过，但既有 WebKit 编辑器发生一次就绪超时；原阈值独立复查及整个 WebKit
矩阵随后通过（12/12）。未证明超时根因，保留失败记录；最终 Files 检查也在
真实 DOM 中核对跨会话同名文件的两个不同 ID。

复用 Ark 产物的来源筛选在三引擎均通过，本项没有新增模型请求。当前观察版本
为 T2 编辑验证后的 `v-37083588ebbd`，原始生成记录为 `v-4d33b4cdb21b`，
分别记录原始生成与本次读取事实。


Final T4 offline verification passed **8,958 tests, 26 skipped**,
with zero failures/errors in 916.088 seconds. All workers' response evidence
assembled successfully: **1,167 shapes, 212/212 routes**, no breaking drift.
The full frontend suite passed 705 cases; 58 targeted backend cases, typecheck,
i18n extraction, clean-candidate all-files pre-commit, 38 PR harness scenarios,
165-directory bilingual coverage (1,537 direct assets), source secret scan
(3,857 files), 212-route contract check, and Skills installer/package checks
(16 selftests; 2,283 files / 604 Skills) passed. Final source and dist are paired.
The two original document modifications remain byte-identical and excluded.

T4 完整离线验证：8,958 项通过、26 项跳过，零失败/错误，
耗时 916.088 秒；全部响应证据完整合并，1,167 种形状无破坏性漂移，
212 条路由全部覆盖。前端、针对性后端、类型、双语提取、全量 pre-commit、
harness、目录清单、secret scan、响应契约和 Skills 包检查均通过。
原有两份未提交文档保持逐字节一致，前端源码和 dist 同批交付。


Initial T4 commit `d0d614fb88bdf3702259f1655f644fd35f8f275c` reached a
[Chromium CI failure](https://github.com/PKU-YuanGroup/OpenAI4S/actions/runs/34855451356/job/104013715852)
in the existing Stage 1 trusted-delivery browser harness. That harness searched
only the first rendered page for a delegated artifact among more than 100 rows;
correct 50-row pagination made the assumption false. The harness now uses the
real Files filename search before opening the same target. All existing producer,
immutable-link, checksum, deduplication and zero-execution assertions remain.
The complete local Stage 1 acceptance then passed: 100 links checked initially,
after reload and after reopen; child-frame provenance and immutable old bytes
verified; no model or external network calls; all owned resources cleaned up.
The production code and dist are unchanged by this follow-up. The 8,958-test full
suite remains evidence for those unchanged sources; the affected Stage 1 browser
acceptance and 24 crosswalk/Stage tests were additionally run after the change.

T4 首次提交的 Chromium CI 在既有 Stage 1 脚本中失败：脚本假定目标产物一定
位于首屏，而正确的 50 项分页使该假定失效。现通过真实文件名搜索控件定位
同一目标，保留全部 producer、不可变链接、校验、去重和零执行断言。
本地完整 Stage 1 已通过：初次、刷新、重开分别核对 100 个链接，验证子任务
溯源和旧版本字节，未调用模型或外部网络，临时资源清理完成。
本次跟进只调整验收脚本与记录，生产代码和 dist 未变；补跑完整受影响浏览器
验收及 24 项 crosswalk/Stage 测试，保留此前 8,958 项完整套件证据。


Final T4 candidate `0f49f82520ff1cc87362d500974f658042bdad02` passed all
25 applicable jobs (four not applicable jobs skipped) in [CI 34856536266](https://github.com/PKU-YuanGroup/OpenAI4S/actions/runs/34856536266).
The Chromium job's actual Stage 1 summary confirms 300 successful link/checksum
checks, delegated producer identity, immutable old bytes, complete cleanup and
zero live model calls. Its Files matrix also passed. A one-time read-only browser
inspection during the long tail observed Python 3.12 at 99% and shape capture at
92%, without visible failures; neither was called complete until the final success.
The earlier run's remaining jobs were cancelled by the repository's concurrency
rule when the tested follow-up was pushed; its Chromium failure remains recorded.
The follow-up wheel's entry bytes are identical to the independently installed
smoke-tested wheel. Its sdist also excludes both original user document edits.

T4 最终候选的 25 项适用 CI 门禁全部通过，四项不适用作业跳过。Chromium 的
实际 Stage 1 记录确认 300 次链接与校验和检查、子任务来源、旧版本字节和
完整清理均通过，未调用真实模型；Files 矩阵也通过。原失败运行及随后因仓库
并发规则取消的剩余作业如实保留。最终 wheel 内容与独立安装验证的 wheel
一致，sdist 继续排除原有两份文档修改。T5 在上述最终 CI 成功后开始。


## T5 — navigation ownership / 导航归属

The original implementation failed all nine new controlled-order regressions.
Navigation still uses the existing `_openGen`; session and folder reads now have
independent request identities and captured project scopes. Success, failure,
paging, follow-up reads and finally only publish for their current owner. A newer
same-visit refresh inherits any pending load-more budget and immediately takes
over a navigation wait, without waiting for the obsolete socket or sending a
replacement request itself. Failed reads preserve confirmed rows and report a
GET-only retry; malformed success bodies cannot imply an empty project.

Project opens, switches, deep links and accepted creation continuations check
navigation identity. Upload destinations remain bound to their accepted frame;
a new visit cannot borrow an old creation flight or delete its successor's cache.
The project switcher retains the open frame as before and refreshes that frame's
Files snapshot for the new navigation generation. Background execution is not
cancelled. No API, database schema, migration, quota or runtime dependency changes.

原代码在九项新增乱序回归中全部失败。现沿用 `_openGen` 作为导航身份，会话与
文件夹请求各自记录独立代次及固定项目范围；成功、失败、分页、后续读取和
finally 均验证归属。同次导航的后台刷新继承正在加载的目标页数，并立即接管
导航等待，不等待旧连接结束，也不为接管重复发请求。读取失败保留已确认行并
提供只读重试，畸形 200 不会被当作空项目。项目打开、切换、深链和会话创建
续执行均核对身份；已接受上传保持原 frame 归属。项目菜单仍保留已打开会话，
并为新导航代次重新读取 Files 快照；不取消后台任务，不改 API、schema、迁移、
配额或核心依赖。

Independent read-only review found and closed four additional edges: opening a
conversation erased folder errors; refresh swallowed in-flight load-more;
navigation waited for an obsolete request; generic read-error text falsely denied
an already accepted creation. A subsequent joint review found and closed the
retained-frame Files snapshot gap. Browser fault assertions were strengthened to
capture real response bytes before an ABA rename, then wait for client text
consumption and rendering before checking late-response effects.

独立只读复核发现并关闭了打开会话抹掉文件夹错误、后台刷新吞掉加载更多、
导航等待旧请求、通用错误提示否认已接受创建四个边界；联合复核还修复了保留
会话的 Files 快照缺口。浏览器先固定旧响应字节，再改名验证往返导航，并等
页面实际消费迟到响应和完成渲染后，才断言列表及 loading 未被覆盖。

Current local evidence: 728 frontend tests passed, typecheck/build and i18n
extraction passed, clean-candidate all-files pre-commit passed. Chromium, Firefox
and WebKit navigation scenes passed real 100/101 session pages, folder/ABA races,
Files retention, failed/malformed read retry, zero auto-created frames and zero
cancel/interrupt requests. Full offline capture and the complete Chromium CI
browser sequence (including Stage 1), followed by all-engine matrices, are pending.

当前本地证据：728 项前端测试、类型检查、构建、双语提取及干净候选全量
pre-commit 通过。三引擎导航场景通过真实 100/101 会话分页、文件夹及往返乱序、
Files 保留、失败/畸形读取重试；零自动创建、零取消/中断请求。
完整离线捕获及完整 Chromium CI 串（含 Stage 1）与三引擎矩阵仍在进行。

Real Ark navigation evidence belongs to frame `f-ab8ec20a985a` in project
`proj_c3d91520494d`. The accepted job reported `running=true` before A→B→A;
one message was submitted, no frame was created and no cancel/interrupt was sent.
Navigation finished about 31 ms before the first recorded model invocation, so
this proves navigation during an accepted running job, not navigation during
visible SSE output. The same job then completed two actual Ark requests:

| Request ID | Seconds | Actual prompt / completion / total tokens |
| --- | ---: | --- |
| `021789399282909c175de5f11b223efcc3ac4becea38bcfd240a0` | 9.341 | 15188 / 132 / 15320 |
| `02178939929226233d44b650730fd07bb29a2150d8432a03a6709` | 8.812 | 15299 / 200 / 15499 |

Both returned HTTP 200 on the configured Ark plan/v3 endpoint; requested model
`doubao-seed-2.0-pro`, returned `doubao-seed-2-1-turbo-260628`. Raw reasoning counts
were 71/138 and cache counts 0/15160. Each response was capped at 1024 output tokens.
The initial harness incorrectly waited for `completed`; the actual status API
returns `done`. Its failure remains recorded. A GET-only browser reopen verified
`done`, `running=false` and the stored `NAVIGATION_READY` completion, with zero
additional model or POST requests. Receipts retain this correction and exact
per-call usage; no credential is stored in delivery content.

真实 Ark 验证在任务已接受且 `running=true` 时执行 A→B→A：仅一次消息提交，
零会话创建、零取消/中断。导航返回比首次模型调用早约 31 毫秒，因此只证明
已接受任务运行阶段的切换，不将其描述为可见流式输出阶段切换。同一任务随后
完成上表两次真实 Ark 请求，总实际用量 30,819 tokens。原脚本误等 `completed`
而真实终态为 `done`；保留原失败，并通过零新增模型/POST 的只读重开确认
任务结束及 `NAVIGATION_READY` 内容。请求、耗时、原始 usage 与修正边界均留存，
凭据不进入交付内容。


Final independent review found no remaining substantive blockers and confirmed
all 47 closed Crosswalk evidence digests unchanged. The complete local Chromium
CI sequence passed smoke, admission fault, P1 controls, Stage 1 trusted delivery
(300 immutable link/checksum checks), and sandbox preview. All three full browser
matrices passed 39/39. The wheel/sdist passed release verification and an isolated
`--no-deps`, `-I` install smoke (13 modules, 604 Skills); the sdist contains the
committed versions of the two original user documents, not their working edits.
Other gates passed: 38 PR harness scenarios, 212/212 route contracts, 165-directory
bilingual coverage (1,541 direct assets), secret scan (3,861 sources), and Skills
installer/package checks (16 tests, 2,283 files / 604 Skills). Full offline capture
is the remaining local gate before committing this stage.

最终独立复核无剩余实质阻断，47 条已关闭 Crosswalk 的证据摘要未变化。
完整 Chromium CI 串及三引擎矩阵 39/39 通过；Stage 1 核对 300 个不可变链接
及校验和。wheel/sdist、独立无依赖安装 smoke、harness、路由契约、双语目录、
secret scan、Skills 安装及打包门禁均通过；两份原有文档工作区修改未进入包中。
完整离线捕获仍是本项提交前最后一道本地门禁。


Final T5 offline capture passed **8,958 tests, 26 skipped**,
with zero failures/errors in 825.789 seconds. The complete worker evidence
assembled 1,167 route/status shapes over all 212 routes with no breaking drift.
Local acceptance is complete; commit/push and the matching CI are pending.

T5 最终完整离线捕获通过 **8,958 项、跳过 26 项**，
零失败/错误，耗时 825.789 秒；1,167 种响应形状覆盖全部 212 条路由，
无破坏性漂移。本地验收已完成，等待提交、推送及对应提交的 CI。


Initial T5 commit `09c62b3dee4e54707e038ba187971a8de5ab4a39` encountered a
[Firefox CI failure](https://github.com/PKU-YuanGroup/OpenAI4S/actions/runs/34863558886/job/104041610293).
The Files helper's final empty-element assertion observed `1` instead of `0`;
its card/count/more/IDs/empty reads crossed multiple asynchronous browser turns.
The log does not identify the exact phase or establish a unique production cause.
The helper now captures the entire DOM projection in one evaluation and waits for
cards, count, pagination and empty state together, with the same timeout and richer
failure evidence. The following navigation scene also omitted resetting persistent
Files type/origin filters. A real Firefox run with retained CSV/Generated filters
reproduced the missing uploaded-text card; the same reproduction passed after the
scene explicitly reset all its filter preconditions. CI's exact retained values
remain unknown, so that specific causal link is an inference, not a recorded fact.
No production code, dist or timeout changed in this follow-up. The successful
8,958-test capture, 728 frontend cases and package install smoke continue to cover
those unchanged sources; all three full browser matrices are being rerun for the
changed helpers. The initial failure remains in the evidence record.

T5 初次提交在 Firefox CI 的 Files 空态断言失败，之后导航场景等待卡片超时。
Files helper 原先跨多轮异步读取 DOM，现一次采集完整投影，并在同一原有期限内
同时等待卡片、数量、分页及空态一致；日志不足以唯一证明原失败的生产根因。
导航场景遗漏清理持久 type/origin 筛选，本地真实 Firefox 保留 CSV/Generated
筛选可稳定复现上传文本卡片缺失，清理后同一复现通过；但 CI 当时的具体筛选值
未记录，不将推断写成已知事实。此次只改验收脚本与记录，生产源码、dist 和
超时阈值未变；复用原完整离线、前端和安装证据，并重跑受影响的三引擎矩阵。


The follow-up passed independent read-only review, the full three-engine matrix
(39/39), the forced retained-filter reproduction, 136 Crosswalk/Stage1/static UI
contract tests, and clean-candidate all-files pre-commit. No assertion or timeout
was weakened. The tested follow-up will be pushed for a new matching CI run; the
repository may cancel unfinished jobs in the earlier failed run by its configured
branch concurrency rule. Its Firefox failure is not erased or reported as a pass.

跟进修正通过独立只读复核、三引擎矩阵 39/39、强制残留筛选复现、136 项
Crosswalk/Stage1/静态 UI 契约回归及干净候选全量 pre-commit。未削弱断言或
放宽期限。跟进提交将触发对应新 CI；原失败运行中的未完成作业可能被仓库
分支并发规则自动取消，原 Firefox 失败仍保留，不计为通过。


The second T5 CI candidate `e950f896e80c0af485f517f29df34954052aca88`
passed Firefox and WebKit but exposed a real production race in
[Chromium P1 controls](https://github.com/PKU-YuanGroup/OpenAI4S/actions/runs/34864726201/job/104045895387).
Two deliberate New clicks shared a navigation generation: the first frame was
published before its directory read finished, and its late opener invalidated
the second accepted creation. A controlled regression failed before the fix.
Fresh New requests now acquire navigation ownership before POST; the no-frame
Attach/first-Send shared creation keeps its existing visit. A failed fresh request
recovers the retained frame through read-only directory/history/Files reads,
without replaying POST or rolling the generation back. The accepted frames are
preserved. Tests exercise both response orders using an opener that really
advances navigation, plus failure recovery. All 731 frontend cases and the
source/dist build pass. Real-button controlled scenarios passed Chromium,
Firefox and WebKit: four accepted creations, one injected rejected creation,
no retry/delete/cancel, and restored retained-frame Files. The complete browser
sequence and independent review are still pending for this follow-up.

T5 第二份候选在 Firefox、WebKit 通过，但 Chromium 的既有 P1 控件检查发现
真实竞态：连续两次新建共享导航代次，第一次延迟打开使第二次已接受创建失去
导航归属。新增受控回归先复现失败；fresh 新建现在在 POST 前取得新导航身份，
无当前会话的 Attach/首发共享路径保持原行为。新建失败后只读恢复保留会话的
目录、消息及 Files，不重发 POST、不回退代次，也不删除已接受的会话。
731 项前端测试和源码/dist 构建通过；真实按钮在三个引擎通过两种响应顺序及
失败恢复检查。完整浏览器串及本次独立复核仍待完成，原 CI 失败保留。


Read-only review of this follow-up tightened failed-creation recovery: it follows
replacement directory reads, recovers history and Files without rewriting the
retained frame's address or sidebar scope, clears obsolete earlier-page loading,
and restores the creation error only while that intent still owns navigation.
Tests now cover frame A with sidebar B and prevent a late recovery from posting
an old error after a new visit. The final frontend suite passes 732 tests.
A separate local smoke failure observed two head reads versus one in its baseline.
That baseline had HTTP caching enabled, while only the fault reload installed a
Playwright route (which disables HTTP cache, per the installed Playwright API
contract). Both measured reloads now use the same route/cache mode; only the exact
historical response switches to 404. All version, request-count and Retry assertions
and timeouts remain. The original failure is retained; cache asymmetry is a proven
harness defect, not a claim that its log uniquely identifies every extra request.
The second CI run completed with 24 passing jobs and the single Chromium failure.

本次只读复核进一步收紧失败恢复：跟随被刷新替代的目录读取，只读恢复历史和
Files，保留原会话地址与当前侧栏范围，清理失效分页的加载状态，并仅在仍持有
导航归属时恢复错误提示。新增 A 会话+B 侧栏和晚到恢复不得重贴旧错误的断言，
最终前端 732 项通过。另保留 smoke 的 head 请求数 2 对 1 失败记录；确认正常
基线与故障回合的 Playwright route/HTTP 缓存条件不同，现统一两回合的拦截与
缓存条件，只切换固定历史版本的 404。请求数、版本和重试断言及期限均保留，
不把此条件缺口解释为日志已唯一证明每个额外请求来源。第二次 CI 最终为
24 个作业通过，仅 Chromium 失败。


Equalizing cache mode alone did not remove the smoke's 2-versus-1 head-count
failure. A subsequent CDP diagnostic run passed 1/1 and traced its head reads to
`fillDataPreview → tileThumb → renderConversationArtifacts`; no stack was captured
for the earlier failure, so its exact cause remains unproven. The old comparison
also depended on how often legitimate artifact-list refreshes repaint thumbnails.
The acceptance fixture now temporarily hides the target thumbnail using the real
artifact-priority API while retaining the real immutable Notebook bindings and
metadata. Normal historical reads, injected 404 and Retry must each make **zero**
head requests. All fixed-version bytes/figures/tables/download assertions remain,
and priority is restored afterward. This isolates the contract under test without
weakening it to tolerate extra head requests. The controlled C1 case has passed;
the remaining complete browser sequence is still running.

统一缓存条件后仍保留一次 2 对 1 失败。后续 CDP 诊断以 1 对 1 通过，其 head
调用栈来自普通缩略图；早先失败没有调用栈，具体来源仍不作已证实结论。
原比较还依赖合法列表刷新重画缩略图的次数。验收现经真实产物 priority 接口
暂时隐藏目标缩略图，保留真实元数据与 Notebook 不可变绑定，严格要求正常
历史读取、404 和 Retry 各自零 head 请求，且保留全部版本字节、图、表和下载
断言，结束后恢复 priority。受控 C1 已通过，完整浏览器串仍在执行。


The complete final smoke passed, including the revised full-range cleanup and
waiting for the Retry error to be rendered before the zero-head assertion.
Independent review found no remaining blockers and reconfirmed the Notebook
export claim behind Crosswalk `R7/P1-04`. Only its evidence digest was re-recorded;
the other 46 closed rows are unchanged. The complete Chromium sequence passed
smoke, admission fault, P1 controls, Stage 1 (300 immutable link/checksum checks)
and sandbox preview. Chromium/Firefox/WebKit matrices passed 39/39. Final package
resources and an isolated no-dependency install smoke passed (13 modules, 604 Skills).
The offline run started before the evidence re-recording still needs its final
failure summary; a fresh complete run will establish the final capture.

最终完整 smoke 通过，包含覆盖正常阶段失败的清理以及重试错误重新渲染后才作
零 head 断言。独立复核无剩余阻断，并重新核对 `R7/P1-04` 的 Notebook 导出
声明，只重录该条证据摘要，其余 46 条已关闭记录不变。完整 Chromium 串及
三引擎矩阵 39/39 通过；交付包资源与独立无依赖安装 smoke 通过。先前离线
运行早于摘要重录启动，仍等待最终失败详情；将重新完整运行以建立最终捕获。


### T5 final acceptance / 最终验收

Final commit `2f53bf9c7a2d69bc54f2cdd85c8b06b4a911bb61` is synchronized
with `origin/next`. CI [34869797915](https://github.com/PKU-YuanGroup/OpenAI4S/actions/runs/34869797915)
passed all 25 applicable jobs; four unrelated jobs were skipped. The fresh
complete offline run passed 8958 tests with 26 skips; capture checked 1167
shapes across 212/212 routes. The earlier pre-crosswalk-update run had exactly
one failure (stale evidence digest), 8957 passes and 26 skips. Original user
document hashes remain unchanged. Independent review checked the final SHA's
Chromium, Linux interrupt/full sandbox, container and installed-wheel job logs.

最终提交与远端 next 同步；CI 25 个适用作业通过，4 个不适用作业跳过。
重新完整离线运行 8958 通过、26 跳过，捕获核对 1167 形状及 212/212 路由。
摘要重录前的运行仅一项旧摘要失败，其余 8957 通过、26 跳过。原有两份用户
文档字节保持不变。独立复核核对了同一 SHA 的 Chromium、Linux 两类沙箱、
容器和 wheel 安装作业证据。

### T6 — provenance and lightweight exports / 溯源与轻量导出

Local acceptance complete; delivery CI pending. Reproductions exposed malformed/failed lineage reads normalized
to empty, a false reproduction-generation label, swallowed required export
reads and mixed version/name/size metadata when the head changed during export.
Network validators now preserve legal historical nullable values and extension
fields while rejecting malformed core records. Explicit read errors have a
read-only retry; missing recorded evidence has separate copy. Export targets
and session titles are frozen before reads, and every required read must pass
before a Blob/download/success message is created. Full session package export
is unchanged. Source identity uses the artifact's owning session and actual
producing Cell ID; delegated records retain their own identity.

本地验收完成，等待交付 CI。已复现失败／畸形读取被伪装成空记录、虚假的复现代码生成提示、
导出吞掉必需读取失败，以及导出期间版本、文件名和大小混用。网络校验保留
合法历史空值和扩展字段，拒绝畸形核心记录；错误提供只读重试，无记录单独
说明。导出前固定目标和标题，全部必需读取成功后才创建 Blob、下载和成功
提示。完整会话包导出不变。来源结合产物所属会话与实际生产 Cell ID；子任务
保留自身身份。本地浏览器、全量门禁和只读复核已完成，等待提交与 CI。


Independent read-only review found and closed duplicate reads in the real
synchronous Viewer composition, delegated-code misstatement, invalidation by a
sidebar-only project switch, missing/invalid environment source, lost stable
ownership in exact-version resolution and existing tabs, and historical null
metadata incorrectly filled from the latest head. Exact history now retains its
unknown fields; bare deep links do not invent session ownership. Recorded Cell
links require the actual owning session and producing ID in the Notebook, then
locate that DOM identity, including folded revisions. Missing source in this
response does not imply a recorded child Cell never existed. Same-byte capture
observations remain intact.

独立只读复核关闭了真实同步 Viewer 组合的重复读取、子任务代码误述、仅侧栏
切换使可见读取失效、环境来源缺失／畸形、固定版本解析和旧标签丢失所属信息，
以及历史空值借用最新头事实的问题。历史未知字段保持未知，裸深链不虚构所属
会话；Cell 链接要求实际所属会话及 Notebook 中的生产 ID，再按该 DOM 身份
定位，包含折叠的旧修订。响应未包含源码不代表已记录的子 Cell 不存在；相同
字节的后续捕获记录保持不变。

Local frozen-candidate evidence:

- Offline pytest: **8958 passed, 26 skipped, zero failures/errors**, 834.034 s.
  The same full run captured **1167 shapes / 212 of 212 routes**, with no breaking
  drift. Response contract check passed independently.
- Frontend: **800 tests / 78 files**, typechecks and production build passed;
  committed source and dist agree. All-file pre-commit, mypy, directory coverage
  (165 directories / 1547 direct files), source secret scan (3867 files), harness
  (38 scenarios), Skill selftest and npm pack check (604 Skills) passed.
- Complete Chromium smoke, admission-fault, P1 controls, Stage 1 and sandbox
  preview passed. Chromium, Firefox and WebKit matrices passed **42/42 checks**.
  New controls prove one GET per click/retry, no partial downloads after required
  read failures or malformed success, retained-frame reads after sidebar changes,
  precise producer navigation, and fixed export metadata while real WS events
  advance the head. Unit tests cover ABA and both success/failure delivery orders.
- The real Ark-produced `a-e173b9ee7826` was reused at `v-37083588ebbd` in all
  three engines, with no new model requests. Downloaded metadata matches both
  server lineage and version-list reads byte-for-value; JSON SHA-256 is
  `c425e9386f3f7a2c648d0586eb516dde471dcbd95227c4401298a086ec812280`,
  Markdown SHA-256 is
  `de5123c83e722cdd48b773c4714c2a61db8ece9621a4360809c60314142dd545`.
  Original live request/model/usage are recorded under T2; this reuse does not
  constitute a new model or protocol verification.
- Clean-candidate wheel/sdist resource verification and an isolated no-dependency
  wheel install smoke passed (13 modules, 604 Skills). Original user documents
  are excluded from the candidate and retain their original hashes.

本地冻结候选：离线 8958 通过、26 跳过，834.034 秒，零失败／错误；同次运行
捕获 1167 形状、覆盖 212/212 路由，契约另行通过。前端 800 项通过，类型检查、
构建与 dist 一致；全量 pre-commit/mypy、双语目录、密钥扫描、38 项 harness
和 Skills 包检查通过。完整 Chromium 深度走查及三引擎矩阵 42/42 通过；新场景
覆盖一次操作一次读取、失败不下载、侧栏切换、精确生产者，以及真实 head
前进期间的固定导出。ABA 和成功／失败乱序主要由单测覆盖。三引擎复用 T2
真实 Ark 产物导出，两个文件的摘要均一致；未新增模型请求，原请求及 usage
沿用 T2 证据。干净候选发行资产和独立无依赖安装 smoke 通过，原用户文档未变。

Early failed attempts remain in the private evidence directory: the initial
reproduction tests; two browser failures exposing lost owner metadata followed
by a fixture that incorrectly expected producer ID in the ordinary artifact list
(corrected to the real lineage DTO); a default-sandbox run with 10 local-socket
setup errors and an npm cache write denial (both corresponding final checks
passed with their required local permissions). An unstaged deleted old dist name
also confused the directory inventory; the correctly staged clean candidate
passed. These are not represented as successful tests. Delivery CI and final SHA
are recorded in the following stage or the final delivery receipt.

早期失败记录保留在本地证据目录：初始复现、所属信息丢失的浏览器失败，以及
随后错误预期普通列表含生产 ID 的夹具（改用真实 lineage DTO）；默认沙箱下
10 项本地端口初始化错误和 npm 缓存写入受限，对应最终检查均已按所需权限
通过。未暂存的旧 dist 删除项也曾影响目录清单，正确暂存的干净候选已通过。
这些失败不算成功证据；最终 SHA 与 CI 结果写入下一阶段或最终交付记录。


## T6 delivery CI / T6 交付 CI

Commit `14947d25c4ef55aa205ea11f6101c801e2f56ac9` passed all **25 applicable
jobs**, with **4 not-applicable jobs skipped**, in
[CI 34876890803](https://github.com/PKU-YuanGroup/OpenAI4S/actions/runs/34876890803).
Independent review checked job metadata and checkout SHA against that commit.
Chromium, Firefox and WebKit each passed 800 frontend tests, source/dist parity
and 14 browser scenarios, including the new provenance controls.

| Python | Passed / 通过 | Skipped / 跳过 |
|---|---|---|
| 3.10 | 8913 | 71 |
| 3.12 | 8922 | 62 |
| 3.13 | 8916 | 68 |
| 3.14 | 8916 | 68 |

Each Python job had zero failures and six warnings; per-test skip reasons were
not printed and are not inferred. Frozen response shapes reported 1167 shapes,
212/212 routes and no breaking drift, with additive `/compute/remote` and
`/compute/ssh-aliases` observations. That job did not print a pytest total.
Container, independent wheel installs and separate Linux interrupt/full sandbox
jobs passed. The interrupt job allows raw network; egress denial is proved by
the separate full sandbox job. Independent sdist installation remains a T9 gate.
Stage 1 standard-profile readiness uses metadata fixtures, not runtime proof.

该提交 25 项适用 CI 全部成功、4 项不适用跳过。独立审查核对了作业与 checkout
身份；三浏览器各 800 单测、构建一致性与 14 场景通过。各 Python 版本的跳过数
按表保留，不推断未打印的具体原因。响应捕获为 1167 形状、212/212 路由，无
破坏性变化，但有两项 additive 观察，不能称为零差异。容器、独立 wheel 安装
及两项不同边界的 Linux 验证通过；sdist 独立安装继续留在 T9。

## T7 snapshot preparation / T7 快照准备

The [English](pre-upgrade-snapshot-design.md) and
[Chinese](pre-upgrade-snapshot-design_zh.md) design fixes location, owner-only
permissions, 4 GiB image / 8 GiB plus 1 MiB replacement capacity, independent
validation, publication and restore boundaries. S01–S12 are explicitly **future
acceptance cases**, not implemented or executed snapshot-retention tests.

Review closed the distinction between pre-numbered-migration backup and
pre-initialization snapshot, missing versus hot-journal preflight, source logical
state versus recovery byte changes, legacy backup capacity, publication uncertainty
after pointer replacement, full directory fsync ordering, aggregate metadata
limits, and cleanup before starting another candidate. Restore inspection uses
raw read-only SQLite; a separate directory alone cannot isolate absolute file
paths, old authorization or keychain references. No automatic task replay or
credential rollback is claimed.

Existing `tests/test_schema_migrations.py` completed **46 tests successfully**,
including the current successful-migration backup cleanup contract. Independent
read-only review found no remaining blockers. Clean-candidate all-file pre-commit
and mypy passed; bilingual directory coverage passed (165 directories / 1549
files), and source secret scan passed (3869 files). Only six documentation files
change: production, tests, dependencies, schema and committed frontend assets
remain byte-identical to T6. T6 full offline/frontend/browser evidence therefore
continues to apply to that unchanged content; the documentation commit's own CI
is still required before T8. Original user document edits remain excluded.

双语设计明确位置、仅所有者权限、单镜像 4 GiB／替换期 8 GiB 加 1 MiB 容量、
独立校验、发布及恢复边界。S01–S12 全部标为未来验收，未实现快照保留。复核已
关闭初始化前挂点、热日志、源字节与逻辑状态、遗留备份容量、指针替换后不确定、
目录 fsync、元数据合计及旧代清理等问题；恢复先用原始只读 SQLite，独立目录
不等于隔离旧路径、授权或秘密引用，不承诺自动执行或凭据回滚。

现有迁移 46 项通过，独立只读审查无剩余阻断；干净候选全量 pre-commit/mypy、
165 目录／1549 文件清单及 3869 文件密钥扫描通过。本项仅 6 份文档变化，生产、
测试、依赖、schema 与前端资产和 T6 字节一致，相应完整测试复用 T6 证据；仍须
等待本项提交自己的 CI 通过才进入 T8。用户原有文档修改保持排除。


## T7 delivery CI / T7 交付 CI

Commit `1216c98666d4959233215dfc983e97fdde06bb94` passed **25 applicable jobs**,
with **4 not-applicable jobs skipped**, in
[CI 34881084407](https://github.com/PKU-YuanGroup/OpenAI4S/actions/runs/34881084407).
Independent review verified the six-doc scope, reviewed content, job metadata
and main-gate checkout identities. The four Python pass/skip counts match the
T6 CI table above, with zero failures and six warnings per job. All three
engines passed 800 frontend tests, source/dist parity and 14/14 browser scenarios.
Shapes reported 1167 shapes / 212/212 routes without breaking drift; additive
observations were `/compute/remote`, `/compute/ssh-aliases` and `/kernel/packages`.
Container, wheel installation, separate Linux interrupt/full sandbox, route
contract, 38 harness scenarios and remaining applicable gates passed. S01–S12
remain planned future snapshot tests; CI success does not activate retention.

该提交 25 项适用 CI 全部通过、4 项不适用跳过。独立复核核对六文档范围及实际
SHA；Python 数量同上表，三浏览器各 800 单测、构建一致性和 14 场景通过。响应
捕获 1167 形状／212 路由，无破坏性变化，三项 additive 如上。容器、wheel 安装、
独立 Linux 边界、契约及 38 harness 等全部通过；S01–S12 仍是未来快照验收。


## T8 validation / T8 验证

The bilingual slow-connection preparation defines header, admission, body,
upload, WebSocket, observation, output and cleanup budgets; bounded pre-thread
admission; status capacity; and permit ownership. It preserves accepted tasks
when observers disconnect and distinguishes legal slow uploads from deadline
violations. C01–C14 are **future acceptance cases, not executed new features**.
No runtime deadlines, quotas, routes, protocols or frontend assets change.

Independent read-only review passed after tightening admission lock waits,
Expect:100 wire evidence, keepalive re-entry, real socket unblock criteria,
post-admission observer handling and temporary-disk accounting. Existing auth,
upload, body, keepalive and WebSocket regression selection passed **68 tests**,
with 147 deselected. Clean-candidate all-file pre-commit including mypy passed;
bilingual directory coverage passed (165 directories / 1551 files) and source
secret scan passed (3871 files). All non-documentation blobs remain identical
to T7/T6, so unchanged runtime checks reuse T6 evidence; this documentation
commit still requires its own successful CI before T9.

双语准备约定覆盖请求各阶段、线程创建前容量准入、状态查询余量及资源释放归属，
明确合法慢上传、观察断开后任务继续、只读核对未知结果。C01–C14 全部是未来验收，
未启用新期限或配额。独立只读复核通过，现有相关回归 68 项通过、147 项未选中；
干净候选全量 pre-commit/mypy、165 目录／1551 文件清单和 3871 文件密钥扫描通过。
仅六份文档变化，运行时代码与 T7/T6 一致，复用原完整运行证据；进入 T9 前仍须
本项提交 CI 通过。


## T8 delivery CI / T8 交付 CI

Commit `cf68ef11a57eb47c82ea37fdee1d34e239aa034b` passed **25 applicable
jobs**, with **4 not-applicable jobs skipped**, in
[CI 34885167839](https://github.com/PKU-YuanGroup/OpenAI4S/actions/runs/34885167839).
Independent review verified committed blobs, preserved user edits, job metadata
and main-gate checkout identities. Python pass/skip counts match the earlier Python table;
each job reported six warnings and no failures. Three engines each passed 800
frontend tests, source/dist parity and 14/14 scenarios. Shapes reported 1167
shapes / 212/212 routes with no breaking drift; this run had only two additive
observations: `/compute/remote` and `/compute/ssh-aliases`. Container, wheel
installation, separate Linux interrupt/full sandbox, 38 harness scenarios and
all remaining applicable gates passed. C01–C14 remain future acceptance.

本项六文档提交的 25 项适用 CI 全部通过、4 项不适用跳过。独立复核核对提交
及实际 SHA；四 Python 数量同前表，三引擎各 800 单测、构建一致性及 14 场景
通过。响应捕获 1167 形状／212 路由，无破坏性变化，本次仅上述两项 additive。
容器、wheel 安装、独立 Linux 两门禁及 38 harness 等全部通过；C01–C14 未执行。

## T9 final acceptance / T9 最终验收

All final runtime checks below used `cf68ef11a57eb47c82ea37fdee1d34e239aa034b`.
The final delivery changes only this ledger; production, tests, dependencies,
database schema and committed frontend assets remain identical to that successfully
validated runtime. The final delivery reply identifies the documentation
commit and its **own** CI result; this ledger does not predict that result.

Independent read-only acceptance review found no reproducible blockers across
the six core improvements and two preparation items. It additionally ran
**175 frontend tests** and **90 artifact backend tests**, all passing. Full
offline evidence remains **8958 passed / 26 skipped** from T6 on identical
production and test blobs, supplemented by the four-version T8 CI above.
Final checks reuse successful evidence for unchanged content and do not count
skipped jobs, planned P2 cases or metadata-only readiness as executed coverage.

以下最终运行验收针对 `cf68ef11`；交付收尾仅修改本记录，运行内容不变。独立
只读验收未发现六项核心与两项准备的可复现阻断，另行执行前端 175 项和产物
后端 90 项，全部通过。相同运行及测试内容复用 T6 完整离线 8958 通过／26 跳过
和 T8 四版本 CI。最终回复另记本记录提交本身的 CI，不提前宣称该门禁通过。

### Final Ark requests / 最终 Ark 请求

The endpoint and requested/returned models are the same as the live table
above. Eight HTTP requests were made, each to the configured Ark plan/v3
endpoint. Text, callback Stop/READY and the first browser call were capped at 1024
output tokens; the native-tool call and browser artifact recovery at 4096,
with four calls total across the browser Stop/recovery scenario. The
seven measured calls total **46,412 tokens**, plus one **unknown** cancelled
call; that is not a complete aggregate or a cost estimate.

| Scene / 场景 | Request ID / 请求编号 | Seconds / 秒 | Terminal / 终态 | Actual usage: prompt / completion / total / 实际用量 |
|---|---|---:|---|---|
| Non-stream text / 非流文本 | `021789414555313f0c372f6e4f4854baf3c7c3163c5b79d137d23` | 2.843 | stop | 53 / 27 / 80 (reasoning 24; cached 0) |
| Streaming native tool / 流式原生工具 | `021789414558152873b68ad3a5b1c0a1311de00f33c08576b3e81` | 2.772 | tool_calls | 406 / 92 / 498 (reasoning 59; cached 0) |
| Callback Stop + metered drain / 回调后停止及计量排空 | `021789414561242f836aa75e744066f138d0c171bcc7779eb572c` | 3.773 | stop | 57 / 55 / 112 (reasoning 46; cached 0) |
| Next call READY / 下一调用恢复 | `021789414565037b6facf02ebac6f8e2d6055a5df25a907808100` | 3.144 | stop | 51 / 26 / 77 (reasoning 25; cached 0) |
| Browser Stop / 浏览器停止 | `021789414721418474cb012b3b24af2f1528e3bfa9e6caa353044` | 6.426 | Cancelled; transport interrupted; usage unknown / 取消、未知用量 | Unknown / 未知 |
| Browser recovery 1 / 浏览器恢复 1 | `02178941472784653952e287ddd1b7d9109cd0048896ba0d159ad` | 5.778 | tool_calls | 14882 / 148 / 15030 (reasoning 87; cached 2872) |
| Browser recovery 2 / 浏览器恢复 2 | `021789414733748fbcc013cde10a689f8f390ac862f8b04d7ea7c` | 8.302 | stop | 14985 / 86 / 15071 (reasoning 28; cached 14648) |
| Browser recovery 3 / 浏览器恢复 3 | `021789414750933274564af00b8ed8c9fdfc012f2496079304bac` | 5.322 | stop | 15485 / 59 / 15544 (reasoning 1; cached 14648) |

The explicit `live_llm` test cancels after an actual content callback, starts
the next call, and verifies that the old drained result is accounted exactly
once (112 tokens) without old callbacks entering the new turn. The next call
returns READY with 77 tokens. Its raw usage and provider finish reason remain
in the receipt. The separately exercised browser session is **unmetered**:
its first transport is interrupted on Stop and returns no usage. This is
retained as unknown; it is not claimed to have drained or cost zero.

The real Chromium browser received a 33-character `text_chunk` before Stop,
and a read confirmed the original run was still active. Two message POSTs and
one cancel produced a completed next turn and `ark-final-notes.txt`. Recovery
used three model calls and two Cells: the first Cell wrote the file but its
completion bullet failed the past-tense-verb validation, then the next Cell
corrected completion and finished. This is not an error-free, single-Cell run. The
browser visibility predicate also matched the user's prompt, so it does not
independently prove that assistant text was painted before the click; the
receipt supports **real stream arrival before Stop**. The separate live test
proves cancellation after a content callback. No extra live calls were made
to hide or replace this evidence boundary.

显式 `live_llm` 用例在真实内容回调后取消，随后调用恢复；旧调用排空的 112
tokens 恰好记一次，旧文本不进入新回合，下一调用 READY 为 77 tokens。独立
浏览器会话未开启计费排空，停止时中断传输、usage 未返回，仍记未知，不按零。
浏览器 Stop 前收到 33 字符的真实 `text_chunk`，只读状态确认为运行中；两次
message POST、一次取消后下一回合最终完成并生成产物。恢复含三次模型调用、
两个 Cell：首次已写文件但完成摘要未以过去式动词开头，校验失败后下一 Cell
修复完成；不是无中间错误或单 Cell 执行。原可见性断言也可能匹配
用户提示，不能单独证明点击前助手文本已绘制；据此仅认定真实流已到达浏览器。

### Final artifact and package checks / 最终产物及包验证

The generated artifact `a-db7ce2017a22` belongs to frame `f-c00422896ae1`
and project `proj_2acedf8b140c`. Its initial text contained only three synthetic
lines. Chromium, Firefox and WebKit each passed the real editor suite on this
artifact, including delayed loading, conditional save, immutable old bytes,
conflict, draft retention/capacity and lost-response read-only reconciliation.
After editing, all three engines confirmed generated/uploaded source filters
and exported the same edited fixed version `v-21deead6db78`. The observer
recorded no new task POSTs (`taskRequests=[]`) and the server Ark receipt stayed
at four calls; the helper's constant `modelRequests: 0` field is not independent
measurement. Six actual downloads were retained and compared:

- Metadata JSON SHA-256: `5102985f554aef273fed669f770ec1aad1fbc0df25b6299448cc10227e9ca12e`.
- Session Markdown SHA-256: `45a9e21b07bd82f8136b81a076fa1789671d9efbbdf3f76acd4550886dd0d5cc`.

Wheel and sdist were built from a clean candidate excluding the two original
user edits and passed release-artifact verification. Each was installed with
`--no-deps` into a **separate fresh Python 3.12 environment**, outside the
checkout. Both environments contained only OpenAI4S as an installed package
and passed the isolated import/resource smoke: 13 modules and 604 Skills.
The sdist was actually built and installed, not merely inspected as an archive.
The final documentation archive is checked again after this ledger is frozen;
wheel entry equality ties its runtime back to these installations.

最终真实产物在三浏览器编辑通过，再用同一固定版本核对来源筛选及六份实际
导出，两个文件摘要如上，属于编辑后版本而非原始模型字节。实际任务 POST
观察为空，服务端 Ark 回执仍为四次；不以 helper 中固定的零作为调用计量。
wheel 与 sdist 来自排除原有
修改的干净候选，各自真正安装到独立新建的 Python 3.12 环境；均只含 OpenAI4S
自身，仓库外隔离 smoke 的 13 模块和 604 Skills 通过。源码包已实际构建安装，
并非只验证压缩包存在。冻结本记录后再次核对最终文档归档及 wheel 内容一致性。

### Delivery boundaries / 交付边界

Core remains standard-library based. Database migrations, database schema, version and
dependency locks are unchanged from the starting commit. P2 specifies only
future snapshot and slow-connection acceptance; no new runtime feature is
enabled. Ark verifies the Ark path; Anthropic, Responses and Gemini matrices
remain protocol-fixture evidence. DNS cannot be force-interrupted by stdlib:
an unresolved call retains its legacy slot and cannot start an expired
connection after resolution. Missing usage stays unknown.

The original two user document edits are retained byte-for-byte and excluded
from commits and release archives. The provided Ark credential is absent from
delivery sources, retained verification receipts and Git changes. Temporary
validation servers are closed after use. Final work remains on `next`, with
no merge to `main`, version change or release publication.

核心仍为标准库；迁移、数据库结构、版本和依赖锁相对起点未变。两项 P2 仅为准备。
Ark 不代替其他协议夹具；DNS 尚未返回时占用遗留调用名额，返回后不得启动已
过期连接；未知用量仍未知。原有两文档修改逐字节保留且排除在提交和发布包外，
Ark 凭据未进入交付、保留回执或 Git。临时服务用后关闭，最终停在 `next`，不
合并 `main`、修改版本或发布。
