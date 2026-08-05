# 远程计算 Host 后端

[English](README.md)

持续演进中的 `host.compute` job 通道，其 Host 侧实现放在这里；专用 remote-science service 用来找到真实 GPU 主机的那份独立注册表也在这里。重活离开本机只有两条路：走发现到的 `byoc:<id>` provider，或者走用户已经配好的 `ssh:<alias>` 连接。本包只做编排与传输，里面没有调度器，没有 GPU 运行时，也没有任何科学模型的实现。

## 在架构中的位置

Python 的 [`host.compute` SDK](../sdk/compute.py) 把每次调用变成一个 `compute_<operation>` Host RPC。[`HostDispatcher`](../host_dispatch.py) 在第一次用到时为 session 建一个 [`ComputeManager`](manager.py)，并把 `ComputeError` 映射成结构化的软失败。native 的 `compute_submit`、`compute_result`、`compute_cancel`、`compute_close` 只暴露这个控制平面里有界的一小块；SDK 那些更丰富的兼容调用最终也落到同一个 manager。

走 `byoc:*` 时，manager 到 `skills/remote-compute-<id>/` 下面找 provider shim，把 job 归档暂存好，再运行受限的 [`openai4s_compute_provider`](../../openai4s_compute_provider) helper，credential 从 stdin 递进去。走 `ssh:*` 时，它就是对着用户配置的 alias 调本地的 `ssh`/`scp`。回收下来的文件一律落在已配置数据目录下的 `hpc/<job_id>/`。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | 对外只导出 Host 后端要用的两个名字：`ComputeManager` 和结构化的 `ComputeError`。 |
| [`manager.py`](manager.py) | 两条传输路径都在这里。它发现 BYOC 的 provider Skill，路由 `byoc:*` 与 `ssh:*`，并在 session 并发上限达到时拒绝新的提交，暂存输入与 job 模板，并跟踪在跑的 job 和预热的沙箱，负责轮询、取消、关闭与产物回收。由 *agent* 点名的 alias 必须是用户自己交出来过的——注册表里的，或者他自己 `~/.ssh/config` 里的；而且每一条 `ssh`/`scp` argv 都在同一处拼装，所以这道形状检查是结构性的，而不是靠人在每个调用点记着。来自不受控机器的输出会被读干净，免得远端写阻塞，但只保留到一个上限为止：否则一个话多的登录 profile 或者一条 forced command，就能用整个单命令超时窗口逼 daemon 一直分配内存。credential 是按 provider 自己声明的那几个环境变量名挑出来的，而且走 helper 的 stdin 递进去，不进它的环境变量。helper 的环境其余部分就是 daemon 自己那一份，只摘掉了以 `NGC_`、`NVIDIA_`、`HF_` 开头的名字。 |
| [`states.py`](states.py) | 任务状态词表及其转换表，在写入状态时强制执行。`unknown` 有意归为**存活**态：远端操作可能落地也可能没有，所以它会被重新装载并参与调和，而不是被遗忘。终态不会被迟到的探测重新打开——写入同一个状态也不行，因为这次写入带着确立它的那份证据；否则两个都读到 `running` 的轮询者会各自把自己的 manifest 盖到对方头上。 |
| [`manifest.py`](manifest.py) | 一次回收究竟拿到了什么：逐个文件的 `{path, size, sha256}` 加上整份记录的一个摘要，以及把这份记录与任务在 `outputs` 里声明的 glob 对账。声明了却匹配不到任何文件，正是一个退出码为 0 的任务仍然会是 `failed` 的原因——而大小与哈希这一对，是唯一能看出「rc 为 0 但传输被截断」的东西。声明里还可以写 `visibility: hidden` 或 `residency: remote`：两者都不会被算成缺失，而且 stay-remote 的 pattern 是直接不参与传输，不只是在对账时被豁免。 |
| [`registry.py`](registry.py) | 记住有哪些 SSH 主机 alias、默认用哪一台、每台上开通了 `fold`/`score_mutations` 这类 capability 元数据，原子写入 `<data_dir>/remote_compute.json`。native 注册会先探测主机，通过之后才写下验证时间；用旧环境变量 seed 出来的主机则可能一直没验证过。`is_known_alias` 同时是 ssh 传输的授权侧：要么在这里注册过，要么在用户自己的 `~/.ssh/config` 里。它不存 SSH private key，也不存 provider token。 |
| [`safe_archive.py`](safe_archive.py) | 对来自不受控机器的收割结果先枚举后解包——两条传输路径都用它，因为这些字节无论从哪条路回来都得当作攻击者构造的输入。穿越、绝对路径、链接、设备节点与解压炸弹在写出任何字节之前被拒绝，而且拒绝是全有全无的：把一个恶意归档解一半出来，照样是一次沦陷。在本项目支持的 Python 版本上，`tarfile.extractall` 对上述任何一种都不设防。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`templates/`](templates/) | 暂存进 BYOC job 的 shell 模板：运行提交的 command，处理超时与 deadline，并把输出和日志打包好供回收。参见其 [README](templates/README_zh.md)。 |

## 当前生命周期

