# Host 服务

[English](README.md)

Host 侧的 capability service 都放在这里，一个领域一个类，从 shell 授权一直到 Skill 编辑。[`HostDispatcher`](../host_dispatch.py) 负责把它们组合起来，并且始终包在每次调用的外面，充当共享的 RPC 外壳：参数校验、权限与审批、审计记录、不可信输出筛查、活动事件，以及软失败的路由。本包里没有任何一个 service 是独立对外暴露的网络 endpoint，它们只实现各自领域的行为。

## 在架构中的位置

Python worker 侧的 [`host` facade](../sdk/host.py) 发出一次同步 `host_call`。[`kernel/manager.py`](../kernel/manager.py) 把它交给 `HostDispatcher`；dispatcher 先应用策略，再调用下面某个 service。返回值随对应的 `host_response` 发回，阻塞的 Cell 就此恢复执行。能力重叠时，native 控制工具走的也是同一个 dispatcher，这样控制平面和内核内的调用遵守同一套策略。

service 可以返回单键的 `{"error": message}` 表示软失败。Python worker 会把它转成 `RuntimeError`；这既不是成功的科学结果，也不代表任务完成。权限、replay、审计和注入策略统一留在 dispatcher，大多数 service 有意不再实现一遍。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | 重新导出组合代码要用的大部分 service class。`BashAuthorizationService` 和 `ScienceConnectorService` 不在 `__all__` 里，调用方需要各自从它们所在的模块导入。 |
| [`bash.py`](bash.py) | 授权内核本地的 `host.bash`，但从不执行它；本模块不导入 `subprocess`。受信任的 Host 会把 worker 已经做过的安全与 egress 检查再做一遍，对 proposal 脱敏，然后签发一个短时 token，绑定命令摘要、cwd、worker generation 和 challenge。这张 token 只能兑换一次。worker 上报回来的结果，先限制长度并脱敏，再记录。 |
| [`accelerators.py`](accelerators.py) | 用 `nvidia-smi` 探测 daemon 本机可见的 GPU，单独报告容器运行时是否存在，并且有意不把硬件可见误报成模型仓库、checkpoint 或 backend 已就绪。 |
| [`code_evidence.py`](code_evidence.py) | 交付物是源码时的完成证据。既有的产出文件核对只问“这个文件在不在”——对图表是对的问题，对管线就是错的：文件存在并不说明模块能导入，也不说明有任何测试跑过。在 `reusable_pipeline` 与 `codebase_change` 两个任务模式下，完成载荷必须带上 `source_files`、`entry_points`、`architecture_summary`、`test_evidence`，且每一项都对着“本次运行编造不出来”的东西核验：文件必须落在证据根内、与声明的 sha256 一致、并且已登记为 artifact；Python 入口必须能从自身字节 `compile()` 通过，且**绝不** exec（校验入口不能变成在守护进程里执行 agent 代码的途径），R 与其他语言只查存在与字节，因为守护进程没有诚实的解析器；每条测试命令都必须指名跑它的那个 cell，由该行存储的 `ok` 状态与录到的 stdout 判定通过与否。这里刻意没有承载输出文本的字段，于是“测试都过了”根本无法成为一个可以被相信的声明。`analysis_run` 完全跳过这一切——四个字段保持可选且不校验。拿不到上下文一律拒绝而不是降级放行：“无法核验”不等于“已核验”。 |
| [`completion.py`](completion.py) | Cell 唯一的成功契约。它校验一个 `output`、1–4 条已完成 action bullet 和可选的 output schema，并为当前 dispatch context 留下一份通过校验的 submission。bullet 的英文过去时检查不套在 CJK bullet 上——它们的形态不带时态。接入证据提供者后，核对是逐条主张进行的：`output` 里点名的每个文件或 Artifact 都必须有 Artifact 存储、某个已执行 Cell 记录的写入、或文件系统作为凭据，摘要里复述的数字也必须和同一份 submission 的 metrics 一致。大到或深到无法穷尽核对的 output 直接拒绝，而不是只查够得着的部分；反过来，证据提供者自己抛异常时则退回旧的不核对接受——把每个诚实的 submission 都拒掉，会把循环唯一认可的完成信号锁死。 |
| [`credentials.py`](credentials.py) | session 内的凭据，留在内存里，以短时、绑定具体 action、只能用一次的 lease 形式发放。轮换某个凭据，它尚未兑换的 lease 全部作废。原始值不在这里持久化。 |
| [`data.py`](data.py) | 由 Store 支撑的数据面。一边是只读 SQL、schema 访问和 frame 浏览；另一边是 Artifact 的元数据、版本、路径、保存、恢复和图片投影，再加上溯源与血缘的读取和上报。Artifact 的枚举与查找只在调用方自己的 session 和 project 范围内进行。团队模式下，`artifact_path` 不会把进程级 snapshot 路径返回给 Cell；它以流式方式校验 checksum 与 size，再把字节放进按 root frame 隔离的不透明缓存，sandbox 只读开放该精确目录。持久 kernel 为每个实际打开的不同 version 保留一份稳定私有副本；每 session 与 daemon 全局的字节/文件配额以及磁盘余量保留共同约束这类线性增长，删除 session/project 时也会回收对应 root 的缓存。当前 frame 只是用来解析出 `root_frame_id`/`project_id` 这个作用域的句柄，所以同一 session 中更早的 Cell 写出的 Artifact 依然可以访问。兄弟 session 的文件从不就地读取：`materialise_artifact` 会把那个 version 物化成调用方自己的 Artifact，并连一条回溯的血缘边，同时私有复制已冻结、有校验和的字节，使任何可写别名都无法改写任一 version 身份。否则跨 session 直读会让借用方握着一份分析，而它的输入在自己的历史里根本没有对应的 version。它的拒绝和「不存在」不可区分，这里每一条按 version 取值的读取都是如此；血缘图的遍历也默认有界：不传 `max_depth`/`max_nodes` 曾意味着走遍整张表、跨越所有 session，而一张悄悄截断的图给出的是一个错误的血缘断言，而不是缺失的断言。 |
| [`delegation.py`](delegation.py) | session delegation runtime 的门面。把已存储的 agent profile 合进一次 `delegate` 调用，是两种合并而不是一种，因为其中两个字段是**限制**，其余是**设置**：model、provider、steps、permissions 和 capabilities 只是默认值，调用方可以覆盖；而 `skill_names`、`connectors` 和 `unrestricted` 要经 [`resource_allowlist.narrow`](resource_allowlist.py) 取交集，`unrestricted` 更是一个下限，调用方抬不上去。一视同仁地对待它们，就让存储下来的 Specialist 限制沦为建议——Agent 顺着有文档的 `host.delegate(...)` 签名，自己挑了自己的允许名单。specialist persona 会递归地前置到三种请求形态上，包括表示 fan-out 的列表；只处理字符串和 dict 时，向具名 specialist 发起的 fan-out 产出的是一批通用子 Agent，而调用方看到的是一次成功的委派。delegate、children、collect、stop、message、stats 这些调用本身，则直接透传给真正管理子 Agent 的 runtime。 |
| [`delegation_policy.py`](delegation_policy.py) | 把子 Agent 的 method/capability 策略解析一次，然后冻结。只要点名了 capability，策略就进入 restricted 模式。即便如此，除了列出的 capability 及其 alias，还有五个方法（`submit_output`、`prov_record`、`prov_resolve_path`、`search_capabilities`、`capabilities`）照样放行——任何 restricted 策略下都放行，哪怕 capability 列表是空的。逐 method 的 allow/ask/deny 决策和工具可见性一并带上，独立的 unrestricted 模式也会明确出现在投影里，而不是靠推断。 |
| [`resource_allowlist.py`](resource_allowlist.py) | Skill/Connector 的三态允许名单，以及委派时的收窄规则。`None` 表示继承，`[]` 表示全部禁止，列表表示恰好这些——而 Python 把前两者都算作假值，于是 `if not allowed:` 读起来毫无问题，却把「全部禁止」变成了「全部放行」：一个**向开放方向失败**的权限检查，而且恰好发生在用户专门为了上锁而选择的那种配置上。子代只能收窄；继承不等于放宽，所以委派不会变成绕过允许名单的路子。 |
| [`endpoints.py`](endpoints.py) | loopback 端口分配、带 start/stop 脚本的 endpoint 元数据，以及对存活路由的就绪探测。注册只是把这些生命周期脚本存下来：它不执行脚本，也不引入自己的 egress 策略。 |
| [`files.py`](files.py) | 工作区的路径边界，外加那几条预算：不让一个有界的问题付出无界的代价。它解析后期绑定的 session 工作区——并按两个可能改变它的廉价输入做记忆化，因为 `relative()` 每处理一条候选路径就调用它一次，一次覆盖 N 个文件的 glob 于是要多付 N 次 `resolve()`+`mkdir()`——把相对路径关在里面，并拒绝密钥路径。这条拒绝针对相对于显式可信工作区根的路径做目录感知：当根目录是 `$HOME` 之类的位置时，只看 basename 的 denylist 会让 `read_file` 原样返回 `.aws/credentials` 和 `.ssh/known_hosts`。若一次运行有意把工作区根设在凭据目录内，该根仍然可用，因为不会把根的父级 segment 重新套到每个子路径上。检查还会查看解析后的相对路径，并在使用候选前完成一次有界、不会跟随普通符号链接的全工作区别名清点，因此 sibling 符号链接也不能隐藏同一棵 canonical 密钥树；清点遇到不可读、超时或条目预算截断时会拒绝，而不是把信息不全当作安全证明。在 POSIX 上，实际 I/O 通过 `dir_fd`/openat 风格打开和 `O_NOFOLLOW` 固定工作区及每级父目录；最终普通文件由同一个描述符校验并消费，多硬链接文件会被拒绝，写入也只通过固定父目录发布。原生 Windows 缺少这些必需 capability，因此操作会 fail closed，而不会回退到路径名替换或 check-then-open。这封住的是用户态命名空间替换竞态，并不声称能抵御已被攻陷的内核或任意内核文件系统语义变化。两级判据共用同一张表：`is_secret_path` 用于无条件硬前置门，更宽的 `is_credential_path` 用于自动批准复核。宽判据命中后会升级成有审计的 `ask`：有人机通道时可由人复核误判；无人值守时则由确定性策略拒绝，Guardian 不能覆盖。它还拥有 `BoundedTextReader`（在字节预算下增量解码 UTF-8，并用计数器说清有多少**没读到**）和 `BoundedSelection`（取流中最小的 N 个 key，外加所有工作区集合类工具共用的那套截断计数），于是「一个有界通道该报告什么」只有一份定义，read、glob、grep、list 共用。其余方法是兼容分派，转给 class-based 的 file tool，具体读写行为在那边；那些工具都按需 import 这两个原语，好让 CLI 启动路径上的 `openai4s.tools` 不必背上 `openai4s.host` 那约 40 ms 的导入开销。 |
| [`llm.py`](llm.py) | 从运行中的 Cell 同步调用已配置的模型。批量请求会在 fan-out 上限内并发发出。该 service 也报告当前模型，但它给出的模型列表不是一份目录：里面只有一项，就是当前配置的那个模型及其上下文窗口。 |
| [`mcp.py`](mcp.py) | 解析持久化的 MCP connector（先按 id，再按精确显示名），并把 list/tools/call/resource/prompt 操作交给 MCP manager。它持有 connector 的三态允许名单，并且只在这一处查找上强制它，而不是在六个 RPC 入口各写一遍：启动配置正是用 `connector()` 返回的那一行构造的，所以本 specialist 够不着的 connector 根本没有可用来启动的命令——这比逐个调用点拒绝、然后漏掉一个要强。被拒的 connector 报成「不存在」而不是「被拒绝」，这样就没法用一个独特的拒绝去枚举系统里有什么；连 connector 清单本身也一并过滤，因为 Agent 看得见的名字就是它会去要的名字。connector 被停用时，发现类操作是零启动的：`call` 早就会拒绝，但真正拉起进程的是发现，所以 `enabled` 必须挡在 spawn 前面而不只是挡在调用前面。启动用的环境变量来自 Store 的凭据 broker，`secret://` 引用会被解析，而不是原样丢给服务器。筛查返回内容不归它管。有两个 connector 在这里得到额外处理。内置的 `protein-design` 服务器在每条 spawn 路径上都会被约束：路径权威锚在调用方的工作区而不是 daemon 的 cwd，模型准入除非运维显式关掉否则始终必需，缓存的进程按工作区分区，传输超时被排序成活得比后端自己的超时更久——这样一次长时间折叠会以终态记录失败，而不是留下孤儿子进程。托管的 `volcengine-datapro` connector 被收窄到唯一的 `dataPro_search` 工具、只接受校验过的单键 query，发现类操作在本地作答——不会有任何无闸门的鉴权会话去试探用户的真实 key——结果里的 key 会被遮蔽，每次成功搜索还会被索引。权限和不可信输出检查留在 dispatcher；连接状态、超时期限与读取线程纪律留在 [`mcp_client.py`](../mcp_client.py)。 |
| [`model_admission.py`](model_admission.py) | 提供 connector 无关的 live-process 状态机，把真实且已解析成功的 canary 绑定到精确的 operation、backend revision、checkpoint digest 和 execution target。进程重启后会故意忘记准入；真实推理和科学输出解析仍由具体 connector 负责。 |
| [`progress.py`](progress.py) | 待办清单留在这里的内存中；plan 的步骤和评审进度则落在 Store 里。勾掉一个步骤并不以“已批准”为前提：没有显式传 `plan_id` 时，被更新的就是 Store 为该 frame 返回的那个 plan，也就是最新的、未被 discard 的那一个。 |
| [`remote_capabilities.py`](remote_capabilities.py) | 注册要拿证据换。窄范围的结构化 probe spec 会被规范成一条安全的远程命令并真的跑一遍，确认远程 capability 确实存在；验证通过之后，service 元数据才进入 remote-compute 注册表。 |
| [`remote_science.py`](remote_science.py) | 通过 SSH 运行已注册的 folding 与 mutation-scoring wrapper，解析它们显式的结果标记，并为产出该结果的 cell 缓存远程溯源。服务缺失或作业失败时返回错误。它不伪造科学结果。 |
| [`connector_manifest.py`](connector_manifest.py) | 每个科学 connector 依赖上游 API 的哪些字段，声明化。分两级：**required**（数组容器与记录 id，缺了 connector 就返回不了东西）与 **expected**（parser 会读、但缺了能降级的字段）。它不是解析逻辑的第二份拷贝——离线测试证明每条 required 路径都在该 connector 自己的 fixture 里存在且承重（删掉它 adapter 就返回不了记录），因此 manifest 无法虚标。它支撑夜间 canary。 |
| [`science.py`](science.py) | 七个公共数据库，同一个信封：UniProt、PDB、Ensembl、ChEMBL、PubChem、arXiv 和 OpenAlex。请求按允许名单构造，走共享的 fetch 路径发出，每一份响应都规范成同一种记录结构。每次检索还会返回一个溯源信封——来源、请求 URL、查询与过滤条件、检索时间、规范化版本，以及每一次上游响应各自的 SHA-256——没有它，一条取回来的记录就回答不了决定它算不算证据的那两个问题：这在什么时候成立，以及看到的是不是同一批字节。摘要取的是**收到时**的响应字节，且落在七个 adapter 都必经的那一处；对解码后的字符串取哈希回答的是「它们规范化后是否相同」，因为 fetch 路径会替换非法字节、还会把 JSON 反复序列化一遍。当传输层描述不了原始字节时，记录用 `hashed` 直说，而不是把内容哈希冒充成响应哈希；一次检索若发了多个请求，全部按顺序保留，因为由两份响应拼出的结果，只有两份都被指名才可复现。 |
| [`stage10_science.py`](stage10_science.py) | Stage 10 的 ClinVar、PubMed、ClinicalTrials.gov 适配器。默认目录里没有它们；打开 flag 后进入同一套 search 信封，带分页、短缓存、诚实的空结果/429/schema 错误，以及带 provenance 的版本化 Artifact。 |
| [`session.py`](session.py) | 把控制操作钉死在 dispatcher 当前的 root session 上，任何调用都伸不进另一个会话。checkpoint 和待处理的权限申请始终从 Store 读。branch 与 recovery 状态则来自已挂接的 Web session-domain service，这也是 Web 运行时的常规路径；没有挂接 domain 时，状态投影退回到从 Store 读一份只读的 branch 列表，并把 recovery 报成不可用。涉及文件系统的 checkpoint、fork、revert、recovery 操作，同样委托给这个 domain service。 |
| [`skills.py`](skills.py) | Skill 的完整生命周期：搜索、读取、编辑、发布、版本化、回滚、删除。作用域决定磁盘上哪个目录拥有这个 Skill；内置 Skill 始终优先于用户 Skill，写入也被限制在 Skill 目录内。团队模式下，所有由 Host 发起的变更都只允许管理员：project 成员资格只授权另一条面向真人的 HTTP 控制边界，不能授权模型为同事植入 `SKILL.md` 指令或 `kernel.py` sidecar。Skill 的三态允许名单也由这里持有并强制，而且读路径和写路径都管：一个被限制在 `["a"]` 的子 Agent，如果还能覆盖、发布或删除 skill `b`，那它改写的就是**父 Agent**接下来会照着执行的那份 recipe。因为允许名单在这个对象上、语料在旁边的 loader 上，有两个视图是从这里渲染而不是从 loader 渲染的：系统 prompt 的那一段，以及内核内的 sidecar 闸门。拿着 [`HostDispatcher.skill_loader`](../host_dispatch.py) 的调用方得到的是未过滤的语料——一个受限子 Agent 的 prompt 里曾因此点名了全部 34 个 Skill。 |

