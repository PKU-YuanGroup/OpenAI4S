# `openai4s/orchestration/local/`

默认资源平面：本机。与任何集群共用同一份 `AllocationBackend` 契约，所以 reconciler、路由和 CLI 无论有没有调度器都只有一条代码路径——"没配集群"是换了个 *backend*，不是换了个程序。

| 文件 | 是什么 |
|---|---|
| [`__init__.py`](__init__.py) | 重导出 `LocalBackend`。 |
| [`backend.py`](backend.py) | 把 allocation 跑成子进程。提交 token 在这里和在集群上一样被当真：用户代码获准执行之前，一个很小的常驻 supervisor 会把包含 token、PID、PGID 与每次启动独有身份锁的 receipt 落盘并 `fsync`，而用户代码不会继承这把锁。daemon 重启后会收养这个精确 generation；尚未过闸的 wrapper 则必须被证明无法执行用户代码或被确认停止后才允许重试，因此丢失提交响应和进程过快结束都不会制造重复任务。receipt 会一直作为 at-most-once tombstone 保留，直到 reconciler 重新读取到已持久提交的 terminal allocation，并确认其 workload 已终止或已进入更晚的恢复 epoch；只有此时，可选 acknowledgement capability 才会先把 receipt 原子改名为持久 `.acked` 清理标记，再驱逐 `_jobs` 并删除 receipt/lock/identity sidecar。重启会续完中断的标记清理，而日志仍可通过确定性的 allocation 路径读取。收养后消失的进程是 `LOST` 而非 `COMPLETED`：我们只为自己真正收割到的退出码宣称成功。子进程独立进程组，于是取消杀的是整棵树而不是外面那层壳；环境是点名给的而不是 daemon 自己的（那里面有 API key）；`MAX_CONCURRENT` 以 `UNSCHEDULABLE` 拒绝——集群给的正是这个原因——所以没有调用方需要为本地单开一个分支。 |
