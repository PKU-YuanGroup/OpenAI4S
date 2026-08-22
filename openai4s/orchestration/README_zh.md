# `openai4s/orchestration/`

集群**控制平面**：被请求的工作是什么、为其中一次尝试授予了什么资源、以及任何资源平面必须呈现的边界。刻意放在 `server/` 之外、与 [`execution/`](../execution/README_zh.md) 并列——CLI 那条路也需要这些值，而一个只能经 Web handler 触达的控制平面，就是一个离开 Web handler 就没法测的控制平面。

这个包的定义性特征在于它**不包含**什么。INV-2（Backend Opacity）要求编排核心的源码与 import 图永不提及调度器：没有 `slurm`、没有 `partition`、没有 `qos`、没有 `sbatch`。这些词只活在某个 backend 子包和 `cluster.toml` 里。这不是风格偏好而是被检查的：调度器的词汇因此漏不进做策略决定的模块，接第二个 backend 时也不必先把第一个的假设重读一遍。

两条命名规约，由计划 §2 钉死，免得两套词汇混成一套让人糊涂的：

- kernel 层的 `generation` 就是本层的 **`execution_epoch`**——同一个概念（一个必须拒绝旧值的化身计数器，INV-7），按层命名；
- 规范里表示"声明式配置版本"的 generation，在这里一律叫 **`spec_revision`**，绝不叫 generation——那个词已经名花有主。

