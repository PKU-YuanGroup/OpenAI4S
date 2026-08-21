# `openai4s/benchmark/`

带版本的科学工作流基准 runner，清单放在
[`workflows/`](../../workflows/README_zh.md)：十一个 workflow、三十一个真的会
执行的用例，另有一份独立、严格的 Stage 0 现场/安全验收包。

提出这套基准的方案对「什么会让它一文不值」讲得很明确——一个没人执行的 fixture 目录，或者因为被测对象是 mock 所以能过的用例。所以这里每一步都驱动真实子系统：真实的 Store、真实的 kernel manager、真实的 host dispatcher、真实的 compute manager、真实的 connector service、真实的环境事务。被注入的只有离线跑不了的那些——LLM（测试套件本来就 mock 了它）、网络（connector 抓取喂的是录制下来的 body）、包管理器（单元测试里的环境构建不可能去下载一个 solver）——而且每一样都是注入**进**生产代码，而不是把生产代码替换掉。一个自己造答案的 step，衡量的是这个 step 自己。

**声明的结果是契约的一部分。** 一个期望 `failure` 的用例如果跑出了干净的成功，它失败的程度和一个期望成功却抛异常的用例完全一样——只会打分「没抛异常」的基准，对系统中「职责就是拒绝」的那一半什么也没衡量。`provenance`、`recovered`、`permission_denied` 存在的理由相同。

| 文件 | 用途 |
| --- | --- |
| `__init__.py` | 对外表面：原有 workflow API，以及 `AcceptancePack`、`load_acceptance_pack` 和 `run_acceptance_pack`。调用方不应 import 树里的其他实现。 |
| `acceptance.py` | 加载并重放严格、带版本的下一轮验收包：恰好六条现场路径和七类安全动作；每项输出 expected/observed/pass/evidence/duration，并聚合明确写出分母的指标。 |
| `model.py` | workflow 与 case **是什么**，以及从哪里读。清单用 JSON 而非 YAML，理由与内核一致——决定一次发布好不好的东西，不能要求先装一个第三方库才能读；而且它带版本，因为用例能被悄悄改动的基准，跨时间什么也衡量不了。 |
| `runner.py` | 跑一个用例，并判定发生的事情是不是它所声明的。有意思的是这个判定而不是执行：声明的结果与观察到的结果对比，任一方向不一致都算失败。 |
| `steps.py` | 各个 step 的实现，一个 step 名对应一个函数，登记在 `STEPS` 里。每个函数接收共享的 `Context` 与用例的 inputs，返回一个并入结果的 dict；抛异常是 step 报告「工作流走不下去」的方式，由 runner 判定它是否符合声明。`SkipCase` 留给宿主确实跑不了某一步的情况（没有 `Rscript`、没有 shell），那是跳过，不是悄悄算过。 |
| `bringup.py` | 工具 bring-up 契约的校验器：纯 stdlib 检查冻结的 `bringup.json` 记录——自证封印、权重摘要与大小、磁盘上的 generation manifest、canary 解析与下游消费证明、准入、运行时与成本——外加评估方持有的 `expected_weights` 参考摘要缝，以及 `seal_record` 生产者半部。 |

## 为什么清单不放在这里

它们在仓库根目录的 [`workflows/`](../../workflows/README_zh.md)，这样「改动基准的期望」就是一份挨着被它评判的代码的、可评审的 diff——而不是埋在某个包底下的一次 fixture 编辑。

## 下一轮验收的一键入口

稳定、机器可读的一键入口是：

```bash
openai4s benchmark --acceptance --json
```

对应的公开 Python 入口是：

```python
from openai4s.benchmark import run_acceptance_pack

report = run_acceptance_pack()  # 可直接 JSON 序列化；report["pass"] 即 gate
```

CLI 适配层只负责组合：调用这个公开函数，序列化返回值，并且只在
`report["pass"]` 为 true 时返回 0。验收模块刻意不再创建第二套命令解析器。

这份 JSON 同时就是 Stage 0 的机器指标记录，而不只是断言列表。
`manifest_digest` 把规范化后的完整 manifest 内容绑定到 `pack_version`；不经
评审和版本／digest 更新就改 claim、execution mode、断言键或期望值，会失败即
拒绝。当前 `2026-08-16.2` contract 把 `workspace_unchanged=true` 加入
Reviewer 强制期望。Runner 每次都会重新加载并验证这份随包发布的规范声明；
调用方传入的 `AcceptancePack` 只有与规范 pack 完全相等时才允许执行，因此删减
probe 或弱化嵌套期望无法自行签发通过报告。报告再把这个身份和
`recorded_at_ms` 绑定到现场路径 p50/p95 时延、
Reviewer 上报 token、Cell 失败率、相同 checksum/后续 cell 的重复版本率，
以及 planted-case review 命中率。Cell、Reviewer 和 duplicate 都在可能失败
的操作开始前计入分母，因此异常、error 与 interrupted terminal 不会消失。
每项指标都携带分母定义和零样本行为。确定性 Reviewer 注入数据单独放在
`offline_contract`，真实 provider 样本放在 `live_observed`；没有 live 样本时
其值为 `null`。离线数字不能当作当前模型性能。

现场路径实际进入生产 Python/R kernel、Store/Artifact repository、Reviewer
证据流水线、Notebook exporter、Ketcher 路由正文和科研 connector catalog。
Ketcher 通过真实、隔离的 loopback HTTP socket 请求 production Gateway
handler；其临时 access-token banner 只在派生子进程中捕获并验证，不会进入
验收 CLI 流或报告。离线 Reviewer 用例只在该次调用的 LLM 边界注入确定性
响应。若注入 Reviewer 写入正式 workspace，探针会如实观察为
`workspace_unchanged=false` 并让该现场路径失败；Stage 0 不会假装这已经是
未来的只读 Reviewer sandbox。R worker 若无法解析，或被宿主沙箱拦住，就如实报告 unavailable，
不会拿 Python 冒充。Ketcher 当前 placeholder 和 ClinVar 缺失属于
`baseline_observation`，且 `capability_pass=false`；成功重放这些事实不代表
能力已经可用。

报告的 `environment` 对象记录请求及实际 kernel sandbox 姿态、安全控制、
egress／network 姿态、unattended approval 设置、Notebook／team 模式，以及
roadmap flags 在 Stage 0 仍明确标为未消费。冻结的 pack 衡量仓库
默认姿态，不会暗中覆盖调用方配置。因此显式的非默认环境（例如
`OPENAI4S_NOTEBOOK_REPL=1`）会让冻结的只读 Notebook 观察失败。这是报告中
可见的配置漂移，不是基准随机性；跨报告比较前应先检查 `environment`。
network preflight 同样遵守生产优先级：全局 network switch 与 egress gate
的确定性拒绝优先于 permission 的 `allow`。设置
`OPENAI4S_ALLOW_NETWORK=0` 时，观察值必须是 `deny`，冻结的默认姿态
`allow` 断言会明确失败，绝不会为了通过基线而改写事实。

安全用例走真实 Host permission/path、egress、代码分类器与 shell precheck。
实际执行的只有生成 workspace 内的一次读取和一次受限写入。外部写只到达
路径边界检查，不会打开目标；网络、敏感 payload 外发和删除不会到达
transport 或 shell。
