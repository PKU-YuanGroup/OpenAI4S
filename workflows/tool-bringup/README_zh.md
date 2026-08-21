# `workflows/tool-bringup/`

**带冻结、可验证记录的工具 bring-up** — 设计与预测工具在战役开始时**并不**预装：运行必须从公开源构建工具环境、下载并校验权重、编写运行 adapter、在真实靶标上用 canary 验证工具、证明 canary 输出可解析且下游序列设计 adapter 能消费，并把 image digest、权重校验和、运行时与成本冻结进 `bringup.json`。只有通过校验、且准入状态如此声明的记录才能继续。11 个用例各自钉住这份契约的一项检查，包括只有评估方持有的参考摘要才能识破的全量伪造用例。

Steps: `tool_bringup`, `verify_bringup`
Permissions: `environment:apply`, `network:weights`, `workspace:read`
Declared artifacts: `bringup/bringup.json`, `weights/model.weights`, `bringup/canary_output.json`, `bringup/downstream_result.json`

| 文件 | 用途 |
| --- | --- |
| `workflow.json` | 带版本的清单：steps、权限、声明的产物、失败条件与下列用例。版本 `1.0.0`。用 JSON 而非 YAML 的理由与内核一致，带版本则是因为用例能被悄悄改动的基准跨时间什么也衡量不了。 |

## Cases

| 用例 | 声明的结果 | 它钉住什么 |
| --- | --- | --- |
| `tool-bringup/pass` | `provenance` | 一次完整 bring-up 通过参考摘要校验并被准入 |
| `tool-bringup/recovered` | `recovered` | 失败的 canary 被冻结进记录、重跑、带着完整尝试历史重新准入 |
| `tool-bringup/missing-record` | `failure` | 完全没有记录时在任何检查运行前以 `BringupError` 拒绝 |
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
- 权重文件与记录摘要或评估方参考摘要不符
- canary 输出缺失、不可解析或字段不全
- 下游 adapter 未消费输出或其证明未通过校验
- 成本超出声明预算仍被准入

## The `bringup.json` contract

运行冻结在 `bringup/bringup.json` 下的记录包含 `schema_version`、自证的 `record_sha256`、`tool`(名称、版本、来源、revision、adapter，以及指认所构建环境的 `env_name`/`env_generation`)、`weights`(每个文件的 path、sha256、size、source、`verified`)、`canary`(target、command、带摘要的 outputs、含 status/format/fields 的解析证明与下游消费证明)、`admission`(状态与理由)、`runtime`(墙钟时间与尝试历史)以及 `cost`(可选的 `budget_hours` 内的 `gpu_h`)。校验器是 `openai4s.benchmark.bringup.verify_bringup`，harness 的 step 在任一检查失败时抛异常——记录缺失以 `BringupError` 拒绝，其余以拼接的问题列表拒绝。

`record_sha256` 只证明内部一致性：任何人都可以同时重写权重文件与其记录摘要并重新封印记录，此时所有内部检查都会通过。真值从 `expected_weights` 这条缝进入——评估方从 reference 构建冻结的摘要——这正是 `forged-record` 用例所演示的。真实的 binder/MD 战役 query 会要求 agent 运行产出这份记录，evaluator 会带着参考摘要调用同一个 `verify_bringup`；那就是"只有 PASS 才准进入 production"的机制落点。

两处边界是刻意为之并已写明的。离线模拟通过真实的 `EnvironmentStore` 事务构建环境(注入 fake 包管理器)，并经 `sys.executable` 对安装好的工具脚本跑 canary——记录里的 env 解释器是 stub，"未预装"的隔离强制留待后续阶段。`env_generation` 检查与用例根目录的 `environments/<env>/generations/<id>/manifest.json` 布局耦合：真实战役要么保持该相对布局，要么去掉这一条检查。
