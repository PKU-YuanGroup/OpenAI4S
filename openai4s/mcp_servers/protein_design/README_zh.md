# 可审计的 protein-design MCP server

[English](README.md)

本包通过 stdio MCP 暴露九个原子 protein-design 工具。server 和编排层只使用
Python 标准库；RFdiffusion、ProteinMPNN、ColabFold、PyRosetta、ESM-2 和 OpenMM
均在单独配置的环境中运行，不会作为 OpenAI4S core 的 import 依赖。

## 哪些部分是真实实现，哪些需要部署者准备

这不是只写了 schema 的示例，而是真实的 MCP server：它通过 stdio 完成 MCP
`initialize`、`tools/list` 和 `tools/call` 交互，启动已配置的 backend 子进程，验证输入
与输出，并返回结构化终态结果。OpenAI4S 使用与其他 custom MCP server 相同的持久化
connector manager 启动它。

它不是内置模型 runtime。仅把本包放在源码树中，不会安装 RFdiffusion、ProteinMPNN、
ColabFold、PyRosetta、ESM-2、OpenMM、checkpoint 或 GPU driver。需要在
**Customize → Connectors** 中注册，并 bring up 下述 backend 配置。Agent 调用默认把
路径 root 绑定到当前会话工作区，不会相对 daemon 的源码 checkout 解释 target 和资产。默认离线
测试使用真实 MCP 进程和 fake scientific executable 验证命令构造；每个真实 GPU backend
是否成功执行，仍属于 operator 部署验收。

## Connector 配置

在 **Customize → Connectors** 的 connector directory 中添加 **Protein Design**。
添加表示明确启用这项能力，但此时不会启动进程；OpenAI4S 只会在
第一次工具发现或工具调用时懒启动 MCP server。等价的自定义 stdio command 使用
OpenAI4S 安装环境的 Python 解释器，参数为：

```text
-m openai4s.mcp_servers.protein_design
```

Agent 路径会把 `OPENAI4S_PROTEIN_DESIGN_ROOT` 绑定为当前会话工作区；operator 也可
在 connector 设置中显式指定其他 root。所有输入路径都会在这个 root 下解析，越界
路径会被拒绝。

分别用以下环境变量冻结 backend revision：

- `OPENAI4S_RFDIFFUSION_REVISION`
- `OPENAI4S_PROTEINMPNN_REVISION`
- `OPENAI4S_COLABFOLD_REVISION`
- `OPENAI4S_PYROSETTA_REVISION`
- `OPENAI4S_ESM2_REVISION`
- `OPENAI4S_OPENMM_REVISION`

命令配置使用 JSON 字符串数组，不是 shell 片段：

- `OPENAI4S_RFDIFFUSION_COMMAND`，或者 `OPENAI4S_RFDIFFUSION_PATH` 加
  `OPENAI4S_RFDIFFUSION_PYTHON`；
- `OPENAI4S_PROTEINMPNN_COMMAND`，或者 `OPENAI4S_PROTEINMPNN_PATH` 加
  `OPENAI4S_PROTEINMPNN_PYTHON`；
- `OPENAI4S_COLABFOLD_COMMAND`；
- 可选依赖 worker 使用 `OPENAI4S_PYROSETTA_PYTHON`、`OPENAI4S_ESM2_PYTHON`
  和 `OPENAI4S_OPENMM_PYTHON`。

Blind complex prediction 还要求设置 `OPENAI4S_PROTEIN_DESIGN_OFFLINE_PREFIX`：
这个 JSON 数组必须明确创建无网络执行边界，例如包含 `--unshare-net` 的 bubblewrap
前缀。仅设置 offline 环境变量不能被当作网络隔离证明。

RFdiffusion、ProteinMPNN 和 ESM-2 调用都提供 checkpoint 路径与预期 SHA-256。未给
路径时，Agent 先询问用户是否已有该文件；`stage_model_asset` 可导入经审批的本地路径，
用户没有文件时才走常规审批下载路径。connector 不会在科学调用里暗中下载权重。
内置 Agent runtime 只在同一 MCP 进程用同一 tool、backend revision、checkpoint digest
和 execution target 成功执行 `run_mode=canary` 后，才放行 `run_mode=formal`。
ColabFold prediction 改用 JSON bundle manifest，其中相对 `data_dir`
与 `files` 要列出完整模型数据树及 SHA-256；server 会拒绝 symlink 和未列出的文件，
并在启动前验证全部内容。

## 工具边界

| 工具 | 契约 |
| --- | --- |
| `generate_backbone` | 单次 RFdiffusion attempt，显式 seed/chain/hotspot，保留 PDB、`.trb` 和终态 manifest。 |
| `design_sequence` | ProteinMPNN design chain 和 chain-local fixed position，运行后独立验证序列和 residue map。 |
| `predict_structure` | 冻结参数、no-MSA、no-template 的 monomer prediction，并保留 raw scores。 |
| `predict_complex` | OS 级断网的 blind complex prediction，保留 raw PAE、interface PAE 和 ipTM。 |
| `rosetta_score` | Rosetta 物理能量证据。 |
| `rosetta_relax` | 带 seed 的 FastRelax 和显式输出结构。 |
| `rosetta_interface_score` | dG、dSASA、packstat，以及语义正确命名的 unsatisfied-H-bond delta。 |
| `score_stability` | ESM-2 masked pseudo-log-likelihood，只标为 sequence naturalness。 |
| `energy_minimize` | OpenMM refinement 证据，不能证明设计能够折叠、结合或实现功能。 |

这里有意不提供 `design_binder`、hotspot suggestion、近似 interface analyzer 或伪 status
工具。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | Service 的窄导出。 |
| [`__main__.py`](__main__.py) | stdio module entry point。 |
| [`schemas.py`](schemas.py) | 九个工具的封闭 MCP schema 和证据描述。 |
| [`server.py`](server.py) | 最小 MCP JSON-RPC framing 和 structured result 投影。 |
| [`service.py`](service.py) | 路径约束、命令构造、digest、终态记录和运行后验证。 |
| [`scientific_backend.py`](scientific_backend.py) | 单独执行的 PyRosetta、ESM-2、OpenMM 可选依赖 worker。 |
| [`README.md`](README.md) | 英文配置、边界和文件清单。 |
| [`README_zh.md`](README_zh.md) | 中文配置、边界和文件清单。 |

## 上游影响

工具选择和部分 backend wrapper 思路参考了 Apache-2.0 许可的
`jasonkim8652/protein-design-mcp` revision
`7a45f13d5c7667513f4b3cfc47e472f3209b1be1`。本实现围绕 OpenAI4S 的 stdlib、
provenance 和可复现约束重写，没有 vendoring 该项目、其依赖、容器或权重。各模型包和
权重继续遵循各自的上游许可证。
