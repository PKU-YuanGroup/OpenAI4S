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
| [`manager.py`](manager.py) | 两条传输路径都在这里。它发现 BYOC 的 provider Skill，路由 `byoc:*` 与 `ssh:*`，并在 session 内存中的并发上限达到时拒绝新的提交，暂存输入与 job 模板，并跟踪在跑的 job 和预热的沙箱，负责轮询、取消、关闭与产物回收。credential 是按 provider 自己声明的那几个环境变量名挑出来的，而且走 helper 的 stdin 递进去，不进它的环境变量。helper 的环境其余部分就是 daemon 自己那一份，只摘掉了以 `NGC_`、`NVIDIA_`、`HF_` 开头的名字。 |
| [`states.py`](states.py) | 任务状态词表及其转换表，在写入状态时强制执行。终态不会被迟到的探测重新打开；`unknown` 有意归为**存活**态：远端操作可能落地也可能没有，所以它会被重新装载并参与调和，而不是被遗忘。 |
| [`registry.py`](registry.py) | 记住有哪些 SSH 主机 alias、默认用哪一台、每台上开通了 `fold`/`score_mutations` 这类 capability 元数据，原子写入 `<data_dir>/remote_compute.json`。native 注册会先探测主机，通过之后才写下验证时间；用旧环境变量 seed 出来的主机则可能一直没验证过。它不存 SSH private key，也不存 provider token。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`templates/`](templates/) | 暂存进 BYOC job 的 shell 模板：运行提交的 command，处理超时与 deadline，并把输出和日志打包好供回收。参见其 [README](templates/README_zh.md)。 |

## 当前生命周期

1. `submit` 校验 provider 家族，并检查 manager 在 session 内维护的并发计数。
2. BYOC 提交会新建或复用一个 provider 沙箱，用 wrapper、command 和输入拼出 `in.tar.gz`，然后调用 helper 的 create 与 submit 操作。SSH 提交则建一个远程工作目录，用 `nohup` 起 `run.sh`。
3. `result` 轮询内存里那个确切的 job。BYOC 路径上，helper 的 wait 会暂存出 `out.tar.gz`，由 manager 解包到 `hpc/<job_id>/`；SSH 兼容路径只把日志拷回来，工作目录留在远端。
4. `cancel` 给远程进程发信号，或者终止 BYOC 沙箱；`close` 释放已知的 provider handle，并把点名的 live handle 标成 closed。

## 持久化、审批与成熟度边界

- **job 记录是持久的，预热沙箱 handle 不是。** job 行在提交*之前*就写入，并带上 provider receipt，所以重启后的 manager 会把每个可能仍在占用远端资源的 job 重新装载回来，计入并发计数，并且仍然可以轮询或取消它。`reconcile()` 只上报这些 job，有意不重新提交——一个在途的 job 可能在跑也可能没跑，猜错的代价要么是重复计费，要么是丢结果。仍然只在内存里的是：每个 provider 的预热 byoc 沙箱 handle，所以重启后接不回一个已经预热的容器（但那个容器里正在跑的 job 仍可通过 receipt 恢复）。[`registry.py`](registry.py) 持久化那份专用的 SSH capability 目录。
- **没有后台轮询器。** 真正去探测远端并回收产物的是 `result()`；没人轮询的 job 永远不会被回收。
- native 的 `compute_submit` 需要审批。对已经授权过的那个确切 job，回收结果、取消和关闭有意不再问第二次。更丰富的直接调用 `compute_ssh`/`compute_scp` 比这道有界的 native tool 审批门宽，不能拿后者的批准当它们的批准。
- BYOC 的隔离由 provider 运行时和 provider 自身共同实现，只能实测，不能假定。credential 是按声明的环境变量名挑出来的，通过 helper 的 auth 输入传过去；如果 secret 藏在没人声明的变量名里，基于名称的清理就拦不住它。
- 当前的 SSH job 路径有意做得很基础：记账只在本地内存，远程目录用完不删，声明的 output pattern 没有完整回收，报出来的终止退出码也不是持久的、调度器级别的契约。
- 回收下来的字节、SQLite 元数据、远端 provider 的状态、正在跑的科学内核，这几层不在同一个事务里。暂存或回收做了一半，或者进程崩了，都可能让某一层跑到另一层前面。
- native 注册路径会先探测、再写 `verified_at`；旧的 `OPENAI4S_FOLD_SSH` seed 和调用方自己填的元数据则可能从未验证过。解析出一个 capability 并不能证明那台主机此刻还连得上，所以远程服务不可用时，[`host/remote_science.py`](../host/remote_science.py) 必须自己去查，并如实报错。
- provider 发现只认同时带着 `provider.json` 和 `provider.py` 的已安装 Skill 目录。这里没有实现 SLURM、Kubernetes，也没有任何通用的集群调度器。

## 相关文档

- [远程计算](../../docs/compute.md)
- [安全模型](../../docs/security.md)
- [包边界](../../docs/package-architecture.md)
- [Worker runtime](../../openai4s_compute_provider/README_zh.md)

- [`safe_archive.py`](safe_archive.py) —— 对来自不受控机器的收割结果先枚举后解包：穿越、绝对路径、链接、设备节点与解压炸弹在写出任何字节之前被拒绝。
