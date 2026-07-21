# 可选 Jupyter 适配器

[English](README.md)

这个适配器是一个可选的 bridge。它负责导出并安装 Python/R 的 KernelSpec；Jupyter 启动其中一个之后，它把 Jupyter 的消息适配到现有的 OpenAI4S 内核 manager 上。这个 bridge 刻意保持独立：它不挂到 Web session 上，也不提供 Host RPC、Gateway 的 Artifact 捕获、Action Ledger 历史或 Engine 的完成语义。

## 在架构中的位置

该 bridge 是 [`../../kernel/`](../../kernel/) 里内层循环 worker 之上的 Host 侧适配器。科学代码仍然经过 `Kernel` 和加固过的逐行 JSON worker 协议。Jupyter 前端不是 OpenAI4S 外层 Agent 循环的另一种实现：这里没有供应商原生工具批次，也没有 `finalize_response` 动作。

`kernelspec.py` 仍然只依赖标准库。`bridge.py` 只在 Jupyter 进程真正启动它的时候才导入 `ipykernel`/ZeroMQ。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 重新导出 KernelSpec 的描述、状态、写入和安装辅助函数。导入这个包不会连带导入 Jupyter、IPython 或 ZeroMQ。 |
| [`bridge.py`](./bridge.py) | `ipykernel` bridge 本身，以及 KernelSpec 启动的那个 CLI。它拉起一个 OpenAI4S 的 Python 或 R 运行时，把 Jupyter 的执行、中断、关闭消息映射到它上面。中断发给确切的那个子 worker，而不是发给 bridge 自己所在的进程组。 |
| [`kernelspec.py`](./kernelspec.py) | 生成 Python 和 R 两份 `kernel.json`，解析用户目录或显式 prefix 目标，再把每个 spec 目录原子地写进去。目标已存在时直接报错，除非调用方显式要求 `replace`；即便 replace 也只重写 `kernel.json`，不会递归删除用户文件。它还负责报告可选依赖是否已安装。 |

## 贡献者边界

- 没装 Jupyter 时，KernelSpec 的生成代码也必须能正常导入。
- 执行一律走现有的内核 manager；前端代码不要直接去读写 worker 的文件描述符。
- 在这些集成真正端到端做完之前，不要把这个独立 bridge 说成共享 Web notebook 状态、Host capability 或 Artifact 溯源。
