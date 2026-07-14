---
title: 安全加固
description: 面向运维人员的账户隔离、网络暴露、内核沙箱、权限、Secret 与审计检查清单。
canonical: true
last_verified: 2026-07-14
verification: code-and-tests
status: current
audience: [operators, contributors]
verified_commit: a92e736
owner: OpenAI4S maintainers
---

# 安全加固

OpenAI4S 会执行模型编写的 Python/R、需要审批的 shell 操作，以及控制覆盖随能力而异的远程操作。请把它作为**单用户代码执行工作台**来加固，而不是供互不信任用户使用的 Web 应用。以下控制可以降低模型失误与不可信内容带来的风险，但不会建立多租户安全边界。

有关清单背后的确切强制执行和失败语义，请阅读[安全架构](../security.md)。

## 基线配置

可信主机部署可使用以下配置：

```dotenv
OPENAI4S_HOST=127.0.0.1
OPENAI4S_DATA_DIR=/var/lib/openai4s
OPENAI4S_KERNEL_SANDBOX=enforce
OPENAI4S_EGRESS=allowlist
OPENAI4S_SAFETY=heuristic
OPENAI4S_SAFETY_AUDIT_HOOK=1
OPENAI4S_INJECTION_SCAN=1
OPENAI4S_BIOSECURITY=1
OPENAI4S_NOTEBOOK_REPL=0
OPENAI4S_UNATTENDED_APPROVAL=deny
```

`OPENAI4S_UNATTENDED_APPROVAL=deny` 是对默认行为的明确记录；除 `allow` 外的所有值都保持 deny/pending。这些设置的强度并不相同：操作系统沙箱和权限决定属于强制执行控制，而若干模型/内容筛查器是 heuristic、advisory 或 fail-open。

## 隔离操作系统账户

- 为守护进程创建一个专用账户，不要以 root 运行。
- 不要让该账户访问无关仓库、浏览器 profile、个人 home 目录、cloud CLI 或广泛的 `sudo` 权限。
- 只授予所选计算工作流必需的 SSH identity 与 remote host。
- 以 `umask 077` 启动；将数据目录设为 mode `0700`；使用 `chmod -R go-rwx "$OPENAI4S_DATA_DIR"` 递归移除 group/other 访问，但不改变 owner execute bit。
- 在可行时，让守护进程只能读取、不能修改源码/发行目录。发行版专属虚拟环境是个例外，因为首次执行 `serve` 可能安装缺失的科学包；最好在启动前填充完毕，再把发行版设为不可变。
- 将备份放在另一个私有路径下并加密。数据库和日志都可能包含凭据与研究内容。

即便启用了内核沙箱，操作系统账户隔离仍然重要：worker 继承守护进程用户的 identity；沙箱为解释器所需而允许广泛 host read；`auto` 还可能在没有隔离的情况下继续运行。

## 不要将工作台暴露到公网

默认回环 listener 没有登录机制，因为预期由本地操作系统边界进行访问控制。请保留这一模式：

```bash
ssh -N -L 8760:127.0.0.1:8760 user@trusted-host
```

只有当操作员理解新增边界时，才使用可信 VPN 或本地已认证/TLS 反向代理。后端仍应保留回环绑定。内置非回环 token：

- 是全进程共享的一枚 token，而非用户 identity；
- 最初通过 URL 交付，随后存入 `HttpOnly` cookie；
- 不提供 TLS、role、revocation list、rate limit 或 tenant isolation；
- `/health` 不要求认证；
- 与 Origin 检查并存，而后者会接受没有 `Origin` header 的非浏览器请求。

它是可信网络中的纵深防御，不是公开部署机制。尤其要注意，Gateway 暴露 Host 侧本地计算作业路由；不要假设所有代码执行面都位于 Python/R 沙箱内。

将 `openai4s.org/docs/` 作为生成的静态文件，通过独立 Web 服务器配置提供，最好还使用另一个无特权账户。静态服务器绝不能读取 `OPENAI4S_DATA_DIR`、环境文件、SSH key 或工作台日志。

