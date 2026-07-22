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

- **Gateway 组合。** [`gateway.py`](gateway.py) 建起标准库的 `ThreadingHTTPServer`，把其余部分都装配进去：路由、REST handler、WebSocket frame 的编解码与续传、会话 runner、各个 service、存储和静态资源。[`daemon.py`](daemon.py) 是另一回事：它是遗留的最小兼容服务，只暴露 `/`、`/health` 和 `/run`，不属于 Gateway 的组合。新增算法通常应该放进职责收敛的模块，而不是塞进门面。
- **REST 与 WebSocket。** REST 负责有界的请求/响应操作，并提供会话领域的读模型。WebSocket 通道承载实时流：Agent 文本、Action 与 Cell 生命周期、审批、Notebook 更新和终止事件，并做缓冲，让重连的浏览器可以续传。
- **会话服务与投影。** mutation service 管理计划、审阅、Artifact、分支、恢复、会话包、Skill 与删除。projection service 把规范 Ledger、执行、血缘、Context 和 Security 状态转成经脱敏、可以安全交给浏览器的 DTO。投影只是一个视图，它永远不是底层的终止信号或事务信号。
- **内核所有权。** 每个 Web 会话通过 `SessionRunner` 拥有一个 Python slot 和一个 R slot，两者相互独立、惰性启动。[`execution_coordinator.py`](execution_coordinator.py) 发放 FIFO ticket，让 Agent、用户 REPL、恢复和生命周期这几类写入方不会互相压到一起；中断只会打到持有那把 lease 的确切 owner。Tool-only 路由不会启动前台的会话 slot，不过个别工具可以自己管理一个专用 worker。
- **持久化边界。** 持久事实经 `Store` 仓储写入。WebSocket 状态和活着的内核命名空间只存在于进程内。没有任何事务能同时覆盖 SQLite、工作区文件、内核进程和 socket 投递这四者。

## 完成、Notebook 与恢复边界

