---
title: 安全架构
description: OpenAI4S 的信任边界、强制控制、heuristic 筛查器、失败模式与安全访问假设。
canonical: true
last_verified: 2026-07-14
verification: code-and-tests
status: current
audience: [operators, contributors, users]
verified_commit: a92e736
owner: OpenAI4S maintainers
---

# 安全架构

本页是 OpenAI4S 安全文档的稳定兼容入口。运维人员还应遵循具体的[安全加固检查清单](operations/security-hardening.md)。

> OpenAI4S 是本地或可信主机上的单用户科学工作台，**不是**经过加固的公开多租户服务。它会执行模型编写的代码，并开放高权限的本地/远程操作。许多操作会进入 permission 与 audit control，但 remote-compute 覆盖并不统一；让守护进程保持回环绑定，并通过专用操作系统账户隔离。

## 威胁模型与信任边界

该设计试图限制模型编写的 Cell、shell command、不可信 Web/MCP content 和用户安装扩展带来的意外或对抗性行为。它假设 Host operator、daemon code 和 built-in component 均可信，不宣称能够防御恶意管理员、kernel exploit、受入侵的 Python/R interpreter，或多个互不信任用户共享同一个 daemon account。

| 边界 | 运行位置 | 安全含义 |
|---|---|---|
| 公开文档 | `/docs/` 下的静态文件 | 可以公开；不含工作台 runtime 或 data |
| HTTP/WebSocket Gateway | Daemon process | 单用户 control surface；loopback 是主要访问边界 |
| JSON control tool 与 Host RPC | Daemon process | 按能力配置 permission、audit、egress、injection-screening 与 path-policy 检查；覆盖并不统一 |
| Python/R worker | Child process | 可选 OS sandbox 加 sanitized environment；Python 支持 Cell 中 Host RPC，R 不支持 |
| Kernel subprocess / `host.bash` | Worker identity 下 | 继承 worker boundary；shell 还需要一次性 Host capability |
| Local compute job | Daemon-side job manager | 高权限本地操作，不属于 Python/R sandbox |
| `host.compute` provider helper 与 SSH service | 本地 helper/container 或 remote host | 独立的实验性边界：submission 需要 approval，但 result/cancel/close 与 legacy direct SSH/SCP 并非全部进入同一 approval gate |

没有任何一层单独就已足够。预期的安全状态组合了 loopback access、OS-account isolation、kernel sandboxing、least-privilege approval、file/secret policy 与 audit record。

## 控制矩阵

| 层 | 范围 | 默认值 | 失败行为 |
|---|---|---|---|
| Loopback bind | Gateway | `127.0.0.1` | Loopback 无内置 login；依赖 Host access control |
| Non-loopback token | Gateway | 非回环时自动启用，也可通过 `OPENAI4S_REQUIRE_TOKEN=1` 启用 | 拒绝缺少单一 process token 的请求，但这不是 user/role authentication 或 TLS |
| Origin check | 会变更状态的 `/api` request 与 WebSocket upgrade | 开启 | 拒绝带有 cross-origin `Origin` 的请求；没有 `Origin` 的 client 会被接受 |
| Child environment allowlist | Python/R worker 与 descendant | 始终开启 | 构建新的 allowlisted environment；不复制 daemon environment |
| OS kernel sandbox | Python/R worker 与 descendant | `OPENAI4S_KERNEL_SANDBOX=auto` | `auto` 失败时带 warning/status 继续无沙箱运行；`enforce` 拒绝启动 |
| Raw worker network boundary | OS sandbox | 沙箱强制执行时阻止 | 沙箱 off/degraded 时不强制；Host RPC networking 属于独立边界 |
| Permission broker | 有风险的 Host/control action | seeded rule、交互式 `ask` | Headless/unattended 默认为 deny/pending；显式 `allow` override 会放行 |
| `host.bash` capability | 精确 shell invocation | 必需 | 缺失、过期、复用、不匹配或 generation 错误的 token 均 fail-closed |
| Workspace 与 secret-file check | File tool | 开启 | 拒绝 path escape 与部分 secret-shaped path |
| Code classifier | Agent 编写的 Cell | `heuristic` | 高置信度 static match 会阻止；classifier exception fail-open |
| Python `dlopen` audit hook | Python worker | 开启 | 阻止从 writable root 加载目标库；不是 syscall sandbox，R 中也不存在 |
| Injection scanner | 部分不可信 tool output | 开启 | 标注检测到的内容；不移除/阻止，错误时 fail-open |
| Biosecurity prompt/screener | Agent policy；CLI Cell path 执行 screener | 开启 | CLI `BLOCK` 拒绝 Cell；`ESCALATE` 仅为 advisory；模型缺失/报错会允许；Web 当前只有 prompt guidance |
| Host egress allowlist | 部分 Web/search/bash path | `OPENAI4S_EGRESS=off` | 关闭或无法识别的 mode 会 fail-open |
| Web fetch SSRF guard | `web_fetch` redirect chain | 开启 | 阻止解析后的 private/loopback/link-local/reserved target，除非显式 override |