## 控制、安全与失败边界

- 授权与审计边界是 [`HostDispatcher`](../host_dispatch.py)，而不是单个 service。直接调用 service 属于受信任的进程内组合，会绕过这层外壳。
- `host.bash` 不在这里执行：它的 shell 始终通过 [`sdk/bash.py`](../sdk/bash.py) 在科学 worker 内跑，[`bash.py`](bash.py) 只负责签发和兑换那张一次性 capability；上报的 stdout/stderr 在持久化前会被限制长度并脱敏。但别把这条当成整个包的性质。[`remote_science.py`](remote_science.py) 和 [`remote_capabilities.py`](remote_capabilities.py) 的 runner 默认都是 `subprocess.run`，会直接从受信任的 Host 进程起子进程，用 `ssh -o ConnectTimeout=15 -o BatchMode=yes <host> <command>` 连到已注册的远程 GPU 主机。
- [`credentials.py`](credentials.py) 里的凭据值只存在于内存，但任何拿到兑换值的消费者，就拥有了它对应的权限。基于名称的脱敏不能证明任意输出中不含密钥。
- [`files.py`](files.py) 约束路径，实际的读写行为由 Tool class 负责。Artifact 快照与溯源注册是两个独立的持久化步骤，尽力而为、不作保证，也不构成一次全局的文件系统/SQLite 事务。
- Endpoint 的 start/stop 脚本只是元数据。就绪探测成功并不能证明租户隔离、身份认证或公网暴露是安全的。
- 通用的 `host.compute`、远程 capability 开通、folding 和 mutation scoring 都还是演进中的集成面。存在一条已注册的路由或一个 service class，不代表 provider 凭据、远程软件、GPU 容量或端到端的 UI 恢复流程已经配置妥当。
- 公共数据库、MCP、LLM 和远程 SSH 调用都可能各自失败，或返回带有恶意的内容。dispatcher 的筛查只是多加的一层，不是对科学正确性的验证。

## 相关文档

- [系统架构](../../docs/architecture.md)
- [安全模型](../../docs/security.md)
- [远程计算](../../docs/compute.md)
- [Skills](../../docs/skills.md)
