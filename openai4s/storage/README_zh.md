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
| [`annotations.py`](annotations.py) | 单个 session/Artifact 语境下的图像评审 pin：归一化坐标、正文、序号、状态流转与删除。分配序号和插入行放在同一个临界区里，因此并发的两个 pin 不会拿到同一个号。它同时负责把一个 pin 恰好一次地接纳进一条消息。`reserve` 是一条限定在本 frame 内、只从 `open` 出发的 UPDATE，两个竞争请求里恰好一个认领到某一行，输的那个看到的是它不在自己的 reservation 里，而不是悄悄共用；`release` 和 `mark_sent` 只结算属于自己的 `reservation_id`，并且只在自己的 frame 内，因为 reservation id 会随响应发出去，是调用方手里的一个值。每条谓词都自带它的预期，而不是先查后写——`Store` 的可重入锁是**每个实例**一把，而 daemon 会对同一个文件跑不止一个实例，所以「拿到了锁」从来就不是关于数据库的断言。`annotation_admissions` 是每个 reservation 最终去向的持久记录；快照可能正好在一个 pin 流转途中把它抓下来，恢复时会把它结算回 `open` 且不带持有者，因为持有它的那个请求活不过这段间隔。 |
| [`artifact_observations.py`](artifact_observations.py) | 与 version 身份分开的、按 producer 持久化的 capture observation。Stage 1 捕获复用相同 checksum 的 head 时，这里会记录新的 producing Cell、frame、环境/来源和输入 version id，却不改写原 version 的 provenance。cursor 与 delta 都按 root/project 限定；一轮交付使用的读取根本没有无作用域 fallback。Stage 1 的 observation 仍只在本地，还不会进入 Session package/share/export。 |
| [`artifacts.py`](artifacts.py) | 系统对一个产出文件所知道的一切。Artifact 是稳定的身份；内容变化的写入才追加 version，记下字节在哪、当时是在哪个环境快照下做出来的、以及产出它的那个 Cell。`browse_artifacts` 按 `(created_at, artifact_id)` 对单个 project 做 keyset 分页，filename 走转义 `LIKE`，team 可见性在 `LIMIT` 之前生效。Stage 1 opt-in 遇到相同的当前 head 时会复用 version 并写 observation，因此 version 始终表示字节变化，而不只是「又捕获了一次」。血缘边把一个输出 version 连回它所派生自的那些输入 version——UI 能回答“这张图是谁产的、又吃了什么进去”，靠的就是它，而不必去翻工作区。若某个 version 派生自公共数据库，它还带着自己的检索信封——数据从哪来、什么时候取的——以规范 JSON 存放，这样同一次检索派生出的两个 version 直接按文本就相等，而不必比较键序；至于客户端能看到其中多少，由 gateway 的投影决定。在该列出现之前写下的行保持 NULL，而不是回填一次本机从未做过的检索，理由和环境那次迁移不回填是同一个。环境快照的内容地址里含有解释器、环境名**以及**内核 generation：少了 generation，内核重启进同一个环境会算出同一个 id，第二段生命周期产出的每个 artifact 都指向仍然写着第一段的那一行。restore 记录、优先级和最新 head 也在这里。 |
| [`auto_mode.py`](auto_mode.py) | Stage 2 的 SQLite 事实源：Auto Run 选择、run、规范事件、结果审计与 finding、repair 所有权和 action-group 绑定，以及权限评估。每条事实都绑定 root、branch、turn 与 execution；写入通过 immediate 事务和请求摘要保证重启幂等。checkpoint cursor 投影逻辑分支历史，但不删除物理审计轨迹。导入历史会重映射身份、隔离且保持惰性，绝不导入审批授权。Stage 2 中该仓储只持久化和投影事实，不调用 Reviewer、Repair Agent 或 Permission Guardian。版本 29 以 additive 方式增加 `auto_mode_budget_state` / `auto_mode_budget_reservations`（及预算事件日志）：每次准入单条 `BEGIN IMMEDIATE` 预留，动作可能已开始则绝不乐观返还，没有预算行的旧 run 投影为 `legacy=true`。 |
| [`branch_projection.py`](branch_projection.py) | 用不可变的 checkpoint 游标，加上当前 head 之后写入的本地 row，重建出 branch 视角下的逻辑历史。不会为了让某个 branch 读起来正确，就去删物理上只追加的历史。 |
| [`capabilities.py`](capabilities.py) | 持久化的 capability 开关。所有 capability 的优先级规则一致（session 盖过 project，project 盖过 global；没有对应 row 就是启用），一张物化的表负责快速的策略判断，每次变更还会追加一条 event。bootstrap manifest 也存在这里。 |
| [`checkpoint_state.py`](checkpoint_state.py) | 必须跟着 branch 一起走的那部分 session 域状态：plan、评审的活动/设置/批注，以及项目 memory，序列化成规范 JSON 并带一个 SHA-256 完整性摘要。导入进来的状态不会直接采信，而是先校验并隔离，再重映射身份，只恢复通过校验的那部分作用域。 |
| [`compute_jobs.py`](compute_jobs.py) | 远程作业活得比提交它的那个进程更久——ssh 作业在 `nohup` 下继续跑，BYOC sandbox 继续计费——所以用两张表回答两个不同的问题：`compute_jobs` 记录作业此刻在哪、以及重新够到它需要哪些句柄；`compute_job_events` 记录它是怎么走到这一步的，只追加且严格递增编号。单看一个状态，分不清「我们压根没提交」和「我们提交了，但在记下句柄之前把响应弄丢了」，而重启后这两者要求的动作正好相反。幂等键是在尝试提交**之前**就写下的，所以提交路径上任何位置崩溃，都还留着一行可查；恢复时按这个键去查，而不是靠猜——这正是「找回一个作业」和「为它付两次钱」的分界。`owner_key` 则保证重启不会把一个 session 的在跑作业交给另一个。 |
| [`connectors.py`](connectors.py) | 一个 MCP 服务器被配置成了什么样子。命令、参数和环境变量以 JSON 存进去，读的时候再解回来，旁边是启用标志和展示用的名字。真正把这个服务器拉起来是 MCP 客户端的活，不是这张表的。 |
| [`datapro_index.py`](datapro_index.py) | 对成功的 DataPro 检索做无损本地索引。完整保存规范化且已脱敏的结果信封，验证来源与索引的叶节点清单一致，投影顶层与嵌套数据集条目而不合并不同位置的重复项，并提供按字面量、按作用域的搜索；不使用字段白名单，也不静默截断。 |
| [`delegation.py`](delegation.py) | 有界的子 Agent 树，持久化下来，因此重启之后这份投影依然读得对。子任务的名额是在一个 immediate 事务里、按 session 的 spawn 上限预留出来的，跑完再释放，所以一次 fanout 没法悄悄超出预算。子任务的生命周期、结果和 steering 消息也一并存在这里。带请求身份时，reserve 对着 `delegation_requests` 幂等，只给新插入的 request 扣 spawn 预算。 |
| [`delegation_attempts.py`](delegation_attempts.py) | 把持久请求身份和 attempt 身份分开。`delegation_requests` 以 `(root_frame_id, parent_action_group_id, native_call_id)` 唯一；`delegation_attempts` 以 `(request_id, attempt_no)` 唯一。相同 digest 复用原 child；digest 不同则 409。restore 从不创建 attempt——只有显式 continue 才会递增 `attempt_no`。构造函数是被动的；DDL 是编号迁移 29。 |
| [`deletion.py`](deletion.py) | 在单个事务里删掉一个 session 或一个 project 拥有的全部 SQLite 聚合。兼容 schema 大部分没有外键（DataPro entry 是狭窄例外），所以每张归属它的表都要显式点名。它只把已经变成清理候选的文件路径返回出去，自己不做 unlink。 |
| [`delivery.py`](delivery.py) | 可恢复的 completion 发布。最终 assistant 消息和它对应的规范、hash 绑定 Artifact manifest，以 root/branch 作用域的幂等键放在同一个 immediate 事务里提交；同一个键若对应不同内容会失败即拒绝。可查询的 `committed` 行证明消息与 manifest 已持久化，幂等的 `published` 迁移则把 best-effort 发送标记与消息 metadata 在同一事务里更新；Stage 1 通过 REST reopen 恢复消息，不会消费这些行去驱动 socket 自动重发；普通 turn 存活时的有界 WS 序列重放是另一条路径。Session package 导入会在一个事务中，把经验证且已重映射的 manifest 绑定到既有的安全 pending 消息。读取时若消息/manifest hash 或关系损坏，会拒绝而不是投影成已交付。 |
| [`session_imports.py`](session_imports.py) | 在一个 immediate 事务中创建安全占位 project、根 frame 与 Session package 变更隔离。只有该边界提交后才应用 package 提供的展示 metadata，因此中断导入不会留下未隔离的半成品 Session。 |
| [`frames.py`](frames.py) | session 的主干：project、frame 层级以及一个 frame 解析出的作用域、用户看得见的消息、活动步骤、token 计数和 frame 搜索。Cell 执行日志也在这里，每条记录都带着可见性和 replay 策略——只走协议的那种 Cell 因此可以留在审计记录里，同时不出现在只读 Notebook 上。消息可以从两头翻页：默认从最早开始，也可以按 `before_seq` 这个 keyset 游标从最新一页往回走，这才是一段长对话真正需要的方向——升序加 limit 给回的是**最早**那一页，于是一个 640 条消息的 session 打开时停在第 0–299 条，最新的 340 条根本不在里面。用游标而不是 offset，是因为一旦按最新在前排序，每来一条新消息 offset 就会错位；而 `seq` 在同一个 root frame 内本就单调，不需要再加一个次级排序键。 |
| [`kernels.py`](kernels.py) | Python 与 R 内核 generation 的持久 UUID 身份，外加 manifest、owner 与进程元数据、序号、活动记录和终止状态。这些行描述的是进程的生命周期，从不声称把活着的命名空间序列化下来了。 |
| [`memories.py`](memories.py) | 项目级的长期 memory，一张有意做得很小的表。增、改、删，外加按 block 的投影，省得调用方自己去分组。`resolve` 返回生效集合**以及**继承过程做了什么——先是项目自己的 memory，然后才是那些没被本项目某个 block 覆盖掉的全局 memory——因为只给一份合并后的列表，就分不清一条 memory 是被覆盖了还是从未写过；这里的顺序即优先级：预算是从末尾开始截断的，所以上下文满了先掉的是全局那一批。跨项目视图必须由调用方明确点名才拿得到。过期只是把一条 memory 从注入中扣下，从不删除，所以每个 scope 的名额上限数的是**存活**行——若按存储行数来数，一个全部过期的 scope 会拒绝每一次新写入，而被它顶满名额的那些 memory，面板上早已标为已略过——另有一个更大的存储上限，免得前一条规则让 scope 无限增长。 |
| [`metadata.py`](metadata.py) | 五个小仓储合在一个模块里：项目笔记、文件夹、受管 endpoint 元数据、compaction 归档，以及 Host 调用的审计日志。凭据读取是可推导的，不会再往日志里抄一份；带 secret 的 RPC 仍按方法名留下审计记录，但它们的原始参数不会越过持久化边界。 |
| [`migrations.py`](migrations.py) | 版本化、事务化的 schema 迁移。以前根本没有版本标记：每次打开都要把每张表重新探一遍，再对缺的列发 `ALTER TABLE ADD COLUMN`，外面裹一个光秃秃的 `except OperationalError: pass`——于是真正失败的 ALTER 和重跑时那句无害的「列已存在」变得无从分辨，进程就带着一个自以为有、实则缺列的 schema 继续跑。版本 1 的定义是「旧的那一遍已完整跑完」——它按谓词幂等，只补缺失的列，每处回填都带着只选中仍需处理之行的 WHERE，所以在任何库上再跑一次都收敛到同一形状；从 2 开始，每一步都有编号、只执行一次，并连同 checksum 一起记录。版本 24 在同一个迁移事务里安装 capture observations 与 completion deliveries。版本 31 增加可回滚的 Artifact browse 索引 `(project_id, created_at DESC, artifact_id DESC)`。整批迁移跑在一次显式 `BEGIN` 里，而这正是承重的一环：pysqlite 只在 DML 之前开事务，DDL 之前不开，于是一条裸的 ALTER 会以 autocommit 执行，熬过那条本该撤销它的 ROLLBACK。由此换来的不变式是：数据库要么完全在 N，要么完全在 N-1——被中断的升级因此只要再跑一次就能恢复。升级前先过 `PRAGMA integrity_check`，并通过 SQLite 自带的 backup API 另存一份：失败时保留，作为运维手里的退路；升级提交后即删除。 |
| [`permissions.py`](permissions.py) | 解析带作用域的 allow/ask/deny 规则，写入本地默认值，持久化审批请求与事件，并让过了期限的待决 decision 过期。带版本的安全替换可以撤销不安全的旧默认，同时不会恢复已删除或更严格的规则；v4 把旧的 `skills_edit` 静默放行改成 ask，因为 Skill 文档和 sidecar 会进入后续执行。重启后的 continuation grant 绑定得很窄，且只会被原子地消费一次。 |
| [`plans.py`](plans.py) | 某个 frame 的结构化 plan，以及每一步的状态与备注。状态集合在 Python 里强制，而不是写成 SQL CHECK：`plans` 是用 `CREATE TABLE IF NOT EXISTS` 建的，今天加上的约束只对新库生效，对既有库则悄无声息——护的恰好是最不需要护的那批人。状态变更走 compare-and-set；被已死进程留在 `executing` 的行，在启动时与内核 generation 一起被清算：置为 paused 而非 failed，因为已经完成的步骤确实完成了。`get_by_frame` 返回的是最新的、未被 discard 的那个 plan，这也正是为什么一行卡在 `executing` 会永久遮住该 session 之后的每一份草稿。 |
| [`recovery.py`](recovery.py) | 恢复日志。每一次尝试、每一次修复都追加一条有序记录，所以失败或只做了一半的恢复，在 daemon 重启之后依然查得到。 |
| [`settings.py`](settings.py) | 一张 key/value 表，上面搭了两个结构化视图。模型 profile 以 JSON 列表存放，`mutate_model_profiles` 在 `Store` 的锁里完成读取、修改、写回，所以并发编辑不会把哪个 profile 弄丢。消息反馈则按 frame 归键。 |
| [`skills.py`](skills.py) | content-addressed 的 Skill 包：不可变的 blob、文件与 manifest；只在乐观并发下才移动的安装指针；以及只追加的启用/停用历史，其元数据可证明保留版本经过的受信任创作边界。包的校验与物化归 [`skills_loader/versions.py`](../skills_loader/versions.py) 管，不在这里。 |
| [`shares.py`](shares.py) | Web 分享用的 `shares` 表：每个分享一行持久记录，保存生命周期状态（`publishing`/`ready`/`failed`/`revoked`）、当前快照 id、bundle 哈希与可选有效期。一个部分唯一索引保证每个会话至多一个活跃分享；文件系统发布与 lease GC 在 `server/share_service.py`。 |
| [`snapshots.py`](snapshots.py) | 两半。`WorkspaceCAS` 是纯标准库实现的工作区内容寻址存储，带 restore 预览、冲突检测、给子 Agent 私有 scratch 用的 `materialize`/`diff_trees`（从不删除 Artifact version），以及回收 tree 和无人共享的 blob；该保留哪些 tree 是别人告诉它的。`SessionSnapshotRepository` 保存 session 的 branch 与 checkpoint 信封、fork 和操作日志，也正是它查询 checkpoint 行、算出哪些 tree 仍被保留。两半都不会读写研究者自己的 Git 仓库。 |
| [`governance.py`](governance.py) | 团队模式治理（M2）：`project_members`、`invites`、`usage_ledger`、`quotas`。邀请只存 sha256 摘要，兑换是一条把存活谓词写进 WHERE 的 UPDATE——两个并发兑换不可能都赢。用量账本只追加、在 SQL 里聚合；配额裁决就在它读的账本旁边计算，按 day/week/month 滑动窗口,以抛出的 `QuotaExceeded`（码 `QUOTA_EXCEEDED`）返回而不是让调用方自己读行。四张表全部在 `host.query` 拒绝列表上。 |
| [`leases.py`](leases.py) | 一个会话对资源的占用，以及终结它的两个时钟；外加那张 session↔workload 绑定表，好让集群内核能从聊天会话双向找到。承重的那一列是 `last_active_at`，而且只在一个方向上承重：**worker 活着不等于用户在场**，所以传输层、看门狗、以及回收器自己的探测都不写它——只有用户执行了什么才写。把这条搞反，得到的是一份永远自我续期的租约：看着尽职，实则等于没有租约。 |
| [`user_keys.py`](user_keys.py) | 按人存的 LLM 凭据，存的是**引用**（M4-1）。密钥本身走的是这里其他凭据同一个 `SecretBroker`，于是这张表被拷走也只是一串它打不开的槽位名。每个 (user, provider) 一行——因为"自己有 Anthropic 账号、OpenAI 用组里的"是常态而不是稀奇事，一人一把钥匙的设计会逼这种用户在"全部自费"和"一分不出"之间二选一。没有行就是回落，正是这一点让该功能是增量而不是迁移（INV-1）。 |
| [`workloads.py`](workloads.py) | 持久化的 workload 与 allocation，在这里与编排层的 dataclass 互转，所以 reconciler 从不见到数据库行、本模块也从不见到 backend。`allocations(workload_id)` 上那条部分唯一索引就是 INV-3 的全部——旁边的 Python 检查是为了给一句能读的错误，但它要防的失败恰恰发生在**检查与插入之间**：一个 tick 正好死在那儿、重启、然后提交出第二个占着第二块 GPU 的作业。`submission_token` 的 UNIQUE 出于同一类理由：INV-8 的"有没有东西带着这个 token"只有在 token 不可能被铸造两次时才有意义。 |
| [`team.py`](team.py) | 团队模式身份（`OPENAI4S_TEAM_MODE`）：`users`、`auth_sessions`、`team_audit_log` 三张表。口令用 PBKDF2-HMAC-SHA256 加每用户独立盐，迭代数记录在行上，未来提高成本时旧账号在下次登录时懒重哈希而不是集体失效；登录 cookie 只存 token 的 sha256；禁用用户或重置口令会吊销其全部在线会话。三张表都在 `host.query` 拒绝列表里——agent 的 SQL 永远看不到凭据材料。 |

