# Agent 外层循环

[English](README.md)

外层循环的状态机放在这里，还有把它组合成本地/CLI 执行路径的那些适配器。状态机本身不认识任何具体的 provider。Web session runner 在 server 包里另有一套组合，但动作路由和 Engine 契约用的都是这里定义的同一套。

## 在架构中的位置

每个模型回复最多被路由为一种动作：

1. 一个有序的 provider 原生 JSON 控制工具调用批次；
2. 一个单独且有效的 Engine 自有 `finalize_response` 动作；或
3. 第一个完整的 fenced Python/R Cell。

原生调用优先于代码。混在其他调用里、或者格式不合法的 finalize_response 都不构成完成。格式合法但主张超出实际执行的同样不构成完成：两套组合都会在分发现场累计执行证据，一个既没跑 Cell 也没跑工具的回合，不能带着「执行过工作」式的 bullet、`artifacts` 列表或 `metrics` 去 finalize——拒绝以可修复的校验错误呈现，绝不当成完成。`host.submit_output(...)` 是唯一能从 Python Cell 内部发出的完成信号；先前执行过 Cell 之后，后续单独且有效的 `finalize_response` 仍然可以关闭 Engine。普通文本、一般工具 observation、R Cell、取消和最大回合耗尽都不算完成。

只有当动作是代码时，外层循环才会去碰前台的内层内核 manager。所以 tool-only 或 finalize-only 的回合根本不会启动 worker。单个控制工具仍然可以作为自身 capability 的一部分，管理一个专属的 worker。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 包的对外出口：Engine、本地 `Agent` facade 与 `run_task`、各类结果值，以及完成相关的辅助函数。 |
| [`actions.py`](./actions.py) | 模型回复变成动作的唯一入口。它标准化原生调用，识别 Python/R fence；两者同时出现时，原生调用胜出。只有当 `finalize_response` 是回复里唯一的原生调用时，它才被认成 Engine finalizer。两个外层循环都从这里过，因此不会各自跑偏。 |
| [`cell_record.py`](./cell_record.py) | 委托子代理 Cell 的持久 execution_log 记录。`DelegatedCellRecorder` 把子代理执行过的每个 Cell——失败与被中断的也记录，宿主侧崩溃时补一条合成 error 行——以 `frame_id = root_frame_id = <子委托 frame>`、`origin="delegate"` 落库，并盖上子内核的持久 generation id。按子 frame 建键让每个子代理拥有独立的 revision 游标，也从构造上把子 Cell 挡在根 Notebook 投影之外；`frame_detail` 与 lineage 的 `cell_recorded` 因此如实，而投影层零改动。记录器绝不向执行器抛异常(Cell 已经跑完，丢一条记录不该让运行失败)；`ComposedCellHooks` 让它先于可选的 stage-1 Artifact 捕获钩子运行，捕获失败也丢不掉执行记录。 |
| [`compaction.py`](./compaction.py) | 判断上下文什么时候太大、该请谁出去。文本、图像、原生调用、provider wire state 和 system prompt 分开计预算，动作与其结果则始终成对、不拆开。prompt 单独占一档，是因为压缩从来不碰它：常驻上下文——记忆、Skill、specialist、连接器、环境——每一轮都是从头重建的；一旦被算进 `text`，一份很大的 prompt 读起来就成了「你的对话太长了」，于是读者被指向了唯一帮不上忙的那个办法。超大输出移进按摘要寻址的归档，原地只留一段有界预览和 SHA-256 引用。被压掉的那一段整理成结构化交接；同时它原样单独归档一份，带上把它挂回这次运行的 branch、ledger 和 recovery 元数据。 |
| [`control.py`](./control.py) | 执行一个原生工具批次，并保证每一条声明都恰好收到一个结果，取消时也一样。批次开头那一串只读、且资源互不冲突的调用可以并行；第一个会改状态或无法归类的调用就是屏障，结果永远按 provider 的原始顺序写回。哪些调用算执行证据由 `call_reaches_dispatcher` 判定：编造的工具名或被拒绝的参数根本到不了 dispatcher，因此也不能为后面「执行过工作」式的完成主张背书。 |
| [`delegation.py`](./delegation.py) | `host.delegate` 背后的子 Agent 树。树本身持有 fan-out、session、depth 三重预算；每个 runner 只管自己的直接子 Agent、它们的 executor 和收回来的结果。取消一个子 Agent，正好覆盖它的全部后代，被停掉的子 Agent 不可能再迟到地发出输出。引导消息先在内存里排队，到子 Agent 的下一个回合边界才被消费。资源允许名单（`skill_names`、`connectors`）只会被收窄，绝不会被替换，而且合并 spec 的两处都要收窄：一处是把请求项对着 `delegate()` 调用自己的 kwargs 收窄，另一处是在嵌套路径上把子 Agent 对着「父级子 Agent」的 spec 收窄。只有后者能约束到孙子一层；少了它，往下再委派一级就成了绕开上一级已经接受的限制的出口。子 Agent 压缩上下文时，参照的也是**它自己**那个模型声明的窗口，而不是 daemon 的默认值——`overrides["model"]` 把它换到别的模型上时，这两个数字恰好就会分叉。子 Agent 在发起委派的 session 的工作区里运行，属主的读隔离策略逐层原样下传；每个线程池里的子线程都会重新种上父级的执行主体和关联上下文，因为 `ThreadPoolExecutor` 这两样都不会复制。启用 Web 可信 Artifact 捕获时，委派是串行的：同一时刻只有一个同步子 Agent 持有共享工作区快照，并行 fan-out 直接拒绝，而不是错误归属。 |
| [`engine.py`](./engine.py) | 状态机本体。它是纯的、与 provider 无关的，只跟一组 port 打交道：model、context、action executor、completion、cancellation、reply interceptor 和 event。 |
| [`events.py`](./events.py) | `AgentEngine` 发出的类型化生命周期事件。 |
| [`finalize.py`](./finalize.py) | 持有 `finalize_response` 的 schema。provider 那边只看到一份纯元数据的 spec，Host 在接受之前会把同一份封闭 schema 再校验一遍，有效的单独调用则转成结构化 completion record。它有意不注册为控制 `Tool`。接受与否背后的执行证据账本也在这里：executor 在分发现场调用 `note_execution_evidence`——只在内核或 dispatcher 真的跑过之后记，拒绝从不记——`reconcile_completion_claims` 则拒掉零执行却主张执行过工作的 payload。CJK bullet 没有时态形态，永远不会被标记。 |
| [`ledger.py`](./ledger.py) | 把类型化的 Engine 事件写进只追加的 Action Ledger，写入时遮蔽声明过的 secret。往回读时，它把 group 归约成 provider 能接受的重启历史，并给崩溃时没拿到结果的工具调用补上收尾。团队模式下它还把模型用量计到 session 属主头上，尽力而为：没有归属记录时只是两次读、零次写，而且计量永远不会让动作失败。 |
| [`loop.py`](./loop.py) | 向后兼容的本地 `Agent` facade，也是本地进程生命周期的归属地。它把 Engine 接到模型、dispatcher、ledger、委派，以及只在某个回合真的要跑代码时才启动的常驻内核上。当 Web gateway 把它作为被委派的子 Agent 嵌入时，它还接受父 session 的工作区、一份 OS 读隔离策略和 Cell 捕获钩子；不设置时各自保持 CLI 契约——进程 cwd、历史读行为、不做捕获。 |
| [`models.py`](./models.py) | 在 Engine 里流转、与 provider 无关的那些值：标准化后的模型回复、可变的运行状态、一次执行的 outcome，以及最终结果。 |
| [`ports.py`](./ports.py) | Engine 依赖的一组 protocol，每个都配一个不做任何事的默认实现。正是它们让 `engine.py` 不必 import 具体的模型、存储、内核和 UI 代码。 |
| [`runtime.py`](./runtime.py) | 这些 port 在本地的那一侧。阻塞式 LLM 客户端、上下文压缩、原生工具、Python/R Cell 执行、CLI transcript 投影，以及把完成信号读回来——每样一个适配器，而 Engine 一个都不直接看见。它同时决定一次 Cell 结果最多能花掉模型多少上下文：observation 的每一段都有上限，超限的那一段只留预览，全文溢写到一个工作区**相对**路径的内容引用上，Agent 可以自己去打开。之所以是相对路径：绝对路径会把 `$HOME` 泄进上下文，还会让渲染出来的 observation 长度取决于数据目录恰好落在哪儿。模型适配器还把取消一路交到传输层，于是限流退避重试期间就能停下来，而不是等整个重试预算耗完才停。执行证据也是在这里计数的——只算真正分发出去的调用和真正跑过的 Cell，从不算只是声明或被拒绝的；带 `writes_files` 的原生工具会被 Web 捕获钩子前后包住，让它写的文件在正确的 frame 下变成 Artifact；可选的团队模式配额闸门也在这里，在 provider 请求发出之前就能拒绝它。 |

## 扩展与验证契约

- 新增动作类型必须同时经过 `actions.py`、类型化的 model 与 event，以及本地和 Web 两套组合。路由顺序必须保持确定。
- 保持 `engine.py` 不依赖具体的 provider、内核、Store 和 Gateway。
- ledger 里每一个 provider 工具调用都必须配着它的结果，崩溃后为闭合 group 而合成的结果也算在内。
- 改动路由、完成、压缩或委派之后，重跑 Agent 测试；改动执行协议时，内核测试也要一起跑。
