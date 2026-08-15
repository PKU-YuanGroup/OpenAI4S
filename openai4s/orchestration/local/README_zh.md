# `openai4s/orchestration/local/`

默认资源平面：本机。与任何集群共用同一份 `AllocationBackend` 契约，所以 reconciler、路由和 CLI 无论有没有调度器都只有一条代码路径——"没配集群"是换了个 *backend*，不是换了个程序。

| 文件 | 是什么 |
|---|---|
| [`__init__.py`](__init__.py) | 重导出 `LocalBackend`。 |
| [`backend.py`](backend.py) | 把 allocation 跑成子进程。有两处细节比看上去重要。提交 token 在这里和在集群上一样被当真——重复的 token 返回 `Existing` 而不是再 fork 一个进程——因为这是每个安装都有的 backend，一个回答"这当然是新的"的本地 backend，会让 INV-8 的对账路径在除了 CI 里没有的集群之外的所有地方都没被测过。而一个被跟踪却已经消失的进程（daemon 重启了、有人把它杀了）是 `LOST` 而非 `COMPLETED`：我们只为自己真正收割到的退出码宣称成功。子进程独立进程组，于是取消杀的是整棵树而不是外面那层壳；环境是点名给的而不是 daemon 自己的（那里面有 API key）；`MAX_CONCURRENT` 以 `UNSCHEDULABLE` 拒绝——集群给的正是这个原因——所以没有调用方需要为本地单开一个分支。 |
