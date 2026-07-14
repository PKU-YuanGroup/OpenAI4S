# 原生控制工具

[English](README.md)

Agent 用来编排工作、申请权限的那批供应商原生 JSON 工具都声明在这里。控制 catalog 本身已经实现；每个工具指向的服务，各自保留 Implemented、Partial 或 Prototype 状态。shell 执行、科学计算和 `submit_output` 有意留在本包之外，它们都不是原生工具。

## 在架构中的位置

模型回复里出现原生调用时，外层循环会先把有序的工具批次跑完，再去看代码 fence。每个具体的 [`Tool`](./base.py) 自带 JSON schema、审批行为、副作用类别、资源 key、输出策略，以及一个聚焦的 `execute()`。模型发起的调用先经过 `Tool.invoke()` 和 [`HostDispatcher`](../host_dispatch.py)：权限、审批、审计、注入筛查和活动事件先跑一遍，之后受保护的适配器才会碰到 `execute()`。

[`registry.py`](./registry.py) 是唯一实例化内置工具类的地方。[`catalog.py`](./catalog.py) 按 session 构建渐进披露视图，并在其上叠加隔离的动态代理，全局内置注册表不受影响。供应商 schema 只是生成时的提示；dispatch 之前，[`schema.py`](./schema.py) 会把所支持的契约再强制执行一遍。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 公共兼容 facade：重新导出工具类、注册表 helper、native spec、schema helper 和批次上限。 |
| [`artifacts.py`](./artifacts.py) | Artifact 相关工具：列出 Artifact、把已有文件注册进来、查询精确的元数据或精确的版本。恢复历史版本需要审批。 |
| [`background.py`](./background.py) | 对独立的后台 Python Cell worker 做 submit/list/peek/interrupt。这里只是任务编排，不是 shell runtime。 |
| [`base.py`](./base.py) | 不可变的类式 `Tool` 契约：元数据、Host 调用边界、schema 校验、审批目标、资源 key 和 provider-strict 兼容性。 |
| [`capabilities.py`](./capabilities.py) | 声明 `search_capabilities`：搜索隐藏的工具组，并为当前 session 激活命中的那些。激活只增不减。 |
| [`catalog.py`](./catalog.py) | 把内置工具和生效的动态代理组合成一份 session catalog，将工具划入渐进披露分组，并产出当前活动的 native spec 与 prompt 元数据。 |
| [`content_search.py`](./content_search.py) | 在受限工作区内做有界的正则内容搜索。 |
| [`contexts.py`](./contexts.py) | 定义具体工具依赖的三类狭窄运行时 protocol：工作区、环境和通用控制。工具只有在 Host 的策略检查通过之后才拿得到它们。 |
| [`data.py`](./data.py) | 只读访问 Store：受保护的 schema 与 query、frame 浏览，以及有界的 Artifact 血缘遍历。 |
| [`delegation.py`](./delegation.py) | 启动子 Agent，列出并收集直属子 Agent，按精确 ID 停止某一个，或者给运行中的子 Agent 发送引导消息。 |
| [`dynamic.py`](./dynamic.py) | 校验 session 内编写的 Python 工具源码与 manifest，再把每一次冒烟测试、每一次调用都放进全新的 `python -I -S` worker 执行：环境严格无 secret，OS 沙箱强制启用。session/project/global 三级版本通过可信代理解析。 |
| [`dynamic_control.py`](./dynamic_control.py) | Dynamic Tool 的人工治理生命周期：define、list、promote、version-list、activate、rollback。 |
| [`dynamic_scopes.py`](./dynamic_scopes.py) | 保存内容寻址的 project/global Dynamic Tool manifest，以及只追加的激活历史。它不编译、也不执行模型编写的代码。 |
| [`edit.py`](./edit.py) | 工作区内的精确字符串编辑，带一道静态 precheck：退化的编辑请求在申请审批之前就被挡掉。旧版 `edit_file` 的兼容查找仍然保留。 |
| [`env.py`](./env.py) | 环境 list/use/create 三个工具类和实例的兼容 facade。 |
| [`env_create.py`](./env_create.py) | 通过内核 preinstall 服务安装依赖包。 |
| [`env_list.py`](./env_list.py) | 发现预构建好的环境，并可选地对比它们的依赖包覆盖情况。 |
| [`env_use.py`](./env_use.py) | 排队切换到指定的 Python 或 R 环境，在下一个科学 Cell 生效。 |
| [`fs.py`](./fs.py) | 目录列表与文本文件读写工具的兼容 facade。 |
| [`glob_files.py`](./glob_files.py) | 按 glob 在工作区里找文件，结果中会剔除形似 credential 的文件名。 |
| [`list_directory.py`](./list_directory.py) | 列出一个工作区目录，且只限于这个目录之内。 |
| [`mcp.py`](./mcp.py) | MCP 的 server/tool/resource/prompt 发现，以及工具调用和 resource/prompt 读取。外部服务器返回的内容不可信，统一在 Host 边界筛查。 |
| [`native.py`](./native.py) | 把已声明的工具转成可移植、供应商中立的 `ToolSpec` 元数据，并校验函数名在每一个受支持的供应商上都合法。 |
| [`network_access.py`](./network_access.py) | 请求人工批准，把 Host 掌管的出站域名策略放开一个域名。 |
| [`progress.py`](./progress.py) | todo 的读写、已批准 plan 的读取与步骤更新，以及受约束的 review status 控制。 |
| [`read_text_file.py`](./read_text_file.py) | 工作区内有界的 UTF-8 行窗口读取，包含读到二进制文件时的响应契约。 |
| [`registry.py`](./registry.py) | 内置工具唯一的实例化处，按固定顺序创建。把一次调用解析到具体工具，做校验，按每轮上限有序执行一个批次，格式化结果，最后收束成一条有界的 observation。旧版的 fenced 工具 block 也在这里解析。 |
| [`remote_capabilities.py`](./remote_capabilities.py) | 查看远程 GPU capability 的状态。注册一个 capability 必须先通过结构化 probe，再经人工审批。 |
| [`remote_compute.py`](./remote_compute.py) | 供应商中立的远程任务生命周期控制：submit/status/result/cancel/close。原生控制面已经实现，但通用远程计算仍是 Prototype 子系统。 |
| [`schema.py`](./schema.py) | 零依赖的 JSON Schema 子集，用于 definition 校验、参数强制、object schema 标准化和 provider-strict 检查。 |
| [`science.py`](./science.py) | 对受支持的公共科学数据库做标准化的 catalog 与 search 访问。 |
| [`search.py`](./search.py) | glob 和内容搜索工具类、实例的兼容 facade。 |
| [`session.py`](./session.py) | session 状态、创建不可变 checkpoint、从一个精确游标 fork 出只读分支、非修改性的 revert 预览，以及待审批项检查。应用 revert 不在这里暴露。 |
| [`skills.py`](./skills.py) | 渐进式的 Skill 搜索与加载，外加 status/history，以及需要审批的版本 rollback。 |
| [`taxonomy.py`](./taxonomy.py) | 稳定的副作用类别，以及规范化的 resource-key/workspace-target。审计事件记录它们，资源冲突调度也按它们比较。 |
| [`web.py`](./web.py) | Web 搜索/抓取工具类和实例的兼容 facade。 |
| [`web_fetch.py`](./web_fetch.py) | 标准化单个 URL 的抓取与资源身份，同时保留 Host 的软失败行为。 |
| [`web_search.py`](./web_search.py) | 标准化实时 Web 搜索，同样保留 Host 的软失败行为。 |
| [`write_file.py`](./write_file.py) | 在受限工作区里创建或覆盖一个 UTF-8 文件，并给这次写入打上标记，让 Web 控制工具边界能把它捕获成 Artifact。 |

## 新增或修改工具

- schema、副作用声明、权限目标、资源 key 和具体行为，都放在具名的 `Tool` 子类上；只通过 `registry.py:TOOL_TYPES` 实例化。
- 模型来的输入绝不直接调用 `execute()`。要走 `invoke()`/`HostDispatcher`，否则安全与审计这一层就被绕过去了。
- schema 要在受支持的供应商之间保持可移植，并且在本地再强制校验一遍。`writes_files`、网络使用、危险操作和不可信输出都要如实标注。
- 科学算法留在代码和 Skill 里，服务行为留在聚焦的 Host 模块里。原生工具本身应该保持为一个小的编排控制平面。
- 没有强制启用的 OS 沙箱时，Dynamic Tool 必须失败即拒绝。仅靠静态 AST 限制不构成隔离边界。
