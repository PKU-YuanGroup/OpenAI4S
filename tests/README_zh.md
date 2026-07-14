# Tests（测试套件）

[English](README.md)

OpenAI4S 的离线正确性门禁。`uv run pytest` 用确定性 fake 跑完这里的每一个模块，而且必须一直是绿的：供应商无关的 Agent 引擎、Host 服务、常驻 Python/R 内核协议、仓储、安全边界、Tool、Skill 与 Web 组合。可复用的场景和计分评测属于另一层，放在 [`../harness/`](../harness/)。

## 离线契约

- `uv run pytest` 不能依赖真实 LLM、API key、网络、GPU、SSH 主机、Docker daemon、浏览器或实验室系统。[`conftest.py`](conftest.py) 为每个测试把 `~/.openai4s` 指向一个临时目录，并装上 fake provider 与 key。
- 需要外部资源的测试必须挂上在 `pyproject.toml` 里注册的标记，由使用者显式选择开启；[`test_marker_policy.py`](test_marker_policy.py) 守着这条规矩。
- 捕获下来的输入和对字节敏感的样本放在 `fixtures/`；测试不得静默改写它们。
- 网络、子进程、provider、时钟、UUID 与文件系统这几处边界要么被 mock，要么被限制住。唯一的例外是明确声明过、并且单独调用的 smoke 程序。
- 跑单个模块用 `uv run pytest tests/test_kernel.py`，跑单个用例用 `uv run pytest tests/test_agent.py::test_max_turns_stop`，跑完整门禁就是 `uv run pytest`。

## 支持与 Smoke 文件

| 文件 | 职责 |
| --- | --- |
| [`conftest.py`](conftest.py) | 建立 import 路径，给每个测试一份独立的数据目录，配好 fake LLM 的配置与 key，用完清理 `Store`，并存放共享的 pytest fixture。 |
| [`browser_smoke.mjs`](browser_smoke.mjs) | 用真实浏览器驱动运行中的 Gateway UI 与流式交互路径。本地要和 pytest 分开调用；普通 PR CI workflow 会自动跑它。 |
| [`scientific_renderers_smoke.cjs`](scientific_renderers_smoke.cjs) | 一个轻依赖的 Node 运行器，按契约检查 Web UI 里那些 UMD 科学 Artifact 解析器。 |

## 测试模块

下面列出全部 `test_*.py`，写的是这个模块存在的理由，而不是它用例的清单。多数模块还额外带着围绕失败、重启、脱敏与并发的回归用例，这一行不会一一点名。

