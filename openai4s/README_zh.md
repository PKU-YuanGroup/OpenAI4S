# `openai4s` 包

[English](README.md)

这里是 OpenAI4S 的顶层 Python 包。核心已经实现；某个面若仍是 Prototype，或某项操作按设计只能以 Partial 收场，都会在各自的说明里标出。外层 Agent 循环、原生 JSON 控制工具、持久化科学内核、Host RPC 服务、存储、安全层以及 Web/CLI 适配器都挂在这个目录下，把它们组合起来的控制平面只用标准库。

## 在架构中的位置

OpenAI4S 有两个嵌套循环。[`agent/`](./agent/) 里的外层循环在每个模型步骤中最多接受一个经过路由的动作：一个有序的原生工具批次、Engine 自有的 `finalize_response`，或者一个完整的 Python/R Cell。[`kernel/`](./kernel/) 里的内层循环让语言 worker 一直活着，并在 Python Cell 尚未结束时应答同步的 `host.*` 调用。[`host_dispatch.py`](./host_dispatch.py) 是这两个平面之间的兼容与组合边界，边界背后的具体行为放在 [`host/`](./host/) 下的聚焦服务里。

纯控制类的工作可以由 Engine 自有的 finalizer 收尾。在 Python Cell 内部，只有 `host.submit_output(...)` 能发出完成信号；即使前面已经执行过 Cell，之后单独且有效的一次 `finalize_response` 仍然可以关闭 Engine。普通文本、原生工具结果、R Cell、取消和回合耗尽，本身都不是完成信号。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 声明包名与版本。导入它不会启动任何服务。 |
| [`__main__.py`](./__main__.py) | 让 `python -m openai4s` 可用，转交给 CLI 入口。 |
| [`artifact_restore.py`](./artifact_restore.py) | Artifact 恢复的唯一路径，原生控制平面和 Web 都走这里。它先校验历史快照，再把那些字节复制回工作区。落库的是一个新版本。历史不会被改写。受信任的快照根目录——daemon 自己写入不可变快照的每一个目录，包含会话导入在内——由一处统一推导：两份靠手工维护的清单，正是「某个目录写得进去、读的时候却被拒」的由来。这条边界管的是收容，不是完整性：每次读取时字节仍要与版本行记录的 sha256 和大小逐一比对。它的拒绝是带类型的，因此本模块自己写的那句话（「校验和不匹配」）能传到调用方，而从 OS 层逃出来、动辄带着绝对路径的异常文本则不能。 |
| [`bash_capability.py`](./bash_capability.py) | 保存语言无关的版本标记和命令摘要，短时、一次性的 shell capability 靠它们完成绑定。 |
| [`capabilities.py`](./capabilities.py) | 通过仓储接口判定某个 capability 或 specialist profile 在给定作用域下是否启用。 |
| [`config.py`](./config.py) | 零依赖加载 `.env`，并定义 `LLMConfig`、`SecurityConfig`、`ShareConfig` 和全局 `Config` 数据类。只有 LLM 的 key、base URL 和 model id 走分层解析：先按供应商的变量，再看通用的 `OPENAI4S_LLM_*`，再退到供应商的内置默认值；key 还会最后兜底到该供应商惯用的变量（`ANTHROPIC_API_KEY`、`OPENAI_API_KEY` 等）。其余字段各按自己的默认值来：端口和回合上限读一个环境变量，读不到就用字面默认值；`data_dir` 和 `skills_dir` 退回算出来的路径（`~/.openai4s` 和仓库里的 `skills/`）；`egress_allowlist` 根本不读环境变量，它直接复制自 `egress.EGRESS_GROUPS`。`ensure_dirs()` 还会把数据目录以及它创建的每个子目录收紧成仅属主可访问：那里放着凭据数据库、artifact 和日志，而按进程 umask 创建的话，本机每个账号都读得到。 |
| [`datapro.py`](./datapro.py) | 唯一受管的火山方舟 DataPro connector：固定且公开的 transport 元数据、只经 SecretBroker 解析的 Agent Plan Key（只在 Ark 活跃时复用其 key）、严格的 `structuredContent.code` 判定、凭据脱敏，以及供网页与 Agent 共用的查询结果 Artifact payload。 |
| [`doubao_search.py`](./doubao_search.py) | 固定、纯标准库的豆包搜索客户端。每次有界 POST 前才从当前 Store 解析共用 Agent Plan Key，拒绝重定向、清除上游反射的凭据，并把经过验证的 `Result.WebResults` 归一成现有 `web_search` 工具的结果。 |
| [`endpoint_identity.py`](./endpoint_identity.py) | LLM endpoint 的唯一写法，以及剥离其中 secret 的唯一位置。`base_url` 原本按用户输入原样存储、由 `GET /model-profiles` 发布、并冻结进不可变 revision——所以带 userinfo 或 query token 的 URL 会把凭据同时留在这三处，这是 7.2「secret 不进 snapshot」被 endpoint 字段而非 key 字段绕过。在存储处就归一，意味着后续任何界面都不必记得去脱敏。 |
| [`egress.py`](./egress.py) | Host 持有的出站域名允许名单。Web 与 shell 的策略边界会查它，但它要显式打开才生效：除非 `OPENAI4S_EGRESS` 被设成生效值（`allowlist`、`on`、`1`、`enforce` 等），模式就是 `off`，出站调用不做任何允许名单检查，一律放行。真正打开时，它是 OS 沙箱的补充，不是替代。 |
| [`execution_principal.py`](./execution_principal.py) | 通过 `ContextVar` 把明确的单用户、服务或团队身份随执行一起传递。团队模式下缺失 principal 会成为权限失败，而不是退回不受限访问，因此 Host 数据读取不能静默跨越租户边界。 |
| [`host_dispatch.py`](./host_dispatch.py) | 内核 `host_call` RPC 的兼容与组合 facade。一次调用要先过权限、审批、审计、回放、筛查和步骤事件策略，才会落到具体的 Host 服务上。 |
| [`http_deadline.py`](./http_deadline.py) | 鉴权 HTTP exchange 共用的纯标准库绝对截止机制。定制 HTTP(S) connection 只把当前 live socket 交给短时 watchdog，使 TCP connect、proxy CONNECT、TLS、响应状态/Header 与 body 读取共用一份墙钟预算；每条退出路径都会取消并 join timer。系统 DNS 解析仍是明确记录的标准库不可取消边界。 |
| [`jobs.py`](./jobs.py) | 在本进程内运行后台计算任务，限制同时活跃的任务数，并按字节、在二进制读取上限制输出缓冲——按字符设上限、又读的是文本管道，那道上限只在 `readline` 已经把任务在下一个换行符之前打印的全部内容分配出来*之后*才做裁剪。终态由唯一一处判定，且判定之后不再被覆盖；停止一个任务走的是与内核 shell 执行器同一套进程组逻辑，因为真正要紧的场景恰恰是 shell 退出了、它拉起的活儿没退。注册表在内存里，但每个任务还会在数据目录下留下一份原子写入的回执：以前重启一次，一个跑了四小时的任务就直接从 Jobs 面板上消失，所以现在未完成的任务会以终态 `abandoned` 被重新收编——不复活，也刻意既不叫 `failed`（那是在指责命令本身）也不叫 `cancelled`（那是在声称有人想停它）。 |
| [`mcp_client.py`](./mcp_client.py) | 纯标准库 MCP 客户端 facade 与进程级 manager。stdio JSON-RPC 仍是默认 transport；显式的受管 connector 可以分派到 Streamable HTTP sibling。连接覆盖工具、资源与 prompt，带绝对截止时间并按 connector id 单飞构建。服务器发起的 sampling 仍不在范围内。 |
| [`mcp_http.py`](./mcp_http.py) | 纯标准库 MCP Streamable HTTP transport：独立 JSON-RPC POST、JSON/SSE 响应、协商后的 session 与协议版本、有界响应体、网络策略/SSRF 检查，并拒绝重定向以免鉴权 Header 被带往另一 origin。 |
| [`mcp_protocol.py`](./mcp_protocol.py) | stdio 与 Streamable HTTP 客户端共用的 MCP 响应大小边界和带类型 transport 错误。 |
| [`onboarding.py`](./onboarding.py) | 无界面 CLI 使用的首次模型/供应商配置，做成一个小服务是为了可测试。API key 一律经 secret broker 读写，绝不落成普通设置行：迁移之后那一行存的是一个引用，而无论 keychain 里那个值还在不在，引用本身都是 truthy——直接读原始值的话，一个已被手动吊销的 key 依然会被报成「已配置」。 |
| [`permissions.py`](./permissions.py) | 进程级的权限 broker。它解析 allow/deny/ask 规则；需要用户拍板时，持久化一条审批请求并阻塞当前回合，同时处理取消与超时。无人值守的执行默认失败即拒绝，也仅仅是默认：运维把 `OPENAI4S_UNATTENDED_APPROVAL` 设成 `allow`，就等于主动选择了失败即放行，此后每一条无人应答的审批都会被放过。 |
| [`pkgscan.py`](./pkgscan.py) | 扫描 Python、conda 和 R 环境里包的可用性并做名称归一化，全程不把这些包导入核心。 |
| [`platform_support.py`](./platform_support.py) | kernel 可以在哪些平台上启动，一处声明。Windows 在 spawn 路径上被**拒绝**，而不是在 onboarding 时被警告一句——警告后继续和拒绝是两种不同的承诺，而对一个以「结果可信」为立身之本的产品来说，半可用的 kernel 是更糟的结局。 |
| [`prompts.py`](./prompts.py) | 核心自己要发的那批小型单用途 prompt：压缩、审查 gate、溯源、Skill 检索、抽取、编辑和安全。 |
| [`replay.py`](./replay.py) | 把成功的 `host.*` 结果记进离线回放 tape（溯源、凭据读取这类内部管道调用刻意不入 tape）；导出的 notebook 回放这盘 tape 时，它负责发现调用顺序的漂移。 |
| [`review.py`](./review.py) | 对已完成回合的证据做一次有界、无工具的审查，并把 JSON verdict 标准化。审查者动不了工作区。 |
| [`scientific_reviewer.py`](./scientific_reviewer.py) | Stage 3 Scientific Reviewer V2：只读冻结 Evidence Snapshot、严格 `pass`/`issues`/`incomplete` schema，以及冻结的 provider/base_url/model 指纹。省略 Artifact 不能判 pass。 |
| [`store.py`](./store.py) | 持久化层的兼容 facade。唯一那条 SQLite 连接放在这里，schema 和受保护的只读查询也放在这里。各个聚焦的 storage 仓储拿到的是同一条连接和同一把锁。migration 已经不属于这个 facade：它们带版本、走事务、按 checksum 记录，放在 [`storage/migrations.py`](./storage/migrations.py) 里——每次打开都把所有表重新探一遍、再把失败的 `ALTER` 逐个吞掉，会让「这个数据库是不是最新的」变成代码自己都答不上来的问题。`close()` 是幂等的，并且只逐出恰好是它自己的那个缓存实例，因此之后 `get_store(path)` 可以为同一路径开出新的一代。 |
| [`webtools.py`](./webtools.py) | Host 侧的 Web 搜索、抓取、探测与下载。transport 优先走标准库。内容转换在这里做，网络开关、SSRF 检查和 egress 强制也都在这里生效——而且是每一跳都生效，这正是重定向要手工跟随、而不是交给 `urllib` 的原因：由 opener 在内部走完的跳转链，只在第一个 URL 上被检查过一次，之后再没有。任何绕开这里触达网络的能力（内置 Skill 用裸 `urllib` 拉一个归档、探测资源是否存在时另起一个函数），就等于同时跳过了允许名单和这道 guard。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`adapters/`](./adapters/) | 位于标准库运行时核心之外的可选生态适配器。 |
| [`agent/`](./agent/) | 供应商中立的外层循环。它路由动作并收尾，在超过 token 阈值时压缩上下文，把活儿分发给子 Agent，也负责组合本地运行时。 |
| [`cli/`](./cli/) | 命令行生命周期与一次性任务入口，以及环境、支持面、benchmark 和分享相关的命令。 |
| [`compute/`](./compute/) | Host 侧的 BYOC/远程计算注册表与任务编排；通用远程计算仍是 Prototype 能力面。 |
| [`execution/`](./execution/) | 科学 Cell 在内核之外要经过的环节：准入、取消、依赖投影、结果值和超时恢复。 |
| [`host/`](./host/) | `HostDispatcher` 组合 facade 背后的聚焦服务。 |
| [`kernel/`](./kernel/) | 常驻 Python/R worker 的所在地。语言无关的 manager 协议也在这里，还有环境选择、沙箱集成和 Cell 内的 Host RPC。 |
| [`llm/`](./llm/) | 供应商中立的 LLM 客户端。capabilities、标准化的消息与工具，以及标准库 transport，都架在每家供应商各自的 wire 适配器之上。 |
| [`mcp_servers/`](./mcp_servers/) | 用于演示和测试的内置纯标准库 MCP 示例服务器。 |
| [`orchestration/`](./orchestration/) | 集群控制平面：被请求的工作是什么、为其中一次尝试授予了什么资源，以及任何资源平面都要呈现的 backend Protocol。它的定义性特征在于它**不**包含什么——一条被检查的规则（INV-2）把调度器的每一个词都挡在核心的源码与 import 图之外，好让做策略的代码长不出带调度器形状的假设。 |
| [`sdk/`](./sdk/) | 注入 Python Cell 的兼容 `host` facade 和远程计算命名空间。 |
| [`security/`](./security/) | 沙箱和子进程环境隔离。它也筛查代码与内容、检查注入，并提供这些层要用的策略辅助模块。每一层都是独立的，其中有几层会失败即放行。 |
| [`server/`](./server/) | 标准库 HTTP/WebSocket workbench：session 服务、投影、恢复和静态 UI。恢复执行、分支 fork/激活/revert/undo 这组控件，以及 Notebook 的四种导出形式，都已端到端接通——每一个都受能力门控：控件会带着理由被禁用，而不是等到路由那里才失败，这也正是只有带可证明检查点映射的记录才提供 Fork 的原因。这里的 `Partial` 说的是一次恢复的**结果**，不是没做完的功能：任意历史命名空间被刻意地从不序列化，因此一次无法重建并验证它的恢复，按设计就以 Partial 收场。 |
| [`share/`](./share/) | Web 分享传输层：隧道线协议、纯标准库 WSS 客户端、daemon 侧出站 `TunnelClient`、无状态公网 relay，以及 SSRF 加固的 bundle 下载。快照本身在 `server/share_projection.py` 服务端构建。 |
| [`skills_loader/`](./skills_loader/) | 发现 Skill，并渐进披露：先只给名称和摘要，正文要等到真正加载。它同时负责 sidecar 校验、版本化安装和回滚。 |
| [`telemetry/`](./telemetry/) | 选择加入的匿名遥测，默认关闭。仅计数与枚举，零自由文本——而且强制是针对**值**的：`{"error_type": "ValueError"}` 和 `{"error_type": "FileNotFoundError: /home/y/unpublished/cohort.csv"}` 过的是同一个键检查。刻意没有可容纳自由文本的取值域，因此新增这类字段必须先新增一个域类。 |
| [`storage/`](./storage/) | 通过 `Store` 使用的聚焦 SQLite 仓储，以及它们背后带版本的 migration runner。 |
| [`benchmark/`](./benchmark/) | 带版本的科学工作流基准的 runner，清单在 [`workflows/`](../workflows/README_zh.md)。每一步都驱动生产代码——真实的 Store、kernel manager、host dispatcher 与 compute manager——只有离线跑不了的才被注入：模型、网络、包管理器。声明的结果是契约的一部分，所以期望 `failure` 的用例在跑出干净成功时判失败。 |
| [`tools/`](./tools/) | 基于类的供应商原生控制工具。每个工具自带 schema。围着它们的是注册表、动态工具生命周期，以及对 fenced 调用的兼容支持。 |

