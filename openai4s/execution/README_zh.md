# 科学 Cell 执行策略

[English](./README.md)

**状态：共用执行层已实现。** 本包保存当外层循环动作或显式 notebook 请求到达科学 Python/R Cell 时使用的供应商/UI 中立策略。

## 架构位置

本包位于外层循环/Web 适配器与 [`../kernel/`](../kernel/) 中持久 manager 之间。它本身不解析模型回复、不执行 Host RPC，也不直接运行代码；它负责按 session 串行化 writer、给每个请求分配精确 owner/ticket/lease 身份、投影命名空间依赖、定义标准化请求/结果值并监督超时。

FIFO coordinator 覆盖 Agent、用户 REPL、lifecycle 和 recovery writer。取消以精确 ticket 为目标；适配器仍必须通过匹配的 kernel generation/lease 发送中断。这可以避免过期取消误中断新的 owner。

## 本目录直属文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 重新导出 coordinator 错误/类型、Cell 请求/结果值和捕获元数据。 |
| [`coordinator.py`](./coordinator.py) | 实现可观测的逐 session FIFO 准入、ticket 生命周期、精确取消信号、lease、队列快照和关闭/恢复转换，但不直接执行代码。 |
| [`dependencies.py`](./dependencies.py) | 使用 Python AST 和保守的 R lexer，记录 best-effort 命名空间读/写/删除、visibility、replay policy 和 stale-cell 投影；它不是安全边界。 |
| [`models.py`](./models.py) | 定义供应商/UI 中立的 `CellRequest`、`CaptureResult` 和 `CellExecutionResult` 数据类。 |
| [`watchdog.py`](./watchdog.py) | 对一个冻结 kernel lease 应用协议中立的超时阶梯：等待、精确中断 owner、必要时 kill，然后按策略重启或放弃。 |

## 直属子目录

无。

## 并发与恢复契约

- session 作用域的 writer 不得绕过 `SessionExecutionCoordinator`。
- 在中断/恢复路径中携带精确 ticket 和 kernel generation；仅外观相关的 ID 不够。
- 把依赖元数据视为保守投影。动态 import、反射、原生扩展和任意副作用无法通过静态分析证明。
- 保持 watchdog policy 与 Web session、Artifact、完成和持久化解耦，使适配器可以安全复用。