- Cell 的结果是回到外层循环的一条 observation，它本身不代表任务完成。要完成，必须有一个单独且有效的、由 Engine 拥有的 `finalize_response`，或者 Python Cell 内部调用的 `host.submit_output(...)`。R Cell 根本无法完成任务。
- 只包含 `host.submit_output` 协议调用的 Cell 照样会执行，也会留在原始执行历史与审计记录里，但实时和重新打开的 Notebook 投影会把它过滤掉。`.ipynb` exporter 读的是没有套用该过滤的不可变执行历史，所以它导出的是 raw/audit 版本，可能带上这个系统 Cell。
- 恢复执行已经接入 REST/UI、FIFO 所有权和 Python/R 候选内核流水线，但仍然是 **Partial**：不安全或非确定性的 Cell 会被归类为 `never`；系统不会去序列化任意的历史命名空间；某个语言的候选内核如果无法变为 active，整个恢复可以就此停下并给出显式的 Partial 结果。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | 稳定的包门面，导出 `build_server` 与 `serve`。 |
| [`action_timeline.py`](action_timeline.py) | 把规范的 Action Ledger 投影成 UI 真正看到的 Timeline。一条记录足以说清：跑的是什么、怎么结束的、用掉哪些权限、花了多少用量、引用了哪些 Artifact，而且这些内容都有界、都经过脱敏。供应商的 `wire_state` 和原始参数字符串被刻意省略，避免有人把一个调试端点变成凭据或协议的转储口。 |
| [`agent_run.py`](agent_run.py) | 把 `AgentEngine` 适配到 Web 契约。它流式输出安全的文本与代码草稿，发出 Web 事件，处理取消，并通过注入的端口执行原生 Action 或 Cell。 |
| [`artifacts.py`](artifacts.py) | Agent 写出的工作区文件在这里变成带版本的 Artifact。UI 上的编辑、重命名、上传、恢复和提升也走同一个 service，版本每动一次，快照、溯源和广播都跟着对齐。 |
| [`cell_run.py`](cell_run.py) | 按固定顺序跑完一个 Python/R Cell：执行准入、安全检查、内核执行、实时输出、Artifact 捕获、执行日志、终止投影。这个事务跑完只是一条 observation，它不会判定 Agent 的任务已经完成。 |
| [`completions.py`](completions.py) | 生成用户看到的那段叙述。进度和结果文字都做了本地化；结构化的 completion 是照着真实的 Artifact 版本增量渲染的，而不是照着一句声称。隐藏推理不会进到这里。 |
| [`daemon.py`](daemon.py) | 遗留的最小线程 HTTP 服务，为兼容而保留 `/`、`/health` 和 `/run`。它不是 Gateway，也不拥有 Gateway 的 WebSocket、Origin/认证检查或单例生命周期。 |
| [`execution_coordinator.py`](execution_coordinator.py) | 会话级 FIFO 执行所有权的 Web 适配层。ticket 状态会被投影成 WebSocket 事件；已准入的 ticket 会绑定到它的取消事件和当时那把内核 lease 上；中断只会打到由那个执行 id 精确持有的那把 lease。 |
| [`execution_views.py`](execution_views.py) | 读不可变的 Cell 历史，回答 Notebook 想问的问题：这个 Cell 跑在哪个运行时 generation 上、依赖了什么、之后是否已经失效、重试过几次、数据从哪来。 |
| [`gateway.py`](gateway.py) | HTTP/WebSocket 的主组合门面。协议 frame 的编解码、hub 与续传缓冲、`SessionState` 与 `SessionRunner`、REST 路由、静态资源和安全检查都落在这里，本表所列全部 service 的装配也在这里。 |
| [`global_views.py`](global_views.py) | 组合跨会话的项目级研究 Timeline 与 Artifact 血缘视图。 |
| [`model_discovery.py`](model_discovery.py) | 探测一小份固定的 loopback URL 名录，找出 OpenAI-compatible 的模型服务；探测时关闭代理、拒绝重定向，调用方无法把它变成通用的 SSRF 原语。结果只是一个 profile 建议：不会改动模型设置，也不会存下凭据。 |
| [`model_profiles.py`](model_profiles.py) | 一个模型供应商 profile 进来时要过这里，被校验和迁移；落库、激活、删除时还要再过一次。凡是要公开出去的东西，凭据都会被清掉。顶部的模型选择器也由它构建：只列当前模型和已保存的 profile，别的一概不列——没人配过的 endpoint 不该出现在那里，选了也只会在发消息时失败。 |
| [`notebook_export.py`](notebook_export.py) | 把原始的不可变 Python 或 R 执行历史确定性地导出为只读 `.ipynb` 文件和带 checksum 描述的 bundle。它不套用 Notebook 投影那道过滤，所以只含协议调用的 completion Cell 仍可能出现在导出结果里。 |
| [`plans.py`](plans.py) | 管理结构化计划的生命周期。planner 的回复先被解析、规范化，草稿和它的 JSON Artifact 落库，公开的审阅形态由此暴露，通过审阅的计划再被带到执行。实时的 `host.plan_update` 变更仍留在 `HostDispatcher`。 |
| [`recovery_control.py`](recovery_control.py) | 投影恢复 journal 与 generation 状态，并组合出当前可行的、经校验和脱敏的恢复 Action 计划。只有在工作区目录树和完整的 bootstrap 清单都在的前提下，它才会说某个 checkpoint 可恢复。 |
| [`recovery_execution.py`](recovery_execution.py) | 在精确的执行所有权下执行一次恢复 mutation。所有语言候选内核跑在同一个 recovery id 下，遇到第一个未完成的候选就停，最后落一条持久的会话终止事件。 |
| [`recovery_recipe.py`](recovery_recipe.py) | 把不可变的 Cell 事实、依赖闭包、环境需求、sidecar 和确定性检查编译成一份恢复 recipe。保守是有意为之：影响状态却过不了这些检查的 Cell 会以 `never` 重放步骤的形式留在 recipe 里，于是校验会报 Partial，而不是默默宣称旧命名空间还在。 |
| [`recovery_runtime.py`](recovery_runtime.py) | 恢复流水线接上真实基础设施的地方。它为一个会话拉起候选的 Python 和 R 内核，探测环境，做 bootstrap，做验证，然后提交或回滚。 |
| [`renderers.py`](renderers.py) | 从 Artifact kind、content-type 和扩展名到安全科学 renderer 的注册表，以及公开的 renderer 描述。它只有元数据：不导入任何科学库，也不执行 Artifact 的内容。 |
| [`reviews.py`](reviews.py) | 先攒出一次科学审阅所依据的有界证据包，再把这次审阅推到结果。整个过程可取消，结果会落到持久化、用量记账和公开的审阅事件上。 |
| [`session_branching.py`](session_branching.py) | 让一个会话长出分支所需的全部动作：打 checkpoint、隔离 fork、预览 revert、激活分支，以及把 revert/undo 历史只追加地记下来。revert 从不改写旧的 checkpoint：它先把当前状态记成撤销目标；如果当前 head 之后有外部文件被改动，这次操作会记为 `conflict`，一个字节都不会动。 |
| [`session_deletion.py`](session_deletion.py) | 会话被持久删除后的清理。会话聚合、工作区、快照/CAS 引用和进程内状态都会清掉，而这个会话自己 scope 之外的东西一概不碰。 |
| [`session_domain.py`](session_domain.py) | 高层的会话领域组合，路由 handler 调它，而不是自己去拼装仓储。它对外承接 checkpoint 与 cursor checkpoint、分支、Timeline、导出、renderer、会话包操作与恢复。 |
| [`session_package.py`](session_package.py) | 创建和导入会话 ZIP 包，过程确定、带 checksum。传输这一段由过滤秘密、防路径穿越和隔离区中转来把关。导入会先校验整个压缩包再创建任何东西；导入进来的会话落在一个已结束的内核 generation 上，这是一条显式的只读/待恢复边界。 |
| [`session_recovery.py`](session_recovery.py) | 启动时协调过期的运行时状态，并在 activity 与恢复阻塞条件的约束下确定性地回收空闲内核。旧 daemon 遗留下来的活 generation 会被标成 `abandoned` 并保持可审计；这里没有任何代码反序列化对象，也不声称内存还活着。 |
| [`session_runtime.py`](session_runtime.py) | 保存会话的控制平面对象，例如 dispatcher、委派树和动态 capability，让语言 worker 可以启动、替换或停止，而不丢掉这些状态。 |
| [`skill_sidecars.py`](skill_sidecars.py) | 把 worker 实际加载成功的 Skill sidecar 记到那个精确的内核 generation 上，用 compare-and-swap 合并进内容寻址的 bootstrap 清单，这样恢复重放的就是真实观察到的东西。Host 进程从不导入或执行 sidecar。 |
| [`share_projection.py`](share_projection.py) | 把一个会话构建成一份冻结、扁平化的 `ShareProjection`（单一 synthetic root、无 checkpoint、无 memories/策略），再分两路序列化：一个 `import_bytes` 兼容的 bundle 和一份脱敏的查看器文档。复用会话包的失败即拒 secret 闸门。 |
| [`share_router.py`](share_router.py) | 单个分享的只读公网请求处理器：仅 GET/HEAD、有且仅有两个读取根（内存查看器资产 + 当前 lease 的快照）、严格 CSP、单段 Range，以及统一 404。它绝不触碰内核、dispatcher 或任何 gateway 路由。 |
| [`share_service.py`](share_service.py) | Web 分享的两阶段发布（DB 状态机 + 不可变版本目录 + `current.json` 指针），带 SnapshotLease 引用计数 GC、崩溃恢复、有效期清扫与撤销。FIFO 准入与隧道客户端由外部注入。 |
| [`skills.py`](skills.py) | Web Customize 里用户自撰 Skill 文档的生命周期。它管增删改查和导入，管 UI 读的那份目录投影，也管能力的启用。 |
| [`titles.py`](titles.py) | 在后台根据第一条消息生成会话标题。模型配置延迟绑定，持久化和广播都做了防竞态处理。 |
| [`variable_inspector.py`](variable_inspector.py) | 通过一个很窄的 manager 协议请求，读取活着且空闲的 Python/R 命名空间，返回有界、净化过的变量预览。它不会创建会话，也不会创建 worker，更不会进入 Cell 事务。 |
| [`workbench_state.py`](workbench_state.py) | 根据持久状态与实时状态投影 Context 和 Security 面板。它不暴露消息内容；在真实 worker 报回自测结果之前，它也不会声称 OS 沙箱已经存在。 |
| [`ws_frames.py`](ws_frames.py) | 由 gateway WebSocket 与分享隧道共用的、加固过的 RFC 6455 帧编解码。按角色的读取会校验掩码方向、FIN、RSV、opcode、canonical 长度、64 位最高位、控制帧大小与载荷上限；gateway 通过别名保留原有调用点。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`webui/`](webui/) | 手写的浏览器客户端与科学 Artifact renderer，由 gateway 作为静态文件提供。没有构建步骤，也没有 npm。客户端唯一的第三方库是 3Dmol，按需从 `webui/vendor/` 注入；自带的那份加载不上时，会回退到 `3Dmol.org` 的 CDN。 |