## Gateway 以本地访问为先

默认 Gateway 监听 `127.0.0.1:8760`。通过隧道访问远程可信主机：

```bash
ssh -N -L 8760:127.0.0.1:8760 user@trusted-host
```

在 loopback 上，Gateway 有意不提供应用 login。绑定非回环地址会生成随机 process token，打印含 token 的 URL，通过 query string 接收后，将浏览器重定向以移除 query，并设置 `HttpOnly` cookie。`/health` 仍公开。该 token 不提供 encryption、identity、role、per-user data separation、rate limiting 或安全互联网暴露。

当变更型 API request 携带的 `Origin` network location 与 `Host` 不同时，会被拒绝；WebSocket upgrade 也执行同样的 same-origin check。没有 `Origin` 的非浏览器请求会通过。应把它视为 CSRF 纵深防御，而不是身份验证。

工作台与公开静态文档必须保持独立部署。在 `openai4s.org/docs/` 发布静态文档，不构成把 daemon 反向代理到同一公开 origin 下的理由。

## Worker 进程隔离

### 清理后的环境

每个 Python/R worker environment 都会由明确的 runtime 与可信 OpenAI4S allowlist 重新构建。Provider/model/cloud/OAuth credential、proxy URL、loader injection variable、shell startup injection setting，以及 credential-shaped name 都不会继承。所选 interpreter path、workspace、generation 与 Host protocol value 由 manager 合成。

这可以阻止 ambient daemon secret 越过正常 spawn boundary，但无法保护 operator 放进允许变量、workspace file、command、package 或未识别外部 channel 中的 secret。

### Seatbelt 与 bubblewrap

纯 stdlib sandbox adapter 会使用以下方式包装 worker：

- macOS 上通过 `sandbox-exec` 使用 Seatbelt；或
- Linux 上通过 `bwrap` 使用 bubblewrap。

接受 worker 前，它会真实探测 workspace write、private-temp write、outside-write denial，以及配置后 raw-network denial。强制沙箱为解释器所需而大范围允许 Host read，同时对 OpenAI4S database、checkout `.env`、`~/.ssh`、`.netrc` 和 `.pgpass` 实施定向 read denial。写入被限制到 Session workspace 与 private temp。Linux 使用 read-only root bind、选定 read mask 与 private network namespace；有意保留 Host PID namespace。Seatbelt 实施明确的 write/network policy 与定向 read denial。

`OPENAI4S_KERNEL_SANDBOX` 接受：

- `auto`——成功测试后强制使用，否则发出警告并报告 unavailable，同时继续无沙箱运行；
- `enforce`——worker 启动前 fail-closed；
- `off`——明确关闭并报告该边界。

