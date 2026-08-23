# `openai4s/orchestration/slurm/`

Slurm backend——**全树唯一被允许叫出调度器名字的地方**。INV-2 主张的是编排核心的洁净，而不是"任何地方的代码都不许知道作业怎么提交"：总得有人知道。把这份知识收进一个目录，正是这条规则可被检查的原因：泄漏守卫按路径跳过这里，于是这里**之外**任何带调度器形状的东西都是缺陷，而不是见仁见智。

| 文件 | 是什么 |
|---|---|
| [`__init__.py`](__init__.py) | 重导出 backend、broker 与 cluster 配置。由组装代码 import；编排核心永不触达。 |
| [`backend.py`](backend.py) | `AllocationBackend` 实现，以及把 `TIMEOUT` 翻成 `(FAILED, TIME_LIMIT_EXCEEDED)` 的状态表。三条行为是承重的而非顺带的。丢失的提交是 `Unknown` 而非失败——并且 `submit` 开头就先问"这个 token 是不是已经在外面了"，所以 `Unknown` 之后的重试返回 `Existing`，而不是往集群上再放一个作业（INV-8）。队列与账务里都找不到的作业是 `LOST` 而非 `COMPLETED`：为找不到的工作宣称成功，是唯一一种会悄悄弄丢科研成果的答案。而联系不上的调度器不改变 phase——一次集群故障不该终结所有人的作业。 |
| [`broker.py`](broker.py) | 子进程边界：所有 `sbatch`/`squeue`/`sacct`/`scancel` 调用与全部解析都在这儿，好让 INV-9 在一个地方可查。argv 是列表且 `shell=False`，所以名为 `gpu; rm -rf /` 的 profile 是一个参数而不是一条命令；job name 与 comment 另有字符类限制，因为正是这两个字段把我们自己的标识符送**进**调度器输出、再从解析器里取回来。带凭据形状的环境变量一律拒绝——秘密以 0600 文件的**路径**形式传递。`SlurmCommandError` 区分 `timed_out`、`unreachable` 与普通拒绝；写测试时证明了为什么必须区分：把"PATH 上没有调度器"吞成"没有这个作业"，会把所有在跑的作业判成丢失。 |
| [`profiles.py`](profiles.py) | `cluster.toml`：把科研人员说得出的 profile 名（`gpu-interactive`）映射到管理员掌控的 partition 与 QoS 的唯一一个文件（D5）。`ClusterProfile.public()` 是给 admin 看的视图，刻意不含这两者——JSON body 里的队列名只是走得慢一点的同一种泄漏。有 `tomllib` 就用它，没有就用一个刻意做小的回退：`requires-python` 是 3.10、CI 也跑 3.10，只认 tomllib 会让整个集群功能在下限版本上直接死掉。回退只认表、字符串、整数与布尔，其余一律带行号**拒绝**而不是猜——读不出来还能补救，读错了就是排到错误的队列上。 |
