# 与代码同行的文档

[English](README.md)

随 OpenAI4S 源码一起分发的文档放在这里，历史遗留的兼容链接也保留在这里。公开的双语网站由
[`Nobody-Zhang/openai4s-docs`](https://github.com/Nobody-Zhang/openai4s-docs)
单独维护；本目录里的内部计划不会发布到那个网站。

## 文件

| 文件 | 职责与状态 |
| --- | --- |
| `architecture.md` | 当前的双循环架构与 Host API 概览，也是贡献者使用的兼容入口。 |
| `ark-agent-plan-9.9.png` | 源码仓库根 README 展示的火山方舟 Agent 套餐价格截图。 |
| `backend-extension-guide.md` | 当前的扩展接缝：新增一个 Tool、Host 服务、存储仓储、provider、Skill 或 Web 会话服务时，各自该接在哪里。 |
| `backend-refactor-architecture.md` | backend refactor 的历史设计记录。它记的是当时定下的方案，不能用来证明当前已经端到端实现。 |
| `compute.md` | 远程计算、BYOC provider 与 `host.fold` 的行为和限制。 |
| `configuration.md` | provider、环境、daemon、内核与数据目录分别怎么配置。 |
| `jupyter.md` | 可选的 Jupyter 适配器：它对外暴露什么、执行边界划在哪里，以及相关的兼容说明。 |
| `package-architecture.md` | 分解工作期间使用的历史清单，记录包与归属关系。 |
| `platforms.md` | 代码实际强制执行的平台支持矩阵：macOS 稳定、Linux beta、Windows **拒绝启动**而不是仅仅警告。它点名尚未满足的门槛（Developer ID 签名与公证）而不是暗示已经满足，并解释各层级差异的来源——不是代码不同，而是被证明的程度不同。 |
| `plan-corecoder-refactor.md` | 内部的历史重构计划；不进入公开网站的内容。 |
| `refactor-plan.md` | 为保留决策上下文而留存的历史迁移计划。 |
| `release-validation.md` | 发布要过的几道关卡：离线 CI、发布包检查、import 冒烟，以及有意留在 CI 之外的外部关卡。 |
| `science-connectors.md` | `science_search` 背后的七个公开科学数据库：各自的接口、学科范围，以及归一化后返回的记录字段。 |
| `security.md` | 威胁模型、信任边界、各层防护与已知的覆盖缺口。 |
| `skills.md` | 内置与用户 Skill 的格式、加载方式、sidecar 与生命周期。 |
| `startup-guide.md` | 双语 macOS `.dmg` 上手全流程：安装、Gatekeeper，以及在 UI 里配置模型 + Tavily 搜索 Key。 |
| `webapp-api.md` | REST/WebSocket 功能面的详细契约与兼容行为。 |
| `response-schemas.json` | 离线套件触达的每条 HTTP 响应的形状，从真实响应里抓取固化，不是手写的。由 [`scripts/capture_response_schemas.py`](../scripts/capture_response_schemas.py) 生成；这里出现 diff 就意味着某条 route 改变了它的返回。覆盖率是部分的，而且刻意可见：文件里没有的 route，就是没有任何离线测试触达的 route。描述宿主机而非 API 的子树——目前是内核的 `sandbox` 块，它的字段**类型**在能强制 sandbox 的机器和不能的机器上本就不同——记为 `machine_state`，不予固化。带 `stubbed_backend` 标记的测试不贡献任何形状：把服务换成桩之后，路由返回的是编造出来的东西，把它作为契约发布比让这条路由没有形状更糟，因为读的人会当真。这些路由改由单元测试看守。 |
| `v02-decisions.md` | nextgen 改进提案第 8 节里那些待定决策的所有者签署答复，2026-07-20 冻结。依赖其中任何一条的工作，在答案被记录到一个 reviewer 查得到的地方之前不得启动。每一行还写明这个选择放弃了什么——代价看不见的决策，后来会被悄悄推翻。|
| `webapp.md` | Web workbench 的概念、投影、状态与面向运维的行为。 |
| `webshare.md` | Web 分享：只读快照 + 出站 relay 隧道、部署方式与信任模型。 |

## 在架构中的位置

可执行行为与测试优先于文字描述。历史计划若与 `openai4s/` 或 `tests/` 冲突，以实现和契约测试为
当前事实，并去更新独立的文档仓库。