`OPENAI4S_KERNEL_ALLOW_RAW_NETWORK=1` 是 Host 全局兼容 override，不应在常规部署中启用。沙箱状态属于一个精确 worker generation；Python/R worker 实际运行测试前保持 `not_started`。

这是进程 containment layer，不是 VM、seccomp policy 或 tenant boundary。Host-side service 与 local compute job 位于其外部。

## 文件、Shell 与审批

File capability 会以活动 Session workspace 为基准解析 path 并拒绝 escape。`.env`、key file 和常见 SSH private-key name 等 secret-shaped target 会在 file-tool envelope 中被阻止。沙箱仍然重要，因为 worker 未沙箱化时，static file-tool check 无法调解任意 library call。

系统没有注册 native shell tool。`host.bash` 请求 Host 授权精确的 command hash、canonical working directory、当前 worker generation、random challenge 与短 expiry。worker 验证并一次性消费 capability，随后自行启动 subprocess；Host 不执行 shell。frame ID 仍是 audit context，不是 consume-time 额外绑定。static command 与 URL-domain check 属于纵深防御，不是 shell parser 或完整 path jail。

Permission broker 依据 SQLite-backed rule 处理有风险的 action：

- `allow`：继续；
- `deny`：返回可恢复错误；
- `ask`：持久化 decision request，并等待人工响应或超时。

没有 browser subscriber 时绝不会悄然批准。除非显式设置 `OPENAI4S_UNATTENDED_APPROVAL=allow`，headless execution 默认 deny。Conversation/project/global rule 会持久保留；宽泛 wildcard rule 会扩大后续 authority。

持久化 request 并不是 execution replay。实时 decision 可恢复精确的 blocked call。Daemon 重启后，批准仍存在的 request 只会记录旧操作未执行，并要求显式 fresh continuation/replan。仅用于重启恢复的 `once` grant 会精确匹配、15 分钟后过期，并且只由匹配的新 action 消费。存储的 approval payload 绝不会作为参数 replay。

## Classifier 与内容筛查

### Agent Cell classifier

`OPENAI4S_SAFETY` 支持 `off`、`heuristic`（默认）和 `llm`：

1. 不含已识别 risk token 的代码，通过 static fast path 判为 safe。
2. 高置信度 attack signature 会被阻止。
3. `heuristic` 模式下，含 risk token 但无 signature 的其余代码会被允许。
4. `llm` 模式下，其余代码交给模型；无法解析的回答视为 unsafe，但 key 缺失、model/transport exception 或 outer gate exception 会 fail-open。

只有 **agent-origin** Cell 会被分类。启用 Notebook REPL 后，其中的 user Cell 会跳过该 classifier。Web 与 CLI 均对 agent Cell 应用 code classifier。

### Prompt-injection scanner

部分 Web、search、MCP 与 tool 声明的不可信结果会被扫描。命中时，会在内容前添加 warning 或增加 warning field，以便模型把 payload 当作数据。原内容会被保留。因此，该 scanner 是 annotation mechanism，不是 content-security boundary。可选 model pass 和所有 scanner exception 都会 fail-open。

### Biosecurity policy

启用后，校准过的 biosecurity prompt 会包含在 CLI 和 Web system prompt 中。独立 trajectory screener 当前由 CLI Agent 的 pre-exec path 调用；其中 `BLOCK` 会阻止 Cell，`ESCALATE` 只作为 advisory 写入日志。未配置模型或 screener exception 会返回 `ALLOW`。

Web Gateway 当前的 pre-exec callback 会运行 code classifier，但**不会**调用 `screen_trajectory`；Web biosecurity 目前只是 prompt-level guidance。运维人员不能把 CLI screener 描述为整个 Gateway 的统一强制控制。

### Python audit hook

Python worker 安装的 CPython audit hook 针对 agent-writable root 下 shared object 的 `ctypes.dlopen`。最合适的理解是防范一种逃逸模式。它仅适用于 Python，不调解任意 syscall、preloaded native dependency、R 或 Host-side execution。

## 网络策略