1. `submit` 校验 provider 家族、检查并发计数，走 `ssh:*` 时还会拒绝用户从未交出来过的目标主机。job 行在真正发起远程调用*之前*就已占位，所以这次提交无论以哪种方式结束，都留下了可供对账的东西。
2. BYOC 提交会新建或复用一个 provider 沙箱，用 wrapper、command 和输入拼出 `in.tar.gz`，然后调用 helper 的 create 与 submit 操作。提交里带着容器的绝对到期时刻，连同 harvest margin 与 term grace 一起递过去，wrapper 的看门狗这才真正上膛：以前 Host 侧根本不产出这几个值，容器可能在 job 跑到一半时被回收，结果一并带走。SSH 提交则建一个远程工作目录，用 `nohup` 起 `run.sh`。
3. `result` 轮询本 session 拥有的那个确切 job。BYOC 路径上，helper 的 wait 会暂存出 `out.tar.gz`；SSH 路径则在远端自己把工作目录打成归档再拉回来。两者都要过同一个抗恶意输入的解包器，最终落到 `hpc/<job_id>/`。SSH 的工作目录本身仍留在远端，被删掉的只有那份暂存的 tarball。
4. `cancel` 给远程 process group 发信号并确认它确实没了，或者终止 BYOC 沙箱。`close` 逐个终止每个 job，只有确认之后才标成 `cancelled`；没能确认的仍然算存活、继续占着并发槽，并被点名回报给调用方。又因为 BYOC 的 job 每个 provider 共用一个沙箱，一次确认成功的沙箱终止会结束骑在它上面的每一个存活 job——包括这个 handle 从未点过名的那些。

## 持久化、审批与成熟度边界

- **job 记录是持久的，预热沙箱 handle 不是。** job 行在提交*之前*就写入，并带上 provider receipt，所以重启后的 manager 会把每个可能仍在占用远端资源的 job 重新装载回来，计入并发计数，并且仍然可以轮询或取消它。`reconcile()` 只上报这些 job，有意不重新提交——一个在途的 job 可能在跑也可能没跑，猜错的代价要么是重复计费，要么是丢结果。仍然只在内存里的是：每个 provider 的预热 byoc 沙箱 handle，所以重启后接不回一个已经预热的容器（但那个容器里正在跑的 job 仍可通过 receipt 恢复）。[`registry.py`](registry.py) 持久化那份专用的 SSH capability 目录。
- **一个 job 一个终态，而且在同一处判定。** `_commit_terminal` 先把终态写进账本，之后内存和任何观察者才被允许相信它，并且不吞异常：compare-and-swap 输掉时会回读那一行、报出真正胜出的状态，其余任何写失败则直接抛出，而不是当成成功回报。那种情况下这一行仍算存活，`reconcile` 会一直把它捞出来——这是偏安全的方向；宣称一个账本从未接受过的成功则不是。
- **重复提交是被拒绝，而不是被悄悄跑两遍。** 如果某个 `idempotency_key` 已经落在本 owner 的某一行上，第二次提交就是一个点名了胜出者的错误；而那些抢在前置检查之后才碰头的并发对，则由 UNIQUE 索引兜住。提交成功的回执——沙箱 id，或者远程 pid/pgid——在内存相信这个 job 干净地跑起来之前就已持久化；写失败会退化成 `unknown`/`submit_indeterminate`，而不是在一行没有回执的记录上报出一个干净的 `running`——那是一个还在计费、却再没有东西叫得出它名字的孤儿。
- **没有后台轮询器。** 真正去探测远端并回收产物的是 `result()`；没人轮询的 job 永远不会被回收。
- **session 之间互相看不见对方的 job。** 重新装载、`result`、`cancel` 以及逐 job 的事件流，全都通过同一张按 owner 划定的表来解析，所以属于别的 session 的 job id 与一个根本不存在的 id 无从区分。
- native 的 `compute_submit` 需要审批。对已经授权过的那个确切 job，回收结果、取消和关闭有意不再问第二次。更丰富的直接调用 `compute_ssh`/`compute_scp` 如今同样要求 alias 是用户交出来过的，但它们压根不在 native 审批门里——不能拿 `compute_submit` 的批准当它们的批准。
- **BYOC 的隔离现在由 Host 建立并经过验证，不再靠假定。** manager 用 [`security/byoc_confinement.py`](../security/byoc_confinement.py) 的 OS 边界把 helper 包起来，并把 helper 自己那道探测所要比对的锚值递过去；helper 在读取 credential 之前从内部自查，边界不成立就以 71 退出、什么都不做。`enforce` 在*每一个* op 上都 fail closed，而不只是在 submit；`auto` 则显式地降级成不受限的那种形态。这道边界不包含什么：网络仍然是通的，这是有意的——调 provider 的 REST API 就是 helper 的全部工作。credential 是按声明的环境变量名挑出来的，通过 helper 的 auth 输入传过去；如果 secret 藏在没人声明的变量名里，基于名称的清理就拦不住它。
- SSH 这条 job 路径已经是持久的，也确实按声明去回收产物，但它仍然不是一个调度器。远程工作目录用完不删；判断 deadline 是否触发，靠的是 wrapper 自己写的标记文件加一次挂钟时间校验，而不是从退出码 124 反推——任何命令都可以自己以 124 退出；而这些标记文件所在的目录，job 自己就写得了，所以这个退出状态是防御性的协调信号，不是有证明力的契约。
- 回收下来的字节、SQLite 元数据、远端 provider 的状态、正在跑的科学内核，这几层不在同一个事务里。暂存或回收做了一半，或者进程崩了，都可能让某一层跑到另一层前面。
- native 注册路径会先探测、再写 `verified_at`；旧的 `OPENAI4S_FOLD_SSH` seed 和调用方自己填的元数据则可能从未验证过。解析出一个 capability 并不能证明那台主机此刻还连得上，所以远程服务不可用时，[`host/remote_science.py`](../host/remote_science.py) 必须自己去查，并如实报错。
- provider 发现只认同时带着 `provider.json` 和 `provider.py` 的已安装 Skill 目录。这里没有实现 SLURM、Kubernetes，也没有任何通用的集群调度器。

## 相关文档

- [远程计算](../../docs/compute.md)
- [安全模型](../../docs/security.md)
- [包边界](../../docs/package-architecture.md)
- [Worker runtime](../../openai4s_compute_provider/README_zh.md)
