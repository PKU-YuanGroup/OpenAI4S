# 原生控制工具

[English](README.md)

Agent 用来编排工作、申请权限的那批供应商原生 JSON 工具都声明在这里。控制 catalog 本身已经实现；每个工具指向的服务，各自保留 Implemented、Partial 或 Prototype 状态。shell 执行、科学计算和 `submit_output` 有意留在本包之外，它们都不是原生工具。

## 在架构中的位置

模型回复里出现原生调用时，外层循环会先把有序的工具批次跑完，再去看代码 fence。每个具体的 [`Tool`](./base.py) 自带 JSON schema、审批行为、副作用类别、资源 key、输出策略，以及一个聚焦的 `execute()`。模型发起的调用先经过 `Tool.invoke()` 和 [`HostDispatcher`](../host_dispatch.py)：权限、审批、审计、注入筛查和活动事件先跑一遍，之后受保护的适配器才会碰到 `execute()`。

[`registry.py`](./registry.py) 是唯一实例化内置工具类的地方。[`catalog.py`](./catalog.py) 按 session 构建渐进披露视图，并在其上叠加隔离的动态代理，全局内置注册表不受影响。供应商 schema 只是生成时的提示；dispatch 之前，[`schema.py`](./schema.py) 会把所支持的契约再强制执行一遍。