## 持久化模型

- **Canonical history：** action group 与 event、capability event、恢复日志条目、Skill version、checkpoint 操作记录，都是以追加为主。execution attempt 和内核 generation 确实有会推进的生命周期字段，但推进它不能改写已经完结的历史。
- **UI/session projection：** frame、消息与步骤、当前 branch、Artifact head、设置、plan、批注和 profile 都是可变视图。它们不是终止信号，也不是审计记录。
- **Trusted delivery：** 带 Artifact 的最终消息，只有在对应的 `completion_deliveries` 行与已验证 manifest 和消息一起提交之后才可投影。生产者只发送一次 WebSocket 投影并尽力写入发布标记；turn 仍存活时，普通的有界 WS 序列缓冲可能重放。delivery ledger 不驱动重发，终态或重启后的恢复读取持久 REST 消息；socket 从来不是事实源。
- **Workspace state：** `WorkspaceCAS` 把不可变的 blob 和 tree manifest 存在 OpenAI4S 数据目录下面。它会跳过自己识别出的 secret 路径，拒绝超过大小上限的文件，也从不读取或改动研究者的 Git index 与 branch。
- **Kernel state：** checkpoint 信封里装的是环境与 generation 的引用，还有一份重放配方，不是 pickle 下来的 Python/R 内存。到底哪些东西能真的重建，由 [`kernel/recovery.py`](../kernel/recovery.py) 决定。

