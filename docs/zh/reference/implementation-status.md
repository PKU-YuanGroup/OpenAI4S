---
title: 实现状态
description: 依据代码验证的 OpenAI4S Core、Web、Recovery、Rendering、Skill、Jupyter、安全与远程计算成熟度标签。
canonical: true
last_verified: 2026-07-14
verification: code-and-tests
status: current
audience: [contributors, operators, users]
verified_commit: a92e736
owner: OpenAI4S maintainers
---

# 实现状态

本页描述源码树中已经实现的内容，而非预期架构。每个标签只适用于所述范围；**Implemented** 不代表可安全暴露于互联网、支持多租户、已经针对所有数据集通过科学验证，或已经与每个外部 provider 做过实时测试。

## 标签

| 标签 | 含义 |
|---|---|
| **Implemented** | 已接入相应产品路径，并且在所述范围内有离线契约测试覆盖 |
| **Partial** | 存在有用的端到端路径，但重要格式、生命周期情形、产品控制或保证仍受限制 |
| **Prototype** | 可执行的早期集成，需要专家监督和针对部署的验证；不代表生产支持承诺 |
| **Planned** | 已在公开架构中命名，但尚没有可用的端到端产品路径 |
| **Historical** | 为兼容性或参考而保留，并非当前首选功能面 |

## 核心运行时

| 领域 | 状态 | 已实现范围与边界 |
|---|---|---|
| Provider-neutral outer loop | **Implemented** | 路由一个有序 native-tool batch、唯一且由 Engine 拥有的 `finalize_response`、一个完整 Python/R Cell，或不执行 action。native call 优先，terminal state 有 ledger 支撑。 |
| Native JSON control tool | **Implemented** | 已接入命名的 `Tool` class、provider-wire normalization、permission/resource metadata、有序结果、有界并行只读 wave，以及单一 registry composition root。 |
| Python Code-as-Action kernel | **Implemented** | 按需启动的持久 subprocess、stdout/stderr 捕获、准确 Cell 行号映射、资源计量、Cell 中同步 Host RPC、取消与 generation identity。 |
| R analysis kernel | **Implemented** | 使用共享 frame/result protocol 与 fd 隔离 I/O 的独立持久 R worker，按需启动。R 仅用于分析，有意不提供 Cell 中 Host RPC 或 `host.submit_output`。 |
| Kernel execution coordination | **Implemented** | Web Agent、可选 user REPL、lifecycle、branch 与 recovery mutation 共享精确的 FIFO ownership/lease；取消只作用于某个 execution owner。 |
| Action Ledger 与完成语义 | **Implemented** | Provider declaration、canonical tool result、attempt、terminal event、structured completion 与确定性 Web fallback message 都会持久化。plain prose 与 max-turn exhaustion 不会被悄然当作完成。 |
| Context compaction 与 raw archive | **Implemented** | 基于 token threshold 的 compaction 会保留 atomic tool group，并把压缩掉的 raw slice 归档到数据目录。 |
| Host capability envelope | **Implemented** | 在适用路径上，native tool 与 Python Host RPC 共享 permission、audit/replay、activity event、file policy、untrusted-result screening 与 service routing。 |
| Object-level provenance | **Partial** | Python worker tagging 覆盖受支持的文件读取、部分 JSON/scalar/indexing 操作与 Artifact write edge。它仅适用于 Python，instrumentation 无法捕获每个第三方 transformation、native object、copy 或手动 file path。缺少 object tag 时仍提供 Cell/file/Artifact provenance。 |
| Standalone one-shot CLI | **Implemented** | `openai4s run` 在没有 Web daemon 的情况下组合共享 engine 与 lazy kernel，但不提供持久 Web Session 生命周期。 |

## Web 工作台与持久化