以工作区为后端的文件工具共用一条 capability 检查过的边界。在 POSIX
上，它们用 `dir_fd`/openat 风格的操作和 `O_NOFOLLOW` 遍历固定的父目录，
通过校验过的同一个描述符消费读内容，拒绝多硬链接的普通文件，并在该
固定父目录内发布暂存写入。原生 Windows 缺少所需 capability，因此这些
操作会 fail closed，而不会退回到路径名 `os.replace` 或 check-then-open。
这抵御的是并发的用户态命名空间替换，不是已被攻陷的内核或任意内核
文件系统语义变化。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 公共兼容 facade：重新导出工具类、注册表 helper、native spec、schema helper 和批次上限。 |
| [`artifacts.py`](./artifacts.py) | Artifact 相关工具：列出 Artifact、把已有文件注册进来、查询精确的元数据或精确的版本。恢复历史版本需要审批。 |
| [`background.py`](./background.py) | 对独立的后台 Python Cell worker 做 submit/list/peek/interrupt。这里只是任务编排，不是 shell runtime。 |
| [`base.py`](./base.py) | 不可变的类式 `Tool` 契约：元数据、Host 调用边界、schema 校验、审批目标、资源 key 和 provider-strict 兼容性。如果一个工具的写入、只读或副作用声明取决于运行时的特性开关，就通过 `writes_files_for()`、`read_only_for()`、`side_effect_class_for()` 作答，它们默认回退到静态属性；这样 Artifact 捕获、冲突调度和审计读到的，才是这次调用真正会产生的结果。 |
| [`capabilities.py`](./capabilities.py) | 声明 `search_capabilities`：搜索隐藏的工具组，并为当前 session 激活命中的那些。激活只增不减。 |
| [`catalog.py`](./catalog.py) | 把内置工具和生效的动态代理组合成一份 session catalog，将工具划入渐进披露分组，并产出当前活动的 native spec 与 prompt 元数据。 |
| [`content_search.py`](./content_search.py) | 在受限工作区内做有界的正则内容搜索。`include` 与不加过滤的默认行为一样是递归的：`Path.glob("*.py")` 只匹配直属子项，于是照着 schema 自己给的例子传参，反而让搜索比不过滤时更**窄**，而且它一声不吭。有四条界限可能让它提前停下——命中数上限、候选文件数上限、目录遍历本身，以及一个大到读不完的文件——是哪一条生效会写进返回结果，因为一个不加说明的空结果读起来就是「这棵树里没有」。 |
| [`contexts.py`](./contexts.py) | 定义具体工具依赖的几类狭窄运行时 protocol：工作区、环境、单一用途的科学检索结果写入器，以及通用控制。工具只有在 Host 的策略检查通过之后才拿得到它们。实时适配器上还挂着主搜索入口和那个结果写入器，它们只是普通方法，没有对应的 `_m_*` dispatcher handler，因此在内核 Host 线路上根本寻址不到，也就绕不开 `web_search`/`science_search` 自身的权限、审计与不可信输出信封。 |
| [`data.py`](./data.py) | 只读访问 Store：受保护的 schema 与 query、frame 浏览，以及有界的 Artifact 血缘遍历。 |
| [`delegation.py`](./delegation.py) | 启动子 Agent，列出并收集直属子 Agent，按精确 ID 停止某一个，或者给运行中的子 Agent 发送引导消息。 |
| [`dynamic.py`](./dynamic.py) | 校验 session 内编写的 Python 工具源码与 manifest，再把每一次冒烟测试、每一次调用都放进全新的 `python -I -S` worker 执行：环境严格无 secret，OS 沙箱强制启用。session/project/global 三级版本通过可信代理解析。 |
| [`dynamic_control.py`](./dynamic_control.py) | Dynamic Tool 的人工治理生命周期：define、list、promote、version-list、activate、rollback。 |
| [`dynamic_scopes.py`](./dynamic_scopes.py) | 保存内容寻址的 project/global Dynamic Tool manifest，以及只追加的激活历史。它不编译、也不执行模型编写的代码。 |
| [`edit.py`](./edit.py) | 工作区内的精确字符串编辑，带一道静态 precheck：退化的编辑请求在申请审批之前就被挡掉。源文件从同一个已经校验、no-follow 的描述符读取；重写过程是流式的，每次只持有一个 chunk，写进旁边一个暂存文件——并在 chunk 边界上带过 `len(old_string) - 1` 个字符，跨边界的匹配才不会漏掉。决定这次编辑是否放行的匹配计数要到最后才知道，所以被拒或不唯一的编辑会丢弃暂存文件，原文件分毫未动。最后通过固定父目录内的描述符相对 rename 原子换入。旧版 `edit_file` 的兼容查找仍然保留。 |
| [`env.py`](./env.py) | 环境 list/use/create 三个工具类和实例的兼容 facade。 |
| [`env_create.py`](./env_create.py) | 通过内核 preinstall 服务安装依赖包。 |
| [`env_list.py`](./env_list.py) | 发现预构建好的环境，并可选地对比它们的依赖包覆盖情况。 |
| [`env_use.py`](./env_use.py) | 排队切换到指定的 Python 或 R 环境，在下一个科学 Cell 生效。会话拒绝这次登记时，返回的是错误：它以前会被报成一次成功的切换、只在旁边附一句说明，而下一个 Cell 照旧跑在原来的环境里。 |
| [`fs.py`](./fs.py) | 目录列表与文本文件读写工具的兼容 facade。 |
| [`glob_files.py`](./glob_files.py) | 按 glob 在工作区里找文件，结果中会剔除形似 credential 的路径——文件名与 credential 目录一并剔除。生成器逐条消费，只经由 `BoundedSelection` 保留最小的那一批，于是回答一个「一千条」的问题，不必为一个百万文件的目录留住一百万条路径；遍历本身也受条目数和秒数两个预算约束，而不只是约束留下来的东西。`count` 就是实际返回的数量——它以前是切片**之前**的总数，却印在切片后的列表旁边，而且和同一族里 `content_search` 的 `count` 含义相左；原先那份信息现在由 `total_count`、`dropped` 和 `truncated` 承担。 |
| [`list_directory.py`](./list_directory.py) | 列出一个工作区目录，且只限于这个目录之内——沿用 `glob_files` 的条目上限和同一套截断计数，于是两个工具在同一处截断、也用同一种方式说明。它原先根本没有上限，还要为每个条目付一次 `stat()` 和一个 dict。 |
| [`mcp.py`](./mcp.py) | MCP 的 server/tool/resource/prompt 发现，以及工具调用和 resource/prompt 读取。外部服务器返回的内容不可信，统一在 Host 边界筛查。`mcp_tools` 比这个控制工具出现得更早，所以 `list_mcp_tools` 在 `execute()` 和 `resource_keys()` 里都接受 dispatcher 可能递过来的两种形状：原生的对象参数，以及内核 SDK `host.mcp.tools(server)` 发来的裸位置字符串。 |
| [`model_assets.py`](./model_assets.py) | 在精确路径审批后，把用户已有的本地 checkpoint 导入受限的会话工作区，流式计算 SHA-256，并明确保持 staged、尚未 admitted；只有真实推理 canary 成功后才能正式使用。 |
| [`native.py`](./native.py) | 把已声明的工具转成可移植、供应商中立的 `ToolSpec` 元数据，并校验函数名在每一个受支持的供应商上都合法。 |
| [`network_access.py`](./network_access.py) | 请求人工批准，把 Host 掌管的出站域名策略放开一个域名。 |
| [`progress.py`](./progress.py) | todo 的读写、已批准 plan 的读取与步骤更新，以及受约束的 review status 控制。 |
| [`read_text_file.py`](./read_text_file.py) | 工作区内有界的 UTF-8 行窗口读取，包含读到二进制文件时的响应契约。两条界限，因为 `limit` 数的是行数，对每行有多长只字未提：文件按字节预算流式读取，而不是整个读进来再切；留下的窗口另有一个字符上限。任何一条生效时，返回结果还会报出扫过的字节数与文件的真实大小，这样 `total_lines` 在它不再是总数时不会被当成总数。 |
| [`registry.py`](./registry.py) | 内置工具唯一的实例化处，按固定顺序创建。把一次调用解析到具体工具，做校验，按每轮上限有序执行一个批次，格式化结果，最后收束成一条有界的 observation。旧版的 fenced 工具 block 也在这里解析。 |
| [`remote_capabilities.py`](./remote_capabilities.py) | 查看远程 GPU capability 的状态。注册一个 capability 必须先通过结构化 probe，再经人工审批。另一半由 `accelerator_status` 单独汇报：本机 GPU、已配置的 SSH GPU 主机和模型后端就绪度是三种互相独立的状态，候选路由按固定的探测顺序列出但不替用户选定，而已注册的 SSH 主机只是持久化配置，不代表一次实时可达性探测。 |
| [`remote_compute.py`](./remote_compute.py) | 供应商中立的远程任务生命周期控制：submit/status/result/cancel/close。原生控制面已经实现，但通用远程计算仍是 Prototype 子系统。 |
| [`schema.py`](./schema.py) | 零依赖的 JSON Schema 子集，用于 definition 校验、参数强制、object schema 标准化和 provider-strict 检查。 |
| [`science.py`](./science.py) | 对受支持的公共科学数据库做标准化的 catalog 与 search 访问。开启 Stage 10 连接器之后，同一次 `science_search` 还会把结果写进工作区并打上标记，交给 Gateway 的可信原生捕获转成 Artifact；因此它只在这个开关下才把自己声明成工作区写入——走 `base.py` 里那套运行时感知的声明，因为一次会生成 Artifact 的检索，不能按只读来调度和审计。 |
| [`search.py`](./search.py) | glob 和内容搜索工具类、实例的兼容 facade。 |
| [`session.py`](./session.py) | session 状态、创建不可变 checkpoint、从一个精确游标 fork 出只读分支、非修改性的 revert 预览，以及待审批项检查。应用 revert 不在这里暴露。 |
| [`skills.py`](./skills.py) | 渐进式的 Skill 搜索与加载，外加 status/history，以及需要审批的版本 rollback。`list_skills` 是紧凑的控制面视图：精确总数、精选 Skill 的名字，以及每个内置合集一行 id/数量——合集只有在用 `collection` 显式点名时才展开，并按 `offset`/`next_offset` 分页；完整元数据仍留在 Cell 里的 `host.skills.list()`。输出预算是按内置文档的真实体量定的，不是继承来的：`load_skill` 的上限装得下最大的那份完整 `SKILL.md`；`search_skills` 则是把最长的几份 recipe 截短、各自附一个 `load_skill` 指针，而不是任由通用格式化器从 JSON 尾部把最后几条命中连名字一起删掉。 |
| [`taxonomy.py`](./taxonomy.py) | 稳定的副作用类别，以及规范化的 resource-key/workspace-target。审计事件记录它们，资源冲突调度也按它们比较。 |
| [`web.py`](./web.py) | Web 搜索/抓取工具类和实例的兼容 facade。 |
| [`web_download.py`](./web_download.py) | 把一个 URL 直接下载进会话工作区，用于 `web_fetch` 无法表示成文本的内容。两道闸在这里汇合：URL 这一侧沿用 `web_fetch` 的（必须开启网络、每一跳都过出网允许名单、私有/元数据地址一律拒绝），路径这一侧沿用 `write_file` 的（目标路径先相对工作区解析，越界在**发起请求之前**就被拒——这样被拒的路径不会顺带泄露那个 URL 是否可达）。字节上限在读取过程中生效。响应先落在 daemon 私有的临时目录里，完全在工作区命名空间之外；只有下载完整结束，内容才会被拷进经由固定父目录描述符打开的同目录暂存文件，而且这次拷贝会在发布之前重新核对字节数和 SHA-256。 |
| [`web_fetch.py`](./web_fetch.py) | 标准化单个 URL 的抓取与资源身份，同时保留 Host 的软失败行为。 |
| [`web_search.py`](./web_search.py) | 标准化实时 Web 搜索，同样保留 Host 的软失败行为。选哪个搜索提供方由 Host 侧决定：配置了 Agent Plan Key 就走豆包搜索，否则走内置的免密钥引擎；查询只是撞上豆包自己的长度／结果数上限时同样退回内置引擎——不能因为提供方的一个特有限制，就让这一轮什么结果都拿不到。 |
| [`write_file.py`](./write_file.py) | 在受限工作区里创建或覆盖一个 UTF-8 文件，并给这次写入打上标记，让 Web 控制工具边界能把它捕获成 Artifact。它通过已经校验的父目录描述符创建同目录暂存文件，再用描述符相对 rename 原子发布：`write_text` 是先截断后写入，中途被打断曾会在原本完整的文件位置留下一个写了一半的文件。暂存文件会继承目标原有的权限位，否则一次覆盖会悄悄把权限收紧。 |

