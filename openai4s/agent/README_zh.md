# Agent 外层循环

[English](./README.md)

**状态：已实现。** 本包负责供应商中立的外层循环状态机，以及为本地/CLI 执行组合该状态机的适配器。Web session runner 通过自己的 server 组合复用相同的动作路由和 Engine 契约。

## 架构位置

每个模型回复最多被路由为一种动作：

1. 一个有序的供应商原生 JSON 控制工具调用批次；
2. 一个单独且有效的 Engine 自有 `finalize_response` 动作；或
3. 第一个完整的 fenced Python/R Cell。

原生调用优先于代码。混合或格式错误的 finalizer 不构成完成。`host.submit_output(...)` 是唯一能从 Python Cell 内部发出的完成信号；先前执行过 Cell 后，后续单独且有效的 `finalize_response` 仍可关闭 Engine。普通文本、一般工具 observation、R Cell、取消和最大回合耗尽仍是未完成 outcome。

外层循环只在执行代码时调用它的前台内层 kernel manager。因此，tool-only 或 finalizer-only 路由不会启动该 Worker；个别控制工具仍可作为自身 capability 的一部分管理独立专用 Worker。

## 本目录直属文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 重新导出 Engine、本地 `Agent` facade、结果值、完成辅助函数和 `run_task`。 |
| [`actions.py`](./actions.py) | 单一动作解析/路由点：标准化原生调用、识别 Python/R fence、强制原生调用优先，并识别单独的 Engine finalizer。 |
| [`compaction.py`](./compaction.py) | 估算上下文预算、保持动作/结果对不可拆分、按摘要外置超大输出、生成结构化 handoff，并归档被压缩的片段。 |
| [`control.py`](./control.py) | 校验并执行原生工具批次，包括取消、资源冲突检查和安全的只读并行 wave，同时保持结果顺序。 |
| [`delegation.py`](./delegation.py) | 实现有界子 Agent 树、fan-out/session/depth 预算、精确后代取消、结果收集和回合边界 steering。 |
| [`engine.py`](./engine.py) | 建立在 model、context、action executor、completion、cancellation、interceptor 和 event port 上的纯供应商中立外层状态机。 |
| [`events.py`](./events.py) | 定义 `AgentEngine` 发出的类型化生命周期事件。 |
| [`finalize.py`](./finalize.py) | 定义并校验 Engine 自有 `finalize_response` schema，将有效的单独调用转换为结构化 completion record；它不注册为控制 `Tool`。 |
| [`ledger.py`](./ledger.py) | 将类型化 Engine 事件持久化到追加式 Action Ledger，遮蔽声明的 secret，并把不完整 group 归约为供应商安全的重启历史。 |
| [`loop.py`](./loop.py) | 向后兼容的本地 `Agent` facade，把 Engine 与模型、dispatcher、延迟持久 kernel、ledger、delegation 和进程生命周期组合起来。 |
| [`models.py`](./models.py) | 保存模型回复、运行状态、执行 outcome 和最终 Engine result 的供应商中立可变/不可变值。 |
| [`ports.py`](./ports.py) | 定义 protocol 和 no-op 默认实现，使纯 Engine 与具体模型、存储、kernel 和 UI 代码隔离。 |
| [`runtime.py`](./runtime.py) | 面向阻塞 LLM 客户端、压缩、原生工具、Python/R kernel、transcript 投影和完成捕获的本地适配器。 |

## 直属子目录

无。

## 扩展与验证契约

- 新增动作类型必须同时经过 `actions.py`、类型化 model/event 以及本地和 Web 两套组合；顺序必须保持确定。
- 保持 `engine.py` 不依赖具体 provider、kernel、Store 和 Gateway。
- 在 ledger 中保持供应商工具调用/结果的原子配对，包括崩溃后的合成闭合。
- 修改路由、完成、压缩或委派后运行 Agent 测试；改变执行协议时还必须运行 kernel 测试。
