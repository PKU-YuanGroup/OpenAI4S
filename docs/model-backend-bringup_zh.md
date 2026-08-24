# 模型后端 bring-up 与准入

[English](model-backend-bringup.md)

OpenAI4S 把模型发现、加速器路由、模型资产获取、后端 bring-up 和正式科学计算视为彼此
独立的状态。这是所有 Agent 入口共同遵守的框架级契约，并非只存在于某一个 Skill 的
工作流说明中。

Protein Design MCP connector 是第一个采用者。其他依赖 checkpoint 的 connector 可以
复用相同的 Host 工具和准入状态机，但每个 connector 仍需实现真实推理 canary，并解析
自己的输出。框架不能因为 GPU 可见、包已安装，或某个文件名看起来像 checkpoint，就
推断模型已经可用。

## 状态模型

| 状态 | 含义 | 不能证明什么 |
| --- | --- | --- |
| discovered | Agent 能看到 connector 或工具。 | 后端、GPU 或权重已经存在。 |
| route selected | 用户选择了 `local` 或某个 `ssh:<alias>` 执行目标。 | 路线当前可达或已经部署。 |
| staged | 精确模型字节已导入 `model-assets/` 并计算摘要。 | 后端能加载或运行这些字节。 |
| admitted | 真实最小推理成功，解析后的终态记录在所选路线报告了预期 checkpoint digest。 | 后续输入的实验有效性或科学成功。 |
| formal | 正式科学调用复用了完全一致的准入身份。 | 超出该次调用真实返回内容的证据。 |

一次准入身份同时绑定：

- connector namespace 与 operation；
- 不可变 backend revision；
- checkpoint SHA-256；
- execution target。

其中任意一项变化都必须重新运行 canary。准入有意只在 backend 进程生命周期内有效：
进程重启后同样要重新运行 canary。新进程尚未证明其环境、import、driver 访问、模型加载、
推理路径和输出 parser 仍然正常。

## 用户可见流程

1. 先选择具体 operation。通用 connector 发现阶段不得询问或下载 checkpoint。
2. operation 需要 GPU 时调用 `host.accelerator_status()`。它先探测本地 NVIDIA 硬件，
   再报告已配置的 SSH GPU 路线。存在多个候选时，Agent 必须让用户选择，不能静默偏向
   本地或远程。
3. operation 需要 checkpoint 且用户未给路径时，停下来询问用户是否已有本地文件。
   这个问题未回答前，不得搜索或下载权重。
4. 用户有文件时调用 `host.stage_model_asset(source_path, ...)`。这是需要精确路径审批的
   导入操作；它拒绝 secret 路径和 symlink source，分块复制，并可核对预期 SHA-256。
5. 用户没有文件时，先请求联网授权，再走普通受控下载路径。下载源不受框架维护的模型
   白名单限制；本次获批的 source、revision 与最终 SHA-256 会成为这次 bring-up 的证据。
   下载完成后仍通过同一个 staging 工具导入。
6. 用 `run_mode="canary"` 运行真实最小推理，adapter、backend revision、staged checkpoint
   digest 与 execution target 必须和预定的正式调用一致。
7. 只有 connector 返回成功终态记录、解析结果可用，并且观测到的 checkpoint digest
   等于请求 digest 时才准入。失败 canary 仍要保留为失败终态证据。
8. 用 `run_mode="formal"` 重试原始 operation。没有完全一致的当前进程准入时，connector
   必须拒绝执行。

缺少源码、环境或权重是 bring-up 条件，不能仅凭「没有预装」就宣布 backend 不可用。

## 框架功能面

### 加速器路由

`host.accelerator_status()` 返回本地探测证据、已配置 SSH 路线、有序候选执行目标和
`selection_required`。硬件可见性与 provider 注册、模型就绪状态互相独立。因此，SSH
registry 为空不能说明本机无 GPU；`nvidia-smi` 成功也不能说明模型后端已经安装。