| 文件 | 是什么 |
|---|---|
| [`bootstrap.py`](bootstrap.py) | 被调度器放到某个节点上的 worker 用来证明"我就是这个 daemon 要的那个 worker"的凭据：对 `(allocation_id, epoch, rank, expires_at, nonce)` 的 HMAC，用每 daemon 一份的 0600 密钥签名，密钥经 `os.link` 发布，于是两个 daemon 不会各铸一份。每个字段都有理由——没有 `epoch`，上一个化身的凭据仍然有效，而这正是 INV-7 禁止的；没有 `nonce`，被截获的凭据可以重放。它以**文件**形式传递，只把路径告诉调度器，这也正是 broker 拒绝凭据形状环境变量名的原因：作业的环境，任何能跑 `scontrol show job` 的人都读得到。 |
| [`worker_gateway.py`](worker_gateway.py) | 这些 worker 拨回来的地方。除非 `OPENAI4S_WORKER_LISTEN` 明说，否则不开——默认开着的监听器，对每一台永远不会跑集群作业的笔记本都是一个攻击面。由 worker 做客户端，因为计算节点通常谁也连不上而 daemon 通常连得上；凭据在交换任何一个协议字节**之前**就被校验并烧掉：这条 socket 承载 `host_call` 流量，先服务后检查的监听器，在"后"的那段时间里就是一个远程执行面。拒绝只说 "refused"——过期、重放、伪造是同一个词，因为差别本身就是一个 oracle。会合按 `(allocation, epoch)` 归键，于是上一个 epoch 的迟到者满足不了当前 epoch 的等待。 |
| [`reclaimer.py`](reclaimer.py) | 会失效的租约，好让闲置会话别再占着 GPU。它只表达意图——带原因码的 `request_stop`——就到此为止；reconciler 的屏障是资源真正被归还的唯一地方，第二条拆除路径会在最要紧的那种故障上与第一条产生分歧。它存在的意义在于把一处微妙之处做对：**worker 活着不等于用户在场**。一个进程健康、socket 连着、心跳按时到达的会话，只要没人在里面跑过东西，它就是闲置的，而且仍占着别人在排队等的东西。所以这个循环只读 `last_active_at`，从不写它。是哪个时钟到点决定用哪个原因码——因为对一个再也回不来的会话说"过会儿再来"，是错的那句话。 |
| [`session.py`](session.py) | 内核跑在被授予资源上的聊天会话。就绪是四个条件的合取（INV-5），并且会指出缺的是哪一个——因为资源平面说 "running" 只意味着某处起了个进程：不代表它连回了我们、不代表它的文件在用户数据所在的位置、也不代表它算得出 `1+1`。另一半是 `AttemptPreparer`：持久 spec 是运维读得回来的那份，而**被提交**的那份是每次尝试从它派生出来的，好让一份绑定到 (allocation, epoch) 的凭据有存在的可能。正是这次派生，使得签名永不写进 `spec_json`，也使得一次恢复是真正的新尝试，而不是丢掉的那次身份的重放。 |
| [`local/`](./local/) | 默认 backend：本机。每个安装都有它，所以 INV-8 的对账在那里是真正实现而不是打桩——否则这条不变量在 CI 里没有的集群之外就没被测过。 |
| [`reconciler.py`](reconciler.py) | 那个循环：周期性地把 desired 与 observed 对比一遍，每个 workload 每 tick 至多走一步，而且每一步都写成可重复执行的——因为"tick 跑到一半死掉"是 daemon 真正会遇到的唯一失败模式。`Unknown` 的提交永不盲目重试；下一个 tick 先问 `find_by_token`（INV-8）。观察到 `BACKEND_UNAVAILABLE` 什么都不动，于是调度器重启不会变成一片 workload 集体死亡。取消屏障写成一个方法，好让计划钉死的顺序——fence → 取消任务 → drain → 释放 → 观察终态 → 标记终态——是一段能读的顺序，而不是从"调用恰好写在哪儿"里浮现出来的顺序；而且它可重入，因为走不了第二遍的屏障，在 backend 第一次迟滞时就会把 workload 卡死。只有从持久存储重新读到 terminal allocation，并确认其父 workload 已终止或已进入更晚恢复 epoch 后，才会确认可选 backend 的恢复事实；构造期与 tick 末尾两次清扫封住提交前后的崩溃窗口。 |
| [`ports.py`](ports.py) 之 `TaskRunner` | 可选，且与 `AllocationBackend` 分开：不是每个资源平面都有"步"这个概念，而没人实现得了的 Protocol 只会被实现得很糟。调用方用 `isinstance(backend, TaskRunner)` 去问，答案是否定时就明说，而不是退而求其次去提交第二个 allocation——那正是 INV-4 禁止的事。 |
| [`slurm/`](./slurm/) | Slurm backend——唯一被允许叫出调度器名字的目录，也正是它让上面那条规则可被检查而不是停留在愿望上。泄漏守卫按名字跳过它，所以调度器的词出现在别处就是缺陷。 |
| [`__init__.py`](__init__.py) | 只重导出契约，别的什么都不做。import 这个包不得连带拉进任何实现——这正是泄漏守卫在运行时主张的其中一条，也正是 backend 由组装代码去 import、而不从这里 import 的原因。 |
| [`models.py`](models.py) | 词汇表：`Workload`（kind ∈ SESSION/BATCH）、`Allocation`（一次尝试、一个 epoch）、`ResourceProfile`（科研人员用自己的单位说出的诉求）、`Phase`，以及计划附录 C 的 `Reason` 原因码。两个形状承载着最容易丢的不变量：`ExternalHandle` 把 backend 自己的 id **包起来**，好让 INV-2 在十几个调用点之后依然活着；`SubmissionToken` 在尝试提交**之前**就铸好——这就是 INV-8 的全部，因为"我那次提交到底落没落"必须是一个关于「backend 被要求记下来的东西」的问题。`Phase.is_terminal` 与 `Phase.is_active_allocation` 是 schema 里那条部分唯一索引所强制的集合的唯一可读副本。 |
| [`ports.py`](ports.py) | `AllocationBackend` Protocol——submit / observe / cancel / find_by_token / diagnostics——以及作为四种情形（而非一个布尔）的 `SubmitResult`。`Created`、`Existing`（已经有一次带着这个 token 的提交在那儿了，正是它让重试变安全）、`Rejected`（这是个答复：workload 可以干净地失败）与 `Unknown`（**不是**"重试我"：调用方必须先按 token 对账，因为盲目重试正是一次提交变成两个各占一块 GPU 的作业的方式）。`Unknown` 之所以自带 token，正是为此——让调用方自己去状态里捞，就是这一步被跳过的方式。`TerminalAllocationAcknowledger` 是单独的可选 capability，因此没有持久恢复事实的 backend 不必实现一个毫无意义的方法。 |
