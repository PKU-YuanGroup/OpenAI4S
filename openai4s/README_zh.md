# `openai4s` 包

[English](README.md)

这里是 OpenAI4S 的顶层 Python 包。核心已经实现，扩展面中仍为 Partial 的部分都会在各自的说明里标出。外层 Agent 循环、原生 JSON 控制工具、持久化科学内核、Host RPC 服务、存储、安全层以及 Web/CLI 适配器都挂在这个目录下，把它们组合起来的控制平面只用标准库。

## 在架构中的位置

OpenAI4S 有两个嵌套循环。[`agent/`](./agent/) 里的外层循环在每个模型步骤中最多接受一个经过路由的动作：一个有序的原生工具批次、Engine 自有的 `finalize_response`，或者一个完整的 Python/R Cell。[`kernel/`](./kernel/) 里的内层循环让语言 worker 一直活着，并在 Python Cell 尚未结束时应答同步的 `host.*` 调用。[`host_dispatch.py`](./host_dispatch.py) 是这两个平面之间的兼容与组合边界，边界背后的具体行为放在 [`host/`](./host/) 下的聚焦服务里。

纯控制类的工作可以由 Engine 自有的 finalizer 收尾。在 Python Cell 内部，只有 `host.submit_output(...)` 能发出完成信号；即使前面已经执行过 Cell，之后单独且有效的一次 `finalize_response` 仍然可以关闭 Engine。普通文本、原生工具结果、R Cell、取消和回合耗尽，本身都不是完成信号。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 声明包名与版本。导入它不会启动任何服务。 |
| [`__main__.py`](./__main__.py) | 让 `python -m openai4s` 可用，转交给 CLI 入口。 |
| [`artifact_restore.py`](./artifact_restore.py) | Artifact 恢复的唯一路径，原生控制平面和 Web 都走这里。它先校验历史快照，再把那些字节复制回工作区。落库的是一个新版本。历史不会被改写。 |
| [`bash_capability.py`](./bash_capability.py) | 保存语言无关的版本标记和命令摘要，短时、一次性的 shell capability 靠它们完成绑定。 |
| [`capabilities.py`](./capabilities.py) | 通过仓储接口判定某个 capability 或 specialist profile 在给定作用域下是否启用。 |
| [`config.py`](./config.py) | 零依赖加载 `.env`，并定义 `LLMConfig`、`SecurityConfig` 和全局 `Config` 数据类。只有 LLM 的 key、base URL 和 model id 走分层解析：先按供应商的变量，再看通用的 `OPENAI4S_LLM_*`，再退到供应商的内置默认值；key 还会最后兜底到该供应商惯用的变量（`ANTHROPIC_API_KEY`、`OPENAI_API_KEY` 等）。其余字段各按自己的默认值来：端口和回合上限读一个环境变量，读不到就用字面默认值；`data_dir` 和 `skills_dir` 退回算出来的路径（`~/.openai4s` 和仓库里的 `skills/`）；`egress_allowlist` 根本不读环境变量，它直接复制自 `egress.EGRESS_GROUPS`。 |
| [`egress.py`](./egress.py) | Host 持有的出站域名允许名单。Web 与 shell 的策略边界会查它，但它要显式打开才生效：除非 `OPENAI4S_EGRESS` 被设成生效值（`allowlist`、`on`、`1`、`enforce` 等），模式就是 `off`，出站调用不做任何允许名单检查，一律放行。真正打开时，它是 OS 沙箱的补充，不是替代。 |
| [`host_dispatch.py`](./host_dispatch.py) | 内核 `host_call` RPC 的兼容与组合 facade。一次调用要先过权限、审批、审计、回放、筛查和步骤事件策略，才会落到具体的 Host 服务上。 |
| [`jobs.py`](./jobs.py) | 在进程内运行后台计算任务，并限制其输出缓冲的大小。任务留下的工作文件可以持久化，但注册表本身只在内存里。 |
| [`mcp_client.py`](./mcp_client.py) | 纯标准库的 MCP stdio JSON-RPC 客户端，外加进程级的连接 manager：每个 connector 保持一条连接，覆盖工具、资源和 prompt。服务器发起的 sampling 不在范围内。 |
| [`onboarding.py`](./onboarding.py) | 无界面 CLI 使用的首次模型/供应商配置，做成一个小服务是为了可测试。 |
| [`permissions.py`](./permissions.py) | 进程级的权限 broker。它解析 allow/deny/ask 规则；需要用户拍板时，持久化一条审批请求并阻塞当前回合，同时处理取消与超时。无人值守的执行默认失败即拒绝，也仅仅是默认：运维把 `OPENAI4S_UNATTENDED_APPROVAL` 设成 `allow`，就等于主动选择了失败即放行，此后每一条无人应答的审批都会被放过。 |
| [`pkgscan.py`](./pkgscan.py) | 扫描 Python、conda 和 R 环境里包的可用性并做名称归一化，全程不把这些包导入核心。 |
| [`prompts.py`](./prompts.py) | 核心自己要发的那批小型单用途 prompt：压缩、审查 gate、溯源、Skill 检索、抽取、编辑和安全。 |
| [`replay.py`](./replay.py) | 把成功的 `host.*` 结果记进离线回放 tape（溯源、凭据读取这类内部管道调用刻意不入 tape）；导出的 notebook 回放这盘 tape 时，它负责发现调用顺序的漂移。 |
| [`review.py`](./review.py) | 对已完成回合的证据做一次有界、无工具的审查，并把 JSON verdict 标准化。审查者动不了工作区。 |
| [`store.py`](./store.py) | 持久化层的兼容 facade。唯一那条 SQLite 连接放在这里，schema 与 migration、受保护的只读查询也放在这里。各个聚焦的 storage 仓储拿到的是同一条连接和同一把锁。 |
| [`webtools.py`](./webtools.py) | Host 侧的 Web 搜索与抓取。transport 优先走标准库。内容转换在这里做，网络开关、SSRF 检查和 egress 强制也都在这里生效。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`adapters/`](./adapters/) | 位于标准库运行时核心之外的可选生态适配器。 |
| [`agent/`](./agent/) | 供应商中立的外层循环。它路由动作并收尾，在超过 token 阈值时压缩上下文，把活儿分发给子 Agent，也负责组合本地运行时。 |
| [`cli/`](./cli/) | 命令行生命周期和一次性任务入口。 |
| [`compute/`](./compute/) | Host 侧的 BYOC/远程计算注册表与任务编排；通用远程计算仍是 Prototype 能力面。 |
| [`execution/`](./execution/) | 科学 Cell 在内核之外要经过的环节：准入、取消、依赖投影、结果值和超时恢复。 |
| [`host/`](./host/) | `HostDispatcher` 组合 facade 背后的聚焦服务。 |
| [`kernel/`](./kernel/) | 常驻 Python/R worker 的所在地。语言无关的 manager 协议也在这里，还有环境选择、沙箱集成和 Cell 内的 Host RPC。 |
| [`llm/`](./llm/) | 供应商中立的 LLM 客户端。capabilities、标准化的消息与工具，以及标准库 transport，都架在每家供应商各自的 wire 适配器之上。 |
| [`mcp_servers/`](./mcp_servers/) | 用于演示和测试的内置纯标准库 MCP 示例服务器。 |
| [`sdk/`](./sdk/) | 注入 Python Cell 的兼容 `host` facade 和远程计算命名空间。 |
| [`security/`](./security/) | 沙箱和子进程环境隔离。它也筛查代码与内容、检查注入，并提供这些层要用的策略辅助模块。每一层都是独立的，其中有几层会失败即放行。 |
| [`server/`](./server/) | 标准库 HTTP/WebSocket workbench：session 服务、投影、恢复和静态 UI。若干专用的 UI/恢复工作流仍为 Partial。 |
| [`share/`](./share/) | Web 分享传输层：隧道线协议、纯标准库 WSS 客户端、daemon 侧出站 `TunnelClient`、无状态公网 relay，以及 SSRF 加固的 bundle 下载。快照本身在 `server/share_projection.py` 服务端构建。 |
| [`skills_loader/`](./skills_loader/) | 发现 Skill，并渐进披露：先只给名称和摘要，正文要等到真正加载。它同时负责 sidecar 校验、版本化安装和回滚。 |
| [`storage/`](./storage/) | 通过 `Store` 使用的聚焦 SQLite 仓储。 |
| [`tools/`](./tools/) | 基于类的供应商原生控制工具。每个工具自带 schema。围着它们的是注册表、动态工具生命周期，以及对 fenced 调用的兼容支持。 |

## 修改规则

- 核心只能靠 Python 标准库导入；可选的科学依赖必须在每一个使用点上加保护。
- 新的领域行为写进对应的聚焦 service/repository/tool，不要整体重写 `host_dispatch.py` 或 `store.py`。
- 内核协议有几条不变量：只有一方读取 frame、响应按 ID 路由、事务锁、generation 检查。改动时不要破坏它们。
- 安全与持久化标签按字面理解：尽力而为的投影和 Partial 能力面，不能写成保证。

## Trust Foundation 模块

- [`observability.py`](observability.py) —— correlation ID 与按形状脱敏的结构化日志。
- [`diagnostics.py`](diagnostics.py) —— 脱敏诊断包与有界的日志保留。
- [`evidence.py`](evidence.py) —— 仅用标准库校验导出的包，服务于尚不信任本机的接收方。
