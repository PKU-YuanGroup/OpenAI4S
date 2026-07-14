# 远程 Job Shell 模板

[English](README.md)

这些 template 由 [`ComputeManager`](../manager.py) 复制到每个 job 的 staging tree。它们在 BYOC provider sandbox 内运行；不是 daemon startup script，也不用于直接 SSH 兼容路径。

## 文件

| 文件 | 职责 |
|---|---|
| [`run.sh.tmpl`](run.sh.tmpl) | 最小 job 入口：启用严格 Bash 模式、切换到 staged work directory，并在 `{{COMMAND}}` 处替换提交的 command。 |
| [`wrapper.sh.tmpl`](wrapper.sh.tmpl) | 在独立 process group 中监督 `run.sh`，分离 stdout/stderr，执行 job 与 sandbox-deadline 终止、回收后代进程、写入 phase/deadline marker，并无条件尝试把 `out/` 和日志归档为 `out.tar.gz`。 |

## 子目录

这里没有受跟踪的子目录。

## 运行时契约

- 用户代码必须把期望结果放到 `./out/`；目录为空只会产生 warning。
- Deadline-control 环境值会在 source `.job_env` 前读取并设为只读，防止 job 提供的变量放宽这些限制。
- Workload 在独立 session/process group 中运行。TERM、grace、再 KILL 的处理会在 stage 结果前尽力停止后代进程。
- `.phase` 记录 `done:<rc>:<wall>` 或 `harvest_failed:<rc>:<wall>`。Deadline/job-timeout sentinel 的写入顺序是 Host/provider 分类契约的一部分。
- 即使 timeout 或 workload 非零退出，也会尝试 stage output，以便 harvest 日志和部分结果。

## 安全与可移植性边界

- `{{COMMAND}}` 有意作为可执行 job 内容，而不是经过 shell escaping 的数据。安全性依赖 provider sandbox 及 submission 时授予的权限。
- Wrapper 假设 Linux/GNU 风格环境，具备 Bash、`setsid`、`timeout`、`tar`、process group 和 `/proc/<pid>/stat`；它不是可移植的本地 shell wrapper。
- `.job_env` 在已授权 sandbox 内被 source。控制变量受到保护，但任意 job environment value 仍对 workload 可用。
- Marker 文件是防御性协调信号，不是 cryptographic attestation。Wrapper 会缩小伪造窗口；Host 仍必须把远程输出视为不可信。
- Template 只生成单个 job archive。Host-side 解包、路径校验、持久化注册、Artifact versioning 和科学验证都是独立职责，并可独立失败。

## 相关文档

- [Compute 后端](../README_zh.md)
- [远程计算](../../../docs/compute.md)
- [Worker runtime](../../../openai4s_compute_provider/README_zh.md)
