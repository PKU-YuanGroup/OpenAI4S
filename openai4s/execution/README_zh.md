# 科学 Cell 执行策略

[English](README.md)

这是共用的执行层。外层循环的动作，或者显式的 notebook 请求，一旦落到一个科学 Python/R Cell 上，接下来要用的策略就都在本包里。这些策略与是哪个 provider 产出的 Cell、哪个 UI 发起的请求都无关。

## 在架构中的位置

本包夹在上层的外层循环/Web 适配器与下层 [`../kernel/`](../kernel/) 中的常驻 manager 之间。它不解析模型回复，不做 Host RPC，也不亲自执行代码。它负责的是这几件事：同一个 session 同时只有一个科学写入方、每个请求都有精确的 owner/ticket/lease 身份、投影出 Cell 依赖的命名空间、定义两侧适配器共用的标准化请求与结果值，以及监督超时。

FIFO coordinator 覆盖 Agent、用户 REPL、lifecycle 和 recovery 这几类写入方。取消只针对精确的 ticket；中断信号仍然要由适配器通过匹配的内核 generation 和 lease 送达。这样，一个过期的取消才不会打断新的 owner。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 对外重新导出 coordinator 的 ticket 与错误类型、Cell 请求/结果与捕获相关的取值，以及 watchdog 策略。 |
| [`coordinator.py`](./coordinator.py) | 逐 session 的 FIFO 准入，以及一个 ticket 从排队到终止的整个可观测生命周期：队列位置、只有其精确持有者能看到的取消信号、供 UI 与持久化使用的快照，还有关闭与恢复时的状态转换。它只放行和释放写入方，自己不执行代码，也不发送进程信号；信号由调用方通过绑定到该 ticket 的内核 lease 发出。 |
| [`dependencies.py`](./dependencies.py) | 用 Python 的 `ast` 和一个刻意写得很小的 R lexer，记录每个 Cell 读了什么、写了什么、删了什么，并据此投影出哪些早先的 Cell 已经失效。`visibility` 与 `replay_policy` 的默认值也由它给出。遇到会改动命名空间却给不出稳定变量名的写法，它会标成 uncertain，而不是猜一个结果：这是一份保守投影，不是安全边界。 |
| [`models.py`](./models.py) | 跨边界传递的三个数据类：`CellRequest`、`CaptureResult` 和 `CellExecutionResult`，里面不出现任何 provider 或 UI 类型。 |
| [`watchdog.py`](./watchdog.py) | 针对一个冻结的内核 lease 的协议中立超时阶梯：先等待，超时后中断精确的 owner，中断不奏效就 kill，然后按策略重启或放弃。等待人工权限决策期间，超时预算会冻结，但取消仍然能穿透。 |

## 并发与恢复契约

- session 作用域的写入方一律不得绕过 `SessionExecutionCoordinator`。
- 中断与恢复路径上必须带着精确的 ticket 和内核 generation。看起来相关的 ID 不算数。
- 依赖元数据只是保守投影。动态 import、反射、原生扩展和任意副作用，静态分析都证明不了。
- watchdog 策略要与 Web session、Artifact、任务完成和持久化保持无关，适配器才能放心复用它。
