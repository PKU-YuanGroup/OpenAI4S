# Host 服务

[English](README.md)

Host 侧的 capability service 都放在这里，一个领域一个类，从 shell 授权一直到 Skill 编辑。[`HostDispatcher`](../host_dispatch.py) 负责把它们组合起来，并且始终包在每次调用的外面，充当共享的 RPC 外壳：参数校验、权限与审批、审计记录、不可信输出筛查、活动事件，以及软失败的路由。本包里没有任何一个 service 是独立对外暴露的网络 endpoint，它们只实现各自领域的行为。

## 在架构中的位置

Python worker 侧的 [`host` facade](../sdk/host.py) 发出一次同步 `host_call`。[`kernel/manager.py`](../kernel/manager.py) 把它交给 `HostDispatcher`；dispatcher 先应用策略，再调用下面某个 service。返回值随对应的 `host_response` 发回，阻塞的 Cell 就此恢复执行。能力重叠时，native 控制工具走的也是同一个 dispatcher，这样控制平面和内核内的调用遵守同一套策略。

service 可以返回单键的 `{"error": message}` 表示软失败。Python worker 会把它转成 `RuntimeError`；这既不是成功的科学结果，也不代表任务完成。权限、replay、审计和注入策略统一留在 dispatcher，大多数 service 有意不再实现一遍。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | 重新导出组合代码要用的大部分 service class。`BashAuthorizationService` 和 `ScienceConnectorService` 不在 `__all__` 里，调用方需要各自从它们所在的模块导入。 |
| [`bash.py`](bash.py) | 授权内核本地的 `host.bash`，但从不执行它；本模块不导入 `subprocess`。受信任的 Host 会把 worker 已经做过的安全与 egress 检查再做一遍，对 proposal 脱敏，然后签发一个短时 token，绑定命令摘要、cwd、worker generation 和 challenge。这张 token 只能兑换一次。worker 上报回来的结果，先限制长度并脱敏，再记录。 |
| [`completion.py`](completion.py) | Cell 唯一的成功契约。它校验一个 `output`、1–4 条已完成 action bullet 和可选的 output schema，并为当前 dispatch context 留下一份通过校验的 submission。 |
| [`credentials.py`](credentials.py) | session 内的凭据，留在内存里，以短时、绑定具体 action、只能用一次的 lease 形式发放。轮换某个凭据，它尚未兑换的 lease 全部作废。原始值不在这里持久化。 |
| [`data.py`](data.py) | 由 Store 支撑的数据面。一边是只读 SQL、schema 访问和 frame 浏览；另一边是 Artifact 的元数据、版本、路径、保存、恢复和图片投影，再加上溯源与血缘的读取和上报。Artifact 的枚举与查找只在调用方自己的 session 和 project 范围内进行。当前 frame 只是用来解析出 `root_frame_id`/`project_id` 这个作用域的句柄，所以同一 session 中更早的 Cell 写出的 Artifact 依然可以访问。 |
| [`delegation.py`](delegation.py) | session delegation runtime 的门面。它套用已存储的 agent profile override，注入内建的 specialist context；delegate、children、collect、stop、message、stats 这些调用本身，则直接透传给真正管理子 Agent 的 runtime。 |
| [`delegation_policy.py`](delegation_policy.py) | 把子 Agent 的 method/capability 策略解析一次，然后冻结。只要点名了 capability，策略就进入 restricted 模式。即便如此，除了列出的 capability 及其 alias，还有五个方法（`submit_output`、`prov_record`、`prov_resolve_path`、`search_capabilities`、`capabilities`）照样放行——任何 restricted 策略下都放行，哪怕 capability 列表是空的。逐 method 的 allow/ask/deny 决策和工具可见性一并带上，独立的 unrestricted 模式也会明确出现在投影里，而不是靠推断。 |
| [`endpoints.py`](endpoints.py) | loopback 端口分配、带 start/stop 脚本的 endpoint 元数据，以及对存活路由的就绪探测。注册只是把这些生命周期脚本存下来：它不执行脚本，也不引入自己的 egress 策略。 |
| [`files.py`](files.py) | 工作区的路径边界，只做这一件事。它解析后期绑定的 session 工作区，把相对路径关在里面，拒绝命中密钥文件名。其余方法是兼容分派，转给 class-based 的 file tool，具体读写行为在那边。 |
| [`llm.py`](llm.py) | 从运行中的 Cell 同步调用已配置的模型。批量请求会在 fan-out 上限内并发发出。该 service 也报告当前模型，但它给出的模型列表不是一份目录：里面只有一项，就是当前配置的那个模型及其上下文窗口。 |
| [`mcp.py`](mcp.py) | 解析持久化的 MCP connector（先按 id，再按精确显示名），并把 list/tools/call/resource/prompt 操作交给 MCP manager。筛查返回内容不归它管。权限和不可信输出检查留在 dispatcher。 |
| [`progress.py`](progress.py) | 待办清单留在这里的内存中；plan 的步骤和评审进度则落在 Store 里。勾掉一个步骤并不以“已批准”为前提：没有显式传 `plan_id` 时，被更新的就是 Store 为该 frame 返回的那个 plan，也就是最新的、未被 discard 的那一个。 |
| [`remote_capabilities.py`](remote_capabilities.py) | 注册要拿证据换。窄范围的结构化 probe spec 会被规范成一条安全的远程命令并真的跑一遍，确认远程 capability 确实存在；验证通过之后，service 元数据才进入 remote-compute 注册表。 |
| [`remote_science.py`](remote_science.py) | 通过 SSH 运行已注册的 folding 与 mutation-scoring wrapper，解析它们显式的结果标记，并为产出该结果的 cell 缓存远程溯源。服务缺失或作业失败时返回错误。它不伪造科学结果。 |
| [`science.py`](science.py) | 七个公共数据库，同一个信封：UniProt、PDB、Ensembl、ChEMBL、PubChem、arXiv 和 OpenAlex。请求按允许名单构造，走共享的 fetch 路径发出，每一份响应都规范成同一种记录结构。 |
| [`session.py`](session.py) | 把控制操作钉死在 dispatcher 当前的 root session 上，任何调用都伸不进另一个会话。checkpoint 和待处理的权限申请始终从 Store 读。branch 与 recovery 状态则来自已挂接的 Web session-domain service，这也是 Web 运行时的常规路径；没有挂接 domain 时，状态投影退回到从 Store 读一份只读的 branch 列表，并把 recovery 报成不可用。涉及文件系统的 checkpoint、fork、revert、recovery 操作，同样委托给这个 domain service。 |
| [`skills.py`](skills.py) | Skill 的完整生命周期：搜索、读取、编辑、发布、版本化、回滚、删除。作用域决定磁盘上哪个目录拥有这个 Skill；内置 Skill 始终优先于用户 Skill，写入也被限制在 Skill 目录内。 |

