# Worker 侧 Host SDK

[English](README.md)

本包是 Python 内核侧的内层循环实现。[`worker.py`](../kernel/worker.py) 调用 `build_host(host_call, ...)`，并把返回的 singleton 注入为 `host`。大多数方法都是轻量同步 facade：编码公开 Python 参数，向 daemon 发送一个 `host_call`，等待匹配的 `host_response`，解码结果，并返回或在软错误时抛出异常。

## 在架构中的位置

SDK 不是授权边界，通常也不实现 capability 的具体行为。[`HostDispatcher`](../host_dispatch.py) 与 [`openai4s/host`](../host) 中的 service 负责校验、权限、审批、审计、筛查和 Host 侧工作。有两个重要的 worker-local 例外：

- `host.bash(...)` 在 sandboxed scientific worker 内执行子进程，但必须先由 Host 签发并原子消费精确的一次性 capability。
- `host.compute` 在本地创建 Python handle object，而所有 provider 发现、job submission、status、cancel 和 harvest 都通过 Host call 完成。

R 分析 worker 不导入本包，也没有 `host` singleton。

## 文件

| 文件 | 职责 |
|---|---|
| [`__init__.py`](__init__.py) | 导出 Python worker 使用的组合入口 `build_host`。 |
| [`bash.py`](bash.py) | Worker-local shell executor：提交精确 command/cwd/generation/challenge proposal，校验返回的 capability，仅消费一次，抓取有界 workspace 元数据快照，启动子进程，并向 Host 上报脱敏/有界结果及文件 diff。 |
| [`compute.py`](compute.py) | 实现 `host.compute` namespace 和本地 instance/job handle，规范化 provider 参数与路径，把操作映射为 `compute_<op>` RPC，并提供 status/wait/result/cancel/close/attach 语义而不嵌入 provider transport。 |
| [`host.py`](host.py) | 定义公开 `host.*` facade、严格的顶层 snake_case/camelCase wire codec，以及 skills/query/lineage/endpoints/credentials/MCP/environments/science/compute namespace、文件/网络/delegation/session helper 和 `host.submit_output`。 |

## 子目录

本包没有受跟踪的子目录。

## RPC 与完成契约

- 每次调用在 Python Cell 内同步执行。即使用户代码创建线程，worker 的 Host-call transaction lock 也只允许一个请求处于 in-flight 状态。
- 值为 `None` 的可选字段会被省略，而不是编码为 JSON `null`，因为严格 Host schema 会区分“未提供”和非法 null。
- Host soft-error object 会转换为 `RuntimeError`。Provider/compute 错误可能携带结构化 error-kind 或 concurrency 信息，但仍然是失败。
- `host.submit_output(...)` 是唯一能把 Python Cell 标记为成功完成的 SDK 方法。打印、返回 Python value、R 结果或普通 Host call 成功都不会结束外层 Agent run。

## 安全与失败边界

- SDK 是运行在强大 Python 进程中的受信任代码。其参数检查有助于协议完整性，但不能替代 dispatcher 权限或 OS sandbox。
- Shell 授权把 token、command digest、canonical cwd、workspace root、worker generation、challenge 和过期时间绑定在一起。Worker 校验与 Host 消费都会 fail closed；daemon 重启会使未消费的内存 capability 失效。
- Shell stdout/stderr 脱敏是防御性且基于形状的，无法保证经刻意变换或未识别的 secret 不出现在输出中。
- Compute handle 是便利投影，并非持久化 Python object。Cell/kernel 重启后必须按 job ID 重建或 attach；可用性取决于 manager/provider 的持久化或内存状态。
- `host.compute` 是持续演进的集成面。存在公开 SDK 方法本身并不保证 provider 已配置、隔离成功、有远程容量、harvest 成功，或每个后续操作都有第二次审批。

## 相关文档

- [系统架构](../../docs/architecture.md)
- [安全模型](../../docs/security.md)
- [远程计算](../../docs/compute.md)