| 领域 | 状态 | 已实现范围与边界 |
|---|---|---|
| Gateway 与静态 Web UI | **Implemented** | 标准库 HTTP/WebSocket Gateway、project/Session、streaming turn、permission、plan、Artifact、Notebook trace、Customize surface 与安全 Session projection，可作为本地/可信主机上的单用户工作台运行。它不是公开多用户服务器。 |
| HTTP/WebSocket contract | **Implemented** | 当前 route 与 event shape 已在 [Web API](../webapp-api.md) 中手动记录，并由契约测试覆盖。没有生成的 OpenAPI schema，部分历史 response shape 有意保持不一致。 |
| Action Timeline | **Implemented** | 已接入经过脱敏的分页 REST projection，以及 native call、Cell、mutation、delegation 与 finalization 的 UI card。permission wait 仍保留独立交互 card，不会变成 raw Timeline argument。 |
| Versioned Artifact | **Implemented** | 已接入 Cell/control-tool 文件捕获、以追加为主的 version row、best-effort 不可变 snapshot binding、append-only restore、metadata、annotation、ZIP download、environment snapshot 与 version-bound renderer descriptor。只有 snapshot copy 与 binding 成功的版本才具备 restore-grade 不可变性；object-level lineage completeness 仍为 Partial。 |
| Live Notebook trace | **Implemented** | 会投影 immutable Python/R Cell source、output、error、figure、file、retry revision、runtime segment 与 exact ownership。direct protocol-only submission Cell 不显示在 live/read-only Notebook 中，但仍保留在 audit record。 |
| Notebook developer REPL | **Implemented** | 显式设置 `OPENAI4S_NOTEBOOK_REPL=1` 后，可通过同一个 FIFO queue 输入多行 Python/R。默认关闭，user Cell 会绕过 agent code classifier。 |
| Branch、checkpoint、Revert/Undo | **Partial** | 已接入 content-addressed workspace snapshot、branch fork/activate、preview/apply/undo、Artifact/policy/environment，以及 structured plan/review/memory state。cursor checkpoint 是 best-effort；legacy checkpoint 可能缺少 side-state；任意内存变量不是 checkpoint snapshot；没有专用 assistant-message fork 操作入口。 |
| Kernel Recovery Journal | **Partial** | 已实现 status/action、build-first candidate worker、bootstrap manifest、frozen Python Skill sidecar、CAS/Artifact check、replay-safety check 与 atomic publish。没有明确且经过验证的 recipe/symbol coverage 时，先前 namespace state 会保持 Partial，而不是猜测。 |
| Portable Session export/import | **Implemented** | 已接入 deterministic hashed package、path/size/secret validation、identity remapping、downgraded authority 与 ended/view-only quarantine。导入绝不恢复 live namespace，也不是实例备份。 |
| Variable Inspector | **Implemented** | 手动、有界、仅空闲时进行的 Python/R inspection 会避免 custom repr/active binding，且绝不启动 worker。fingerprint 是 sample，不是 namespace serialization。 |
| Scientific renderer registry | **Partial** | 已有 version-bound safe descriptor，以及 3D molecule、2D chemistry、genome record、sequence/MSA、table、image、PDF、sandboxed HTML、LaTeX、Markdown 与 text 的 UI path。若干宣称的 extension/capability 超出了受限本地 parser 的能力（例如 binary columnar table 和完整 chemistry/genome tooling），因此 catalog presence 不代表完整格式支持。 |
| Python/R Notebook export | **Implemented** | 提供确定性的逐语言 `.ipynb` 与稳定的双语言 ZIP bundle；UI 链接 bundle，而逐语言选择仍仅在 API 层提供。 |
| Local model discovery | **Implemented** | 使用已禁用 proxy/redirect 的固定 loopback-only catalog 进行 probe 并返回 suggestion。未知模型在明确配置前保持 conservative。 |

## Skill 与扩展

| 领域 | 状态 | 已实现范围与边界 |
|---|---|---|
| Skill loader 与 progressive disclosure | **Implemented** | 已接入 bundled/user root、frontmatter、enablement、search/load、compile-checked Python sidecar、origin separation 与 Store-generation-safe capability lookup。 |
| Bundled Skill catalog | **Partial** | 当前源码树包含 **32 个 bundled Skill directory**。catalog loading 已实现；许多 scientific Skill 需要外部模型、包、数据服务、GPU 或 SSH，默认离线测试套件不会实时验证这些依赖。每次发布应从源码树计数，不要复制旧的“24”或“28”说法。 |
| User Skill lifecycle | **Implemented** | 已接入 user-space confinement、bundled-name precedence、immutable version、`draft`/`personal` 与 Web `user` origin、project overlay 及 rollback。用户内容仍属于可执行扩展代码。 |
| Dynamic control tool | **Partial** | 已有 Session/project/global manifest、schema/policy check、persistence、hash binding 与 rollback。模型编写的实现会在 enforced OS sandbox 下以新的隔离 Python 进程完成测试和调用；sandbox 不可用时，定义会 fail closed。它仍是有作用域的 dynamic-tool system，而不是通用 plugin ABI 或 hot-unload mechanism。 |
| MCP client | **Partial** | 可复用 stdio JSON-RPC client 与 bundled example server 支持 tool/resource/prompt。sampling、server-initiated request 与通用第三方 connector security guarantee 不在当前 client 范围内。 |

## 安全与运维

