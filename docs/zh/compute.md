---
title: 远程计算
description: 通用远程作业与专用 SSH 科学服务当前的 Partial/Prototype 支持边界。
canonical: true
last_verified: 2026-07-14
verification: code-and-tests
implementation_status: Partial/Prototype
status: current
audience: [operators, contributors, users]
verified_commit: a92e736
owner: OpenAI4S maintainers
---

# 远程计算

远程计算属于 **Partial/Prototype**。OpenAI4S 包含真实的 transport、provider、registry、approval 与 SSH service code，但尚不是持久 scheduler，也不是生产级不可信执行边界。依赖其科学结果前，必须针对具体部署验证每个 provider 与 remote wrapper。

系统提供两个互不相同的 API：

| API | 用途 | 当前状态 |
|---|---|---|
| `host.compute` | 向 `ssh:<alias>` 或 `byoc:<id>` 分发通用 command/job | **Prototype**：具备可用 scaffolding，但 job state 位于进程内，并存在 provider-specific gap |
| `host.fold` / `host.score_mutations` | 从已验证 SSH capability registry 中选择同步、专用 scientific wrapper | **Partial**：具有真实 no-fabrication path，但 provisioning 与 validation 取决于部署 |

不要把其中一条路径描述为另一条的实现。`host.fold` 不通过 `host.compute` 提交，而通用 `host.compute` job 也不会继承 folding service 的 result validation。

## `host.compute`：通用作业分发

