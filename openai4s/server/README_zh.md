# Server（服务端）

[English](README.md)

Web 应用放在这里。本包把供应商无关的 Agent Engine、常驻的 Python 和 R 内核、Host 能力边界与 SQLite 仓储组合成一个 HTTP/WebSocket 服务，全部只用标准库写成。领域逻辑属于本目录里那些职责收敛的 service；[`gateway.py`](gateway.py) 是把它们组合起来的兼容与传输门面。

## 在架构中的位置

```text
浏览器
  |  REST 请求 + WebSocket 事件
  v
gateway.py
  |-- 会话领域服务与只读投影
  |-- AgentEngine 适配器（agent_run.py）
  |-- FIFO 执行所有权（execution_coordinator.py）
  `-- 会话拥有、惰性启动且彼此独立的 Python/R 内核 slot
         |
         `-- HostDispatcher -> 权限、工具、Artifact、数据与委派
```

- **Gateway 组合。** [`gateway.py`](gateway.py) 建起标准库的 `ThreadingHTTPServer`，把其余部分都装配进去：路由、REST handler、WebSocket frame 的编解码与续传、会话 runner、各个 service、存储和静态资源。新增算法通常应该放进职责收敛的模块，而不是塞进门面。
- **REST 与 WebSocket。** REST 负责有界的请求/响应操作，并提供会话领域的读模型。WebSocket 通道承载实时流：Agent 文本、Action 与 Cell 生命周期、审批、Notebook 更新和终止事件，并做缓冲，让重连的浏览器可以续传。
- **会话服务与投影。** mutation service 管理计划、审阅、Artifact、分支、恢复、会话包、Skill 与删除。projection service 把规范 Ledger、执行、血缘、Context 和 Security 状态转成经脱敏、可以安全交给浏览器的 DTO。投影只是一个视图，它永远不是底层的终止信号或事务信号。
- **内核所有权。** 每个 Web 会话通过 `SessionRunner` 拥有一个 Python slot 和一个 R slot，两者相互独立、惰性启动。[`execution_coordinator.py`](execution_coordinator.py) 发放 FIFO ticket，让 Agent、用户 REPL、恢复和生命周期这几类写入方不会互相压到一起；中断只会打到持有那把 lease 的确切 owner。Tool-only 路由不会启动前台的会话 slot，不过个别工具可以自己管理一个专用 worker。
- **持久化边界。** 持久事实经 `Store` 仓储写入。WebSocket 状态和活着的内核命名空间只存在于进程内。没有任何事务能同时覆盖 SQLite、工作区文件、内核进程和 socket 投递这四者。

## 版本化的对外接口

- **只有一个前缀，没有旧别名。** 所有对外可达的东西都在 `/api/v1` 之下，而 `API_ROOT` 只在 [`contract.py`](contract.py) 里定义一次，因为有两类完全不同的调用方要用它：gateway 按它路由，CLI 按它拼出 daemon 的 URL。它们真的漂移过——`openai4s share` 把 `/api/` 写死，于是它的每一条子命令都撞上 daemon 自己那句「API 是版本化的」拒绝而 404。现在没有版本号的 `/api/...` 会明确收到那句拒绝，而不是掉进 SPA 外壳、以 200 回一段 HTML。
- **凭据是必需的，不是可选的。** daemon 能执行代码，所以哪怕在 loopback 上，没有 token 也不作答；只有 `/health` 和 `/api/v1/auth/status` 例外，后者存在是因为：不能用一个客户端无权读取的响应去告诉它「你需要 token」。`?token=` 只允许在根页面上换成 cookie——这是白名单，不是做减法：旧的减法规则让 `/preview/<id>?token=…` 变成一个既种下持久 cookie、**又**把文件本身交出去的链接。
- **失败只有一种信封。** 一次失败带着它一直都有的人类可读 `error` 文本，外加一个稳定的机器 `code`，以及把它和结构化日志系在一起的 `request_id`；这层增补是**追加式**的，所以只读 `error` 的客户端不受影响。`GatewayError` 的文本原样透出，因为 409/413/429 这类拒绝里写的正是用户据以行动的信息。任意异常则是在边界处被**投影**成安全形态，而不是在信封里被过滤掉：过滤器必须去猜哪些消息是安全的，而它会朝着毁掉那些好消息的方向猜错。
- **读取分页，事件编号。** 会话列表是 keyset 分页：一个不透明游标（解析它就等于和排序键耦合）、一个观察出来而非推断出来的 `has_more`，以及读不出来的游标直接 400，而不是悄悄从第一页重来。WebSocket 事件带一个会话内单调递增的 `seq`；重连的客户端从自己的游标续传，整段回放包在 `replay_begin`/`replay_end` 之间；而一个无法定位到**本** daemon 这条流上的游标——epoch 不同，或者根本没有 epoch——会被判成缺口，而不是被采信。
- **一轮由它的 execution 标识。** frame 活得比 turn 长，两个 turn 会重叠，客户端也可以合法地复用 `X-Request-Id`，所以 `processing`、两个终止事件和续传窗口上带的都是 `execution_id`。一个请求也只产出一条权威的失败：内外两层处理器按 job id 交接，所以收尾阶段再出故障，也不会追加第二条与第一条相矛盾的终止记录。
- **一次准入是一条持久记录，不是一种指望。** 一条钉住的评论只会被恰好一轮消费，而 reservation id 由客户端在**发送之前**铸出——服务端铸的 id 对一个丢了 202 的浏览器来说根本不存在，也就无从追问。`GET /api/v1/frames/{id}/admissions/{reservation}` 会回答它后来怎么了，于是丢了应答的客户端既不必重发（重复干活），也不必放弃这些评论（静默丢失）。状态是从 pin 推出来的——那才是一轮真正消费掉的东西；ledger 只提供关联信息。启动时仍处于 `reserved` 的，都是崩溃留下的滞留项，会被释放。

