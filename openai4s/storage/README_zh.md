# 存储层仓储

[English](README.md)

[`Store`](../store.py) 背后的领域仓储都放在这里。`Store` 持有唯一的 SQLite 连接、schema 与 migration、查询检查器、可重入锁、缓存的 facade generation 以及兼容 API；本包里的每个仓储都是拿到那一份连接和那一把锁，谁也不会自己再开一个数据库。

## 在架构中的位置

外层循环把它的规范 Action Ledger 和 execution attempt 写在这里。Web 与 CLI 的投影也走同一个 `Store` facade 落库：frame、消息、Cell、Artifact、permission、plan、delegation、内核 generation 身份、checkpoint 和 recovery event。Host 侧的服务则读取窄化的仓储投影，用于数据、策略、session 控制、Skill、connector 和进度。

SQLite 事务能让明确划定的一组 row 保持原子，但它没法一口气横跨 SQLite、工作区文件、content-addressed blob、运行中的 Python/R 命名空间、远程计算和 WebSocket event。所以各仓储把三样东西分得很清楚：只追加的历史、可变的物化投影，以及对数据库之外那些文件的尽力而为的绑定。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | 把 `Store` 组合 facade 要装配的仓储类重新导出。 |
| [`actions.py`](actions.py) | 规范的 Action Ledger。group 与 event 一旦写下就不可变，reducer 靠重放它们还原 provider 历史、工具批次和 Cell observation。execution attempt 在工作开始前就分配好；之后每个生命周期节点只填一次，所以已完成的 attempt 永远不会被改写。 |
| [`activation.py`](activation.py) | 在一个事务里激活某个 checkpoint branch：所选 branch、session capability、会话级 permission 规则、可见的 Artifact head、checkpoint state 和所选的 Python 环境一起切换。这样即使崩溃，也不会出现 branch id 已经发布、周边策略和数据却还停在另一个 branch 的情况。 |
| [`agents.py`](agents.py) | 具名的专家 Agent profile，其 skill 与 connector 覆盖以 JSON 存放。 |
| [`annotations.py`](annotations.py) | 单个 session/Artifact 语境下的图像评审 pin：归一化坐标、正文、序号、状态流转与删除。分配序号和插入行放在同一个临界区里，因此并发的两个 pin 不会拿到同一个号。 |
| [`artifacts.py`](artifacts.py) | 系统对一个产出文件所知道的一切。Artifact 是稳定的身份；每次写入追加一个 version，记下字节在哪、当时是在哪个环境快照下做出来的、以及产出它的那个 Cell。血缘边把一个输出 version 连回它所派生自的那些输入 version——UI 能回答“这张图是谁产的、又吃了什么进去”，靠的就是它，而不必去翻工作区。restore 记录、优先级和最新 head 也在这里。 |
| [`branch_projection.py`](branch_projection.py) | 用不可变的 checkpoint 游标，加上当前 head 之后写入的本地 row，重建出 branch 视角下的逻辑历史。不会为了让某个 branch 读起来正确，就去删物理上只追加的历史。 |
| [`capabilities.py`](capabilities.py) | 持久化的 capability 开关。所有 capability 的优先级规则一致（session 盖过 project，project 盖过 global；没有对应 row 就是启用），一张物化的表负责快速的策略判断，每次变更还会追加一条 event。bootstrap manifest 也存在这里。 |
| [`checkpoint_state.py`](checkpoint_state.py) | 必须跟着 branch 一起走的那部分 session 域状态：plan、评审的活动/设置/批注，以及项目 memory，序列化成规范 JSON 并带一个 SHA-256 完整性摘要。导入进来的状态不会直接采信，而是先校验并隔离，再重映射身份，只恢复通过校验的那部分作用域。 |
| [`connectors.py`](connectors.py) | 一个 MCP 服务器被配置成了什么样子。命令、参数和环境变量以 JSON 存进去，读的时候再解回来，旁边是启用标志和展示用的名字。真正把这个服务器拉起来是 MCP 客户端的活，不是这张表的。 |
| [`delegation.py`](delegation.py) | 有界的子 Agent 树，持久化下来，因此重启之后这份投影依然读得对。子任务的名额是在一个 immediate 事务里、按 session 的 spawn 上限预留出来的，跑完再释放，所以一次 fanout 没法悄悄超出预算。子任务的生命周期、结果和 steering 消息也一并存在这里。 |
| [`deletion.py`](deletion.py) | 在单个事务里删掉一个 session 或一个 project 拥有的全部 SQLite 聚合。兼容 schema 里没有外键，所以每张归属它的表都要显式点名。它只把已经变成清理候选的文件路径返回出去，自己不做 unlink。 |
| [`frames.py`](frames.py) | session 的主干：project、frame 层级以及一个 frame 解析出的作用域、用户看得见的消息、活动步骤、token 计数和 frame 搜索。Cell 执行日志也在这里，每条记录都带着可见性和 replay 策略——只走协议的那种 Cell 因此可以留在审计记录里，同时不出现在只读 Notebook 上。 |
| [`kernels.py`](kernels.py) | Python 与 R 内核 generation 的持久 UUID 身份，外加 manifest、owner 与进程元数据、序号、活动记录和终止状态。这些行描述的是进程的生命周期，从不声称把活着的命名空间序列化下来了。 |
| [`memories.py`](memories.py) | 项目级的长期 memory，一张有意做得很小的表。增、查、删，外加按 category 和 block 的投影，省得调用方自己去分组。 |
| [`metadata.py`](metadata.py) | 五个小仓储合在一个模块里：项目笔记、文件夹、受管 endpoint 元数据、compaction 归档，以及 Host 调用的审计日志。凭据读取是可推导的，不会再往日志里抄一份；带 secret 的 RPC 仍按方法名留下审计记录，但它们的原始参数不会越过持久化边界。 |
| [`permissions.py`](permissions.py) | 解析带作用域的 allow/ask/deny 规则，写入本地默认值，持久化审批请求与事件，并让过了期限的待决 decision 过期。重启后的 continuation grant 绑定得很窄，且只会被原子地消费一次。 |
| [`plans.py`](plans.py) | 某个 frame 的结构化 plan，以及每一步的状态与备注。 |
| [`recovery.py`](recovery.py) | 恢复日志。每一次尝试、每一次修复都追加一条有序记录，所以失败或只做了一半的恢复，在 daemon 重启之后依然查得到。 |
| [`settings.py`](settings.py) | 一张 key/value 表，上面搭了两个结构化视图。模型 profile 以 JSON 列表存放，`mutate_model_profiles` 在 `Store` 的锁里完成读取、修改、写回，所以并发编辑不会把哪个 profile 弄丢。消息反馈则按 frame 归键。 |
| [`skills.py`](skills.py) | content-addressed 的 Skill 包：不可变的 blob、文件与 manifest；只在乐观并发下才移动的安装指针；以及只追加的启用/停用历史。包的校验与物化归 [`skills_loader/versions.py`](../skills_loader/versions.py) 管，不在这里。 |
| [`snapshots.py`](snapshots.py) | 两半。`WorkspaceCAS` 是纯标准库实现的工作区内容寻址存储，带 restore 预览、冲突检测，以及回收 tree 和无人共享的 blob；该保留哪些 tree 是别人告诉它的。`SessionSnapshotRepository` 保存 session 的 branch 与 checkpoint 信封、fork 和操作日志，也正是它查询 checkpoint 行、算出哪些 tree 仍被保留。两半都不会读写研究者自己的 Git 仓库。 |

