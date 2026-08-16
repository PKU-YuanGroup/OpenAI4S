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
| `docker.md` | 双语容器指南：镜像、`compose.yaml`、Kubernetes 清单，以及通配绑定究竟改变了什么。它把容器与内核沙箱之间的取舍说清楚，而不是暗示容器能替代沙箱；写明镜像期望的 `OPENAI4S_SECRET_<SCOPE>_<NAME>` 推导变量名；也列出了今天真实存在的限制——没有 R、不支持 IPv6、没有访问日志、启动横幅里带着凭据。 |
| `jupyter.md` | 可选的 Jupyter 适配器：它对外暴露什么、执行边界划在哪里，以及相关的兼容说明。 |
| `package-architecture.md` | 分解工作期间使用的历史清单，记录包与归属关系。 |
| `platforms.md` | 代码实际强制执行的平台支持矩阵：macOS 稳定、Linux beta、Windows **拒绝启动**而不是仅仅警告。它点名尚未满足的门槛（Developer ID 签名与公证）而不是暗示已经满足，并解释各层级差异的来源——不是代码不同，而是被证明的程度不同。此外还回答了另一个问题：每个平台真正发出去的是什么，以及为什么「有 Windows 下载」和「Windows 平台被拒绝」并不矛盾。 |
| `plan-corecoder-refactor.md` | 内部的历史重构计划；不进入公开网站的内容。 |
| `refactor-plan.md` | 为保留决策上下文而留存的历史迁移计划。 |
| `release-validation.md` | 发布要过的几道关卡：本地关卡、证据包、被强制的契约、macOS app image、Linux app bundle、Windows 包、可信发布、draft-first 流水线，以及有意留在 CI 之外的外部关卡。它还把 macOS 的签名状态收敛成一个具名取值，由证据而不是由配置算出，并且直说本版本里 `verified` 不可达——不需要任何读者从「没有声明」里去推断一条限制。 |
| `science-connectors.md` | `science_search` 背后的七个公开科学数据库：各自的接口、学科范围，以及归一化后返回的记录字段。 |
| `security.md` | 威胁模型、信任边界、各层防护与已知的覆盖缺口。 |
| `skills.md` | 内置与用户 Skill 的格式、加载方式、sidecar 与生命周期。 |
| `startup-guide.md` | 双语 macOS `.dmg` 上手全流程：安装、Gatekeeper、配置模型，以及在 UI 里用一个 Agent Plan Key 授权豆包搜索；Tavily/免密钥搜索保留为备用。 |
| `team-server.md` / `team-server_zh.md` | 多用户模式的运维页：开什么、按什么顺序开、每个开关到底暴露了什么。里面所有东西默认都是关的，所以默认安装仍是它一直以来的那个单用户工作台（INV-1）。它对两件最容易搞错的事说得很直白——团队模式加的是账号而不是"可以暴露"；relay 也不是访问实验室服务器的第三条路（它发布的是单个会话的脱敏投影，不是工作台）。 |
| `team-server-plan.md` | 多用户 Team Server 模式的冻结执行计划（M1 多租户 → M2 治理 → M3a/M3b Slurm 编排 → M4）：产品决策、约束性不变量、逐里程碑工作项与门禁，以及为自主执行代理写就的非阻塞规则。该计划是意图记录；执行期间只允许追加其「偏差记录」附录。 |
| `webapp-api.md` | REST/WebSocket 功能面的详细契约与兼容行为。 |
| `windows-wsl.md` | 双语 Windows/WSL2 安装与运维指南：Ubuntu 24.04、bubblewrap 安装前自检、校验后离线安装、安全浏览器 URL、后台生命周期、国内镜像，以及 localhost:7897 在 NAT/镜像网络下的区别。 |
| `response-schemas.json` | 离线套件触达的每条 HTTP 响应的形状，从真实响应里抓取固化，不是手写的。由 [`scripts/capture_response_schemas.py`](../scripts/capture_response_schemas.py) 生成；这里出现 diff 就意味着某条 route 改变了它的返回。覆盖率是部分的，而且刻意可见：文件里没有的 route，就是没有任何离线测试触达的 route。描述宿主机而非 API 的子树——内核的 `sandbox` 块，它的字段**类型**在能强制 sandbox 的机器和不能的机器上本就不同；以及 `default_host`，compute registry 把它定义为 `"<alias>" | null`，于是配了 ssh alias 的机器与没配的机器对它的类型看法不同——记为 `machine_state`，不予固化。把这类字段钉住并不能抓到漂移，只会把抓取这份文件的那台机器冻结下来，然后把其他每一台机器都报成 API 的破坏性变更。带 `stubbed_backend` 标记的测试不贡献任何形状：把服务换成桩之后，路由返回的是编造出来的东西，把它作为契约发布比让这条路由没有形状更糟，因为读的人会当真。这些路由改由单元测试看守。 |
| `response-contract.json` | 有哪些 route，以及每条 route 给出的是**哪一类**答案——调用方据以分支的状态码与 content type。它和上面那份一样是抓取出来的、不是手写的，只是方法不同：[`scripts/capture_response_contract.py`](../scripts/capture_response_contract.py) 不带任何参数地把每条已知 route 打到真实 handler 上，所以大多数条目记录的是调用方写错时拿到的那个 4xx，而不是成功响应体。这也是它无需重跑整个套件、因而足够便宜的原因。当某条可路由的 route 在这里根本没有条目时，`--check` 判定失败——一条驱动不到的 route 是测试的缺口，不是可以容忍的缺席。两份文件里它更粗：这份说的是「route 会应答，以及以什么形式」，`response-schemas.json` 说的是「里面那个 JSON 长什么样」。 |
| `plan-crosswalk.json` | 综合报告中 56 条原始提案各自的真实去向，以可机器校验的数据形式存在，而不是散文里的一张表。每个 `(source, original_id)` 一行，记录整合去向、来自受限词汇表的状态、证明它的测试，以及——对任何未验证项——缺失的那次运行。`tests/test_plan_crosswalk.py` 强制 56 个唯一 key 各出现一次，因此重复行、丢失行，或者一个指不到任何现存测试的 `closed`，都会失败而不是被忽略。状态来自一次只读的生产调用链审计，不继承 [`next-version-progress.md`](next-version-progress.md)。 |
| `v02-decisions.md` | nextgen 改进提案第 8 节里那些待定决策的所有者签署答复，2026-07-20 冻结。依赖其中任何一条的工作，在答案被记录到一个 reviewer 查得到的地方之前不得启动。每一行还写明这个选择放弃了什么——代价看不见的决策，后来会被悄悄推翻。|
| `v03-decisions.md` | v0.3 的所有者签署答复，2026-07-26 冻结，包含推翻 v0.2「每个 Phase 一个大 PR」的那一条，以及本版本据以衡量的验收口径。它还用一张表列出所有无法从工作副本验证的事项——GitHub Actions 的真实执行、Developer ID 证书、公证、PyPI OIDC、实机浏览器、Linux CI——好让它们不出现在「已验证」一栏是有意为之，而不是疏漏。|
| `next-version-progress.md` | v0.3 的逐项事实记录：什么落地了、在哪个提交、以及那一列真正承重的内容——为证明每个新测试确实会失败，把什么缺陷放了回去。不会失败的测试什么也没测，而存在一个同名的类不构成完成证据。凡是证明所需的那次运行需要本仓库没有的机器，一律标 `Implemented but unverified` 并写明缺的是哪一次运行。|
| `webapp.md` | Web workbench 的概念、投影、状态与面向运维的行为。 |
| `webshare.md` | Web 分享：只读快照 + 出站 relay 隧道、部署方式与信任模型。 |

## 在架构中的位置

可执行行为与测试优先于文字描述。历史计划若与 `openai4s/` 或 `tests/` 冲突，以实现和契约测试为
当前事实，并去更新独立的文档仓库。