面向 worker 的 [compute SDK](https://github.com/PKU-YuanGroup/OpenAI4S/blob/main/openai4s/sdk/compute.py) 会将 `compute_*` Host RPC call 发送给 Session 的 [ComputeManager](https://github.com/PKU-YuanGroup/OpenAI4S/blob/main/openai4s/compute/manager.py)。Provider family 命名为：

- `ssh:<alias>`：daemon account 已配置的 SSH name；
- `byoc:<id>`：从 `skills/remote-compute-<id>/provider.json` 与 `provider.py` 中发现的 provider。

仓库当前内置 SSH recipe 和一个 BYOC provider：`byoc:nvidia`。NVIDIA provider 支持 provider-defined Docker/NIM mode，但须满足本地 Docker/GPU 或 managed-service 前置条件并具备凭据。出现在 catalog 中不代表这些外部前置条件可用。

代表性的 API 形态如下：

```python
compute = host.compute.create(
    "byoc:nvidia",
    provider_params={"nvidia": {"mode": "hosted"}},
)
job = compute.submit_job(
    intent="run a validated inference script",
    command="bash run_inference.sh",
    inputs=[{"src": "run_inference.sh"}, {"src": "input.json"}],
    outputs=["out/*"],
    timeout_seconds=900,
)

status = job.result()
```

Approval 覆盖范围小于 SDK surface。`compute_submit` 需要 approval；result polling、cancellation 与 provider close 不会再次请求 approval，legacy direct SSH/SCP helper 也不进入 Tool permission gate。这些仍是有风险的外部操作，会由 Host-side code 审计或路由，但这并不等同于 approval。应严格缩小 submission target，并限制 daemon account 的 SSH identity。

### 当前 Prototype 限制

- Job record、concurrency counter 与 BYOC sandbox handle 位于 Session 进程内的 `ComputeManager` 中，不存入 SQLite，也不会以 scheduler job 的形式跨 daemon/dispatcher replacement 存活。
- Manager 当前没有实现旧版 SDK comment 描述的 background polling/notification loop。调用方必须轮询 `job.result()`；不要依赖 `compute_done` notification delivery。
- 通用 SSH submit path 会启动 remote script，但当前不会自动 stage 声明的 `inputs`、持久保存可靠的 remote exit code 或收取声明的 scientific `outputs`；result path 只拉取日志，并在原处保留 remote work directory。应使用明确且经过验证的 transfer operation，并检查 remote state。
- BYOC path 具有 provider-specific staging 与 harvest behavior，但并非 adversarial-provider boundary。只启用经过审核的 provider code 和可信 output source。
- Provider discovery 只证明 metadata 与 Python shim 存在，不会 probe live credential、capacity、image、endpoint compatibility、scientific model version 或 output correctness。
- 默认测试使用 fake 且离线运行。Docker、GPU、SSH、external API 与 large-output behavior 需要显式选择的实时验证。
- 本地 Gateway `/compute/jobs` 是另一个 Host-side job surface，不是 `host.compute` provider sandbox。

由于状态不持久，启动昂贵任务前，应在 live Job object 外记录 provider、remote work directory/sandbox identity、command revision、input hash 与 expected output。工作台 Stop button 或 kernel loss 不等于取消远程 scheduler；必须明确取消或清理 provider resource。

## BYOC 凭据边界

Host 只读取 provider `provider.json` 声明的 secret environment name，并通过 stdin/fd 3 把值发送给 helper，而不是放入 job process environment。Helper 在导入 provider code 前执行 baseline secret-name/prefix scrubbing，并在读取 credential 前执行 provider-declared prefix scrubbing。

这是一种基于名称的 heuristic。放在无法识别变量名下的 secret 可能在 provider import 时仍可见。Provider module 是可执行的可信扩展，不是纯数据 manifest。参见[安全架构](security.md#remote-compute-boundaries)。

内置 NVIDIA provider 声明 `NGC_API_KEY` 与 `NVIDIA_API_KEY`。不要把这些值放入源码、job command、provider parameter、log 或 Artifact。

## `host.fold` 与 `host.score_mutations`

这些服务使用 [remote capability registry](https://github.com/PKU-YuanGroup/OpenAI4S/blob/main/openai4s/compute/registry.py)，并通过 [RemoteScienceService](https://github.com/PKU-YuanGroup/OpenAI4S/blob/main/openai4s/host/remote_science.py) 直接经 SSH 运行已注册 wrapper。

### `host.fold`

`host.fold(sequence, ...)` 接受一条 protein sequence，将其清理为支持的 amino-acid alphabet，当前路径上限为 1,200 个 residue，并调用已注册的 `fold` script。预期 wrapper 返回 structured manifest 与 base64-encoded PDB，可选 confidence 与 provenance block。当前契约只支持 single-sequence，不宣称具备 MSA workflow。

### `host.score_mutations`

`host.score_mutations(sequence, ...)` 接受不超过 1,024 个 residue 的 protein sequence，并调用已注册的 `score_mutations` wrapper。预期结果包含 structured summary 与 encoded CSV score table。

### No-fabrication 契约

当不存在具备 capability 的 host、SSH 失败、wrapper 超时，或缺少/无法解析必要 structured output 时，两项服务都会返回单键 `{"error": ...}` 结果。它们不会用随机 coordinate、heuristic score 或捏造 model output 替代缺失结果。

该契约证明 Host service 没有故意 fallback fabrication，但不能独立验证 remote wrapper 是否运行了所声明模型、使用了所声明 weight，或生成科学有效的输出。

## Host 与 Capability 注册

Settings 可以注册一个已存在于 daemon account `~/.ssh/config` 中的 SSH alias。OpenAI4S 把 host metadata 与 capability record 存在 `remote_compute.json` 中；private key 与 ssh-agent credential 留在 registry 外。

`host.register_remote_capability(...)` 会经 SSH 验证 structured `path_exists` 或 `executable_exists` probe，再记录 capability。probe 成功只代表当时存在该 path 或 binary，并不代表：

- 对 wrapper content 的 cryptographic attestation；
- model-weight/version verification；
- scientific golden test；
- ongoing health check；
- 可不受限制使用 remote account 的授权。

内置 `REMOTE_GPU_PROVISIONER` 是 LLM-driven specialist，会检查 host、运行经批准的 shell step，并仅在 probe 成功后注册。它不是 deterministic installer，成功 delegation 也不能等同于受支持且可复现的 production service。运维人员应通过经过审核的 infrastructure provision wrapper，固定 version/hash，运行 known-answer test，再注册已验证 path。

## Provenance 与结果处理

专用服务可以把 wrapper 提供的 remote environment/provenance JSON block 附加到 producing Cell 的 Artifact environment snapshot。缺失或 malformed remote provenance 不会导致失败。应将其视为远程提供的 metadata，而非 attestation。

对于任何远程路径，至少应捕获：

- provider/SSH alias 与 remote directory 或 sandbox identity；
- command/wrapper revision 与 model/weight identifier；
- input Artifact version ID 与 content hash；
- container/environment lock 信息；
- start/end time、exit status、stdout/stderr tail 与 output hash；
- expected output file 存在且可解析的显式确认；
- remote retention 与 cleanup decision。

只有通过验证后，才把结果提升为 versioned Artifact。`hpc/` 下收取的文件属于普通实例数据，必须纳入 backup/retention policy。

## 配置

| 设置 | 用途 |
|---|---|
| `~/.ssh/config` 与 ssh-agent/key file | SSH alias 与 authentication；除非单独捕获，否则不在 OpenAI4S backup 内 |
| `OPENAI4S_INSTALL_ID` | 可选稳定 BYOC owner tag；受管部署应明确设置 |
| `NVIDIA_API_KEY` / `NGC_API_KEY` | 内置 NVIDIA provider credential |
| `OPENAI4S_FOLD_SSH` | registry 为空时，为初始 folding SSH host 提供的 compatibility seed |
| `OPENAI4S_FOLD_SCRIPT` | Compatibility/default fold wrapper path |
| `OPENAI4S_FOLD_JOBS_DIR` | Remote folding work root |
| `OPENAI4S_ESM_JOBS_DIR` | Remote mutation-scoring work root |

配置本身不会使 capability 变为 live。以 daemon account 身份非交互地 probe SSH，使用已知输入测试精确 wrapper，验证 result parsing，并测试 cancellation/cleanup。

## 就绪检查清单

将远程计算用于重要工作前：

1. 使用专用 remote account 与 least-privilege SSH key。
2. 审核 provider 与 wrapper code；固定 image、dependency、model weight 与 hash。
3. 验证 network destination 与 credential forwarding。
4. 运行一个小型 known-answer job，并与独立预期比较输出。
5. 测试 timeout、non-zero exit、malformed output、SSH connection lost、cancellation 与 daemon restart。
6. 确认所选 provider 会自动传输哪些 input/output；不要从另一个 provider family 推断。
7. 记录 remote cleanup 与 cost ownership。
8. 由于 `host.compute` Job object 不持久，应保留使用已记录 remote identifier 的手动恢复路径。

在某项具体部署通过这些检查前，应把远程计算报告为不可用或实验性，而不是暗示存在 UI card 或 Skill 就能保证执行。