## 一致性与安全边界

- 只有 snapshot 绑定成功之后，一个 Artifact version 才是完全不可变的。legacy/flag-off 捕获在 snapshot 失败后可能仍然保留活的、按路径引用的文件，因此一般调用方仍必须检查元数据。Stage 1 trusted delivery 更严格：没有冻结的普通文件快照，或稳定字节与记录的大小/SHA-256 不一致时，它不会声称或链接这个 version。
- 工作区 restore 会感知冲突，也会逐个文件做原子替换，但它不是覆盖整个文件系统的事务。中途失败可能留下一棵只恢复了一半的目录树，诊断这种情况要靠操作记录和恢复日志。
- checkpoint 激活只保证它列出的那些会话级数据库投影是原子的，仅此而已。project 和 global 策略仍然是活的，文件系统与内核的恢复另行协调。
- Agent 的只读 SQL 由 `Store` 的查询检查器强制，而不是靠给出受限的数据库账号。仓储方法本身属于受信任的进程内代码。由于这道检查器跑在唯一那条共享连接上，**解除**它和**装上**它一样承重：在 Python 3.10（本项目声明的下限）上，`set_authorizer(None)` 并不会摘掉 C 侧的转接层，只是让它背后没有可调用对象，SQLite 会把这次失败的回调读成 `SQLITE_DENY`——于是那句意为「不设限制」的调用实际含义是「全部拒绝」，一次 `host.query` 就足以让本进程之后的每一次写入都报未授权。所以解除检查器的方式是装上一个放行的回调。
- permission decision 持久化的是授权元数据，不是可以续跑的 Python 栈，也不是执行参数。重启之后，必须由一个匹配的新 action 来消费那个窄绑定的 continuation grant。
- connector 配置和其他 JSON 元数据一样，可能带着敏感的运维输入。审计脱敏规则只覆盖特定的 Host 调用，覆盖不到任何人随手存进来的字段，因此部署时的备份要按同等级别保护。
- 删除先提交 SQLite 里的归属变更，然后才把候选路径返回出去。服务端在 unlink 之前必须重新校验这些路径；数据库事务成功，并不证明字节真的清理干净了。

## 相关文档

- [系统架构](../../docs/architecture.md)
- [Web 运行时](../../docs/webapp.md)
- [安全模型](../../docs/security.md)
- [Store facade](../store.py)
