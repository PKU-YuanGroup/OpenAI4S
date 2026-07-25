# 内核运行时

[English](README.md)

常驻的 Python 和 R 内核放在这里，也就是系统的科学执行平面。外层 Agent 循环一次最多交给本包一个完整的 Cell；worker 第一次用到时才启动，之后用语言无关的 JSON Lines 协议驱动，命名空间在 Cell 之间一直保留。内层的同步 Host RPC 只有 Python 有。

## 在架构中的位置

1. [`agent/engine.py`](../agent/engine.py) 负责路由 Cell action，但不依赖任何具体的内核实现。
2. CLI 与 Web 组合层创建惰性的 [`Kernel`](manager.py)，或者受监督的 Python/R slot。
3. manager 发出一个 `execute` frame。Python worker 可能回一个 `host_call`；manager 分派这次调用并写回对应的 `host_response`，然后继续等待这个 Cell 的最终 response。兼容性 acknowledgement 不是正常的完成路径。
4. worker 返回捕获的输出、错误与中断信息、探测报告和资源用量；命名空间检查走的是另一条有界的请求，不是伪造出来的 Cell。Cell 结果会成为外层循环的又一条 observation；只有 Python 里的 `host.submit_output(...)` 能从 Cell 内部完成任务。

对每个 worker，manager 必须是唯一读取 frame 的一方。worker 侧的协议写锁把 frame 串起来发，Host-call 事务锁保证同一时刻只有一个同步 RPC 在飞。Web 执行协调器和 [`KernelSupervisor`](supervisor.py) 只在协议外围协调写入者和生命周期，它们都不代理协议本身。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | 对外导出 `Kernel`、`KernelBusyError`、`KernelLease` 和 `KernelSupervisor`。 |
| [`background.py`](background.py) | `host.exec_background` 就住在这里。一个要跑很久的 Cell——训练、长仿真——会拿到属于它自己的 worker 进程，因此不会卡住前台内核，也不会卡住 Agent 这一轮。`exec_peek` 随时读出它已经积累的 stdout，不用等；`exec_interrupt` 发一次幂等的 SIGINT。这类任务看不见前台命名空间，也没有任何东西落盘。 |
| [`environment.py`](environment.py) | 决定内核能继承到什么。子进程环境是照着一份很短的显式允许名单造出来的，不是从 `os.environ` 抄一份，所以 provider key、云 token、agent socket 和动态加载器注入变量都停在进程边界之外。Cell 之后拉起的任何东西，`host.bash` 也算在内，继承的是同一份过滤后的环境。 |
| [`environments.py`](environments.py) | 环境选择：让任务换到一个本来就装好了所需包的解释器，而不是每次都现装。预置的 conda 环境从 `OPENAI4S_ENV_ROOTS` 或常见安装根目录里发现，探测 `bin/python` 或 `bin/Rscript`，连同包集合一起缓存。daemon 自己的解释器始终作为合成的 `base` 环境对外提供，所以再怎么选，也不会让一个 session 落到没有 Python 内核可用。 |
| [`guards.py`](guards.py) | 探测从一个 Cell 漏到下一个 Cell 的状态：Cell 打开却没关掉的 pyplot figure，以及少数进程级全局注册表——它们会在 Cell 之前被 pin 住，之后再做 diff。这些只是廉价的探测，不是隔离手段。对应的可选库不在时，这一项不做任何事；`OPENAI4S_GUARDS_OFF=1` 则把整套关掉。 |
| [`lazy.py`](lazy.py) | 只调用工具、或只做结构化完成的一轮不需要解释器，这个类就负责别让它白启动。一个所有者、一次启动、线程安全。候选 worker 会尽早发布，好让取消操作还够得着它；bootstrap 失败时，这个候选内核会被原子地摘掉并关停，不会被复用。 |
| [`manager.py`](manager.py) | 一个 worker 的 Host 侧。它拉起子进程，用 OS 沙箱把命令包住，并且是唯一读取该 worker JSON Lines frame 的一方。发出去的是一个 `execute` frame；回来的可能是流式输出 chunk、最终 response，也可能是一个 `host_call`——必须先写回 `host_response`，被阻塞的那个 Cell 才能继续。中断和重启 generation 也由这里驱动。 |
| [`preinstall.py`](preinstall.py) | 内核这边的包管理，刻意隔着一层：它是辅助工具，标准库核心不会对它形成硬导入依赖。它会报告科学基线里哪些已经可导入，在 daemon 启动时把剩下的装上，也按调用方指定的名字随时安装。新装的包只有新进程才看得到，所以装完之后重启内核是调用方的事。 |
| [`provenance.py`](provenance.py) | 在 Python worker 内部安装对象级的血缘插桩：经受支持的读取路径读进来的对象，会带上来源 Artifact 的 `version_id`；这些对象后来被写出去时，它们累积的输入版本会被上报。它能看到多少算多少，不声称看得全。 |
| [`r_kernel.py`](r_kernel.py) | 解析出真实的 `Rscript`，并构造文件描述符安全的命令，让 R sibling 通过公共 manager 跑起来；绝不会被静默换成 Python。 |
| [`r_worker.R`](r_worker.R) | 常驻的 R worker，execute/response 契约与 Python 完全一致：输出捕获、中断、出错的行号与调用、资源计量。入站 frame 用 `jsonlite` 解析，出站 JSON 却是手写转义的——所以即便这套 R 里没装 `jsonlite`，它也能报出一个结构化的干净错误，而不是直接死掉。它是分析通道：没有 `host` 对象，没有 Cell 中途 RPC，也没法从 Cell 内部完成任务。 |
| [`recovery.py`](recovery.py) | 用内容寻址的规范 bootstrap recipe，加上保守分类过的 replay step，构建替代内核，并在别人看到它之前先做验证。只有验证全部通过才发布候选内核；状态无法安全重建时，如实报告 `partial`。 |
| [`env_generations.py`](env_generations.py) | 把环境变更当事务：`plan` 什么也不碰，`apply` 构建一个**新的** generation 并且只有到最后才移动 `current` 指针，失败的 apply 让原环境原封不动，`rollback` 只是把指针指回一个仍在磁盘上的 generation。generation 直接在它的最终 prefix 上构建——Conda 会把绝对路径烤进去，所以「改名的暂存目录」等于一个坏掉的环境——被做成原子的是它的**可见性**。指代 generation 的 id 被限定在它自己的环境内，因为它会被拼进路径，并被之后每一次内核启动读回。 |
| [`supervisor.py`](supervisor.py) | 只管持久的 Python/R session slot，再往下就不碰了。调用方拿到的 lease 写明了它当时操作的是哪一个 generation；只有这个 lease 仍然对得上活着的 slot，中断、重启和 watchdog 替换才会真的执行——迟到的调用方因此杀不掉那个已经顶替上来的新内核。它从不读取协议 frame。 |
| [`worker.py`](worker.py) | 常驻的 Python worker，也是必须把琐碎细节全做对的那个文件。协议用的文件描述符被从 stdout 挪开，于是 Cell 代码里一句乱飞的 print 只会落到 stderr，不会污染协议通道。`host` 注入到命名空间里，并被限制成同时只有一个 Host-call 事务。Cell 源码会登记进 `linecache`，所以 traceback 指向的正是研究者真正写下的那一行。SIGINT 处理、guards、audit hook、溯源，以及有界的变量检查与用量应答，都是在这里装好的。 |

