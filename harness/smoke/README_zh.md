# Harness Smoke 检查

[English](README.md)

本目录包含跨越真实 runtime 或平台边界、必须显式 opt-in 的小型检查。它们不会被离线 core 导入，也不属于默认 pytest collection。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | 标记 opt-in smoke package，不自动执行任何检查。 |
| [`macos_sandbox.py`](macos_sandbox.py) | Darwin/Seatbelt fail-closed smoke：要求 sandbox 强制生效，证明工作区外写入与网络被阻止、工作区内写入有效，并验证 worker subprocess 不继承秘密。 |
| [`.gitkeep`](.gitkeep) | 保留 smoke 扩展目录。 |

## 直属子目录

无。

macOS 检查只能在 Darwin 的定时/显式环境运行；在不支持或 sandbox 降级时会故意失败。另见根目录 [Harness 规则](../README_zh.md#基本规则)。