## 持久化模型

- **Canonical history：** action group 与 event、capability event、恢复日志条目、Skill version、checkpoint 操作记录，都是以追加为主。execution attempt 和内核 generation 确实有会推进的生命周期字段，但推进它不能改写已经完结的历史。
- **UI/session projection：** frame、消息与步骤、当前 branch、Artifact head、设置、plan、批注和 profile 都是可变视图。它们不是终止信号，也不是审计记录。
- **Workspace state：** `WorkspaceCAS` 把不可变的 blob 和 tree manifest 存在 OpenAI4S 数据目录下面。它会跳过自己识别出的 secret 路径，拒绝超过大小上限的文件，也从不读取或改动研究者的 Git index 与 branch。
- **Kernel state：** checkpoint 信封里装的是环境与 generation 的引用，还有一份重放配方，不是 pickle 下来的 Python/R 内存。到底哪些东西能真的重建，由 [`kernel/recovery.py`](../kernel/recovery.py) 决定。

## 一致性与安全边界

- 只有 snapshot 绑定成功之后，一个 Artifact version 才是完全不可变的。snapshot 捕获失败时，那一行可能仍然指向一个活的、按路径引用的文件。调用方必须去看元数据，不能默认每个 version 背后都有冻结的字节。
- 工作区 restore 会感知冲突，也会逐个文件做原子替换，但它不是覆盖整个文件系统的事务。中途失败可能留下一棵只恢复了一半的目录树，诊断这种情况要靠操作记录和恢复日志。
- checkpoint 激活只保证它列出的那些会话级数据库投影是原子的，仅此而已。project 和 global 策略仍然是活的，文件系统与内核的恢复另行协调。
- Agent 的只读 SQL 由 `Store` 的查询检查器强制，而不是靠给出受限的数据库账号。仓储方法本身属于受信任的进程内代码。
- permission decision 持久化的是授权元数据，不是可以续跑的 Python 栈，也不是执行参数。重启之后，必须由一个匹配的新 action 来消费那个窄绑定的 continuation grant。
- connector 配置和其他 JSON 元数据一样，可能带着敏感的运维输入。审计脱敏规则只覆盖特定的 Host 调用，覆盖不到任何人随手存进来的字段，因此部署时的备份要按同等级别保护。
- 删除先提交 SQLite 里的归属变更，然后才把候选路径返回出去。服务端在 unlink 之前必须重新校验这些路径；数据库事务成功，并不证明字节真的清理干净了。

## 相关文档

- [系统架构](../../docs/architecture.md)
- [Web 运行时](../../docs/webapp.md)
- [安全模型](../../docs/security.md)
- [Store facade](../store.py)
