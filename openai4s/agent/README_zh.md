# Agent 外层循环

[English](README.md)

外层循环的状态机放在这里，还有把它组合成本地/CLI 执行路径的那些适配器。状态机本身不认识任何具体的 provider。Web session runner 在 server 包里另有一套组合，但动作路由和 Engine 契约用的都是这里定义的同一套。

## 在架构中的位置

每个模型回复最多被路由为一种动作：

1. 一个有序的 provider 原生 JSON 控制工具调用批次；
2. 一个单独且有效的 Engine 自有 `finalize_response` 动作；或
3. 第一个完整的 fenced Python/R Cell。

原生调用优先于代码。混在其他调用里、或者格式不合法的 finalize_response 都不构成完成。`host.submit_output(...)` 是唯一能从 Python Cell 内部发出的完成信号；先前执行过 Cell 之后，后续单独且有效的 `finalize_response` 仍然可以关闭 Engine。普通文本、一般工具 observation、R Cell、取消和最大回合耗尽都不算完成。

只有当动作是代码时，外层循环才会去碰前台的内层内核 manager。所以 tool-only 或 finalize-only 的回合根本不会启动 worker。单个控制工具仍然可以作为自身 capability 的一部分，管理一个专属的 worker。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 包的对外出口：Engine、本地 `Agent` facade 与 `run_task`、各类结果值，以及完成相关的辅助函数。 |
| [`actions.py`](./actions.py) | 模型回复变成动作的唯一入口。它标准化原生调用，识别 Python/R fence；两者同时出现时，原生调用胜出。只有当 `finalize_response` 是回复里唯一的原生调用时，它才被认成 Engine finalizer。两个外层循环都从这里过，因此不会各自跑偏。 |
| [`compaction.py`](./compaction.py) | 判断上下文什么时候太大、该请谁出去。文本、图像、原生调用和 provider wire state 分开计预算，动作与其结果则始终成对、不拆开。超大输出移进按摘要寻址的归档，原地只留一段有界预览和 SHA-256 引用。被压掉的那一段整理成结构化交接；同时它原样单独归档一份，带上把它挂回这次运行的 branch、ledger 和 recovery 元数据。 |
| [`control.py`](./control.py) | 执行一个原生工具批次，并保证每一条声明都恰好收到一个结果，取消时也一样。批次开头那一串只读、且资源互不冲突的调用可以并行；第一个会改状态或无法归类的调用就是屏障，结果永远按 provider 的原始顺序写回。 |
| [`delegation.py`](./delegation.py) | `host.delegate` 背后的子 Agent 树。树本身持有 fan-out、session、depth 三重预算；每个 runner 只管自己的直接子 Agent、它们的 executor 和收回来的结果。取消一个子 Agent，正好覆盖它的全部后代，被停掉的子 Agent 不可能再迟到地发出输出。引导消息先在内存里排队，到子 Agent 的下一个回合边界才被消费。 |
| [`engine.py`](./engine.py) | 状态机本体。它是纯的、与 provider 无关的，只跟一组 port 打交道：model、context、action executor、completion、cancellation、reply interceptor 和 event。 |
| [`events.py`](./events.py) | `AgentEngine` 发出的类型化生命周期事件。 |
| [`finalize.py`](./finalize.py) | 持有 `finalize_response` 的 schema。provider 那边只看到一份纯元数据的 spec，Host 在接受之前会把同一份封闭 schema 再校验一遍，有效的单独调用则转成结构化 completion record。它有意不注册为控制 `Tool`。 |
| [`ledger.py`](./ledger.py) | 把类型化的 Engine 事件写进只追加的 Action Ledger，写入时遮蔽声明过的 secret。往回读时，它把 group 归约成 provider 能接受的重启历史，并给崩溃时没拿到结果的工具调用补上收尾。 |
| [`loop.py`](./loop.py) | 向后兼容的本地 `Agent` facade，也是本地进程生命周期的归属地。它把 Engine 接到模型、dispatcher、ledger、委派，以及只在某个回合真的要跑代码时才启动的常驻内核上。 |
| [`models.py`](./models.py) | 在 Engine 里流转、与 provider 无关的那些值：标准化后的模型回复、可变的运行状态、一次执行的 outcome，以及最终结果。 |
| [`ports.py`](./ports.py) | Engine 依赖的一组 protocol，每个都配一个不做任何事的默认实现。正是它们让 `engine.py` 不必 import 具体的模型、存储、内核和 UI 代码。 |
| [`runtime.py`](./runtime.py) | 这些 port 在本地的那一侧。阻塞式 LLM 客户端、上下文压缩、原生工具、Python/R Cell 执行、CLI transcript 投影，以及把完成信号读回来——每样一个适配器，而 Engine 一个都不直接看见。 |

## 扩展与验证契约

- 新增动作类型必须同时经过 `actions.py`、类型化的 model 与 event，以及本地和 Web 两套组合。路由顺序必须保持确定。
- 保持 `engine.py` 不依赖具体的 provider、内核、Store 和 Gateway。
- ledger 里每一个 provider 工具调用都必须配着它的结果，崩溃后为闭合 group 而合成的结果也算在内。
- 改动路由、完成、压缩或委派之后，重跑 Agent 测试；改动执行协议时，内核测试也要一起跑。
