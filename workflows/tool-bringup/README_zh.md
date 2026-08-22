# `workflows/tool-bringup/`

**带冻结、可验证记录的工具 bring-up** — 设计与预测工具在战役开始时**并不**预装：运行必须从公开源构建工具环境、下载并校验权重、编写运行 adapter、在真实靶标上用 canary 验证工具、证明 canary 输出可解析且下游序列设计 adapter 能消费，并把 environment generation identity、adapter 与权重校验和、累计运行时与累计成本冻结进 `bringup.json`。只有通过校验、且准入状态如此声明的记录才能继续。14 个用例（2 条准入／恢复路径和 12 条拒绝路径）各自钉住这份契约的一项检查，包括只有评估方持有的参考摘要才能识破的全量伪造用例。

Steps: `tool_bringup`, `verify_bringup`
Permissions: `environment:apply`, `network:weights`, `workspace:read`, `workspace:write`
Declared artifacts: `bringup/bringup.json`, `bringup/adapter.py`, `weights/model.weights`, `bringup/canary_output.json`, `bringup/downstream_result.json`

| 文件 | 用途 |
| --- | --- |
| `workflow.json` | 带版本的清单：steps、权限、声明的产物、失败条件与下列用例。版本 `1.0.0`。用 JSON 而非 YAML 的理由与内核一致，带版本则是因为用例能被悄悄改动的基准跨时间什么也衡量不了。 |

## Cases

| 用例 | 声明的结果 | 它钉住什么 |
| --- | --- | --- |
| `tool-bringup/pass` | `provenance` | 一次完整 bring-up 通过参考摘要校验并被准入 |
| `tool-bringup/recovered` | `recovered` | 失败的 canary 被冻结、重跑，并带着精确的 `failed → passed` 历史及累计记账重新准入 |
| `tool-bringup/recovery-budget-exceeded` | `failure` | retry 不能替换第一次尝试冻结的预算；累计成本超出它就拒绝准入 |
| `tool-bringup/missing-record` | `failure` | 完全没有记录时在任何检查运行前以 `BringupError` 拒绝 |
| `tool-bringup/fail-build` | `failure` | 环境 apply 失败时冻结一次拒绝尝试，而不是在不存在的 generation 上继续跑 canary |
| `tool-bringup/spec-mismatch` | `failure` | 安装包集合不匹配 `design-tool==1.0.0` 时拒绝准入 |
| `tool-bringup/canary-no-output` | `failure` | 退出 0 但无输出的 canary 产生不了任何可验证的东西 |
| `tool-bringup/unparseable-canary` | `failure` | 不能按声明格式解析的输出拒绝准入 |
| `tool-bringup/downstream-refused` | `failure` | 不愿消费输出的下游 adapter 拒绝准入 |
| `tool-bringup/tampered-weights` | `failure` | 翻转一个权重字节被记录摘要识破 |
| `tool-bringup/canary-output-deleted` | `failure` | 记录声称的输出文件已消失会被识破 |
| `tool-bringup/forged-record` | `failure` | 载荷、摘要、封印全部重写——只有评估方持有的参考摘要能发现 |
| `tool-bringup/wrong-weights` | `failure` | 诚实下载但与参考摘要不符的权重会被识破 |
| `tool-bringup/budget-exceeded` | `failure` | 超出声明预算的成本拒绝准入 |

## Failure conditions the manifest declares

- bring-up 记录缺失或被改写却仍被采信
- 环境构建失败，或安装包集合与声明 spec 不一致
- 权重文件与记录摘要或评估方参考摘要不符
- canary 输出缺失、不可解析或字段不全
- 下游 adapter 未消费输出或其证明未通过校验
- 单次尝试或恢复 campaign 的累计成本超出首次冻结的预算却仍被准入

## The `bringup.json` contract

运行冻结在 `bringup/bringup.json` 下的记录包含 `schema_version`；自证的 `record_sha256`；`tool`（名称、版本、来源、revision、带受限 path/sha256/size 的 adapter 对象，以及作为构建环境身份的 `env_name`/`env_generation`）；`weights`（每个文件的 path、sha256、size、source、`verified`）；`canary`（target、schema v1 固定为 `python bin/tool --target … --weights …` 且不含解释器或临时根目录绝对路径的可移植逻辑 command、带摘要的 outputs、含 status/format/fields 的解析证明与下游消费证明）；`admission`（状态与理由）；`runtime`（各次 wall time 之和，以及带 status/reason/wall_s/gpu_h 的非空尝试记录）；以及 `cost`（各次 `gpu_h` 之和，受第一次尝试冻结的 `budget_hours` 约束）。retry 会同时累计运行时与成本，不能替换预算。只有最终尝试 passed 且累计成本仍在预算内时，准入才是 `verified`。校验器是 `openai4s.benchmark.bringup.verify_bringup`，workflow benchmark step 在任一检查失败或报告未准入时抛异常——记录缺失以 `BringupError` 拒绝，其余以拼接的问题列表拒绝。

`record_sha256` 只证明内部一致性：任何人都可以同步重写权重、canary 与下游证明、三处记录摘要，再重新封印记录。真值从精确的 `expected_weights` 这条缝进入——评估方从 reference 构建冻结的摘要——`forged-record` 正是通过保持所有内部关系一致、同时改变受 reference 约束的字节来证明这一点。真实的 binder/MD 战役 query 会要求 agent 运行产出这份记录，evaluator 会带着完整参考摘要集合调用同一个 `verify_bringup`；那就是“只有 PASS 才准进入 production”的机制落点。

两处边界是刻意为之并已写明的。离线 workflow benchmark 通过真实的 `EnvironmentStore` 事务构建环境（注入 fake 包管理器），并用测试解释器执行安装好的工具 fixture；`bringup.json` 只记录可移植的逻辑 command（`python bin/tool …`），绝不记录 `sys.executable` 或临时目录绝对路径。记录中的环境解释器是 stub，因此“未预装”隔离的强制执行仍留待后续阶段。`env_generation` 证明绑定到用例根目录下 `environments/<env>/generations/<id>/manifest.json` 的身份与受限 prefix；真实战役需在提交中保持这一相对布局。