| 文件 | 打开这个文件的理由 |
| --- | --- |
| [`test_action_ledger_repository.py`](test_action_ledger_repository.py) | 四条断言，讲的都是 Ledger 存储层拒绝做什么：覆盖已有的 group 或 event、把原子的 Tool group 拆开、改写已经终止的执行 attempt。列的迁移是只增不改的。 |
| [`test_action_ledger_runtime.py`](test_action_ledger_runtime.py) | Ledger 的另一半：运行中的循环究竟往里写了什么。参数在落库之前就已脱敏，分支只继承 checkpoint 过的父前缀，被中断的原生 group 会被原子地收尾，重开 store 之后历史还能重建回来。 |
| [`test_action_routing_eval.py`](test_action_routing_eval.py) | 与其说是断言套件，不如说是一个打分器。它把离线的路由 fixture 送进 Tool/Code/Finalize 路由，再输出一份供人工审阅的失败与混淆报告。 |
| [`test_action_timeline_service.py`](test_action_timeline_service.py) | 当 Timeline 把不该给用户看的东西显示出来时，来看这个文件。这个投影是公开的，所以测试大多在讲它扣下了什么——原始 payload、秘密、provider ID——以及它不许扣下什么，比如更早的那次 Tool 失败。 |
| [`test_actions.py`](test_actions.py) | 两个外层循环共用的这一份回复解析。优先级规则钉在这里：原生调用压过代码 fence，没有闭合的 fence 永远不可执行，文档顺序里只有第一个 Cell 会跑。 |
| [`test_admet_genetic.py`](test_admet_genetic.py) | 内置的 ADMET genetic Skill：能被发现、helper 的聚合是确定性的，以及它生成的 dashboard 会转义脚本和 HTML 定界符。 |
| [`test_agent.py`](test_agent.py) | 全套里覆盖面最宽的一个模块——离线外层循环的完整链路。Code-as-Action 循环、没有 R 时 R Cell 软失败成一条 observation、token 估算、把一个 Cell 和它的 observation 压在同一个原子段里的 compaction，以及委派的上限。改坏了循环，通常先在这里露馅。 |
| [`test_agent_control.py`](test_agent_control.py) | 压力之下的原生 Tool 批次。哪怕其中一次调用失败，或者整轮在批次中途被取消，批次里的每个调用最终都要落到一个结果。互不相干的只读调用可以并行，但一个会写的调用是它之后所有调用的 barrier。 |
| [`test_agent_engine.py`](test_agent_engine.py) | 单独用 fake port 驱动 `AgentEngine`。最后那个测试才是这个模块的要点：引擎不许 import 任何运行时基础设施。其余的钉住路由优先级、可重放的历史分组，以及取消究竟在哪几个时刻取胜。 |
| [`test_agent_hybrid.py`](test_agent_hybrid.py) | 关于 hybrid `Agent` 门面的两个测试：原生调用压过代码、且它的规范历史能活到下一轮；被复用的 agent 在接新任务前会清掉上一次的提交。 |
| [`test_agent_profile_repository.py`](test_agent_profile_repository.py) | 落在 SQLite 里的具名 agent profile，主要是那些别扭的地方——假值的老式序列化、列表读取时的 JSON 解码边界，以及 upsert 必须扛住的“先读后写”那段空隙。 |
| [`test_agent_runtime.py`](test_agent_runtime.py) | 纯引擎与真实基础设施之间的本地适配器。两条规则占主导：原生调用解析出错或超出上限时绝不许下发，同时也绝不许把 Tool 结果弄丢。compaction 会把尾部撑开，好让 assistant 的 Tool group 保持原子；一个熔断器会掐掉反复低收益的 compaction。 |
| [`test_analysis_skills.py`](test_analysis_skills.py) | 内置的分析类 Skill 在这里是真的被执行，不只是被列出来。它的数据审计能抓出分组泄漏，AUC 会处理并列，bootstrap 是确定性的。 |
| [`test_annotation_repository.py`](test_annotation_repository.py) | 图像 annotation。一共三个测试，真正要紧的是并发钉图时的序号分配——它必须是原子的。 |
| [`test_artifact_control_tools.py`](test_artifact_control_tools.py) | Artifact 恢复是这里最危险的操作，所以这个模块大半在讲它怎么拒绝：损坏的快照、不可信的 ID、版本切出之后又被改动过的工作区。恢复始终要过审批，store 写失败时会回滚。 |
| [`test_artifact_manager.py`](test_artifact_manager.py) | 工作区 Artifact 的捕获、版本化与 cell 提升。其中很大一部分是在防重复：临时版本必须合并进去，而不是变成第二个版本；R 的捕获也不许跑 Python 的图像探测。提升会拒绝符号链接的输出路径，图像只以安全的 data URL 形式内嵌。 |
| [`test_artifact_mutation_service.py`](test_artifact_mutation_service.py) | 界面上的交互式编辑、重命名、上传与删除。事件形状被一字不差地钉住，因为前端要读它们；任何想逃出工作区的路径都会失败即拒绝。 |
| [`test_artifact_repository.py`](test_artifact_repository.py) | manager 底下的 artifact、版本、环境与血缘四类仓储。要盯的是事务这条承诺：血缘写失败时，整个 `record_cell` 事务连同那个版本一起回滚。 |
| [`test_artifact_scope.py`](test_artifact_scope.py) | 子 frame 产出的 Artifact 到底归谁。scope 从根 project 继承下来；显式指定的根若与已知的生产者冲突，会被拒绝；一次迁移会修好规则出现之前写下的那些会话。 |
| [`test_backend_import_contract.py`](test_backend_import_contract.py) | 这是一次源码扫描，不是行为测试。在实现逐步搬进新包的过程中，调用方只能 import 已声明的门面表面，并且不许新增对未声明的旧模块内部符号的依赖。 |
| [`test_background_cleanup.py`](test_background_cleanup.py) | 一个测试，一条保证：关闭会话时先中断、再杀掉它的后台内核，不让任何 worker 活得比会话久。 |
| [`test_bash_authorization.py`](test_bash_authorization.py) | `host.bash` 在内核本地执行，Host 从不代跑，只负责授权。这个模块就是“这张 token 足够了”的完整论证：它绑定命令、cwd、generation 与 challenge，一次性、会过期，而且被篡改后 worker 会在拉起任何子进程之前就拒掉。 |
| [`test_capability_state.py`](test_capability_state.py) | 持久的 Skill 与 Specialist 启用状态。它防的是这样一种失败：某个 Skill 在提示词里是禁用的，在 `search` 里却不是；或者 loader 还攥着一个已经关闭的 `Store` 的仓储。 |
| [`test_catalyst_sar_screening.py`](test_catalyst_sar_screening.py) | catalyst SAR Skill：它文档里的流水线和硬性锁定、随包带的 CONTCAR 目录，以及 POSCAR 解析器的边界情况——负的缩放系数、被截断的文件、要先做净化才能当文件名的结构化名字。 |
| [`test_cell_dependencies.py`](test_cell_dependencies.py) | Python 与 R Cell 的静态依赖分析，而且刻意允许它说“我不知道”。动态写命名空间或解析失败时，这个 Cell 会被标成不确定，而不是猜一个结果；失效状态再从那里沿依赖传递下去。 |
| [`test_cell_execution_service.py`](test_cell_execution_service.py) | Web 的 Cell 服务，事务顺序都在这里。attempt 在 Cell 准备之前就分配好，哪怕 worker 抛异常也要收尾。R 的协议异常只关掉当时正在执行的那个 lease，绝不误伤它的替身。只走协议的 `host.submit_output` 照样进审计，只是不作为 Notebook cell 流式播出去。 |
| [`test_cell_watchdog.py`](test_cell_watchdog.py) | 一个停不下来的 Cell 的超时恢复。要读的是那个不显眼的情形：当 Cell 停在等待权限审批时，超时预算会被冻结——否则一个人思考一分钟就足以杀掉内核。 |
| [`test_checkpoint_state_snapshots.py`](test_checkpoint_state_snapshots.py) | 绑定到不可变 checkpoint 的 plan、review 与记忆状态。两个行为是承重的：遗留 checkpoint 被当成“部分状态”，而不是会抹掉现有数据的空状态；损坏的状态失败即拒绝，而不是恢复出半个自己。 |
| [`test_cli_contract.py`](test_cli_contract.py) | 别人写脚本会依赖的那层 CLI 表面：入口、子命令、选项与 help 文本。`status` 只报告本地数据目录，并不宣称 daemon 是健康的。 |
| [`test_compute_nvidia.py`](test_compute_nvidia.py) | NVIDIA BYOC provider，每一次 `docker` 调用都被假的子进程层截住，所以不需要 Docker、GPU 或网络。安全的那一半是两阶段 secret 清洗：provider 的顶层代码在被 import 时，不能读到形似凭据、或带已知前缀的环境变量。 |
| [`test_config.py`](test_config.py) | 分层配置，而且基本围着同一类 bug 转：从模板里抄来的占位 API key 绝不能被当成真 key，无论它是从环境变量来的还是显式传进来的，也不能挡住真正的按 provider 配置的 key。 |
| [`test_connector_repository.py`](test_connector_repository.py) | MCP connector 的那些行：JSON 规范化、排序、启用与停用，以及把它们喂给 Host MCP 服务的 `Store` 门面。 |
| [`test_context_policy_web.py`](test_context_policy_web.py) | 两个测试。超大的上下文输出会变成一个去过重的 Artifact 版本，而不是又一份拷贝；compaction 的 payload 会被链回会话历史，而不是被丢掉。 |
| [`test_data_background_tools.py`](test_data_background_tools.py) | data 与 background 这两类 Tool。有两条策略是被断言出来的，而不是假定的：`query` 严格只读，没有任何能把它放宽的审批路径；提交后台任务要过闸门，中断它则始终可用。 |
| [`test_delegation_persistence.py`](test_delegation_persistence.py) | 跨 daemon 重启的委派。进程死掉时还活着的子任务必须被停掉、它的 lease 必须被 fencing 掉，而不是被新的 generation 悄悄认领。steering、预算、级联取消与删除后的清理也在这里。 |
| [`test_delegation_policy.py`](test_delegation_policy.py) | 四个测试，一个主张：子任务的 capability 与权限策略是真的会强制执行，不是摆设。无效策略在消耗预算之前就失败；嵌套的子任务既放宽不了父任务给的权限，也松不动一条拒绝。 |
| [`test_delegation_runtime.py`](test_delegation_runtime.py) | 跑起来的子 agent。预算由整棵树共享，并在并发的 runner 之间原子预留；深度四是无条件的叶子；停止之后才到达的模型回复，既不能执行也不能提交。 |
| [`test_dynamic_tool_scopes.py`](test_dynamic_tool_scopes.py) | Dynamic Tool 在会话、project、global 三种 scope 下的解析，优先级也是这个顺序。重新提升同样内容会复用版本，但仍然记一笔审计；跨 project 的激活和被篡改的 scoped manifest 都失败即拒绝。 |
| [`test_dynamic_tools.py`](test_dynamic_tools.py) | Agent 自己写的工具，是把新代码送上这台机器最直接的一条路，所以这是个“围堵”模块。每个工具都跑在自己的一次性 worker 里，前面有源码闸门，环境里没有任何 Host 的 secret，而且拿不到强制开启的 OS 沙箱就根本不跑。schema、TTL、权限与超时检查再叠在上面。 |
| [`test_e2e.py`](test_e2e.py) | 只有两个测试，却是全套里最宽的一层栈：真实的 Skill loader、真实的内核子进程、真实的 `HostDispatcher`，只有 LLM 是脚本化的。第二个测试看的是内核抛错时 Agent 实际观察到了什么。 |
| [`test_egress.py`](test_egress.py) | 出站域名的允许名单。前面几个测试要仔细读：这个模式默认是关的，而关掉时它失败即放行。这是一个需要运维显式打开的控制项，而不是系统天然具备的性质。一旦打开，仿冒域名会被拒，运行时的授权可以经 broker 放宽，而一次拒绝会让围栏保持关闭。 |
| [`test_environments.py`](test_environments.py) | conda 环境发现，跑在假的 conda 目录上，所以结果永远不取决于开发者本机装了什么。仅有 R 的环境会被发现，但绝不会作为可运行的 Python 提供出去。 |
| [`test_execution_coordinator.py`](test_execution_coordinator.py) | 单独看 FIFO 协调器。同一时刻一个会话只有一个写入方，不同会话互不阻塞；取消或中断必须报出确切的 ticket 与持有者——来自另一个协调器的 ticket 什么也释放不了。 |
| [`test_execution_view_service.py`](test_execution_view_service.py) | Execution 日志与 Notebook 所依赖的那些 DTO。麻烦的是重试投影：连续的失败会折叠成一行且一条不丢，而这次折叠不许跨越运行时边界或非 Agent 的边界。 |
| [`test_frame_repository.py`](test_frame_repository.py) | project、frame、message、step 与 cell log。执行日志是只追加的，时间戳相同的行按状态修订号排序；删除 project 在单次 commit 里级联完成。 |
| [`test_gateway.py`](test_gateway.py) | 这里最大的一个模块，波及面也最广。它从手写的 WebSocket 一路管上去——帧的编码与解掩码、续传缓冲及其字节预算——再到 keepalive 请求之间的 HTTP 请求体分帧、对跨源 API 写入与 WS 升级的双重拒绝、Artifact 与环境路由，以及必须扛过持久化的占位 key 过滤。 |
| [`test_gateway_engine.py`](test_gateway_engine.py) | 基于 `AgentEngine` 的 Web runner，只测组合边界，内核保持离线。流式增量会藏住 fence，并且排在 Tool 事件与终止事件之前；对话式的 JSON fence 不许把它后面的公开散文截断掉。 |
| [`test_gateway_kernel_lifecycle.py`](test_gateway_kernel_lifecycle.py) | supervisor 之下的 Python 与 R slot，内容就是那些竞态。stop 的意图不许被一个新的 start 抢先，bootstrap 必须在 supervisor 锁之外跑，R 出问题也不许碰到 Python 的 worker。 |
| [`test_gateway_lazy_runtime.py`](test_gateway_lazy_runtime.py) | 惰性在这里是承重性质，不是优化。plan 的一轮、只用原生 Tool 的一轮、只用 Tool 的结构化完成，全都能在内核进程压根不存在的情况下走完；真正拉起 worker 的是第一个 Cell 或第一次 REPL。 |
| [`test_gateway_session_domain_routes.py`](test_gateway_session_domain_routes.py) | 建立在同一份组合之上的 session-domain HTTP 路由。读 UI 代码之前值得先知道：`fork_from_cell` 是刻意失败即拒绝的，因为它还没被支持；变量检查的路由则从不启动 worker。 |
| [`test_gateway_session_lifecycle.py`](test_gateway_session_lifecycle.py) | 跨 daemon 自身生命周期的持久 generation 与 attempt 身份：TTL 清扫、启动时对上一个已死 daemon 留下的 generation 做对账，以及挡住新工作进入正在删除的 project 的准入检查。 |
| [`test_global_research_views.py`](test_global_research_views.py) | 两个跨会话的读模型。project Timeline 把它们合并起来，且不携带任何原始 payload；血缘视图把 Artifact 版本连回产出它的那些 cell。 |
| [`test_governance.py`](test_governance.py) | 用测试写出来的仓库治理。每一个安全扫描与发布用的 action 都固定到具体 commit，secret 扫描用一个校验和固定的二进制扫全部历史，Dependabot 盯着 hook 与 workflow action。 |
| [`test_harness_characterization.py`](test_harness_characterization.py) | 一份特征化 golden，因此它刻意记录的是当前行为，已知 bug 也照记不误，并且标注出来。重新生成 golden 必须是一个显式动作——正是这一点拦得住无声的漂移。 |
| [`test_harness_contract.py`](test_harness_contract.py) | harness 自身：场景 schema、脚本化 provider、故障时刻表，以及一个保留事件顺序、而不是排序的规范化器。声明了却从未触发的故障，会让这个场景判失败。 |
| [`test_host_completion_service.py`](test_host_completion_service.py) | `host.submit_output`，Python Cell 唯一拥有的完成信号。schema 失败是软失败，且不得覆盖掉此前已有的完成；完成要点的过去式检查，除英文外也接受中日韩的表达。 |
| [`test_host_contract.py`](test_host_contract.py) | worker 到 Host 的 wire：注入的门面、单键形式的软错误、camelCase 编解码。最后那个测试最有用——SDK 能调的每一个 `host.*` 方法都必须有对应的分发路由，这样新能力就不会接了一半就发出去。 |
| [`test_host_credentials_service.py`](test_host_credentials_service.py) | 会话本地的 credential 引用。秘密本身从不返回；lease 绑定到单个动作并会过期；轮换一个 credential 会吊销挂在它上面的所有 lease；replay 排除了全部 credential 方法与取值。 |
| [`test_host_data_service.py`](test_host_data_service.py) | `host.query` 以及它周边的数据查询。只读 SQL 在碰到 Store 之前就被强制住，血缘图有界，携带不可信 ID 的 Artifact 标记会被拒绝。 |
| [`test_host_delegation_service.py`](test_host_delegation_service.py) | `host.delegate` 的 Host 这一侧。注入 profile 时不许改动调用方自己传进来的参数；delegate 与 steering 的来源是调用时解析的，而不是构造时就捕获死的。 |
| [`test_host_endpoint_service.py`](test_host_endpoint_service.py) | 托管 endpoint 的 create、status、request 与 close。把一个已有的本地注册重新指到新端口需要审批；空闲端口扫描会关掉它开的每一个 socket；投影里不带秘密。 |
| [`test_host_llm_service.py`](test_host_llm_service.py) | 内核里的 `host.llm`。批量调用保序，并把并发压在请求的上限内；其中一项失败仍然是硬失败，不会被软化成一个空回复。 |
| [`test_host_mcp_service.py`](test_host_mcp_service.py) | Host 上的 MCP：先按 ID 再按精确名字查 connector、Tool 发现与调用路由、资源与 prompt 读取，以及它们各自使用的软错误契约。被停用的 connector 在每一个入口都会被拒，而不只是在列表里不出现。 |
| [`test_host_progress_service.py`](test_host_progress_service.py) | 会话 todo，以及针对已批准 plan 的进度推进。plan 的改动先提交，事件 sink 才跑；sink 失败是尽力而为，它撤不回一个已经推进过的步骤。 |
| [`test_host_remote_capability_service.py`](test_host_remote_capability_service.py) | 远程 capability 探针。校验在 SSH 启动之前就软失败；传输失败绝不写注册表；只有探测真的成功了，注册表才会更新。 |
| [`test_host_remote_science_service.py`](test_host_remote_science_service.py) | 远程折叠与突变打分，禁止编造结果的策略就是这里的全部要点。远端结果不完整或解析不了时，给出的是精确的诊断信息，绝不是一个看起来像模像样的数字。溯源信息按尽力而为解析，并按身份逐条排空。 |
| [`test_host_skill_service.py`](test_host_skill_service.py) | Host 的 Skill 服务：路径约束、只读的内置根目录、sidecar 闸门，以及 publish 或 delete 之后会刷新的目录。用户 Skill 抢不走内置 Skill 声明的名字。 |
| [`test_host_workspace_service.py`](test_host_workspace_service.py) | 限定在工作区内的 Host 文件访问。`resolve` 会拒掉父目录、绝对路径与符号链接这三种逃逸；`glob` 与 `grep` 会把 secret 文件从原本正常的结果里滤掉。 |
| [`test_jupyter_adapter.py`](test_jupyter_adapter.py) | 可选的 Jupyter 路径——正因为它是可选的，这个模块有一半在讲它不存在时会怎样。没装 `ipykernel` 也要能描述并导出 KernelSpec；依赖缺失或装坏了，bridge 要报出来而不是崩掉。bridge 本身是拿真实的加固 Python worker 跑的。 |
| [`test_kernel.py`](test_kernel.py) | 真实的 Python worker，按它实际的协议驱动。命名空间跨 Cell 存活、stdout 与 stderr 的归属、`error_lineno`、用量记账、必须能返回的 mid-cell Host RPC、wire 上限与有界的失步恢复、SIGINT 与用户自己抛的 `KeyboardInterrupt` 之别，还有那几把可能死锁的锁。改动内核协议之后请重跑这一个。 |
| [`test_kernel_generation_storage.py`](test_kernel_generation_storage.py) | 内核 generation 与 attempt 落在哪里。启动时的对账只能放弃上一个更老的 daemon 留下的 generation，绝不能碰活着的那个；generation 的审计记录也不能从 `host.query` 里读到。 |
| [`test_kernel_generation_supervisor.py`](test_kernel_generation_supervisor.py) | 在 supervisor 的整数计数之上再叠一层持久 UUID 身份。整数继续充当 ABA 防护，UUID 才是 lease 与状态投影绑定的对象，好让被替换掉的 worker 能被 fencing 挡住。 |
| [`test_kernel_recovery.py`](test_kernel_recovery.py) | 重建一个已经死掉的内核。恢复在隔离的候选内核里进行，只有重建、replay 与校验全部通过才发布。带外部副作用的步骤根本不 replay；部分恢复会保留旧的 generation，而不是把新的据为己有。 |
| [`test_kernel_sandbox.py`](test_kernel_sandbox.py) | Seatbelt 与 bubblewrap 的命令是怎么拼出来的，以及各个模式的行为。`auto` 会在带可见告警的前提下退化成没有沙箱；`enforce` 在后端缺失或自检失败时失败即拒绝；无效的模式绝不会被悄悄降级成更弱的那个。 |
| [`test_kernel_supervisor.py`](test_kernel_supervisor.py) | 不对底下协议作任何假设的 worker 生命周期。反复出现的主题是“精确”：过期的 lease 中断不了、杀不掉、重启不了、也放弃不了一个新的 worker；一个已死的替身会被拒绝，而不会连累健康的当前 worker。 |
| [`test_lazy_kernel.py`](test_lazy_kernel.py) | 关于 CLI 一次性内核所有权的四个测试。从不跑代码的上下文就从不创建 worker；bootstrap 失败也不会把一个坏掉的 worker 发布出去。 |
| [`test_llm_anthropic_tool_calls.py`](test_llm_anthropic_tool_calls.py) | Anthropic Messages 的 Tool 调用走一轮编解码往返。并行的结果必须回到同一条相邻的 user 消息里——朴素地重建历史，恰恰就错在这一点上。 |
| [`test_llm_capabilities.py`](test_llm_capabilities.py) | Provider 的 capability 目录与 token 记账。用量规范化能容忍缺失或离谱的计数器；成本只由显式配置的价格算出，绝不靠猜。 |
| [`test_llm_gemini_tool_calls.py`](test_llm_gemini_tool_calls.py) | Gemini `generateContent`，以及它别扭的那个角落。wire 没给调用 ID 时会生成一个稳定的本地 ID，但这个 ID 绝不能回放给 Gemini；不透明的 part metadata（包括签名）必须原样回放在原来的 part 上。 |
| [`test_llm_openai_tool_calls.py`](test_llm_openai_tool_calls.py) | OpenAI Chat，包括流式那条路。交错的 Tool 调用能跨 SSE 增量无损聚合；缺终止事件的半截调用会被拒绝，而不是执行一半；错误事件也不会被误当成一次空的成功。 |
| [`test_llm_providers.py`](test_llm_providers.py) | 把网络整个换掉之后的标准库传输层，于是 wire 选择、配置解析、URL、鉴权头与图像转换都能被精确断言。loopback 端点可以不带 API key 就跑，但它不会仅仅因为说同一种 wire，就继承厂商的 Tool 支持。 |
| [`test_llm_responses_tool_calls.py`](test_llm_responses_tool_calls.py) | OpenAI Responses 这条 wire：output item 被保留、并在 function 结果之前回放；不完整或没有终止的 Tool 流会被拒绝；规范的调用历史能在不残留任何 wire 状态的情况下重建出来。 |
| [`test_local_model_discovery.py`](test_local_model_discovery.py) | 发现本地模型端点，这是一个 SSRF 面。候选 URL 必须是字面上的 loopback，opener 拒绝重定向；不可达、非法或超大的探测结果一律不做任何事。 |
| [`test_marker_policy.py`](test_marker_policy.py) | 两个测试守着离线契约。external、network、live-LLM、GPU、SSH、Docker、browser、lab 这些需要显式开启的标记，默认全部排除在外；改动默认的排除表达式，也没法悄悄把真实测试放回来。 |
| [`test_mcp_client.py`](test_mcp_client.py) | 离线的 MCP：JSON-RPC 分帧、stdio 生命周期、请求配对、超时。真正要紧的边界是子进程环境——它按严格的允许名单重建，所以连一个 connector 也没法把 daemon 环境里的秘密顺手交给子进程。 |
| [`test_mcp_control_tools.py`](test_mcp_control_tools.py) | MCP 的原生 Tool。列目录和读内容是两套策略；prompt 参数会被重新校验成字符串；从 connector 回来的内容在给 Agent 看之前先过注入筛查。 |
| [`test_memory_repository.py`](test_memory_repository.py) | 长期记忆：过滤条件跨 `Store` 边界保持不变、遗留的默认分类，以及删除 project 时级联删掉它的记忆。 |
| [`test_metadata_repositories.py`](test_metadata_repositories.py) | 那几个小仓储——笔记、文件夹、动态 endpoint、compaction 归档。真正有牙的是 host call 日志：它在提交之前会做清洗、跳过和截断。 |
| [`test_methodology_skills.py`](test_methodology_skills.py) | 关于纯方法学内置 Skill 的三个测试：以只读方式被发现、能被取回，其中一个还在 Agent 循环里被真正用了一次。 |
| [`test_mineral_spectra_analysis.py`](test_mineral_spectra_analysis.py) | 矿物谱图 Skill，以及它守着的那个设计。流水线是盲的：报告是在不知道真值的情况下产出的，对真值的评估是循环之后一个显式的步骤，绝不掺进循环里。那些纯 helper 在没装科学栈时也能 import。 |
| [`test_model_catalog.py`](test_model_catalog.py) | 可以扩展的 provider 与模型目录。自定义 provider 要先通过校验才能路由；带连字符的 provider 名字仍然能得到一个对 shell 安全的环境变量前缀；内置预设保持不可变，用户预设围着它们加。 |
| [`test_native_tools.py`](test_native_tools.py) | 关于原生 Tool 声明的四个测试。名字必须落在四家 provider 限制的交集里；每次调用拿到的都是一份独立的 schema 拷贝；shell 与完成信号从不作为原生 Tool 声明出去。 |
| [`test_notebook_export.py`](test_notebook_export.py) | 关于导出的三个测试。Python 与 R 各自成为一份独立的只读 Notebook；导出包是确定性的，manifest 校验和对得上；未知的 Notebook 语言会被拒绝。 |
| [`test_onboarding.py`](test_onboarding.py) | 首次运行的 provider 配置，这里的写入必须要么全成、要么全不成。无效的 API key 不能让模型设置只写了一半；切换 provider 会丢掉上一个 provider 的 key，而不是沿用它的默认值；任何响应都不会把秘密带回来。 |
| [`test_orchestration_skills.py`](test_orchestration_skills.py) | 运行时编排类 Skill，跑在真实 worker 与真实 dispatcher 上而不是 fake 上：内核里的 `host` 门面、Skill 自定义、自省查询、托管 endpoint、算力与环境搭建，以及它们留下的审计痕迹。 |
| [`test_permission_repository.py`](test_permission_repository.py) | 落库的权限规则与权限请求。有两点要知道：一个请求会原子地绑到它的 Action Ledger group 上，group 不存在时整笔回滚；重启作用域的一次性授权是原子消费的，因此没法被重放。 |
| [`test_permissions.py`](test_permissions.py) | 权限闸门本身。scope 优先级和模式具体度都在这儿，但同样在这儿的还有那块没有例外的：读 secret 文件或 secret 环境变量会被拒绝，哪怕会话级规则写着允许；无人值守的 broker 在运维没有显式放行时失败即拒绝。 |
| [`test_plan.py`](test_plan.py) | plan 的提取与生命周期。模型回了散文而不是 JSON 时，仍然能靠散文回退路径得到一个 plan；批准一个 plan 会把它当成普通的一轮跑起来。 |
| [`test_plan_repository.py`](test_plan_repository.py) | 三个测试，证明 `PlanRepository` 透过 `Store` 门面的表现完全一致，畸形 JSON 也一样：它会回退，而不是抛出来。 |
| [`test_plan_service.py`](test_plan_service.py) | plan 服务这一层边界。定稿会复用草稿那一行和它的 Artifact，而不是再造一个；Artifact 写失败时它也扛得住，不会把 plan 丢掉。 |
| [`test_protein_mutation_enhancement_skill.py`](test_protein_mutation_enhancement_skill.py) | 蛋白突变 Skill 的纯 helper。枚举、排序与选择轮次都是确定性的；错误路径被明确检查：野生型残基对不上的突变、越界的位点、没有位点的变体，一律抛错，而不是被悄悄打了分。 |
| [`test_provenance_paths.py`](test_provenance_paths.py) | worker 内部的文件系统身份——溯源正是在这里悄悄断掉的。一个 Cell 若在打开文件和写入文件之间换了工作目录，仍然必须给出同一个规范路径；最后那个测试拿真实内核验证了这一点。 |
| [`test_public_api_contract.py`](test_public_api_contract.py) | 公开的 import 表面，钉住它是为了让后端还能继续搬家：包版本、构造函数的参数名、`run_task` 的调用约定，以及 Host 与 server 两个门面。它钉住的只是调用方看得见的东西，仅此而已。 |
| [`test_r_kernel.py`](test_r_kernel.py) | R 内核。大部分测试跑在一个说着同样协议的假 `Rscript` 上，因此不需要装 R；少数几个在本机有 R 时会用真的 R。`sh -c` 的 fd 交换是被直接断言的，因为协议帧跑错描述符正是这条通道的失效方式；子进程刷出的大量 stderr 也不许把读取端拖进死锁。 |
| [`test_recovery_recipe.py`](test_recovery_recipe.py) | 关于恢复配方的三个测试。它会编译出一个依赖闭合的 Python 或 R 命名空间；把外部状态和解不出来的部分留成手工步骤交给人，而不是替人 replay；并且只有记录的源码哈希仍然匹配，它才肯执行。 |
| [`test_release_gates.py`](test_release_gates.py) | 发布之前跑的那些门禁。secret 扫描会报出命中却不回显具体值；发布包校验器会拒掉一个引入了第三方核心依赖的构建——那正是整个项目立身的约束。 |
| [`test_remote_capability_probe.py`](test_remote_capability_probe.py) | SSH 上的命令注入，一项输入一项输入地封死。shell 字符串在 SSH 启动前就被拒；含糊或类型不对的 spec 根本到不了 SSH；而且哪怕被拒绝，这次探测仍然会投影出一条活动记录，让这次尝试可见。 |
| [`test_remote_compute_control_tools.py`](test_remote_compute_control_tools.py) | 远程算力这几个 Tool。提交任务要审批，取消与关闭不用——因为哪怕正在拒绝新工作，清理这条路也必须留着能走。 |
| [`test_renderer_registry.py`](test_renderer_registry.py) | 三个测试：renderer 的选择是确定性的，重复的 renderer ID 会被拒绝，公开 descriptor 里的版本与溯源始终绑在产出它的那个 renderer 上。 |
| [`test_retrosynthesis_evidence_regressions.py`](test_retrosynthesis_evidence_regressions.py) | 逆合成路线的证据检索。这里每个测试都是为了不让“候选”冒充“已核实”：只有候选来源时，coverage 不能被抬过 30；显式的空记录包装不能摇身变成伪证据；没有真正核实过，verified 就一直是 false。检索里的搜索或抓取失败会被隔离，不能把已经拿回来的来源一起拖没。 |
| [`test_retrosynthesis_planning.py`](test_retrosynthesis_planning.py) | 逆合成 Skill，其中两处风险占了大半篇幅。LLM 标注在模型回了解析不了的东西时必须带告警地降级，而不是把整条路线一起拖垮。渲染出的 HTML 必须转义图谱 payload，因为分子名字来自外部。 |
| [`test_retrosynthesis_scoring_regressions.py`](test_retrosynthesis_scoring_regressions.py) | 路线打分与渲染，麻烦的 bug 都藏在这儿。重排之后，标注不能还指着它原来描述的那条路线。库存标志里的字符串 `"false"` 永远不算“有货”。归一化后的决策权重要精确加到 100。已解出但没有反应步骤的路线，不该因为根本用不上的证据被扣分。 |
| [`test_revert_projection.py`](test_revert_projection.py) | 关于 Revert 与 Undo 历史的三个测试：它只追加、且扛得过重启；兄弟分支之间的消息从不混在一起；遗留数据会被回填到规范的根分支。 |
| [`test_review.py`](test_review.py) | 经由网关走完整链路的 Reviewer。它的结论是受约束的：schema 不合法时报为“不可用”，而不是被硬掰成通过；provider 出错也不致命——一次失败的评审不该让被评审的那一轮跟着失败。 |
| [`test_review_service.py`](test_review_service.py) | 同一个 Reviewer，但作为网关之外的服务，这里的实质是并发。被取消的 provider 调用仍然会在自己的线程上跑完，同时挡住重复提交；线程启动失败时，它会把先前已经占下的预留清理掉。 |
| [`test_science_connectors.py`](test_science_connectors.py) | 每一个科学数据库 connector，全部跑在离线 HTTP fake 上。它们共用同一份记录 schema；从不用用户输入拼出任意 URL；上游返回 204、或返回一个从未承诺过的 schema 时，得到的是有界的 connector 错误，而不是崩溃。 |
| [`test_science_control_tools.py`](test_science_control_tools.py) | 关于科学检索两个表面（原生的与内核内的）的三个测试：两个扁平且做过 schema 检查的注册表条目、一个不用网络也能跑的目录，以及只编码顶层字段的 Host wire。 |
| [`test_sdk_compute.py`](test_sdk_compute.py) | worker 侧的 `host.compute` 命名空间：provider、实例、传输、attach、清理。带安全锋刃的是路径处理——本地路径按允许名单做相对化，绝对路径直接拒绝。 |
| [`test_security.py`](test_security.py) | 纵深防御的几层，LLM 全程被 mock。快路径和失败即放行的那几处，要和拦截一样仔细读：常规代码根本不进分类器，而注入筛查在没有配置 key 时是失败即放行的。不失败即放行的是 query 的拒绝名单——即使用拆分标识符绕，它也把 `host.query` 挡在设置、connector 与秘密表之外。 |
| [`test_server_agent_run.py`](test_server_agent_run.py) | 纯引擎外围的 Web 适配器，其中大半在讲用户不该看到什么。fence 要跨增量边界一直藏住；代码草稿是临时的、可丢弃的；只用于完成的 Cell 在执行之前先把草稿清掉。旧版的 fenced Tool 调用仍然会执行，并以一条用户 observation 的形式回来。 |
| [`test_server_completions.py`](test_server_completions.py) | 一轮结束时给用户看的东西。完成消息由真实的 Artifact 增量和实际输出渲染出来，绝不依赖隐藏的推理内容；只跑了代码的一轮，会拿到一个如实的状态，而不是被暗示成成功。 |
| [`test_server_execution_coordinator.py`](test_server_execution_coordinator.py) | Web 侧的准入与取消。没有确切的执行与持有者身份，取消就失败即拒绝；它绝不能退化成“把当下在跑的随便取消掉”。取消排队中的 REPL 也碰不到正在跑的 Agent。 |
| [`test_session_branching.py`](test_session_branching.py) | 关于 checkpoint、fork、revert 与 undo 的四个测试。revert 预览会报告所有状态维度，且一个字都不写盘；工作区被外部改动过时，revert 会被挡下并记录在案，而不是把改动覆盖掉。 |
| [`test_session_control_tools.py`](test_session_control_tools.py) | 当前会话与 capability 这两类 Tool。capability 搜索始终可见，并会激活会话这一组；审批输入在经过 dispatcher 时会被脱敏；当它需要的 domain 不感知文件系统时，写操作失败即拒绝。 |
| [`test_session_deletion.py`](test_session_deletion.py) | 删掉一个会话或 project，同时不能顺手删掉别人的数据。内容寻址存储是共享的，所以它的 GC 要等 checkpoint 的引用发布之后再动；快照清理绝不跟着符号链接走出这棵树；feedback 删除会转义 LIKE 的元字符。 |
| [`test_session_domain_service.py`](test_session_domain_service.py) | 快照、checkpoint、游标 fork、分支、Timeline、导出、renderer 与恢复背后是同一份组合，全都经由 `Store` 门面到达。游标 checkpoint 失败会被审计下来，且不会声称 fork 成功了。 |
| [`test_session_package.py`](test_session_package.py) | 会话的导出与导入，也就意味着一整个不可信压缩包的攻击面。路径穿越、符号链接、压缩比异常、重复或悬空的身份、夹带的秘密，全部拒绝；任何形似 replay hook 的东西都会被隔离，直到一次确认过的全新重启才解锁；导入中途出错时，数据库、工作区、环境与 CAS 会被一起回滚。 |
| [`test_session_recovery.py`](test_session_recovery.py) | 空闲清扫器。释放一个会话之前，每一个阻断条件都必须解除，而正在进行的恢复就是其中之一——清扫器不能把内核从恢复脚下抽走。TTL 解析、持久化的活动记录与启动时的对账也在这里。 |
| [`test_session_snapshots.py`](test_session_snapshots.py) | 内容寻址存储里的工作区快照。做快照时会排除秘密、符号链接与超大文件；恢复时拒绝盖在被外部改动过的工作区上，也不动未跟踪的文件；分支头部的移动由 compare-and-swap 守着。 |
| [`test_session_title_service.py`](test_session_title_service.py) | 后台生成的会话标题。它跑在另一个线程上，所以测试大多是竞态：它不能覆盖掉用户刚刚打上去的标题；如果后台工作启动期间 `Store` 被换掉了，它得解析到新的那个。 |
| [`test_setup_environments.py`](test_setup_environments.py) | 内置的 Python/R 环境 manifest，全程不碰 conda 就能验。两条承诺撑着这个模块：不显式要求更新，就绝不改动已有环境；更新只作用于探测到的 prefix，而且不做 prune，你自己装的包能活下来。它还钉住了 Python manifest 绝不用 pip 装 NumPy-1 ABI 的 RDKit，以及 `--only` 和 `--profile` 不能同时给。 |
| [`test_session_tool_catalog.py`](test_session_tool_catalog.py) | 关于会话 Tool 组合的两个测试。Host 先给 Dynamic Tool 的生命周期把住闸门，代理才在同一份目录里执行；渐进披露保留核心组，只激活相关的那几组。 |
| [`test_settings_repository.py`](test_settings_repository.py) | 设置、模型 profile 与 feedback。模型 profile 的写操作由共享锁串行化；权限播种也走这同一个仓储——这正是两者被放在一起测的原因。 |
| [`test_skill_customization_service.py`](test_skill_customization_service.py) | 在 Web Customize 面板里写出来的用户 Skill。有意思的是路径处理：删 `foo` 不能把 `foo-bar` 一起带走；目录和文档都不能是指向用户根目录之外的符号链接。内置 Skill 保持只读，并在重名时胜出。 |
| [`test_skill_product_surface.py`](test_skill_product_surface.py) | 版本化的个人与 project Skill 作为产品表面：控制类 Tool、SDK 调用，以及 HTTP 的历史与回滚路由。回滚被限定在当前 project 内并留下审计，跨 project 的版本 scope 会被拒。 |
| [`test_skill_sidecar_recovery.py`](test_skill_sidecar_recovery.py) | 两个测试，跑在真实 worker 上。只有真的加载成功的 sidecar 才会被冻进 generation manifest；被篡改的 sidecar 记录会把这个 generation 标成不可恢复，而不是照样 replay。 |
| [`test_skill_versions.py`](test_skill_versions.py) | 内容寻址的 Skill 安装、升级、发布、回滚与删除。有两道防线值得知道：内置 Skill 永远不能成为安装、发布或回滚的目标；对自己 sidecar 字节撒谎的 manifest，会在激活之前被拒。 |
| [`test_skills.py`](test_skills.py) | Skill loader。其中出人意料的一大块是 YAML frontmatter 解析——折叠标量、字面块、chomping 指示符、行内注释——因为一个漏进摘要里的 `>`，正是渐进披露开始输出乱码的起点。每个内置 Skill 里的 import 提示也被当成真正的 Python 检查了一遍。 |
| [`test_store.py`](test_store.py) | schema、迁移，以及调用方今天就依赖的那些行结构。真正会咬人的是 Artifact 的合并规则：临时版本必须合并而不是复制出一个新的；血缘写失败时，版本要跟着一起回滚。 |
| [`test_structured_finalize.py`](test_structured_finalize.py) | `finalize_response` 归引擎所有，不属于 Tool 注册表，而且只有它单独出现时才算完成一次运行。含有它的混合批次不构成完成；无效的调用会变成一条规范的错误结果，模型看得见，也能重来。 |
| [`test_tool_schema.py`](test_tool_schema.py) | 用纯标准库实现的校验器，负责 Host 接受的那个 JSON Schema 子集。嵌套路径和约束违规是一起报出来的，而不是一次报一个；provider 的 strict 模式要求 required 对象递归封闭。 |
| [`test_tools.py`](test_tools.py) | 控制类 Tool 的那些类，以及旧版的 fenced Tool 解析器。解析这部分测试的存在就是为了不让什么东西被误执行：嵌在 Python cell 里、嵌在更长的 fence 里、嵌在波浪号 fence 里的 Tool fence，都不算 Tool 调用。而 `execute` 无论参数多么恶意，都不许抛出来。 |
| [`test_variable_inspector_service.py`](test_variable_inspector_service.py) | 三个测试，把变量检查限制得很窄。它从不进入 Cell 状态；读任何东西之前先检查语言和 lease；协议出错时只给出笼统信息，而不是内核内部细节。 |
| [`test_webtools.py`](test_webtools.py) | HTML 转 Markdown 与几个搜索后端，跑在离线 fixture 上。前三个测试是围着同一个 bug 织的回归网：arXiv 的摘要页，摘要、作者和标题被转换器整个丢掉了。 |
| [`test_webui_static_contract.py`](test_webui_static_contract.py) | 把静态前端当源码读，不开浏览器也不起网关。它抓的是别处抓不到的东西——引用了却不存在的资源、重复的 DOM ID、有名字却没有 SVG 的图标——同时钉住 UI 自己作出的那些安全声明，比如提升后的 Markdown 只允许安全的位图 data 图片、Timeline 是一个按允许名单做的投影。它证明不了 UI 能用；那是 `browser_smoke.mjs` 的活。 |
| [`test_workbench_state_service.py`](test_workbench_state_service.py) | Context 与 Security 两个面板。安全投影是刻意往小了说的：worker 还没启动时，它绝不宣称沙箱是生效的；Python 与 R 的说法不一致时，它报两者中更弱的那个。 |
| [`test_worker_runtime_alias.py`](test_worker_runtime_alias.py) | 六个测试，证明 `openai4s_worker_runtime` 只是一次 re-export：`__all__` 相同、两个名字下是同一批对象、没有影子子模块，也没有自己的入口点。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| `fixtures/` | 字节敏感的捕获 HTML、假解释器 helper 与 renderer 样本。这棵子树刻意不参与目录 README 生成，也不参与自动格式化。 |

## 如何选择位置

聚焦的回归断言放在这里。可复用的脚本化场景、fake provider、规范化后的 trajectory、计分 eval 与经过审阅的 golden，放进 [`../harness/`](../harness/) 这一层。两层的默认检查都保持离线，除非某个入口被显式标记，并作为需要显式选择开启的 smoke 单独调用。