## 修改规则

- 核心只能靠 Python 标准库导入；可选的科学依赖必须在每一个使用点上加保护。
- 新的领域行为写进对应的聚焦 service/repository/tool，不要整体重写 `host_dispatch.py` 或 `store.py`。
- 内核协议有几条不变量：只有一方读取 frame、响应按 ID 路由、事务锁、generation 检查。改动时不要破坏它们。
- 安全与持久化标签按字面理解：尽力而为的投影和 Partial 能力面，不能写成保证。

## Trust Foundation 模块

- [`observability.py`](observability.py) —— correlation ID 与按形状脱敏的结构化日志。
- [`memory_budget.py`](memory_budget.py) —— 一次系统提示里最多能塞进多少「记住的上下文」。原先的注入就是 `mems[:50]`：只数条数，别的什么都不管。50 条粘贴进来的实验方案大约是 60 万字符——在 262k token 的窗口里，用户还没开口就已经有一半以上被背景信息吃掉了，而且每一轮都如此。现在有三条预算（条数、单条长度、总长度），并且会明确告诉模型「有内容被略去了」，这样它可以说明自己记住的上下文并不完整，而不是当作什么都知道来作答。同一个缺口还有「显示」的一半：Context 面板把系统提示算进了对话的 `text` 里，于是常驻上下文看起来像是对话太长，把用户引向 compaction——而 compaction 根本不会动系统提示。现在 `agent/compaction.py` 把 `system_prompt` 作为独立分量上报（`total` 不变），`server/workbench_state.py` 则会报告预算略去了什么。
- [`doctor.py`](doctor.py) —— 一条命令回答「这套安装能不能干活」：模型、运行时、隔离、磁盘、连接器、远程计算。不依赖 daemon——需要它的场景往往正是 daemon 起不来的时候。
- [`diagnostics.py`](diagnostics.py) —— 脱敏诊断包与有界的日志保留。
- [`evidence.py`](evidence.py) —— 仅用标准库校验导出的包，服务于尚不信任本机的接收方。