## 安全与失败边界

- 环境过滤、执行前的分类器、OS 沙箱、worker 内的 audit hook、持久化审批和一次性 shell capability 是彼此独立的层。其中一层在位，并不说明其他几层也成功了。
- [`manager.py`](manager.py) 用 [`security/sandbox.py`](../security/sandbox.py) 包住 worker。无法建立隔离时，`enforce` 失败即拒绝；`auto` 在真实自测失败后可能继续运行，但状态会明确标成降级或 unavailable。
- 工作区里的 Python 代码本来就是全能的，这是有意为之。[`environment.py`](environment.py) 能挡住已识别的 secret 被继承，但它没办法让任意 Cell 代码变得可信。
- R worker 靠 `jsonlite` 解析入站请求。缺这个包时它会发出结构化错误；无论如何，它始终只是分析通道。
- 后台执行走独立 worker，但任务表和累积下来的输出都只在进程内存里，既不落盘，也没有大小上限。跑得久、输出又多的任务会一直吃 daemon 的内存。
- 溯源和 guards 都是观察性的：尽力而为，不保证覆盖。不支持的对象、库、native 转换，或者显式关闭，都可能让血缘不完整。
- Recovery 不会序列化一个存活的 Python/R 命名空间。它建立新的 generation，只重放保守接受的步骤并校验 manifest，因此可能如实返回部分恢复。
- supervisor 的 interrupt/restart 必须带上精确的 lease，并走 session 的执行 barrier。绕开这些所有权规则，就会和 manager 那唯一的 frame 读取方发生竞态。

## 相关文档

- [系统架构](../../docs/architecture.md)
- [安全模型](../../docs/security.md)
- [Jupyter 与内核行为](../../docs/jupyter.md)
- [Web 运行时](../../docs/webapp.md)
