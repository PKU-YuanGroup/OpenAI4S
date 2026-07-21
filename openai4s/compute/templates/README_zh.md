# 远程 job 的 shell 模板

[English](README.md)

[`ComputeManager`](../manager.py) 会把这两个模板复制进它为每个 job 搭好的 staging 目录，它们在 BYOC provider 的沙箱里运行。它们不是 daemon 的启动脚本，直接 SSH 的兼容路径也完全用不到它们。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`run.sh.tmpl`](run.sh.tmpl) | job 的入口，有意写得极小：开启严格 Bash 模式，切到 staged 的工作目录，然后在 `{{COMMAND}}` 处替换掉提交上来的命令。 |
| [`wrapper.sh.tmpl`](wrapper.sh.tmpl) | 在独立的 process group 里监督 `run.sh`：分离 stdout 与 stderr，执行 job 超时和沙箱 deadline 两道终止，回收残留的后代进程，写入 phase 与 deadline 标记文件，并且无论如何都会尝试把 `out/` 连同日志打包成 `out.tar.gz`。 |

## 运行时契约

- 结果必须写到 `./out/` 下面。`out/` 为空只会产生一条 warning。
- deadline 控制相关的环境值在 source `.job_env` 之前就已读入并设为只读，job 没法用自己的变量把这些限制放宽。
- workload 跑在独立的 session 和 process group 里。先 TERM，等一段 grace，再 KILL；在 stage 结果之前，wrapper 会尽力把后代进程一并停掉。
- `.phase` 记录 `done:<rc>:<wall>` 或 `harvest_failed:<rc>:<wall>`。deadline 与 job 超时这两个 sentinel 的写入顺序，是 Host 和 provider 用来判断 job 如何结束的契约的一部分。
- 即使发生超时，或者 workload 以非零码退出，也照样会尝试 stage 输出，好让日志和部分结果仍然能被取回。

## 安全与可移植性边界

- `{{COMMAND}}` 有意作为可执行的 job 内容存在，而不是经过 shell 转义的数据。它的安全性取决于 provider 沙箱，以及提交时授予的权限。
- wrapper 假定运行在 Linux/GNU 风格的环境里：Bash、`setsid`、`timeout`、`tar`、process group 和 `/proc/<pid>/stat` 都得具备。它不是一个可移植的本地 shell wrapper。
- `.job_env` 是在已经获得授权的沙箱内部被 source 的。控制变量受到保护，但其余任意的 job 环境值对 workload 依然可用。
- 标记文件是防御性的协调信号，不是密码学意义上的证明。wrapper 只能把伪造窗口压得更窄；Host 仍然必须把远程输出当作不可信数据。
- 模板产出的只是单个 job 的归档包。Host 侧的解包、路径校验、持久化注册、Artifact 版本管理和科学性验证都是各自独立的职责，也会各自独立地失败。

## 相关文档

- [Compute 后端](../README_zh.md)
- [远程计算](../../../docs/compute.md)
- [Worker runtime](../../../openai4s_compute_provider/README_zh.md)