## 修改注意事项

- [`gateway.py`](gateway.py) 要一直是组合与兼容门面，只做外科式修改。新的领域行为放到真正拥有它的那个 service 里。
- 只要动到内核生命周期、WebSocket 流、执行所有权或 Artifact 捕获，除了跑聚焦测试，还必须在真实浏览器里端到端跑一遍。
- 交给浏览器的 DTO 必须有界且脱敏。原始供应商 payload、工具参数、凭据和不受限的文件系统路径都不该出现在投影里。

另见仓库[架构指南](../../docs/architecture.md)、[Web 应用指南](../../docs/webapp.md)与 [`webui/` README](webui/README_zh.md)。

- [`security_headers.py`](security_headers.py) —— 基于 hash 的 CSP 与加固响应头，作用于每一个响应。
- [`contract.py`](contract.py) —— 版本化对外面的统一信封、错误码与 route/event 清单。
- [`response_schema.py`](response_schema.py) —— 一套小而明确的形状代数（类型、必填键、元素形状），零依赖，因为 core 只用标准库。它回答的是「这个响应的形状变了吗」；它不是 JSON Schema draft-2020-12，也不假装是。
- [`response_capture.py`](response_capture.py) —— 观测各 route 真实返回了什么，并固化进 [`docs/response-schemas.json`](../../docs/response-schemas.json)。它从外面包住 `make_handler` 的 `_api`，而不是在 `_json` 里挂钩子：gateway 测试会把 `handler._json` 换成自己的收集器，钩在真正的 `_json` 里会漏掉几乎所有 route，却看起来在正常工作。这里没有任何代码跑在生产路径上。
