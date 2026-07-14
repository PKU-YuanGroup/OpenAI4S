# `openai4s` 包

[English](./README.md)

**状态：核心已实现，扩展面中明确标注的部分仍为 Partial。** 这里是 OpenAI4S 的顶层 Python 包。它的标准库控制平面组合了供应商中立的外层 Agent 循环、原生 JSON 控制工具、持久化科学内核、Host RPC 服务、存储、安全层以及 Web/CLI 适配器。

## 架构位置

OpenAI4S 包含两个嵌套循环。位于 [`agent/`](./agent/) 的**外层循环**在每个模型步骤中最多接受一个经过路由的动作：一个有序原生工具批次、Engine 自有的 `finalize_response`，或者一个完整的 Python/R Cell。位于 [`kernel/`](./kernel/) 的**内层循环**保持语言 Worker 持久存在，并能在 Python Cell 尚未结束时处理同步 `host.*` 调用。[`host_dispatch.py`](./host_dispatch.py) 是两个平面之间的兼容/组合边界；具体行为位于 [`host/`](./host/) 下的聚焦服务中。

纯控制工作可以通过 Engine 自有的 finalizer 完成。`host.submit_output(...)` 是唯一能从 Python Cell 内部发出的完成信号；先前执行过 Cell 后，后续单独且有效的 `finalize_response` 仍可关闭 Engine。普通文本、原生工具结果、R Cell、取消和回合耗尽本身都不是完成信号。

## 本目录直属文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 定义包身份和版本；导入包时不会启动服务。 |
| [`__main__.py`](./__main__.py) | 将 `python -m openai4s` 转发到 CLI 入口。 |
| [`artifact_restore.py`](./artifact_restore.py) | 原生与 Web 路径共用的追加式 Artifact 恢复服务：校验历史快照、在工作区内恢复字节，并记录新版本而不改写历史。 |
| [`bash_capability.py`](./bash_capability.py) | 定义语言无关的版本标记和命令摘要，用于绑定短时、一次性的 shell capability。 |
| [`capabilities.py`](./capabilities.py) | 通过 repository port 解析有作用域的能力启用状态和 specialist profile 状态。 |
| [`config.py`](./config.py) | 提供零依赖 `.env` 加载、`LLMConfig`、`SecurityConfig`、全局 `Config` 数据类以及分层环境变量解析。 |
| [`egress.py`](./egress.py) | 实现 Host 所有的出站域名 allowlist，用于 Web/shell 策略边界；它补充 OS 沙箱，但不替代沙箱。 |
| [`host_dispatch.py`](./host_dispatch.py) | kernel `host_call` RPC 的兼容/组合 facade；先应用权限、审批、审计、回放、筛查和步骤事件策略，再路由到聚焦 Host 服务。 |
| [`jobs.py`](./jobs.py) | 管理有界的进程内后台计算任务和输出缓冲。任务工作文件可以持久化，但 registry 本身位于内存。 |
| [`mcp_client.py`](./mcp_client.py) | 面向工具、资源和 prompt 的纯标准库 MCP stdio JSON-RPC 客户端及进程级连接管理器；不包含服务器发起的 sampling。 |
| [`onboarding.py`](./onboarding.py) | 供无界面 CLI 使用、可测试的首次模型/供应商配置服务。 |
| [`permissions.py`](./permissions.py) | 进程级权限 broker，处理 allow/deny/ask 规则、持久审批请求、取消、超时和 unattended fail-closed 行为。 |
| [`pkgscan.py`](./pkgscan.py) | 扫描 Python、conda 和 R 环境中的标准化包可用性，而不把这些包导入核心。 |
| [`prompts.py`](./prompts.py) | 保存用于压缩、审查 gate、溯源、Skill 检索、抽取、编辑和安全的小型单用途 prompt。 |
| [`replay.py`](./replay.py) | 将成功的 `host.*` 结果记录为离线回放 tape，并在导出 notebook 回放时检测调用顺序漂移。 |
| [`review.py`](./review.py) | 对已完成回合的证据执行有界、无工具审查并标准化 JSON verdict；不能修改工作区。 |
| [`store.py`](./store.py) | 兼容 facade，持有一个 SQLite 连接、schema/migration、受保护只读查询，以及共享同一锁的聚焦 storage repository。 |
| [`webtools.py`](./webtools.py) | 使用标准库优先的 transport 实现 Host 侧 Web 搜索/抓取、内容转换、网络开关、SSRF 检查和 egress 强制。 |

## 直属子目录

| 目录 | 在架构中的位置 |
| --- | --- |
| [`adapters/`](./adapters/) | 位于标准库运行时核心之外的可选生态适配器。 |
| [`agent/`](./agent/) | 供应商中立的外层循环、动作路由、完成、压缩、委派和本地运行时组合。 |
| [`cli/`](./cli/) | 命令行生命周期和一次性任务入口。 |
| [`compute/`](./compute/) | Host 侧 BYOC/远程计算 registry 与任务编排；通用远程计算仍是 Prototype 能力面。 |
| [`execution/`](./execution/) | 共用的科学 Cell 准入、取消、依赖投影、结果值和超时恢复。 |
| [`host/`](./host/) | `HostDispatcher` 组合 facade 背后的聚焦服务。 |
| [`kernel/`](./kernel/) | 持久 Python/R Worker、语言无关 manager 协议、环境选择、沙箱集成和 Cell 内 Host RPC。 |
| [`llm/`](./llm/) | 供应商中立 LLM 客户端、能力、标准化消息/工具、标准库 transport 和 wire 适配器。 |
| [`mcp_servers/`](./mcp_servers/) | 用于演示和测试的内置纯标准库 MCP 示例服务器。 |
| [`sdk/`](./sdk/) | 注入 Python Cell 的兼容 `host` facade 和远程计算命名空间。 |
| [`security/`](./security/) | 沙箱、环境隔离、代码/内容筛查、注入检查及相关策略辅助模块。 |
| [`server/`](./server/) | 标准库 HTTP/WebSocket workbench、session 服务、投影、恢复和静态 UI；若干专用 UI/恢复工作流仍为 Partial。 |
| [`skills_loader/`](./skills_loader/) | Skill 发现、渐进披露、sidecar 校验、版本化安装和回滚。 |
| [`storage/`](./storage/) | 通过 `Store` 使用的聚焦 SQLite repository。 |
| [`tools/`](./tools/) | 基于类的供应商原生控制工具、schema、registry、动态工具生命周期和兼容 fenced-call 支持。 |

## 修改规则

- 保持核心仅依赖 Python 标准库；每个可选科学依赖的使用点都必须有保护。
- 将领域行为加入对应的聚焦 service/repository/tool，而不是整体重写 `host_dispatch.py` 或 `store.py`。
- 保持 kernel 协议的单 frame reader、按 ID 路由响应、事务锁和 generation 检查。
- 按字面理解安全与持久化标签：best-effort 投影和 Partial 能力不能写成保证。
