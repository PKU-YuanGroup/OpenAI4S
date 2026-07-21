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
| [`bash.py`](bash.py) | 在 worker 里执行 shell 命令，而且只在拿到自己无法签发的授权之后才执行。它提交精确的命令、cwd、内核 generation 和 challenge，再逐项校验返回的 capability 上的每一处绑定，token 只花一次。执行前后各抓一份有界的工作区文件元数据快照。有界的结果和文件增删改清单会上报给 Host，其中形似机密的路径已被遮蔽。 |
| [`compute.py`](compute.py) | 支撑 `host.compute` 命名空间以及本地的 instance 与 job 句柄。它规范化 provider 参数和路径，把每个操作映射为一次 `compute_<op>` RPC，并在此之上提供 status/wait/result/cancel/close/attach 语义。provider 的传输层不在这里。 |
| [`host.py`](host.py) | 公开的 `host.*` 门面。最上面是严格的 wire 编解码，负责 snake_case 与 camelCase 之间的映射。它下面是 skills/query/lineage/endpoints/credentials/MCP/environments/science/compute 等命名空间，文件、网络、委派与会话辅助方法，以及 `host.submit_output`。 |

## RPC 与完成契约

- 每次调用都在 Python Cell 内阻塞。即使用户代码自己起了线程，worker 的 Host-call 事务锁也只允许一个请求同时在飞。
- 值为 `None` 的可选字段会被直接省略，而不是发成 JSON `null`。严格的 Host schema 会区分“字段没给”和“给了一个非法的 null”，后者会被拒绝。
- Host 返回的软失败对象会转成 `RuntimeError`。provider 与 compute 的错误可能带上结构化的错误类别或并发信息，但它们仍然是失败。
- `host.submit_output(...)` 是唯一能把 Python Cell 标记为成功完成的 SDK 方法。打印、返回一个 Python 值、产出 R 结果，或者一次普通的 Host call 调成功了，都不会结束外层的 Agent run。

## 安全与失败边界

- SDK 是运行在一个本就很强大的 Python 进程里的受信任代码。它的参数检查有助于保住协议完整性，但替代不了 dispatcher 的权限判断，也替代不了 OS 沙箱。
- Shell 授权把 token 与命令摘要、canonical cwd、工作区根目录、worker generation、challenge 和过期时间绑在一起。worker 侧校验和 Host 侧消费都是失败即拒绝；daemon 重启会让所有还留在内存里的 capability 失效。
- 对 shell stdout/stderr 的脱敏是防御性的，靠的是形状匹配。它没法保证一个被刻意变换过、或者根本不认识的 secret 不出现在输出里。
- compute 句柄只是便利投影，不是持久化的 Python 对象。Cell 或内核重启之后，必须按 job ID 重新构造句柄或者重新挂回去；能不能挂回，取决于 manager 或 provider 还留着多少持久化或内存中的状态。
- `host.compute` 仍是一个在演进中的集成面。有一个公开的 SDK 方法，本身并不保证 provider 已经配好、隔离确实生效、远端还有容量、结果能成功回收，也不保证后续每一步操作都还会再走一次审批。

## 相关文档

- [系统架构](../../docs/architecture.md)
- [安全模型](../../docs/security.md)
- [远程计算](../../docs/compute.md)