## 必要时强制启用内核沙箱

`OPENAI4S_KERNEL_SANDBOX` 有三种模式：

| 模式 | 行为 |
|---|---|
| `enforce` | 接受每个 worker 前检测并自检 Seatbelt/bubblewrap；边界不可用时拒绝启动 worker |
| `auto` | 自检成功后强制使用；失败则发出警告、以 `unavailable` sandbox status 继续无沙箱运行 |
| `off` | 明确关闭操作系统边界并报告该状态 |

对于无人值守或远程可信主机服务，请使用 `enforce`。在 Linux 上安装 `bubblewrap`，并确认 user namespace/container 限制允许它完成真实自检；在 macOS 上，应在与服务完全相同的上下文中验证 `sandbox-exec`。开发者 shell 中测试通过，不代表受 supervisor 限制的服务也能启动沙箱。

沙箱状态属于每个已启动的 Python/R generation。两种语言均未运行前，工作台会正确报告 `not_started`。部署后，以及修改操作系统、service unit、interpreter 或 mount layout 后，应实际运行每一种启用的语言。

当前边界有意保持以下限制：

- 为使解释器与包正常工作，host filesystem 大范围可读，只对目标 secret path 做 deny/mask；
- 写入被限制到 Session workspace 与私有临时目录；
- 除非启用 Host 全局兼容 override，否则会拒绝 raw worker network；
- Host RPC、守护进程、本地计算作业和远程 provider 操作属于不同信任边界；
- 不宣称具备 seccomp、VM isolation、tenant separation，或能遏制所有 kernel/OS vulnerability。

常规部署不要设置 `OPENAI4S_KERNEL_ALLOW_RAW_NETWORK=1`。如果兼容性要求必须启用，请记录例外并假设任意 Cell 均可访问网络。

## 限制网络出口

`OPENAI4S_EGRESS=off` 是默认值，意味着**不执行应用 allowlist**。设为 `allowlist` 后，可约束 `web_fetch`、`web_search`，以及授权 `host.bash` 调用中能够静态检测出的 URL。请审查 runtime domain grant 与长期 permission rule。

该 allowlist 并不是透明网络防火墙：

- 它只作用于选定的 Host-tool 边界；
- malformed target 与 mode 按设计 fail-open；
- 间接或混淆命令可以绕过静态 shell URL 检测；
- 它不管理每条 daemon-side provider 连接；
- 当操作系统沙箱关闭、降级或明确允许网络时，它无法阻止 raw Python/R socket。

使用操作系统沙箱拒绝 worker raw network，并使用 Host/网络防火墙实施整个部署范围的出口策略。`OPENAI4S_ALLOW_PRIVATE_FETCH=1` 会关闭 Web fetcher 的 private/loopback/metadata address guard，只应在有记录的本地集成中使用。

## 审查权限与交互式执行

- 除非操作员明确需要任意手动 Python/R 输入，否则保留 `OPENAI4S_NOTEBOOK_REPL=0`。启用后，用户 REPL Cell 会绕过 agent code classifier，但仍使用 execution queue、worker sandbox 和 audit path。
- 让无人值守审批保持 deny/pending。设置 `OPENAI4S_UNATTENDED_APPROVAL=allow` 会把无人答复的风险门禁变成自动批准，应视为高风险例外。
- 优先使用 `once` 或 project-scoped approval。定期检查 conversation/project/global rule，并移除宽泛 wildcard rule。
- 重启后的批准不会执行已保存的参数。系统会记录被中断的操作未执行，并要求显式 continue/replan。运维上应保留该行为；不要构建把审批当作 replay 的自动化。
- 远程计算 submission、connector call、文件变更、dynamic tool 以及 package/environment 变更，应使用比只读发现更窄的 target。当前 remote-compute 覆盖并不完整：`compute_submit` 需要 approval，result/cancel/close 不会再次请求 approval，而 legacy direct SSH/SCP helper 不进入 Tool permission gate。对这些路径，应把 daemon account 及其 SSH identity 视为实际安全边界。