## 完成、Notebook 与恢复边界

- Cell 的结果是回到外层循环的一条 observation，它本身不代表任务完成。要完成，必须有一个单独且有效的、由 Engine 拥有的 `finalize_response`，或者 Python Cell 内部调用的 `host.submit_output(...)`。R Cell 根本无法完成任务。
- Stage 1 trusted delivery 开启时，completion 链接只由一个 URL helper 根据精确的不可变 version id 生成。服务端先验证快照作用域、大小和 checksum，再把最终 assistant 消息与 delivery manifest 放进同一事务提交，最后才发带稳定 `delivery_id` 的链接 `text_chunk`。若 socket 发布丢失，普通 REST reopen 仍会恢复已提交消息；`committed` 行与稳定 id 也为未来或运维显式重放提供依据，但 Stage 1 的 delivery ledger 不会自行驱动重发。普通的有界 WS 序列缓冲在 turn 仍存活时可能重放；终态或重启后以 REST 为准。验证或审计失败则不发布成功链接。
- 再次捕获当前 head 的相同字节会复用 version，同时追加一条限定作用域的 capture observation，保留新的 producing Cell 与 lineage；原 version 的 producer/provenance 不会被改写。Stage 1 的 observation 只在本地，Session package、share 与 export 还不会序列化它。
- Stage 1 的 standard-profile 准入完全在本地且没有副作用。普通 turn 仍可走原生工具或结构化完成；一旦路由出 Code Cell，系统会在应用 pending environment、分配身份/attempt、运行安全检查或启动 runtime 之前拒绝。直接 Cell 共用同一边界；批准/恢复的科学 plan 仍在状态切换前检查。UI 持久显示 banner 与只复制不执行的受管修复卡。修复会先构建并验证新的 Python/R generation，再移动指针。
- 只包含 `host.submit_output` 协议调用的 Cell 照样会执行，也会留在原始执行历史与审计记录里，但实时和重新打开的 Notebook 投影会把它过滤掉。`.ipynb` exporter 读的是没有套用该过滤的不可变执行历史，所以它导出的是 raw/audit 版本，可能带上这个系统 Cell。
- 恢复执行已经接入 REST/UI、FIFO 所有权和 Python/R 候选内核流水线，但仍然是 **Partial**：不安全或非确定性的 Cell 会被归类为 `never`；系统不会去序列化任意的历史命名空间；某个语言的候选内核如果无法变为 active，整个恢复可以就此停下并给出显式的 Partial 结果。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | 稳定的包门面，导出 `build_server` 与 `serve`。 |
| [`action_timeline.py`](action_timeline.py) | 把规范的 Action Ledger 投影成 UI 真正看到的 Timeline。一条记录足以说清：跑的是什么、怎么结束的、用掉哪些权限、花了多少用量、引用了哪些 Artifact，而且这些内容都有界、都经过脱敏。供应商的 `wire_state` 和原始参数字符串被刻意省略，避免有人把一个调试端点变成凭据或协议的转储口。 |
| [`attention.py`](attention.py) | 跨 Session 的只读「需要处理」聚合。把 running/queued 执行、待批准、可恢复失败、view-only/blocked 会话，以及 live/unknown 远程计算合成固定 shape 的卡片。team 可见性在聚合、排序、limit 之前生效。`target.surface`/`dock` 是闭集，服务端不返回任意 URL。GET 零副作用：不 spawn kernel、不打 provider、不 retry/approve/harvest。首版不建物化表。 |
| [`attention_routes.py`](attention_routes.py) | `GET /attention?limit&cursor`，一张经校验的 `RouteSpec`。cursor 是绑在调用方 team-scope fingerprint 上的 `(updated_at, id)` keyset；来自另一用户或另一组 filter 的 cursor 返回 `400 invalid_cursor`。retry/approve/restore 仍走现有 mutation 路由。 |
| [`agent_run.py`](agent_run.py) | 把 `AgentEngine` 适配到 Web 契约。它流式输出安全的文本与代码草稿，发出 Web 事件，处理取消，并通过注入的端口执行原生 Action 或 Cell。 |
| [`artifact_refs.py`](artifact_refs.py) | 用户消息里钉住版本的 `@文件` 引用。`@name#v-<version_id>` 发送的是那个确切版本的冻结字节，而不是活文件——旧的解析读到的是后续 cell 留下的任何内容。解析不出来的引用会被**报告**而不是丢掉；二进制 Artifact 只报名字，不会被贴成一片替换字符；同 project 的跨会话引用在**发送时**物化（D3），而不是就地读取。 |
| [`retrieval_source.py`](retrieval_source.py) | 一个版本的检索溯源信息中，可以安全交给客户端的那一部分投影。这个信封是由执行检索的那段代码（包括未经审计的 Skill）写入的自由格式 JSON，而科研 API 把 key 放进查询串是常态——所以键走白名单、值有长度上限，查询串、路径和 userinfo 三处的凭据都会被指纹化。没有溯源信息时返回 `None` 而不是一个空面板：空面板会被读成一条关于数据本身的结论。 |
| [`artifacts.py`](artifacts.py) | Agent 写出的工作区文件在这里变成带版本的 Artifact。UI 上的编辑、重命名、上传、恢复和提升也走同一个 service，版本每动一次，快照、溯源和广播都跟着对齐。trusted 路径会在登记前流式复制、fsync、原子冻结并验证字节；遇到相同 head 时复用 version，同时保留 observation。 |
| [`artifact_index.py`](artifact_index.py) | 按 project 分页的 Artifact 索引：`(created_at, artifact_id)` keyset、绑定 `project_id + q + content_type + origin + team scope` 的不透明 cursor，以及只搜 filename 的转义 `LIKE`。旧的 `/projects/{pid}/artifacts` 数组 route 保持不变。 |
| [`artifact_index_routes.py`](artifact_index_routes.py) | `GET /projects/{pid}/artifact-index`。`RouteSpec` 进入契约清单；filter 变化后沿用旧 cursor 返回 `400 invalid_cursor`。 |
| [`artifact_workbench.py`](artifact_workbench.py) | Stage 9 正式 Artifact workbench：完整数据集表格查询、版本 diff、PDF/HTML locator、结构摘要，以及 Ketcher 3.7.0 包装页。flag 关闭时不生效。 |
| [`artifact_workbench.py`](artifact_workbench.py) | Stage 9 正式 Artifact workbench：完整数据集表格查询、版本 diff、PDF/HTML locator、结构摘要，以及 Ketcher 3.7.0 包装页。flag 关闭时不生效。``GET .../table`` 可带可选 ``version_id``，提供时绝不降级 latest。 |
| [`artifact_workbench_routes.py`](artifact_workbench_routes.py) | Stage 9 的五条 Artifact workbench 路由（`/table`、`/diff`、`/structure`、`/pdf-text`、`/html-outline`）。flag 关闭时返回 403。 |
| [`artifact_workbench_routes.py`](artifact_workbench_routes.py) | Stage 9 Artifact workbench 路由（`/table`、`/table/profile`、`/table/export.csv`、`/diff`、`/structure`、`/pdf-text`、`/html-outline`）。flag 关闭时返回 403。 |
| [`table_profile.py`](table_profile.py) | 共享表格 query parser（锁定既有 `/table` 五参数契约）、列 profile（type/missing/unique/min/max/mean/histogram，bins≤50，exact unique 做不完则 `approximate:true`）、分块 CSV 导出（单块 1 MiB，总量 32 MiB 超限 413）、profile 的进程 LRU，以及资源验收 manifest 门。统计不入库。 |
| [`auto_budget.py`](auto_budget.py) | Auto Mode 原子预算准入：consumer 注册表、canonical 动作指纹、durable-delta 闭集，以及不可验证 token 的 fail-closed。各 sink 在动作前用稳定 `admission_id` 预留；只有明确未开始的调用才可释放。Guardian 字段只投影既有权威状态，不复制计数。 |
| [`auto_mode.py`](auto_mode.py) | 按冻结的「导入隔离 → frame → project → 显式 deployment → 旧 result-review → 内建默认」顺序解析 Stage 2 Auto Mode 选择；对 durable run/audit 状态做有界白名单投影；只把新建且已提交的规范事件作为尽力而为的 WebSocket 提示转发。它不会调用模型、Reviewer、Repair Agent 或权限路径。 |
| [`auto_mode_portability.py`](auto_mode_portability.py) | Session package 与只读 share 共用的“不信任输入也安全”reducer。它验证 Auto Mode 的 scope/reference 闭包，只输出闭合的审计 DTO，把 portable evidence 无法独立证明的结论降级，而且绝不恢复执行或权限能力。 |
| [`auto_mode_routes.py`](auto_mode_routes.py) | 精确承接 `GET/PATCH /frames/{id}/auto-mode` 与只读 `/auto-audits`。经校验的 `RouteSpec` 表进入契约清单；这里刻意没有公开的 run/review/repair/Guardian 状态变更路由。 |
| [`auto_repair.py`](auto_repair.py) | Stage 5 有界 Repair Agent 与再审核循环。Reviewer 保持只读；Repair 不能自我认证；相同 checksum 复用上一版本。 |
| [`guardian_shadow.py`](guardian_shadow.py) | Stage 6 精确动作 Guardian shadow。只记录建议、不执行，并拒绝 standing allow。 |
| [`guardian_enforce.py`](guardian_enforce.py) | Stage 7 无人值守执行。只有非危险精确动作可以 ``allow_once``；Guardian 仍然不能创建 standing allow。 |
| [`evidence_adapters.py`](evidence_adapters.py) | 冻结 Artifact version 的只读 PDF/图像/结构/表格适配器。仅有文件名不算覆盖。 |
| [`evidence_snapshot.py`](evidence_snapshot.py) | 构造不可变的 Stage 3 Evidence Snapshot：计划、checksum、lineage、适配器、省略声明和可解析的 `evidence_refs`。不含主 Agent 隐藏推理。 |
| [`review_scratch.py`](review_scratch.py) | Reviewer 校验用的隔离 scratch：子进程环境已擦除秘密，无网络、不能写正式工作区、不能 MCP、不能 `submit_output`。 |
| [`scientific_review.py`](scientific_review.py) | Stage 3 shadow 编排。冻结 Reviewer 身份，运行确定性检查与独立模型审核，绑定 evidence refs，并记录不把门的 shadow 判断。 |
| [`cell_run.py`](cell_run.py) | 按固定顺序跑完一个 Python/R Cell：readiness 准入、身份/attempt 分配、安全检查、内核执行、实时输出、Artifact 捕获、执行日志、终止投影。Stage 1 的准入端口会在任何 Cell id 或 runtime 出现之前拒绝。这个事务跑完只是一条 observation，它不会判定 Agent 的任务已经完成。 |
| [`completion_gate.py`](completion_gate.py) | Stage 4 的候选→审核→晋升。它先记下 provisional 候选，等 Scientific Reviewer，再盖上 Verified / completed_with_issues / review_unavailable。它不启动 Repair。 |
| [`completions.py`](completions.py) | 生成用户看到的那段叙述。进度和结果文字都做了本地化；结构化的 completion 是照着真实的 Artifact version/capture 增量渲染的，而不是照着一句声称。trusted 链接只来自公共 URL helper 与精确 version id；隐藏推理不会进到这里。 |
| [`compute_tasks.py`](compute_tasks.py) | 一个会话的远程计算工作的只读视图。远程任务的寿命长过发起它的那一轮、内核、乃至守护进程，而那份持久记录原先只能从 cell 里够到。这个页面不能轮询，原因是这套系统特有的：**探测即回收**——`ComputeManager.result()` 才是去联系远端的那一步，而联系远端就会把文件拉回来并结束任务，所以一个会自动刷新的页面等于在没人看着的会话里偷偷做回收。本模块只接收一个 `Store`，完全没有 import `ComputeManager`，所以这条保证是结构性的，而不是一句关于调用顺序的承诺。按 `owner_key`（会话工作区）限定范围；别的会话的任务不会被列出、不计入计数，也不会以「已隐藏」的形式被提及。 |
| [`contract.py`](contract.py) | `API_ROOT`、`RouteSpec` 原语，以及每一条可路由路径与 WebSocket 事件的清单——这份清单是从代码里**推导**出来的，而不是在旁边另立一份手工维护的名单。两条读法在每个路由模块上取并集：既解析 `gateway.py` 及其 `*_routes.py` 兄弟模块里的路由链，也读取已迁移模块运行时的 `ROUTES` 表。手工名单在有人赶时间加了一条路由的那一刻就是错的，而且它错了这件事本身看不见——那正是契约要防的失效方式；源码读法如果路由写法变到读不出来，会以「清单为空」的方式大声失败。若按模块二选一，这个洞会在下一层重新出现（声明旁边残留的 `re.fullmatch`、或者表名不叫 `ROUTES`，都会被静默漏掉），所以两条读法始终都跑。它回答的是有哪些路径存在，而不是它们返回什么。 |
| [`delivery.py`](delivery.py) | 对精确 Artifact version 的 delivery manifest 做验证，不信任可变路径：归属作用域、普通文件类型、不可变快照、稳定 descriptor 身份、大小与 SHA-256 必须全部一致。输出不带路径，由 completion-delivery repository 与最终消息或安全暂存的 Session-package 消息做原子绑定。 |
| [`local_auth.py`](local_auth.py) | daemon 自己的访问令牌：在数据目录下只铸一次、仅属主可读、比较用恒定时间。是文件而非 Store 行，因为 CLI 必须在任何数据库存在之前读到它，而 `openai4s doctor` 恰恰要在数据库本身坏掉时还能工作。此前它活在闭包里、每次重启都换，已发出的每个 cookie 都因此失效。铸造时用 `os.link` 发布——这是唯一只可能成功一次的操作，于是并发启动的多个 daemon 会收敛到同一个令牌，而不是各自握着一个。|
| [`errors.py`](errors.py) | `GatewayError`、稳定的机器错误码、追加式的公开失败信封，以及那个异常投影器：它把任意 `str(e)` 挡在响应体之外，而运维仍能从结构化日志里拿到原始异常。独立成模块，是因为 `GatewayError` 原本位于 gateway 自身 import 区块下方约 5800 行处，兄弟模块从那里 import 它构成循环导入，会让 daemon 在**启动时**就失败。从 `Handler._api` 里切出的每个路由组都要抛这个异常，否则每抽一次就要重新踩一次这个坑。gateway 仍然再导出原来的名字。 |
| [`execution_coordinator.py`](execution_coordinator.py) | 会话级 FIFO 执行所有权的 Web 适配层。ticket 状态会被投影成 WebSocket 事件；已准入的 ticket 会绑定到它的取消事件和当时那把内核 lease 上；中断只会打到由那个执行 id 精确持有的那把 lease。 |
| [`execution_sources.py`](execution_sources.py) | 把已执行代码的层级汇于一处：`GET /frames/{fid}/execution-sources` 投影根 frame 加上每个被委派的子 frame（递归；名称、深度、每 frame 计数，以及每个 Cell 的语言/状态/源码 SHA-256/generation/环境/Artifact 关联——从不内联代码文本本身），`…/execution-sources/export` 则把同一批行渲染成字节确定性的 `sources.zip`：真正执行过的源文件，失败的 Cell 一并包含并标注，附一份新的 manifest 与中英双语的"持久内核"警示。离开 store 的只有 `execution_log` 字段与公开元数据——不含提示词、host 载荷、输出或凭据。 |
| [`execution_views.py`](execution_views.py) | 读不可变的 Cell 历史，回答 Notebook 想问的问题：这个 Cell 跑在哪个运行时 generation 上、依赖了什么、之后是否已经失效、重试过几次、数据从哪来。 |
| [`gateway.py`](gateway.py) | HTTP/WebSocket 的主组合门面。协议 frame 的编解码、hub 与续传缓冲、`SessionState` 与 `SessionRunner`、REST 路由、静态资源和安全检查都落在这里，本表所列全部 service 的装配也在这里。 |
| [`global_views.py`](global_views.py) | 组合跨会话的项目级研究 Timeline 与 Artifact 血缘视图。 |
| [`governance_routes.py`](governance_routes.py) | `/team/*` 管理面（M2）：用户、项目成员、邀请、用量、审计、配额——一张经校验的 `RouteSpec` 表，也是该命名空间集中式 admin-only 声明（每条路由前同一个 `_deny` 门）。原始邀请 token 只出现一次（POST 响应里）；列表只给摘要前缀。每个变更都进团队审计日志（INV-12）。团队模式关闭时返回稳定 `team_off` 形状，契约捕获冻结的正是它。 |
| [`kernel_routes.py`](kernel_routes.py) | 十二条 kernel 路由，作为 `Handler._api` 拆解的第一刀搬出（2100 行 / 261 分支 → 1887 / 237）。选它是因为它是唯一**可被核对**的一组：它拥有十一个冻结响应形状，而 `memory`、`permissions`、`connectors`、`compute` 一个都没有。现在每条「方法 + 路径」匹配器都是模块 `ROUTES` 表里的一个 `RouteSpec`，在声明处即校验，并由契约清单读取。`handle()` 是三态返回——True 表示已发出响应，False 表示这一组没有处理该请求、链条必须继续走到 404；`RouteSpec.match()` 之所以带上方法，正是为了让「路径对、方法不对」的请求不被当成已处理吞掉，而写成 `return bool(regex_matched)` 会吞掉十二个「方法不对」的 404。两处位置依赖是承重的，并且用测试而非注释来保证：调用点必须在 `frame_mutation` 守卫之后（那是七条改写路由——含代码执行端点——唯一的写保护，且这七条已按 `spec.mutates` 参数化进隔离测试），以及在 `workbench` 守卫之后（`GET /frames/{id}/execution` 的 404 完全来自它）。 |
| [`team_policy.py`](team_policy.py) | 谁可以碰什么——写成关于**资源**而不是关于 URL 的谓词。一次外部审查点破了团队模式第一版的真问题：鉴权是"这个路径匹不匹配那两条正则之一"，于是凡不是 `/frames/{id}` 或 `/projects/{id}/…` 的面，统统还是那个单用户 API——五个各自独立的隔离缺陷都出自这同一个形状。把五个调用点各修一遍，等于把病因原样保留。全篇保持两条性质：团队模式关闭时立即返回放行，于是单用户安装跑的还是它一直跑的那段代码（INV-1）；查询抛异常即视为拒绝，因为把读不出来的归属行当成"没有限制"，正是这个产品曾经让一个按版本寻址的产物绕过检查的方式。调用点仍然必须去问——没有任何机制能强迫一条新路由去问——但至少现在只有一个模型要读、只有一处规则要改。 |
| [`compute_session_routes.py`](compute_session_routes.py) | 一个会话的内核跑在哪里、以及好了没有（M3b-6）。它们存在的意义在于 `readiness` 这个字段：集群会话是四个条件而不是一个布尔（INV-5），响应会指出还差哪一个——"在排队等节点""等 worker 连回""正在起内核"是三种不同的等待、三种不同的预期时长，用一个转圈图糊弄全部，正是用户断定这产品坏了的方式。`location: "local"` 是一个答复而不是错误：那是每一个安装的常态。未配置的 profile 会被拒绝而不是猜测，因为猜测正是会话落到主人从未选择的资源上的方式。而 `state_lost_epochs` 就是 UI 拿去做横幅的东西（INV-11）——因为恢复之后产出的结果，看起来和那个已经丢掉的会话产出的结果一模一样。 |
| [`orchestration_routes.py`](orchestration_routes.py) | BatchJob 的提交/列表/详情/取消/日志，外加只读的 profile 列表（M3a-8）。有两处刻意的"不做"：它从不提交——请求只写一行持久记录，由 reconciler 去跟后端说话，于是取消能扛过重启、提交也不会在一个随时可能消失的请求线程里被试两次；它也从不叫出调度器的名字，所以响应里带的是 `allocation_id` 而永远不是作业 id（INV-2）。归属沿用会话那条规则：别人的作业是 404 而非 403，因为"哪些作业存在"本身就说明了同事在做什么。`command` 若给成**字符串**会被拒绝而不是被切分——切分命令行正是引号 bug 变成注入的地方。 |
| [`model_discovery.py`](model_discovery.py) | 探测一小份固定的 loopback URL 名录，找出 OpenAI-compatible 的模型服务；探测时关闭代理、拒绝重定向，调用方无法把它变成通用的 SSRF 原语。`catalog()` 返回这份名录，不开 socket、不后台刷新。结果只是一个 profile 建议：不会改动模型设置，也不会存下凭据。 |
| [`model_profiles.py`](model_profiles.py) | 一个模型供应商 profile 进来时要过这里，被校验和迁移；落库、激活、删除时还要再过一次。凡是要公开出去的东西，凭据都会被清掉。`probe()` 记下三态能力 receipt（验证 tool schema 但绝不执行 tool，单次最多两个极小请求）。顶部的模型选择器也由它构建：只列当前模型和已保存的 profile，别的一概不列——没人配过的 endpoint 不该出现在那里，选了也只会在发消息时失败。 |
| [`onboarding_routes.py`](onboarding_routes.py) | 首次运行的 Web 路由。`GET /onboarding` 脱敏且零出站；`POST /onboarding/complete` 是实例级变更（团队模式下仅 admin）。 |
| [`notebook_export.py`](notebook_export.py) | 把原始的不可变执行历史确定性地导出成四种只读形态：每种语言一个 `.ipynb`、一个把两者打包并带 checksum 描述的 bundle，以及一份 Markdown 文档。前三种是给人重跑用的；Markdown 那份是给人阅读、以及贴进 issue 或方法学章节用的，所以它把两种语言按执行顺序放在同一份文件里——交错本身就是记录——并以一节 `## Inputs` 开头，列出这条分支的各轮所钉住的每个 Artifact 版本。没有输入时这一节整节省略，因为一个空标题也是一种声称。四种形态都不套用 Notebook 投影那道过滤，所以只含协议调用的 completion Cell 仍可能出现在导出结果里。 |
| [`notebook_lineage.py`](notebook_lineage.py) | Stage 8 正式 live Notebook 开关，以及 host 侧 Python/R 读→version 映射和写 lineage。它不改内核。 |
| [`stage12_ga.py`](stage12_ga.py) | Stage 12 GA 总开关声明。它不会打开更早的 Stage。 |
| [`plans.py`](plans.py) | 管理结构化计划的生命周期。planner 的回复先被解析、规范化，草稿和它的 JSON Artifact 落库，公开的审阅形态由此暴露，通过审阅的计划再被带到执行。实时的 `host.plan_update` 变更仍留在 `HostDispatcher`。 |
| [`recovery_control.py`](recovery_control.py) | 投影恢复 journal 与 generation 状态，并组合出当前可行的、经校验和脱敏的恢复 Action 计划。只有在工作区目录树和完整的 bootstrap 清单都在的前提下，它才会说某个 checkpoint 可恢复。 |
| [`recovery_execution.py`](recovery_execution.py) | 在精确的执行所有权下执行一次恢复 mutation。所有语言候选内核跑在同一个 recovery id 下，遇到第一个未完成的候选就停，最后落一条持久的会话终止事件。 |
| [`recovery_recipe.py`](recovery_recipe.py) | 把不可变的 Cell 事实、依赖闭包、环境需求、sidecar 和确定性检查编译成一份恢复 recipe。保守是有意为之：影响状态却过不了这些检查的 Cell 会以 `never` 重放步骤的形式留在 recipe 里，于是校验会报 Partial，而不是默默宣称旧命名空间还在。 |
| [`recovery_runtime.py`](recovery_runtime.py) | 恢复流水线接上真实基础设施的地方。它为一个会话拉起候选的 Python 和 R 内核，探测环境，做 bootstrap，做验证，然后提交或回滚。 |
| [`renderers.py`](renderers.py) | 从 Artifact kind、content-type 和扩展名到安全科学 renderer 的注册表，以及公开的 renderer 描述。它只有元数据：不导入任何科学库，也不执行 Artifact 的内容。 |
| [`response_capture.py`](response_capture.py) | 观测各 route 真实返回了什么，并固化进 [`docs/response-schemas.json`](../../docs/response-schemas.json)。它从外面包住 `make_handler` 的 `_api`，而不是在 `_json` 里挂钩子：gateway 测试会把 `handler._json` 换成自己的收集器，钩在真正的 `_json` 里会漏掉几乎所有 route，却看起来在正常工作。这里没有任何代码跑在生产路径上。字段**类型**随宿主机变化的子树（内核的 `sandbox` 块：能强制 sandbox 时 `backend` 是字符串，不能时是 null）记为不透明而不予固化——固化它等于把捕获时那台机器钉进契约，然后判定其他每一台机器都是破坏性变更。这份清单只收「类型随宿主机变化」的子树，不是给形状不方便的 route 停车用的。 |
| [`response_schema.py`](response_schema.py) | 一套小而明确的形状代数（类型、必填键、元素形状），零依赖，因为 core 只用标准库。它回答的是「这个响应的形状变了吗」；它不是 JSON Schema draft-2020-12，也不假装是。 |
| [`reviews.py`](reviews.py) | 先攒出一次科学审阅所依据的有界证据包，再把这次审阅推到结果。整个过程可取消，结果会落到持久化、用量记账和公开的审阅事件上。 |
| [`security_headers.py`](security_headers.py) | 作用于每个响应的静态 CSP 与加固响应头。所有可执行 UI 代码都放在同源文件中，因此 `script-src` 不需要 `'unsafe-inline'`，也不需要通过重新解析 HTML 动态生成 hash/nonce。 |
| [`session_branching.py`](session_branching.py) | 让一个会话长出分支所需的全部动作：打 checkpoint、隔离 fork、预览 revert、激活分支，以及把 revert/undo 历史只追加地记下来。revert 从不改写旧的 checkpoint：它先把当前状态记成撤销目标；如果当前 head 之后有外部文件被改动，这次操作会记为 `conflict`，一个字节都不会动。 |
| [`session_deletion.py`](session_deletion.py) | 会话被持久删除后的清理。会话聚合、工作区、按 root 隔离的 kernel Artifact 输入缓存、快照/CAS 引用和进程内状态都会清掉，而这个会话自己 scope 之外的东西一概不碰。 |
| [`session_domain.py`](session_domain.py) | 高层的会话领域组合，路由 handler 调它，而不是自己去拼装仓储。它对外承接 checkpoint 与 cursor checkpoint、分支、Timeline、导出、renderer、会话包操作与恢复。 |
| [`session_package.py`](session_package.py) | 创建和导入会话 ZIP 包，过程确定、带 checksum。传输这一段由过滤秘密、防路径穿越和隔离区中转来把关。导入会先校验整个压缩包再创建任何东西；导入进来的会话落在一个已结束的内核 generation 上，这是一条显式的只读/待恢复边界。 |
| [`session_recovery.py`](session_recovery.py) | 启动时协调过期的运行时状态，并在 activity 与恢复阻塞条件的约束下确定性地回收空闲内核。旧 daemon 遗留下来的活 generation 会被标成 `abandoned` 并保持可审计；这里没有任何代码反序列化对象，也不声称内存还活着。 |
| [`session_runtime.py`](session_runtime.py) | 保存会话的控制平面对象，例如 dispatcher、委派树和动态 capability，让语言 worker 可以启动、替换或停止，而不丢掉这些状态。 |
| [`skill_network_admission.py`](skill_network_admission.py) | 把已加载 Skill 的网络 manifest 绑到会话，并在两个执行 sink 上准入：下一格 Python/R Cell，以及 shell capability 签发。求交 manifest × 实测 sandbox posture × Host egress × 调用方绑定。manifest 永不授权。 |
| [`skill_sidecars.py`](skill_sidecars.py) | 消费 worker 的私有 Skill 加载诊断，但不会把与不可信 Cell 共用解释器的声明当作恢复证据。它用 compare-and-swap 把对应 generation 的 sidecar 捕获标为失败，让恢复停止，而不是重放未经独立证明的 sidecar。Host 进程从不导入或执行 sidecar。 |
| [`share_projection.py`](share_projection.py) | 把一个会话构建成一份冻结、扁平化的 `ShareProjection`（单一 synthetic root、无 checkpoint、无 memories/策略），再分两路序列化：一个 `import_bytes` 兼容的 bundle 和一份脱敏的查看器文档。复用会话包的失败即拒 secret 闸门。 |
| [`share_router.py`](share_router.py) | 单个分享的只读公网请求处理器：仅 GET/HEAD、有且仅有两个读取根（内存查看器资产 + 当前 lease 的快照）、严格 CSP、单段 Range，以及统一 404。它绝不触碰内核、dispatcher 或任何 gateway 路由。 |
| [`share_service.py`](share_service.py) | Web 分享的两阶段发布（DB 状态机 + 不可变版本目录 + `current.json` 指针），带 SnapshotLease 引用计数 GC、崩溃恢复、有效期清扫与撤销。FIFO 准入与隧道客户端由外部注入。 |
| [`skills.py`](skills.py) | Web Customize 里用户自撰 Skill 文档的生命周期。它管增删改查和导入，管 UI 读的那份目录投影，也管能力的启用。 |
| [`file_area.py`](file_area.py) | 团队文件区的路径策略（M1-8）：`OPENAI4S_DATA_ROOTS` 列出唯一可达的目录，一切操作都过同一个解析器，包含检查作用在**解析后**的路径上——先解 symlink 和 `..`——所以任何写法的外部路径都过不去，"在白名单外"与"不存在"答同一句话。上传目标再加一条纯文件名规则（无分隔符、无 dot-dot、不许 symlink 跳转），上传能创建文件却不能用来逃逸。 |
| [`file_routes.py`](file_routes.py) | `GET /files`、`GET /files/download`、`POST /files/upload`，经校验的 `RouteSpec` 表。上传是真流式：网关的整体预读对这条路径单独跳过，处理器把 `rfile` 按 1 MiB 块拷进同目录临时文件再原子 `os.replace`——512 MiB 不经过 daemon 内存，截断的 body 也不会在最终名字下留半个文件。guest 在这里什么都不能读（D3：仅回放）。 |
| [`team_auth.py`](team_auth.py) | 团队模式认证（`OPENAI4S_TEAM_MODE`）：`os_user` 登录 cookie；按（用户名, ip）的令牌桶限速——在 PBKDF2 计算**之前**消耗令牌，所以限速同时约束了攻击者能让 daemon 做多少哈希功；以及 loopback CLI 的 service 身份，让服务器上的管理 CLI 继续用 daemon 访问令牌而不冒充任何真人账号。当 trusted proxy 拓扑让所有客户端都呈现为 loopback peer 时，gateway 会禁用该机器身份。每次登录结果都写审计（INV-12）。 |
| [`team_routes.py`](team_routes.py) | 认证路由（`/auth/login`、`/auth/logout`、`/auth/me`），按 `kernel_routes` 的形态组织：一张经校验的 `RouteSpec` 表，运行时分发和契约清单读同一份。两种模式下都确定性应答——团队模式关闭时返回稳定的"已禁用"形状，契约捕获冻结的正是它。Set-Cookie 走 `_send` 带消毒的 header 通道；原始会话 token 只存在于该头和客户端 cookie jar 里。 |
| [`titles.py`](titles.py) | 在后台根据第一条消息生成会话标题。模型配置延迟绑定，持久化和广播都做了防竞态处理。 |
| [`trusted_capture.py`](trusted_capture.py) | 管理 Stage 1 会话级前台 Artifact 捕获、独立后台内核与面向用户的外部工作区变更三者之间的准入边界。同一线程的捕获和外部变更均可在各自类别内嵌套；另一所有者或任何跨类别重叠都会在工作区动作开始前失败即关闭。 |
| [`urls.py`](urls.py) | 服务端统一拥有的持久资源 URL。trusted completion 只接受非空的精确 version id，把它百分号编码到保留的 `/api/v1/artifacts/versions/{version_id}` 命名空间；该路径不会回退为 Artifact id 或文件名，flag-off 的旧 helper 也被隔离在这里，不再散落在 completion 文案中。 |
| [`variable_inspector.py`](variable_inspector.py) | 通过一个很窄的 manager 协议请求，读取活着且空闲的 Python/R 命名空间，返回有界、净化过的变量预览。它不会创建会话，也不会创建 worker，更不会进入 Cell 事务。 |
| [`volcengine_arkcli.py`](volcengine_arkcli.py) | 以固定命令集调用官方 Ark CLI：子进程只继承允许名单内的环境变量，输出有大小上限，JSON 和错误会先校验、脱敏，并且不经过 shell 拼接。 |
| [`volcengine_connector.py`](volcengine_connector.py) | 为 Web 应用投影火山身份、套餐与额度、API Key 和 Endpoint 状态，管理浏览器 OAuth 会话，并将 Ark 明文 Key 一次性交给 `ModelProfileService` 与 SecretBroker。公开响应中的云端资源只使用进程内不透明选择标识。 |
| [`workbench_state.py`](workbench_state.py) | 根据持久状态与实时状态投影 Context 和 Security 面板。它不暴露消息内容；在真实 worker 报回自测结果之前，它也不会声称 OS 沙箱已经存在。 |
| [`ws_frames.py`](ws_frames.py) | 由 gateway WebSocket 与分享隧道共用的、加固过的 RFC 6455 帧编解码。按角色的读取会校验掩码方向、FIN、RSV、opcode、canonical 长度、64 位最高位、控制帧大小与载荷上限；gateway 通过别名保留原有调用点。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`webui/`](webui/) | Gateway 静态树：来自 [`../../frontend/`](../../frontend/) 的提交 Vite `dist/`（默认 SPA 外壳）、共享经典脚本（`style.css`、`theme-bootstrap.js`、`scientific_renderers.js`、`favicon.js`）、自带的 3Dmol/Ketcher/字体、卫星页，以及冻结的 `index.html` + `app.js` 逃生舱（`OPENAI4S_WEBUI=legacy`）。第三方库只存在于 `webui/vendor/`：3Dmol 负责三维结构，另有钉住的 Ketcher 3.7.0 独立版在 Stage 9 打开时提供二维结构编辑。向 `3Dmol.org` 的 CDN 重试那条路已被删除，因为它会在持有会话 Cookie 的页面里悄悄执行第三方脚本；自带的那份渲染不了的分子现在直接退回纯文本展示——这本来就是 CDN 那条路失败时的同一个结果。只读的分享查看器是 `webui/share/` 下另一个独立客户端。 |

## 修改注意事项

- [`gateway.py`](gateway.py) 要一直是组合与兼容门面，只做外科式修改。新的领域行为放到真正拥有它的那个 service 里。
- 只要动到内核生命周期、WebSocket 流、执行所有权或 Artifact 捕获，除了跑聚焦测试，还必须在真实浏览器里端到端跑一遍。
- 交给浏览器的 DTO 必须有界且脱敏。原始供应商 payload、工具参数、凭据和不受限的文件系统路径都不该出现在投影里。

另见仓库[架构指南](../../docs/architecture.md)、[Web 应用指南](../../docs/webapp.md)与 [`webui/` README](webui/README_zh.md)。
