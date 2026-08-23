# Harness Smoke 检查

[English](README.md)

这里放的是几个真正跨越运行时或平台边界的小检查，所以必须显式启用才会跑。离线核心不会导入本包，默认的 pytest collection 也不会收集它。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | 标记这是一个需显式启用的 smoke 包，导入它不会执行任何检查。 |
| [`linux_bwrap_interrupt.py`](linux_bwrap_interrupt.py) | 真实 hosted Linux 的进程身份检查。它在 bubblewrap 下启动带团队读取隔离的 Python 与 R kernel，要求 `--unshare-pid` 和 `--info-fd`，验证命令进程是 PID 2 且已固定 pidfd，中断一个正在运行的长 Cell，再证明同一个持久命名空间仍能执行下一 Cell。CI 特意设置 `OPENAI4S_KERNEL_ALLOW_RAW_NETWORK=1`，让进程身份的证据不依赖网络配置；因此它只证明 private-PID/interrupt 契约，不证明网络隔离。Ubuntu 24.04 还会拒绝没有 profile 的 bwrap 创建 user namespace；CI 加载发行版自带、会剥离子进程 capability 的 `bwrap-userns-restrict`，而不是关闭整台 runner 的 AppArmor 限制。 |
| [`macos_sandbox.py`](macos_sandbox.py) | Darwin/Seatbelt 检查，失败即拒绝：沙箱必须确实强制生效并通过自测，否则程序直接报错。随后它从 worker 内部证明工作区外的写入和对外网络都被挡住、工作区内的写入仍然可用，并且 worker 派生的子进程看不到 daemon 的 secret。 |
| [`linux_sandbox.py`](linux_sandbox.py) | 在 bubblewrap 下检查同样的四条边界。它断言 backend 确实是 bubblewrap——一次回退后仍然通过的运行，报告的是它根本没测过的边界。**只做手动运行：** 先前的 hosted-runner 任务在 private-network 配置阶段失败；新中断任务加载的发行版 AppArmor profile 可能会改变该结果，但完整的文件系统与 egress smoke 尚未在这个配置下重新评估。Linux 层级现在的说法见 [`docs/platforms.md`](../../docs/platforms.md)。 |
| [`sandbox_boundary.py`](sandbox_boundary.py) | 两个 OS smoke 共享的检查：不能写出工作区、不能开 socket、工作区内可写、daemon 的凭据不得进入它派生的子进程。共享而非拷贝，因为两份拷贝会漂移，直到某个平台悄悄不再检查另一个仍在检查的东西。 |
| [`.gitkeep`](.gitkeep) | 保留 smoke 扩展目录。 |

macOS 检查只在 Darwin 上跑，而且只在为它准备的定时或手动触发环境里跑。完整 Linux 边界检查请在允许非特权 user namespace 的主机上手动运行：`OPENAI4S_KERNEL_SANDBOX=enforce uv run python -m harness.smoke.linux_sandbox`。更窄的中断证明在每次 CI 中执行：`OPENAI4S_KERNEL_SANDBOX=enforce OPENAI4S_KERNEL_ALLOW_RAW_NETWORK=1 uv run python -m harness.smoke.linux_bwrap_interrupt`；正因为有 raw-network 例外，它通过时绝不能被表述成完整边界已通过。三者在平台不对或必需沙箱缺失时都会直接报错，而不是给一句警告。另见 Harness 根目录的 [基本规则](../README_zh.md#基本规则)。