一次性的 `host.bash` capability 会绑定 command hash、canonical working directory、worker generation、challenge 与 expiry。它仍然是以 daemon user 权限执行的 shell，不是账户隔离的安全替代品。

## 管理 Secret

- 优先使用私有 supervisor 环境文件或外部 secret manager。绝不要提交 `.env`，也不要把 secret 放进 shell history、命令参数、文档、Session package 或静态站点构建输入。
- 记住，已保存的 model profile 存储于 SQLite。应像保护凭据一样保护数据库备份。
- `host.credentials.set` 的值仅存在于内存中，并会在相关 RPC audit/replay path 中脱敏或跳过；重启后即消失，不是持久 secret store。
- 内核子进程接收 allowlisted environment，而不是 daemon 的完整环境。不要轻易放宽该 allowlist。
- file-tool secret-name guard 与 `host.query` table denylist 只保护特定应用路径，并非通用 data-loss-prevention system。
- 远程计算 provider environment scrubbing 基于变量名。存放在无法识别变量名下的 secret 不保证会被移除。只转发 provider 声明的 key，并在启用前检查 provider code。
- SSH authentication 位于 registry 之外。使用专用 key、严格的 host entry，以及无多余权限的 remote account。

怀疑 prompt injection、非预期远程活动、日志暴露或备份遗失时，应轮换凭据。停止 daemon 不会撤销 provider-side token。

## 理解 heuristic 与 fail-open 层

不要把 UI 中的绿色标签解读为比代码更强的保证：

| 层 | 当前失败行为 |
|---|---|
| Agent Cell classifier | 高置信度静态匹配会阻止；其余 heuristic 允许；classifier exception 与未配置 LLM classification 允许 |
| Injection scanner | 为检测到的 tool content 添加 warning，但不移除或阻止；exception 与不可用 LLM scanning 会允许未标注内容 |
| Biosecurity trajectory screen | CLI 路径中 `BLOCK` 会阻止 Cell，`ESCALATE` 仅为 advisory，model 缺失或报错会返回 `ALLOW`；Web 路径目前只有 prompt guidance，不调用 trajectory screener |
| `dlopen` audit hook | 仅 CPython，针对从 writable root 加载的 shared library；不是通用 syscall policy |
| Egress allowlist | 默认关闭且 fail-open；仅覆盖选定 Host-tool 边界 |
| Sandbox `auto` | 不可用时继续无沙箱运行；只有 `enforce` 会 fail-closed |

这些层仍然是有价值的纵深防御，但运维时必须遵循其真实语义。

## 审计与监控

启动时以及运行第一个 Python/R Cell 后，记录：

- 源码修订版与环境版本；
- `/health` 结果与服务账户 identity；
- Python/R 沙箱 backend、自检结果与网络策略；
- 科学包安装状态；
- 待处理审批与宽泛长期规则；
- 已启用的用户 Skill、dynamic tool、connector 与 remote capability；
- 数据目录下的非预期文件或权限变化；
- 未完成的 `host.compute` job；它们保存在进程内存中，而非持久 scheduler。

Action Timeline 是经过限量和脱敏的用户投影，不是完整 raw audit database。需要取证完整性时，应按事件保留策略保存整个数据目录与服务日志。

## 加固变更需要端到端测试

修改 service supervision、sandbox package、mount、filesystem permission、proxy 或 network policy 后，请测试：

1. 通过所选访问路径进行同源 HTTP 与 WebSocket streaming；
2. 读取一个已有 Session 与 Artifact；
3. 按需启动 Python/R，并检查各语言沙箱状态；
4. 在 workspace 内写文件并捕获 Artifact；
5. 一条被拒绝和一条被批准的 permission flow；
6. Host Web 分别访问允许与阻止的 target；
7. 有活动中及排队任务时优雅停止；
8. 备份并在隔离环境中恢复。

单元测试只是最低要求。浏览器 streaming、worker isolation、package availability、SSH 与外部 provider 都需要针对部署进行验证。