## 新增或修改工具

- schema、副作用声明、权限目标、资源 key 和具体行为，都放在具名的 `Tool` 子类上；只通过 `registry.py:TOOL_TYPES` 实例化。绝不要在模块级别新建工具单例：兼容 facade 导出的那些小写名字都是对已注册实例的 `get_tool(...)` 查找，而一个类型正确的新实例，是注册表从未见过的工具，权限那一整套根本没有接到它身上。
- 模型来的输入绝不直接调用 `execute()`。要走 `invoke()`/`HostDispatcher`，否则安全与审计这一层就被绕过去了。
- schema 要在受支持的供应商之间保持可移植，并且在本地再强制校验一遍。`writes_files`、网络使用、危险操作和不可信输出都要如实标注。如果如实的答案取决于某个运行时特性开关，就重写 `writes_files_for()`/`read_only_for()`/`side_effect_class_for()`，而不是在两个静态答案里挑一个：开关不生效的每一次调用，捕获、调度和审计信的都是那个静态答案。
- 科学算法留在代码和 Skill 里，服务行为留在聚焦的 Host 模块里。原生工具本身应该保持为一个小的编排控制平面。
- 没有强制启用的 OS 沙箱时，Dynamic Tool 必须失败即拒绝。仅靠静态 AST 限制不构成隔离边界。
