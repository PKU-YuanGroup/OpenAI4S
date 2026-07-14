# 内核运行时

[English](README.md)

本目录实现持久化科学执行平面。外层 Agent 循环每次最多选择一个完整的 Python 或 R Cell；本包按需启动相应 worker，通过语言无关的 JSON Lines 协议驱动它，并在多个 Cell 之间保留命名空间。Python 还支持内层同步 Host RPC 循环。

## 在架构中的位置

1. [`agent/engine.py`](../agent/engine.py) 路由 Cell action，但不依赖具体内核实现。
2. CLI 与 Web 组合层创建惰性的 [`Kernel`](manager.py)，或受监督的 Python/R slot。
3. manager 发送一个 `execute` frame。Python worker 可以发送 `host_call`；manager 分派调用并返回匹配的 `host_response`，之后继续等待最终 Cell response。兼容性 acknowledgement 不是正常完成路径。
4. worker 返回捕获的输出、错误/中断信息、guard 和资源用量，并另行提供有界 namespace-inspection request。Cell 结果成为外层循环的新 observation；只有 Python 中的 `host.submit_output(...)` 能从 Cell 内部完成任务。

manager 必须始终是其 worker 的唯一 frame reader。worker 的协议写锁负责串行化 frame，Host-call 事务锁则保证同一时间仅有一个同步 RPC。Web 执行协调器与 [`KernelSupervisor`](supervisor.py) 在协议外围协调写入者和生命周期，而不代理协议流量。

## 文件

| 文件 | 职责 |
|---|---|
| [`__init__.py`](__init__.py) | 导出 `Kernel`、`KernelBusyError`、`KernelLease` 和 `KernelSupervisor`。 |
| [`background.py`](background.py) | 在专用 worker 进程中运行 `host.exec_background` Cell，并以线程安全的内存状态/输出支持 peek 与 interrupt；这些任务不共享前台命名空间，也没有持久化 job storage。 |
| [`environment.py`](environment.py) | 依据显式 allowlist 构造子进程环境，避免内核及其子进程继承 daemon 凭据、agent socket 和动态加载注入变量。 |
| [`environments.py`](environments.py) | 发现并缓存可选 Python/R 环境，解析解释器和已安装包元数据，并始终把当前解释器暴露为合成的 `base` 环境。 |
| [`guards.py`](guards.py) | 对 matplotlib figure 泄漏及选定的进程全局 registry 变更进行 best-effort 跨 Cell 探测；缺少可选库时相应探测为 no-op。 |
| [`lazy.py`](lazy.py) | 为无需启动解释器的 tool/finalize-only 路径提供线程安全的一次性惰性 worker 所有权；候选会及早发布以支持取消，bootstrap 失败后会被分离。 |
| [`manager.py`](manager.py) | Host 侧子进程所有者及唯一 JSON Lines frame reader；处理 execute/response、Python 同步 Host RPC、中断、重启 generation、输出 chunk 和 OS sandbox 包装。 |
| [`preinstall.py`](preinstall.py) | 报告并可选安装科学包基线或指定包，之后由调用方控制内核重启；它是包管理支持，而非 stdlib 核心的硬导入依赖。 |
| [`provenance.py`](provenance.py) | 在 Python 内安装 best-effort 对象级 lineage 插桩，标记支持的读取并把输入 version ID 传播到上报的写入。 |
| [`r_kernel.py`](r_kernel.py) | 解析真实 `Rscript`，并构造文件描述符安全的命令，通过公共 manager 运行 R sibling；绝不会静默替换为 Python。 |
| [`r_worker.R`](r_worker.R) | 持久化 R 分析 worker，提供公共 execute/response 契约、输出捕获、中断、trace 与资源计量；不提供 `host` 对象、Cell 中途 RPC 或完成信号。 |
| [`recovery.py`](recovery.py) | 根据内容寻址、规范化的 bootstrap recipe 和保守分类的 replay step 构建并验证替代内核；仅在验证后发布候选，无法安全重建状态时报告 `partial`。 |
| [`supervisor.py`](supervisor.py) | 管理持久化 Python/R session slot、精确 generation lease、生命周期时间戳及 ABA-safe 中断/watchdog 替换，不读取协议 frame。 |
| [`worker.py`](worker.py) | 持久化 Python worker：隔离协议文件描述符与 stdout、捕获 Cell 输出、保证单 Host-call 事务、注入 `host`、记录源码行、处理 SIGINT、安装 guard/audit hook/provenance，并返回有界检查/用量数据。 |

## 子目录

本包没有受跟踪的子目录。

## 安全与失败边界

- 环境过滤、执行前分类器、OS sandbox、worker 内 audit hook、持久化审批和一次性 shell capability 是彼此独立的层；一层存在不代表其他层成功。
- [`manager.py`](manager.py) 使用 [`security/sandbox.py`](../security/sandbox.py)。无法建立隔离时，`enforce` 会 fail closed；真实自测失败后，`auto` 可能以明确的 degraded/unavailable 状态继续运行。
- Python 代码在获准的 workspace 内有意保持强大能力。[`environment.py`](environment.py) 能阻止已识别 secret 的继承，但不能让任意 Cell 代码自动变可信。
- R worker 的入站请求解析依赖 `jsonlite`。缺少该包时会发出结构化错误，并始终只作为分析通道。
- Background execution 使用专用 worker，但其 job registry 与累积 stream output 都驻留进程内存，并非持久化或有大小上限的存储；输出极多的长任务会增加 daemon 内存。
- Provenance 与 guard 都是观察性、best-effort 机制。不支持的对象、库、native 转换或显式关闭可能导致 lineage 不完整。
- Recovery 不序列化存活的 Python/R namespace。它会建立新 generation，仅 replay 保守接受的步骤并验证 manifest，因此可能如实返回 partial recovery。
- Supervisor 的 interrupt/restart 必须使用精确 lease 和 session 执行 barrier。绕过这些所有权规则会与 manager 的单 frame reader 发生竞态。

## 相关文档

- [系统架构](../../docs/architecture.md)
- [安全模型](../../docs/security.md)
- [Jupyter 与内核行为](../../docs/jupyter.md)
- [Web 运行时](../../docs/webapp.md)
