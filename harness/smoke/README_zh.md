# Harness Smoke 检查

[English](README.md)

这里放的是几个真正跨越运行时或平台边界的小检查，所以必须显式启用才会跑。离线核心不会导入本包，默认的 pytest collection 也不会收集它。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | 标记这是一个需显式启用的 smoke 包，导入它不会执行任何检查。 |
| [`macos_sandbox.py`](macos_sandbox.py) | Darwin/Seatbelt 检查，失败即拒绝：沙箱必须确实强制生效并通过自测，否则程序直接报错。随后它从 worker 内部证明工作区外的写入和对外网络都被挡住、工作区内的写入仍然可用，并且 worker 派生的子进程看不到 daemon 的 secret。 |
| [`linux_sandbox.py`](linux_sandbox.py) | 在 bubblewrap 下检查同样的四条边界。它断言 backend 确实是 bubblewrap——一次回退后仍然通过的运行，报告的是它根本没测过的边界。**只做手动运行：** GitHub 托管的 runner 限制非特权 user namespace，bwrap 无法在自己的 network namespace 里拉起 loopback，于是这个任务每晚都失败，却从未真正测到代码。Linux 层级现在的说法见 [`docs/platforms.md`](../../docs/platforms.md)。 |
| [`sandbox_boundary.py`](sandbox_boundary.py) | 两个 OS smoke 共享的检查：不能写出工作区、不能开 socket、工作区内可写、daemon 的凭据不得进入它派生的子进程。共享而非拷贝，因为两份拷贝会漂移，直到某个平台悄悄不再检查另一个仍在检查的东西。 |
| [`.gitkeep`](.gitkeep) | 保留 smoke 扩展目录。 |

macOS 检查只在 Darwin 上跑，而且只在为它准备的定时或手动触发环境里跑。Linux 检查请在允许非特权 user namespace 的主机上手动运行：`OPENAI4S_KERNEL_SANDBOX=enforce uv run python -m harness.smoke.linux_sandbox`。平台不对，或者沙箱回来的状态是降级的，两者都会直接报错，而不是给一句警告。另见 Harness 根目录的 [基本规则](../README_zh.md#基本规则)。
