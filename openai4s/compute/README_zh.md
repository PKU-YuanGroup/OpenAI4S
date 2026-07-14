# 远程计算 Host 后端

[English](README.md)

本目录是持续演进的 `host.compute` job surface 的 Host 侧后端，也是专用 remote-science service 使用的独立 registry。重型工作通过发现的 `byoc:<id>` provider 或已有 `ssh:<alias>` 连接运行；本包是 orchestration/transport 代码，不是 scheduler、GPU runtime 或科学模型实现。

## 在架构中的位置

Python [`host.compute` SDK](../sdk/compute.py) 发出 `compute_<operation>` Host RPC。[`HostDispatcher`](../host_dispatch.py) 为 session 惰性创建一个 [`ComputeManager`](manager.py)，并把 `ComputeError` 映射为结构化软失败。Native `compute_submit`、`compute_result`、`compute_cancel` 和 `compute_close` tool 暴露有界控制平面子集；更丰富的 SDK 兼容调用仍进入同一 manager。

对于 `byoc:*`，manager 在 `skills/remote-compute-<id>/` 下发现 provider shim、stage job archive，并调用受限的 [`openai4s_compute_provider`](../../openai4s_compute_provider) helper，通过 stdin 传入 credential。对于 `ssh:*`，它针对用户配置的 alias 调用本地 `ssh`/`scp`。Harvest 后的文件放在已配置数据目录的 `hpc/<job_id>/` 树中。

## 文件

| 文件 | 职责 |
|---|---|
| [`__init__.py`](__init__.py) | 导出 Host 后端的 `ComputeManager` 和结构化 `ComputeError`。 |
| [`manager.py`](manager.py) | 发现 BYOC provider Skill，路由 `byoc:*` 与 `ssh:*`，执行内存中的 session concurrency limit，stage input/template，只向 helper 提供 provider 声明的 credential，跟踪 live job/sandbox，并负责 poll/cancel/close 与 output harvest。 |
| [`registry.py`](registry.py) | 将 SSH host alias、默认选择及 `fold`/`score_mutations` 类 capability 元数据原子保存到 `<data_dir>/remote_compute.json`；native 注册在标记验证前会 probe，legacy 环境变量 seed 可能仍未验证。它不存储 SSH private key 或 provider token。 |

## 子目录

| 目录 | 职责 |
|---|---|
| [`templates/`](templates/) | Stage 到 BYOC job 中的 shell template，用于运行提交的 command、处理 timeout/deadline，并打包 output/log 供 harvest。参见其 [README](templates/README_zh.md)。 |

## 当前生命周期

1. `submit` 校验 provider family 和 manager 的 session-local concurrency count。
2. BYOC submission 创建/复用 provider sandbox，根据 wrapper、command 和 input 构建 `in.tar.gz`，然后调用 helper 的 create/submit 操作。SSH submission 创建远程工作目录，并用 `nohup` 启动 `run.sh`。
3. `result` poll 精确的内存 job。BYOC wait 会 stage `out.tar.gz`，manager 将其解包到 `hpc/<job_id>/`；SSH 兼容路径复制日志并在远端保留工作目录。
4. `cancel` 向远程进程发信号或终止 BYOC sandbox；`close` 释放已知 provider handle，并把指定 live handle 标为 closed。

## 持久化、审批与成熟度边界

- **Prototype 状态：** `ComputeManager` 的 job record、concurrency limit 和 warm-sandbox handle 都在内存中。Daemon/manager 重启后，本实现无法 attach 这些 record，即使远端工作或 harvest 文件仍存在。[`registry.py`](registry.py) 只持久化专用 SSH capability catalogue。
- Native `compute_submit` 需要审批。对于已经授权的精确 job，result harvest、cancel 和 close 有意不请求第二次审批。更丰富的直接 `compute_ssh`/`compute_scp` 兼容方法并不等价于该有界 native-tool gate，不能被视为新批准的权限。
- BYOC confinement 由 provider runtime/provider 组合执行，必须实测而不能假定。Credential 依据声明的环境变量名选择，并通过 helper auth input 传递；未知 secret 名称可能逃过基于名称的清理。
- 当前 SSH job 路径有意保持基础实现：job bookkeeping 位于本地内存，远程目录会保留，声明的 output pattern 尚未完整 harvest，terminal exit status 也不是持久化 scheduler-grade 契约。
- Harvest 字节、SQLite 元数据、远程 provider state 与运行中的科学内核不共享同一事务。部分 stage/harvest 或进程崩溃可能让某一层领先于其他层。
- Native 注册路径会在写入 `verified_at` 前 probe；legacy `OPENAI4S_FOLD_SSH` seed 和调用方提供的元数据可能未验证。解析不能证明当前 reachability，因此远程服务不可用时，[`host/remote_science.py`](../host/remote_science.py) 必须检查并如实失败。
- Provider 发现仅限同时包含 `provider.json` 与 `provider.py` 的已安装 Skill 目录。本包未实现 SLURM、Kubernetes 或通用 cluster scheduler。

## 相关文档

- [远程计算](../../docs/compute.md)
- [安全模型](../../docs/security.md)
- [包边界](../../docs/package-architecture.md)
- [Worker runtime](../../openai4s_compute_provider/README_zh.md)
