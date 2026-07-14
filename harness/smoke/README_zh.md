# Harness Smoke 检查

[English](README.md)

这里放的是几个真正跨越运行时或平台边界的小检查，所以必须显式启用才会跑。离线核心不会导入本包，默认的 pytest collection 也不会收集它。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | 标记这是一个需显式启用的 smoke 包，导入它不会执行任何检查。 |
| [`macos_sandbox.py`](macos_sandbox.py) | Darwin/Seatbelt 检查，失败即拒绝：沙箱必须确实强制生效并通过自测，否则程序直接报错。随后它从 worker 内部证明工作区外的写入和对外网络都被挡住、工作区内的写入仍然可用，并且 worker 派生的子进程看不到 daemon 的 secret。 |
| [`.gitkeep`](.gitkeep) | 保留 smoke 扩展目录。 |

macOS 检查只在 Darwin 上跑，而且只在为它准备的定时或手动触发环境里跑。平台不对，或者沙箱回来的状态是降级的，它会直接报错，而不是给一句警告。另见 Harness 根目录的 [基本规则](../README_zh.md#基本规则)。