既有 `host.remote_gpu_status()` 继续提供 SSH capability 的详细视图。选择 SSH 路线后，
仍必须进行实时可达性和 backend preflight。

### 可移植资产 staging

`host.stage_model_asset(...)` 把一个 operator 持有的普通文件导入当前会话 workspace 的
`model-assets/` 下。结果包含可移植相对路径、字节数、SHA-256、`status: "staged"` 和
`admitted: false`。

该工具既不下载文件，也不进行准入。下载审批与 backend 准入是两个独立控制。某些
connector 的模型 bundle 可以使用 manifest 固定每个数据文件，不能把目录名本身当作
稳定模型身份。

### 可复用准入 ledger

`openai4s.host.model_admission.ModelAdmissionLedger` 提供通用身份计算和状态转换。connector
在真实 backend 进程内持有 ledger；只有真实 handler 与 output parser 都成功后，才能把
canary 观测到的 checkpoint digest 交给 ledger。

这种分工是有意的：通用框架能够验证身份和状态转换，只有 connector 才知道
RFdiffusion `.trb`、folding confidence payload、language-model score 文件或其他后端特有
输出是否完整且可解析。

## Protein Design 的采用方式

内置 **Protein Design** connector 把这套契约用于 RFdiffusion、ProteinMPNN、ColabFold
模型数据 bundle 和依赖 ESM-2 checkpoint 的调用。connector manager 会为 Agent 调用启用
准入强制。正式调用缺少匹配 canary 时，会产生并持久保存失败终态记录，而不是从 attempt
记账中消失。

connector 当前以本地 stdio 进程执行。用户选择 SSH execution target 时，在 verified
remote adapter 尚不存在的情况下会明确报不支持，不会偷偷改成本地执行。没有 checkpoint
身份的 Rosetta 和 OpenMM operation 仍遵守各自的 revision、seed、终态记录与输出验证契约。

后端环境变量、网络隔离要求、逐工具 schema 与科学证据边界见
[connector package 文档](../openai4s/mcp_servers/protein_design/README_zh.md)。

## Connector 可移植性与生命周期

仓库内置 Python connector 的持久化记录保存 `@openai4s/python`，而不是某台服务器虚拟
环境的绝对路径。只有启动 connector 时，该 token 才解析为当前 daemon interpreter。
启动迁移只修正匹配的旧内置记录，不改写任意 custom command。

从 **Customize → Connectors** 添加 connector 只是启用配置，并不会立即启动 server；
工具发现或首次调用才会懒启动。编辑启动配置会断开缓存进程，使下一次操作使用新配置。
浏览器把环境值视为 write-only：不会返回已有值；可以显式替换选中的值，也可以显式删除
选中的变量名。

## 接入另一个依赖 checkpoint 的 connector

新 connector 应当：

1. 在封闭 schema 中暴露 immutable backend revision、checkpoint digest、execution target
   和 `canary|formal` run mode；
2. 以 connector 专属 namespace 使用 `ModelAdmissionLedger`；
3. 启动 backend 前核对 checkpoint 字节；
4. 为成功和每条失败路径都写出终态记录；
5. canary 与 formal 使用同一个真实 handler，只缩小 canary 输入或工作量；
6. 调用 `admit()` 前解析并验证 backend-specific 输出；
7. formal 执行前要求完全一致的 admission；
8. 如实处理执行路线——不支持的 remote route 必须明确失败。

除非 connector 能独立重建 canary 所证明的全部 runtime 事实，否则不应把 admission 持久化
到 backend restart 之后。

## 验证范围

离线测试覆盖本地/SSH 路由顺序和用户选择、需审批的资产 staging、digest mismatch 与
symlink 拒绝、准入身份和进程重启、Protein Design canary/formal 强制、MCP 生命周期与
shape 归一化、connector command 迁移、启动配置编辑及静态 UI 契约。真实 GPU 推理仍是
部署验收门禁，因为默认 CI 有意保持离线，也不会安装科学模型 runtime 或 checkpoint。
