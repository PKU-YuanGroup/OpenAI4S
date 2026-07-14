# 原生控制工具

[English](./README.md)

**状态：控制 catalog 已实现；目标服务保留各自的 Implemented/Partial/Prototype 状态。** 本包声明用于编排和权限控制的供应商原生 JSON 工具。它不会把 shell 执行、科学计算或 `submit_output` 变成原生工具。

## 架构位置

当模型回复包含原生调用时，外层循环会先路由有序工具批次，再考虑 fenced 代码。每个具体 [`Tool`](./base.py) 声明 JSON schema、审批行为、副作用类别、资源 key、输出策略和聚焦的 `execute()` 行为。模型发起的调用通过 `Tool.invoke()` 和 [`HostDispatcher`](../host_dispatch.py) 进入；权限、审批、审计、注入筛查和活动事件应用后，受保护适配器才会调用 `execute()`。

[`registry.py`](./registry.py) 是唯一实例化内置工具类的位置。[`catalog.py`](./catalog.py) 构建逐 session 渐进披露视图，并加入隔离的动态 proxy，而不修改全局内置 registry。供应商 schema 是生成提示；[`schema.py`](./schema.py) 会在 dispatch 前再次强制执行所支持的契约。

## 本目录直属文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 公共兼容 facade，重新导出工具类、registry helper、native spec、schema helper 和批次限制。 |
| [`artifacts.py`](./artifacts.py) | 声明 Artifact 列表、现有文件注册、精确元数据/版本查询，以及需要审批的历史版本恢复工具。 |
| [`background.py`](./background.py) | 声明面向独立后台 Python Cell Worker 的 submit/list/peek/interrupt 控制；它不是 shell runtime。 |
| [`base.py`](./base.py) | 定义不可变的类式 `Tool` 契约、元数据、Host 调用边界、schema 校验、审批目标、资源 key 和 provider-strict 兼容性。 |
| [`capabilities.py`](./capabilities.py) | 声明 `search_capabilities`，用于搜索并为当前 session 单调激活隐藏工具组。 |
| [`catalog.py`](./catalog.py) | 将内置工具和有效动态 proxy 组合为 session catalog，划分渐进披露组，并生成活动 native spec/prompt 元数据。 |
| [`content_search.py`](./content_search.py) | 声明受限工作区内的有界正则内容搜索。 |
| [`contexts.py`](./contexts.py) | 定义具体工具通过 Host 策略检查后使用的狭窄 workspace、environment 和通用 control runtime protocol。 |
| [`data.py`](./data.py) | 声明受保护的只读 Store schema/query、frame 浏览和有界 Artifact lineage 遍历工具。 |
| [`delegation.py`](./delegation.py) | 声明子 Agent 启动、直属 child 列表/收集、精确停止和实时 steering 控制。 |
| [`dynamic.py`](./dynamic.py) | 校验 session 编写的 Python 工具源代码/manifest，并在严格无 secret 环境及强制 OS 沙箱中的全新 `python -I -S` Worker 内执行每次 smoke test/调用；通过可信 proxy 解析 session/project/global 版本。 |
| [`dynamic_control.py`](./dynamic_control.py) | 声明受人工治理的 Dynamic Tool define、list、promote、version-list、activate 和 rollback 操作。 |
| [`dynamic_scopes.py`](./dynamic_scopes.py) | 保存内容寻址的 project/global Dynamic Tool manifest 和追加式 activation history，不编译或执行模型编写的代码。 |
| [`edit.py`](./edit.py) | 声明带静态 precheck 的精确字符串工作区编辑，并保留旧版 `edit_file` 兼容查找。 |
| [`env.py`](./env.py) | environment list/use/create 工具类和实例的兼容 facade。 |
| [`env_create.py`](./env_create.py) | 声明通过 kernel preinstall 服务安装 package。 |
| [`env_list.py`](./env_list.py) | 声明预构建 environment 发现和可选 package coverage 对比。 |
| [`env_use.py`](./env_use.py) | 声明为下一个科学 Cell 排队切换到指定 Python 或 R environment。 |
| [`fs.py`](./fs.py) | 目录列表和文本文件读写工具的兼容 facade。 |
| [`glob_files.py`](./glob_files.py) | 声明工作区 glob，同时过滤类似 credential 的 basename。 |
| [`list_directory.py`](./list_directory.py) | 声明对一个工作区目录的受限列表操作。 |
| [`mcp.py`](./mcp.py) | 声明 MCP server/tool/resource/prompt 发现，以及工具调用和 resource/prompt 读取；不可信外部输出在 Host 边界筛查。 |
| [`native.py`](./native.py) | 将已声明工具转换为可移植的供应商中立 `ToolSpec` 元数据，并校验跨供应商函数名。 |
| [`network_access.py`](./network_access.py) | 声明请求人工批准，为一个域名扩大 Host 所有的出站域名策略。 |
| [`progress.py`](./progress.py) | 声明 todo 读写、已批准 plan 读取/步骤更新和受约束 review status 控制。 |
| [`read_text_file.py`](./read_text_file.py) | 声明工作区内有界 UTF-8 行窗口读取，包括 binary-file 响应契约。 |
| [`registry.py`](./registry.py) | 创建有序内置实例；解析工具；解析旧版 fenced 工具 block；校验、执行、限制、格式化并收束有序工具批次。 |
| [`remote_capabilities.py`](./remote_capabilities.py) | 声明远程 GPU capability 状态，以及结构化 probe 成功后需要审批的注册。 |
| [`remote_compute.py`](./remote_compute.py) | 声明供应商中立的远程任务 submit/status/result/cancel/close 生命周期控制。原生控制面已实现，但通用远程计算仍是 Prototype 子系统。 |
| [`schema.py`](./schema.py) | 实现用于 definition 校验、参数强制、标准化 object schema 和 provider-strict 检查的零依赖 JSON Schema 子集。 |
| [`science.py`](./science.py) | 声明对受支持公共科学数据库的标准化 catalog/search 访问。 |
| [`search.py`](./search.py) | glob 和 content-search 工具类/实例的兼容 facade。 |
| [`session.py`](./session.py) | 声明 session 状态、不可变 checkpoint 创建、精确只读 branch fork、非修改性 revert preview 和待审批检查；这里不暴露 revert 应用。 |
| [`skills.py`](./skills.py) | 声明渐进 Skill search/load，以及 status/history 和需要审批的版本 rollback 控制。 |
| [`taxonomy.py`](./taxonomy.py) | 定义审计与冲突调度使用的稳定副作用类别及规范 resource-key/workspace-target 标准化。 |
| [`web.py`](./web.py) | Web 搜索/抓取工具类和实例的兼容 facade。 |
| [`web_fetch.py`](./web_fetch.py) | 声明标准化单 URL 抓取和资源身份，同时保留 Host soft-fail 行为。 |
| [`web_search.py`](./web_search.py) | 声明标准化实时 Web 搜索，并保留 Host soft-fail 行为。 |
| [`write_file.py`](./write_file.py) | 声明对一个受限工作区文件的 UTF-8 创建/覆盖，并标记该写入，使 Web 控制工具边界能够捕获 Artifact。 |

## 直属子目录

无。

## 新增或修改工具

- 在具名 `Tool` 子类上放置 schema、副作用声明、权限目标、资源 key 和聚焦行为；只通过 `registry.py:TOOL_TYPES` 实例化。
- 模型输入不得直接调用 `execute()`；通过 `invoke()`/`HostDispatcher` 进入，避免跳过安全和审计 envelope。
- 保持 schema 在受支持供应商之间可移植，并在本地再次强制。准确标记 `writes_files`、网络使用、危险操作和不可信输出。
- 科学算法放在代码/Skill 中，服务行为放在聚焦 Host 模块中。原生工具应保持为小型编排控制平面。
- 当强制 OS 沙箱不可用时，Dynamic Tool 必须 fail closed；静态 AST 限制本身不是隔离边界。