`web_fetch` 会在每个手动处理的 redirect 上应用 private-address guard。`OPENAI4S_ALLOW_PRIVATE_FETCH=1` 是显式 trusted-local override。

`OPENAI4S_EGRESS=allowlist` 将 Host-owned domain allowlist 应用于部分 Host Web/search path，以及授权 shell command 中静态可见的 URL domain。运行时扩展需要获得 permission。默认 `off` 表示没有应用 allowlist；无法解析的 target 会被允许；该层也不拦截任意 socket。它是 worker network namespace 与 Host firewall 的补充，而不是替代。

`OPENAI4S_ALLOW_NETWORK=0` 会禁用 Web/search helper，但 model/provider traffic 与其他 Host-side integration 有各自路径。部署范围的出口控制属于操作系统/网络边界。

## 数据与 Secret 暴露

Agent 可以通过 `host.query` 发起只读 query，但 Store 会拒绝 write 与访问 denylisted internal/secret-bearing table。denylist 包括 model setting、connector、memory、Host-call log、permission record、raw Action Ledger 与 execution attempt、kernel generation、capability/Skill state、delegation state、branch/checkpoint 与 recovery record。这是应用 guard，不是通用 SQL information-flow proof。

`host.credentials.set(name, value)` 仅将明文存放在 in-memory vault。Credential get/list call 不写入 Host-call log；set argument 会脱敏；replay recording 跳过 set。其他用户内容仍可能含 secret，因此应根据内容保护 database、log、workspace、Artifact、compaction history、导出 Notebook 与 portable Session package。

默认 Notebook 是只读 execution trace。`OPENAI4S_NOTEBOOK_REPL=1` 启用任意用户编写的 Python/R 输入及相应 lifecycle route。Agent 与 user execution 仍共享精确 FIFO ownership 与 cancellation，但启用 REPL 会扩大可提交代码的主体范围。

## 远程计算边界 {#remote-compute-boundaries}

`host.compute` 和专用 SSH 科学服务属于 [Partial/Prototype](compute.md)，不是生产 scheduler 或经过加固的 tenant boundary。Remote credential、provider code、container、SSH account、output 与远程保留都需要独立审核。

BYOC worker runtime 分两阶段清理 provider secret：

1. 加载 provider module 前，baseline 会移除 credential-shaped name 与已知 cloud/provider prefix；
2. 从 stdin/fd 3 读取 credential 前，resident runtime 会按已加载 provider 声明的 prefix 再次清理。

Credential 本身不会放进 helper environment。这是**基于名称**的保证：存放在无法识别变量名下的 secret 仍可能被 provider import-time code 看到。Provider module 是可信扩展代码，使用前必须审核。

Remote capability registry 保存 SSH alias 与 service metadata，不保存 private key。`host.fold` 和 `host.score_mutations` 在 service 不存在或没有可解析结果时返回错误；不会合成 scientific output。Capability registration probe 目前只证明 path/executable check，而不证明科学正确性或持续服务健康。

## 已知安全限制

- 没有多用户 identity 或 authorization model。
- Loopback 不提供应用 authentication；non-loopback token mode 有意保持简化。
- `auto` sandbox mode 可能让 worker 无沙箱运行。
- 沙箱大范围允许 Host read，并在 Linux 上保留 Host PID namespace。
- Classifier、injection scanning 与 biosecurity screening 存在已记录的 fail-open/advisory path。
- Egress allowlist 默认关闭，且仅覆盖部分 Host path。
- User Skill、dynamic tool、scientific package、MCP server、provider module 与 remote wrapper 都是可执行扩展面。
- Local compute job 在 Python/R sandbox 外运行。
- Remote compute job state 与 provider lifecycle 不属于持久化、scheduler-grade security boundary。

要获得当前支持的最强安全状态，请使用 `OPENAI4S_KERNEL_SANDBOX=enforce`、专用账户、仅回环访问、最小化审批、私有数据权限和经过测试的备份。
