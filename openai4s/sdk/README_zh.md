# Worker 侧 Host SDK

[English](README.md)

这里放的是 Agent 代码在 Python Cell 里拿到的那个 `host` 对象，也就是内层循环在 worker 一侧的实现。[`worker.py`](../kernel/worker.py) 调用 `build_host(host_call, ...)`，把返回的单例注入为 `host`。大多数方法都很薄，而且是同步的：把公开的 Python 参数编码好，向 daemon 发出一个 `host_call`，阻塞等待对应的 `host_response`，解码结果，然后返回值，或者在软失败时抛出异常。

## 在架构中的位置

SDK 不是授权边界，通常也不实现 capability 的具体行为。校验、权限、审批、审计、筛查以及真正的工作都在 Host 侧，由 [`HostDispatcher`](../host_dispatch.py) 和 [`openai4s/host`](../host) 下的各个 service 负责。只有两件事确实在 worker 里跑，而且都很关键：

- `host.bash(...)` 在科学 worker 内执行子进程，而且必须先由 Host 签发一个精确的一次性 capability 并原子地消费掉。这个 worker 本身是否受操作系统层面的约束，是另一个问题：要看沙箱模式，也要看约束是否真的建立起来。默认模式是 `auto`，探测或自检失败时它照样把 worker 拉起来，只报一个 `state="unavailable"`，所以子进程完全可能跑在没有沙箱的环境里。
- `host.compute` 只在本地创建 Python 句柄对象。provider 发现、job 提交、状态查询、取消和结果回收全都是 Host call。

R 分析 worker 从不导入本包，那边也没有 `host` 单例。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | 导出 `build_host`，即 Python worker 调用的组合入口。 |
| [`bash.py`](bash.py) | 在 worker 里执行 shell 命令，而且只在拿到自己无法签发的授权之后才执行。它提交精确的命令、cwd、内核 generation 和 challenge，再逐项校验返回的 capability 上的每一处绑定，token 只花一次。执行前后各抓一份有界的工作区文件元数据快照。两条流都是边产出边抽干、只留下尾部——`capture_output=True` 会在上限生效之前先把两条流整份放进 worker 内存，于是这个预算描述的是调用方看见了多少，而不是实际分配了多少——超时也改成压在整个进程**组**上，因为 `subprocess.run(timeout=)` 只杀 shell：`bash -c "python train.py"` 会返回 `completed`、rc 为 0，而它拉起的那个 python 还在继续跑。有界的结果和文件增删改清单会上报给 Host，其中形似机密的路径已被遮蔽；每条流还各自带上 `seen`/`retained`/`dropped`/`truncated`，这样一条打印了五百万字符的命令，就不会被记成一条正好打印了三万字符的命令。 |
| [`compute.py`](compute.py) | 支撑 `host.compute` 命名空间以及本地的 instance 与 job 句柄。它规范化 provider 参数和路径，把每个操作映射为一次 `compute_<op>` RPC，并在此之上提供 submit/status/result/cancel/download/upload/close/attach，以及 `reconcile` 和 `job_history`。provider 的传输层不在这里。后台不会替调用方跑任何东西：真正去探测远端并回收产物的正是 `.result()`，所以一个没人去 poll 的任务永远不会被回收，而且会一直计费到被取消为止。`submit_job` 接受 `idempotency_key`，因为没有它就没有任何依据把一次重试和一个新任务区分开，重试于是变成第二次真实的远端运行。终态词表是从 [`compute/states.py`](../compute/states.py) 导入的，而不是在本地又写一遍——本地那份比对的两个状态 Host 从未产生过，于是任何没被认出来的运行中状态都会被当成终态缓存下来。 |
| [`host.py`](host.py) | 公开的 `host.*` 门面。最上面是严格的 wire 编解码，负责 snake_case 与 camelCase 之间的映射。它下面是 skills/query/lineage/endpoints/credentials/MCP/environments/science/compute 等命名空间，文件、网络、委派与会话辅助方法，以及 `host.submit_output`。`materialise_artifact` 把另一个 session 的某个版本取进来，作为**本** session 自己的 Artifact，并留下一条指回来源的血缘边，这样即便来源 session 之后被删掉，基于它的分析仍然有可解析的溯源；`save_artifact(source=...)` 则带上检索时的来源信封——数据库、确切的请求、抓取时刻、返回字节的哈希——正是它把「一个保存下来的文件」和「一份证据」区分开。网络辅助方法新增了 HEAD 探测、`user_agent` 覆盖和 `web_download`，因为需要其中任何一项的 Skill 之前都改用裸 `urllib`——那样的请求既不受出网允许名单约束，也不经过 SSRF 防护。`web_search` 在配置了 Agent Plan key 时走豆包，否则走兜底引擎，并在 `source` 里报告用的是哪个。模型后端 bring-up 面新增了 `accelerator_status`——分开报告本地 GPU 与 SSH 远端 GPU 注册，因为看得见硬件并不代表某个仓库、环境、checkpoint 或 canary 已通过准入——以及 `stage_model_asset`，它只把用户提供的本地 checkpoint 导入进来，并不做准入。 |

## RPC 与完成契约

- 每次调用都在 Python Cell 内阻塞。即使用户代码自己起了线程，worker 的 Host-call 事务锁也只允许一个请求同时在飞。
- 值为 `None` 的可选字段会被直接省略，而不是发成 JSON `null`。严格的 Host schema 会区分“字段没给”和“给了一个非法的 null”，后者会被拒绝。
- Host 返回的软失败对象会转成 `RuntimeError`。provider 与 compute 的错误可能带上结构化的错误类别或并发信息，但它们仍然是失败。
- `host.submit_output(...)` 是唯一能把 Python Cell 标记为成功完成的 SDK 方法。打印、返回一个 Python 值、产出 R 结果，或者一次普通的 Host call 调成功了，都不会结束外层的 Agent run。

## 安全与失败边界

- SDK 是运行在一个本就很强大的 Python 进程里的受信任代码。它的参数检查有助于保住协议完整性，但替代不了 dispatcher 的权限判断，也替代不了 OS 沙箱。
- Shell 授权把 token 与命令摘要、canonical cwd、工作区根目录、worker generation、challenge 和过期时间绑在一起。worker 侧校验和 Host 侧消费都是失败即拒绝；daemon 重启会让所有还留在内存里的 capability 失效。本地 worker 从自己的环境变量里读 authorization generation；远端 worker 没有这样的环境，改由握手响应把它交过来，并在构造时经 `build_host(generation=...)` 传入——executor 只在构造那一刻读一次兜底值，事后再给就来不及了。
- 对 shell stdout/stderr 的脱敏是防御性的，靠的是形状匹配。它没法保证一个被刻意变换过、或者根本不认识的 secret 不出现在输出里。
- compute 句柄只是便利投影，不是持久化的 Python 对象。Cell 或内核重启之后，必须按 job ID 重新构造句柄或者重新挂回去。真正能活过这一切的是远端那个 job，所以才有 `host.compute.reconcile()`：它把 daemon 上次停下时还活着的任务一一点出来——也正因如此，它一个都不会替你重新提交：一个被找回的任务可能还在跑，也可能已经不在，猜错的代价要么是重复计费，要么是丢掉结果。
- `host.compute` 仍是一个在演进中的集成面。有一个公开的 SDK 方法，本身并不保证 provider 已经配好、隔离确实生效、远端还有容量、结果能成功回收，也不保证后续每一步操作都还会再走一次审批。

## 相关文档

- [系统架构](../../docs/architecture.md)
- [安全模型](../../docs/security.md)
- [远程计算](../../docs/compute.md)