| 领域 | 状态 | 已实现范围与边界 |
|---|---|---|
| OS kernel sandbox adapter | **Implemented** | 已接入 Seatbelt/bubblewrap 检测、真实 write/network 自检、private temp、目标 secret read denial、status reporting、`auto` degradation 与 `enforce` failure。可用性与 containment strength 仍取决于操作系统/部署。 |
| Child environment allowlist | **Implemented** | Python/R child 与 descendant 获得 allowlisted environment，而非 daemon secret。它是 spawn boundary，无法保护被故意放入允许 channel 的 secret。 |
| Durable permission broker | **Implemented** | 已接入 scoped rule、pending decision、reconnect/restart semantics、exact expiring continuation grant、默认 unattended denial 与 audit marker。operator 批准的宽泛规则仍可扩大 policy。 |
| Code/content/biosecurity screening | **Partial** | 已接入 Agent Cell classifier 与 injection annotation；CLI 还会调用 trajectory screener。classifier/scanner/model exception 存在 fail-open path，injection 只做 annotation，`ESCALATE` 仅为 advisory，Web 当前不调用 trajectory screen。 |
| Workbench authentication | **Partial** | 已实现面向可信操作员的 loopback deployment 与可选 process token/origin check。没有 user identity、role、tenant isolation、TLS termination 或 public-service hardening。 |
| Backup 与 disaster recovery | **Partial** | 已有持久 application state 与 Session portability，但没有内置 whole-instance backup scheduler、cross-file hot snapshot、down-migration 或自动 disaster-recovery orchestrator。运维人员必须在停止服务后备份完整数据目录。 |

## 外部平台

| 领域 | 状态 | 已实现范围与边界 |
|---|---|---|
| 通用 `host.compute` | **Prototype** | 已有 `ssh:<alias>` 与发现的 `byoc:<id>` routing、SDK handle、NVIDIA provider code 与 result method。Submission 需要 approval；result/cancel/close 不会再次请求 approval，legacy direct SSH/SCP helper 则绕过 Tool permission gate。job/sandbox state 位于进程内；没有 manager background poller；通用 SSH staging/exit/output harvest 不完整；实时外部测试需显式选择。参见[远程计算](../compute.md)。 |
| `host.fold` / `host.score_mutations` | **Partial** | 已注册的 SSH wrapper 会返回 structured real result 或明确 no-fabrication error。provisioning、model/weight attestation、scientific validation、持续健康状态与远程保留仍由部署方负责。 |
| Remote capability provisioner | **Prototype** | LLM specialist 可通过经批准的 shell action 检查/provision，并仅在 probe 后注册 path/executable。它不是 deterministic installer 或 scientific verifier。 |
| Local compute-job API | **Prototype** | 可信本地 UI 所用的 Host-side process launch、内存 listing/output state 与 cancellation 已存在。Job 默认使用受限的共享根目录，也可使用调用方指定的相对子目录；系统不会自动创建 job-ID 目录。Job metadata 不是持久 registry；只有 command 创建的 working file 可能保留。此功能面位于 worker sandbox 外，绝不能暴露给不可信用户。 |
| Model endpoint provider execution | **Partial** | endpoint/configuration record 与 discovery surface 已存在；provider architecture 描述的完整 scoped inference execution path 尚未统一接线。 |
| SLURM、Kubernetes、Modal 与 laboratory provider | **Planned** | 这些已命名的公开 extension category 在当前源码树中没有可工作的 built-in end-to-end provider。“Planned”不包含发布日期承诺。 |

## Jupyter 兼容性

| 领域 | 状态 | 已实现范围与边界 |
|---|---|---|
| KernelSpec export/install | **Implemented** | 已接入纯标准库 description、export、user/prefix install、replacement check 与 Python/R spec。 |
| Standalone Jupyter wire bridge | **Partial** | 可选 `ipykernel>=7,<8` bridge 通过现有 worker protocol 支持持久 standalone Python/R execution、text stream、structured error、interrupt 与 shutdown。它有独立 namespace，不具备 Web Session Host RPC、Artifact、Ledger、permission、queue 或 recovery。rich display/comm、debugger、completion、inspection、history、stdin 与任意 user expression 均不存在。 |
| 将 Jupyter 附加到 live Web Session | **Planned** | 没有受支持的 adapter 能将外部 Jupyter frontend 附加到已有 Workbench namespace 或 Host RPC context。 |

## 兼容功能面

| 领域 | 状态 | 边界 |
|---|---|---|
| Fenced legacy `tool` block | **Historical** | parser 为已保存 prompt/旧 client 保留，但 native provider JSON tool 才是公开 control plane。 |
| Minimal `server/daemon.py` UI | **Historical** | 源码树中保留一个较小的 compatibility server；`openai4s serve` 组合完整 Gateway workbench。 |
| Compatibility facade | **Implemented** | `gateway.py`、`host_dispatch.py`、`store.py`、`sdk/host.py` 与部分 import 在行为提取至专门 service 的同时保留 public contract。它们是 composition boundary，不是新 feature 的归属位置。 |

## 验证策略

默认 `uv run pytest` 套件离线运行，并排除需要外部网络、实时模型、GPU、SSH、Docker、浏览器或实验室硬件的 marker。测试通过验证的是 deterministic contract，而非外部环境。Kernel、Gateway/WebSocket、浏览器、操作系统沙箱、远程计算与科学模型变更，需要按照[发行验证](../release-validation.md)和[运维](../operations/)执行有针对性的真实运行时检查。

当实现与文档不一致时，以代码和可执行测试为准。接入、移除或实质收窄某个功能面的同一项变更中，应同步更新本页。
