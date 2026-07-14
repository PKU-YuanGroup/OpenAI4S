# 可选 Jupyter 适配器

[English](./README.md)

**状态：已实现为独立的可选 bridge。** 它导出并安装 Python/R KernelSpec；由 Jupyter 启动后，把 Jupyter 消息适配到现有 OpenAI4S kernel manager。它有意**不**附着到 Web session，也不提供 Host RPC、Gateway Artifact 捕获、Action Ledger 历史或 Engine 完成语义。

## 架构位置

该 bridge 是围绕 [`../../kernel/`](../../kernel/) 内层循环 Worker 的 Host 侧适配器。科学代码仍通过 `Kernel` 和加固的逐行 JSON Worker 协议执行。Jupyter frontend 不是另一套 OpenAI4S 外层 Agent 循环：这里不存在供应商原生工具批次或 `finalize_response` 动作。

`kernelspec.py` 仍仅依赖标准库。`bridge.py` 只在 Jupyter 进程真正启动它时导入 `ipykernel`/ZeroMQ。

## 本目录直属文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 重新导出 KernelSpec 描述、状态、写入和安装辅助函数，而不导入 Jupyter 依赖。 |
| [`bridge.py`](./bridge.py) | 定义延迟加载的 `ipykernel` bridge，映射执行/中断/关闭消息，启动 OpenAI4S Python 或 R runtime，并提供由 KernelSpec 调用的 CLI。 |
| [`kernelspec.py`](./kernelspec.py) | 构建、写入并原子安装 Python/R KernelSpec 目录；解析用户/prefix 目标并报告可选依赖状态。 |

## 直属子目录

无。

## 贡献者边界

- 在未安装 Jupyter 时，KernelSpec 生成仍必须可导入。
- 通过现有 kernel manager 路由执行；frontend 代码不要直接读写 Worker 文件描述符。
- 除非这些集成已端到端实现，否则不要把独立 bridge 描述为共享 Web notebook 状态、Host capability 或 Artifact 溯源。