## 控制、安全与失败边界

- 授权与审计边界是 [`HostDispatcher`](../host_dispatch.py)，而不是单个 service。直接调用 service 属于受信任的进程内组合，会绕过这层外壳。
- `host.bash` 不在这里执行：它的 shell 始终通过 [`sdk/bash.py`](../sdk/bash.py) 在科学 worker 内跑，[`bash.py`](bash.py) 只负责签发和兑换那张一次性 capability；上报的 stdout/stderr 在持久化前会被限制长度并脱敏。但别把这条当成整个包的性质。[`remote_science.py`](remote_science.py) 和 [`remote_capabilities.py`](remote_capabilities.py) 的 runner 默认都是 `subprocess.run`，会直接从受信任的 Host 进程起子进程，用 `ssh -o ConnectTimeout=15 -o BatchMode=yes <host> <command>` 连到已注册的远程 GPU 主机。
- [`credentials.py`](credentials.py) 里的凭据值只存在于内存，但任何拿到兑换值的消费者，就拥有了它对应的权限。基于名称的脱敏不能证明任意输出中不含密钥。
- [`files.py`](files.py) 约束路径，实际的读写行为由 Tool class 负责。Artifact 快照与溯源注册是两个独立的持久化步骤，尽力而为、不作保证，也不构成一次全局的文件系统/SQLite 事务。
- Endpoint 的 start/stop 脚本只是元数据。就绪探测成功并不能证明租户隔离、身份认证或公网暴露是安全的。
- 通用的 `host.compute`、远程 capability 开通、folding 和 mutation scoring 都还是演进中的集成面。存在一条已注册的路由或一个 service class，不代表 provider 凭据、远程软件、GPU 容量或端到端的 UI 恢复流程已经配置妥当。
- 公共数据库、MCP、LLM 和远程 SSH 调用都可能各自失败，或返回带有恶意的内容。dispatcher 的筛查只是多加的一层，不是对科学正确性的验证。

## 相关文档

- [系统架构](../../docs/architecture.md)
- [安全模型](../../docs/security.md)
- [远程计算](../../docs/compute.md)
- [Skills](../../docs/skills.md)
