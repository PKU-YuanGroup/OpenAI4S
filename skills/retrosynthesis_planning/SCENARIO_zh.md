# 逆合成规划问题体系：核心预测、路线评价、执行扩展与可信保障

## Scenario Overview

“给定目标分子，规划一条合成路线”不是一个单一模型任务，也不能被严谨地拆成若干彼此完全独立的科学问题。单步前体模型会影响多步树搜索，库存定义会改变 solved 状态，路线验证会影响最终排序，条件与工艺约束又决定路线是否值得进入实验。

本文件采用四层问题体系，而不是串行 pipeline：

1. **核心逆合成问题：** 单步逆合成、多步搜索、库存与用户约束；
2. **路线评价问题：** 反应/路线可行性、路线质量与多样性决策；
3. **合成执行扩展：** 条件/选择性/收率，以及成本/供应/安全/绿色化学/放大；
4. **可信系统保障：** 证据、校准、不确定性、provenance 和失败诊断。

这些项目都可以拥有独立输入、输出和 evaluator，但它们的科学属性不同：Q1、Q2、Q4、Q6 是预测或规划科学问题；Q3 是规划环境与约束；Q5、Q7 是多目标决策问题；Q8 是可信工程问题。文档不再把八项能力都称为彼此独立的化学科学问题。

## 范围声明

- **狭义计算逆合成规划闭环：** Q1–Q5。它覆盖“提出一步反应 → 搜索多步路线 → 到达固定库存 → 验证并选择路线”。
- **可执行合成规划：** Q1–Q7。它进一步考虑条件、选择性、收率、成本、供应、安全和放大。
- **可审计研究系统：** Q1–Q8。它增加来源、校准、不确定性和诚实失败。
- **并非所有合成研究问题的穷尽列表：** 多目标共同中间体规划、实验机器人闭环优化、生产排程、专利自由实施分析和组织内部决策仍可作为扩展 Scenario。

## 问题地图

```text
┌────────────────────── 核心逆合成问题 ──────────────────────┐
│                                                            │
│  Q1 单步逆合成提议 ──▶ Q2 多步路线搜索 ──▶ Q3 库存/约束终止 │
│  （断键 + 前体）          （AND-OR tree）      （规划环境）    │
└───────────────────────────┬────────────────────────────────┘
                            │ 候选路线
                            ▼
┌────────────────────── 路线评价问题 ────────────────────────┐
│ Q4 反应与路线可行性验证 ──▶ Q5 路线质量、排序与多样性       │
└───────────────────────────┬────────────────────────────────┘
                            │ 可审阅路线
                            ▼
┌────────────────────── 合成执行扩展 ────────────────────────┐
│ Q6 条件/选择性/收率 ──▶ Q7 成本/供应/安全/绿色/放大         │
└───────────────────────────┬────────────────────────────────┘
                            │
                            ▼
┌────────────────────── 可信系统保障 ────────────────────────┐
│ Q8 证据、校准、不确定性、provenance、失败诊断               │
│ Q8 同时约束 Q1–Q7，而不是最后追加的一段免责声明              │
└────────────────────────────────────────────────────────────┘
```

## 问题分类总览

| ID | 问题 | 性质 | 可独立评测 | 在完整规划中的作用 |
| --- | --- | --- | --- | --- |
| Q1 | 单步逆合成提议 | 核心预测科学 | 是 | 产生一次反应的前体候选 |
| Q2 | 多步路线搜索 | 核心规划科学 | 是 | 递归组合单步提议 |
| Q3 | 库存与用户约束 | 规划环境/约束 | 是 | 定义何时结束以及什么路线允许 |
| Q4 | 反应与路线可行性 | 科学验证问题 | 是，但包含多种证据层 | 识别无效或低可信路线 |
| Q5 | 路线质量、排序与多样性 | 多目标决策科学 | 是 | 从候选中选出互补路线 |
| Q6 | 条件、选择性与收率 | 相邻预测科学 | 是 | 把反应图转成可测试实验假设 |
| Q7 | 成本、供应、安全、绿色与放大 | 工业决策问题 | 是 | 判断路线是否值得执行 |
| Q8 | 证据、校准、provenance 与失败 | 可信工程 | 是 | 防止预测、证据和假设混淆 |

## 第一层：核心逆合成问题

## Q1. 单步逆合成提议

### 问题定义

给定目标产物，生成一步反应能够到达该产物的候选前体集合。

“反应中心/断键预测”和“前体生成”是同一科学任务的两种常见建模分解，不应被普遍宣称为两个相互独立的问题：

- 图编辑或 synthon 模型通常先预测 reaction center，再完成 synthon；
- 模板模型选择反应模板并实例化前体；
- 序列或生成模型可以直接从产物生成前体，不暴露独立断键结果。

### 固定输入与结构化输出

- **输入：** canonical target SMILES、可选反应类别、保护基/禁止断键/立体化学约束、Top-K。
- **输出：** 无序 precursor set、reaction SMILES、可选 reaction center、raw score、score type、模型与 checkpoint provenance。

### 可执行解决方案

1. 用 RDKit 校验并 canonicalize 目标，建立稳定原子/键索引。
2. 通过 AiZynthFinder expansion policy 或隔离的 `SyntheseusBackend` 调用 RetroChimera 等单步模型。
3. 对所有前体做解析、规范化和无序集合去重，保留不同模型/模板来源。
4. 若模型提供 atom mapping，则计算 bond changes；若不提供，不得由 LLM 伪造 reaction center。
5. 可增加独立正向模型的 round-trip 证据，但不得修改原始后端输出。
6. 不同模型的 raw score 只在其自身语义内解释，不直接转换成统一实验成功概率。

### 评测指标

- precursor-set Top-1 / Top-K exact match；
- 多参考答案或可行性 evaluator 下的 Top-K recall；
- reaction-center bond precision / recall / F1（仅对显式 center 模型）；
- round-trip accuracy；
- invalid、empty、duplicate prediction rate；
- diversity、latency 和推理成本。

### 当前工程状态

已经具备外部模型隔离协议、RetroChimera checkpoint 验证、响应 schema、Top-K 前体和结构化错误。尚缺独立正向模型、atom-mapping adapter 及真实公开数据上的科学精度门禁；现有 replay 只证明工程协议可用。

## Q2. 多步路线搜索

### 问题定义

如何递归调用 Q1，把局部前体提议组合成从目标分子到终止原料的完整路线树？

### 固定输入与结构化输出

- **输入：** 目标、单步 policy、filter policy、搜索算法、预算和 Q3 定义的终止条件。
- **输出：** AND-OR route tree、solved 状态、步骤数、叶节点、搜索统计、原始排名和 provenance。

### 可执行解决方案

1. 使用 AiZynthFinder 的配置化 MCTS/其他搜索算法作为 planner。
2. 分子节点是 OR 选择；一次反应的全部前体是 AND 约束，不能把任一前体成功误判成整步成功。
3. 对循环、重复状态、最大深度、扩展次数、并发和墙钟时间设置硬预算。
4. 保存原始 JSON 和可选 checkpoint，以便重放搜索和区分搜索失败与模型失败。
5. `solved=False` 的部分树只用于诊断，不能呈现为完整路线。

### 评测指标

- solved target rate；
- Top-N reference-route recovery；
- route tree edit / reaction / intermediate similarity；
- 搜索扩展数、深度、时间、内存和单步模型调用次数；
- 超时率、重放一致性和每个 solved target 的成本。

### 当前工程状态

已经能安全构造 `aizynthcli` 命令、导入路线 JSON、规范化 AND-OR tree 并保留 solved 状态。真实搜索依赖独立 AiZynthFinder 环境和模型资产；默认离线测试不声称测量 live planning accuracy。

## Q3. 库存、终止条件与用户约束

### 问题定义

Q3 不是独立化学预测，而是规划问题的环境定义：哪些叶节点算作可得原料，哪些路线违反价格、深度、官能团、设备或用户约束？

### 固定输入与结构化输出

- **输入：** 冻结库存快照、盐/互变异构体/立体化学规则、价格/交期阈值和用户硬约束。
- **输出：** 每个叶节点的库存命中、匹配层级、库存版本、未解决原因及每条路线的约束状态。

### 可执行解决方案

1. 在运行前冻结库存并记录摘要，所有模型和算法使用相同版本。
2. 先做 exact canonical match，再按预声明层级处理盐、互变异构体和立体化学。
3. 把固定库存命中与实时供应商搜索分开；网页可购性是带时间戳的外部证据。
4. 用户硬约束在搜索或过滤阶段显式执行，不允许被高模型分数覆盖。
5. 输出匹配规则和未解决叶节点，而不是只有不可审计的 solved 布尔值。

### 评测指标

- stock-membership precision / recall；
- fully terminated route rate；
- unresolved leaf 数量；
- 各匹配层级的命中率；
- 硬约束违反率；
- 库存 provenance 完整率。

### 当前工程状态

AiZynthFinder 已负责搜索时库存终止，报告能够展示 starting materials 和 solved 状态。逐叶节点匹配层级、库存摘要、价格与交期约束仍需提升为稳定 schema。

## 第二层：路线评价问题

## Q4. 反应步骤与整条路线的可行性验证

### 问题定义

候选路线是否结构自洽，单步反应是否有合理的化学可行性证据，整条路线是否因任一步薄弱环节而失效？

Q4 是一个复合验证问题，不存在可靠的单一“可行概率”。至少要区分：

1. 确定性结构完整性；
2. atom mapping / 守恒检查；
3. 独立正向反应预测；
4. 文献或 ELN 先例；
5. 化学家审阅与实验结果。

### 固定输入与结构化输出

- **输入：** 固定 route tree、reaction SMILES/模板、可选正向模型和证据库。
- **输出：** 分层 error/warning、forward evidence、precedent status、路线最弱步骤和 unknown 项。

### 可执行解决方案

1. 先运行确定性审计：缺失树、非法 SMILES、无前体反应、重复前体、缺少反应身份和简单元素缺口。
2. 有 atom mapping 时计算 bond changes 和守恒，并区分省略试剂与真正结构异常。
3. 用独立于 Q1 的正向模型进行 round trip，防止生成器自我确认。
4. 检索 exact precedent 与 similarity precedent，并明确二者不同。
5. 路线级风险采用 weakest-link/逐步证据汇总，不能用平均值掩盖一个致命步骤。
6. 保留各证据层，不压缩成无法校准的总概率。

### 评测指标

- deterministic error / warning count；
- mapping / conservation pass rate；
- forward Top-K rank 和 round-trip rate；
- exact/similar precedent coverage；
- 对专家可行性标签的 precision、recall、AUROC；
- 最弱步骤识别率和适当 abstention rate。

### 当前工程状态

`structural_audit.py` 已实现结构树、SMILES、前体、反应身份和简单元素缺口检查，并明确声明它不是正向模型。尚缺独立 forward predictor、atom mapper、文献反应 evaluator 和路线级 weakest-link 汇总。

## Q5. 路线质量、排序、去重与多样性

### 问题定义

面对多条候选路线，怎样在多个冲突目标下选出优先审阅且代表不同化学策略的路线集合？

### 固定输入与结构化输出

- **输入：** 固定候选路线、Q3 约束、Q4 验证、可选成本和用户偏好。
- **输出：** 多目标/Pareto 排名、route signature、重复来源、多样性诊断和 Top-N。

### 可执行解决方案

1. 先执行硬约束和确定性 error，不能让高后端分数覆盖失败。
2. 对 solved、步骤数、库存终止、最弱步骤、证据、成本和复杂度做多目标评价。
3. 使用稳定 route signature 合并完全重复树，并保留 `duplicate_count` 和 `source_ranks`。
4. 根据反应、产物、前体和叶节点特征选择不同断键/起始原料路线。
5. 不同后端 raw score 不直接比较；排序权重必须在 evaluator Ground Truth 之外确定。
6. 为达到固定展示数而补回相似路线时，显式标记 `diversity_relaxed=True`。

### 评测指标

- Top-N valid / solved / stock-complete 比例；
- 与化学家排序的 NDCG、Spearman、pairwise accuracy；
- reference-route Top-N recovery；
- duplicate removal rate；
- route cluster / feature coverage 和最大相似度；
- Pareto coverage 与权重扰动稳定性。

### 当前工程状态

`workflow.py` 和 `route_review.py` 已实现规范化排序、稳定签名去重、Jaccard 多样性和 `diversity_relaxed`。这仍不是由工业化学家偏好校准的路线价值函数；Q6、Q7 数据尚未完整进入排序。

## 第三层：合成执行扩展

## Q6. 反应条件、选择性与收率

### 问题定义

对一个固定反应步骤，哪些催化剂、试剂、溶剂、温度和时间值得测试，可能得到怎样的选择性与收率范围？

Q6 是完整 CASP 的重要相邻科学问题，但不是狭义 retrosynthesis generation。为了独立评测，必须固定反应输入，避免更好的 Q1/Q2 结果偷偷提高条件任务分数。

### 固定输入与结构化输出

- **输入：** 固定 reactants、product、可选 reaction center、文献/ELN 和条件模型。
- **输出：** Top-K 条件集合、选择性/收率预测、适用范围、来源和 unknown。

### 可执行解决方案

1. 优先检索 exact 或高相似反应先例，记录底物差异与来源。
2. 可选调用独立条件预测器，并保留多个候选而非一个点估计。
3. 分开预测 reagent、catalyst、solvent、temperature、time、selectivity 和 yield，保留条件间依赖。
4. LLM 只组织证据、指出冲突并提出实验，不冒充条件数据库。
5. 没有支持时输出 unknown / hypothesis，不编造收率区间。

### 评测指标

- reagent / catalyst / solvent Top-K recall；
- condition-set similarity；
- temperature / time error；
- chemo-/regio-/stereo-selectivity accuracy；
- yield MAE 与 calibration；
- evidence-backed rate 和正确 abstention rate。

### 当前工程状态

Skill 能通过 Host 检索证据并生成显式标注的 LLM 条件假设，但没有已验证的条件/收率预测后端。因此当前系统只能辅助 Q6，不能宣称科学上解决了 Q6。

## Q7. 成本、供应、安全、绿色化学与放大决策

### 问题定义

即使化学上可行，一条路线是否在给定组织、设备、地区、时间和规模下值得执行？

Q7 是工业多目标决策问题，不是单一化学预测。输入数据通常具有时间、地区和组织特异性。

### 固定输入与结构化输出

- **输入：** 路线、条件、物料价格/交期、设备能力、安全规则、规模和绿色化学目标。
- **输出：** 物料成本、供应风险、hazard flags、PMI/E-factor 等指标、设备/放大冲突和 Pareto 诊断。

### 可执行解决方案

1. 用带时间戳和地区的供应数据估计物料价格、交期和单一来源风险。
2. 对危险物质、放热、气体、高压、低温、敏感中间体和废物流做规则/数据审计。
3. 在有质量平衡时计算 PMI、E-factor、溶剂和能源指标；缺数据时返回 unavailable。
4. 根据目标规模检查设备、纯化、溶剂置换和中间体稳定性。
5. 以 Pareto front 展示成本、风险、时间和环境影响，不生成虚假单一“工业可行分”。

### 评测指标

- cost / lead-time error；
- supply-risk detection recall；
- hazard recall 与严重度一致性；
- PMI/E-factor error；
- scale/equipment constraint violation rate；
- 与工艺化学家 pairwise preference 的一致性。

### 当前工程状态

当前报告能够承载安全和可执行性提示，但主要是 LLM 假设。仓库尚无供应、EHS、设备、质量平衡或绿色化学数据连接器，因此 Q7 是明确缺口，不能仅靠路线步数替代。

## 第四层：可信系统保障

## Q8. 证据、校准、不确定性、provenance 与失败诊断

### 问题定义

怎样让使用者知道每个结论来自后端预测、确定性计算、外部证据还是 LLM 假设，并在系统不知道时得到可信的 unknown/failure？

Q8 不是化学科学预测，而是让 Q1–Q7 可审计、可复现、可安全使用的系统保障问题。

### 固定输入与结构化输出

- **输入：** Q1–Q7 输出、模型 manifest、原始导出、检索来源、环境和运行日志。
- **输出：** source-labelled JSON、置信/校准信息、provenance、结构化 failure、HTML dashboard 和 Markdown 报告。

### 可执行解决方案

1. 区分 `backend_prediction`、`deterministic_check`、`external_evidence`、`expert_observation` 和 `llm_hypothesis`。
2. 记录模型/版本、checkpoint ID/SHA-256、训练数据、运行包和错误码。
3. 记录库存、配置、搜索预算、原始 route export 和 artifact lineage。
4. 外部检索记录 URL、请求、时间和响应摘要；没有搜索结果不等于否定证据。
5. 对可校准分数报告 calibration；对不可校准 raw score 明确禁止概率解释。
6. checkpoint 缺失、超时、unsolved、证据不足或约束冲突时返回结构化失败/unknown。

### 评测指标

- provenance 完整率；
- replay / normalized digest 一致性；
- source-label accuracy；
- calibration error 或正确的 uncalibrated 标签率；
- failure classification accuracy；
- appropriate abstention rate；
- unsupported claim 数量。

### 当前工程状态

已经具备 path-free model manifest、checkpoint 哈希、版本化响应、结构化 backend error、artifact provenance、dashboard 和 Markdown 报告。仍需把 Q1–Q7 的独立 confidence、calibration 和 evaluator 结果统一到任务级 schema。

## 推荐的独立评测任务

| Benchmark | 固定输入 | 被评测问题 | 必须隔离的影响 |
| --- | --- | --- | --- |
| 单步逆合成 | target + Top-K | Q1 | 不运行多步搜索，不用参考路线调模型 |
| 多步规划 | target + policy + fixed stock + budget | Q2 | 固定 Q1、Q3，避免模型/库存变化污染搜索比较 |
| 库存与约束 | fixed leaves/routes + stock | Q3 | 不让 planner 质量影响匹配准确率 |
| 路线可行性 | fixed reactions/routes | Q4 | 不让生成模型自我验证 |
| 路线排序 | fixed candidate set | Q5 | 不让候选召回差异污染排序指标 |
| 条件/收率 | fixed reaction | Q6 | 不让逆合成质量污染条件任务 |
| 工业决策 | fixed route + conditions + dated data | Q7 | 固定地区、时间、规模与设备 |
| 可信输出 | recorded outputs/failures | Q8 | 不依赖 live 模型即可重放 |

## 自动化实现状态

```text
Q1 单步逆合成提议                  ✓ 工程后端已接；科学精度 live benchmark 待补
Q2 多步路线搜索                    ✓ 依赖 AiZynthFinder 环境与资产
Q3 库存/约束                       △ 基础终止可用；逐叶 provenance 待补
Q4 确定性结构审计                  ✓
Q4 正向/文献/专家可行性             △ 独立后端与 evaluator 待补
Q5 排序、去重和多样性               ✓ 基础能力已实现
Q6 条件、选择性和收率               △ 当前主要是证据与 LLM 假设
Q7 工业成本/供应/EHS/绿色/放大       ✗ 数据连接器与确定性计算待实现
Q8 provenance/失败/审阅             ✓ 基础能力已实现；校准 schema 待补
实验成功与真实放大可行性              ✗ 必须由专家和实验确认
```

## 统一硬性约束

1. **Ground-truth Isolation：** 参考路线、真实前体/条件/收率、人工断键、专家排序和 evaluator 分数只能在对应任务完成后读取。
2. **Problem Isolation：** 独立评测必须固定输入；例如 Q6 使用固定反应，Q5 使用固定候选路线。
3. **Fixed Target and Stock：** canonicalization、库存快照、盐/互变异构体/立体化学规则预先声明。
4. **Budget Parity：** 搜索算法比较使用相同或归一化预算。
5. **Backend Score Separation：** 不同模型 raw score 不直接比较，也不解释为实验成功率。
6. **Route-source Integrity：** 路线节点只能来自声明后端；LLM 不得新增或修复后冒充模型输出。
7. **Independent Validation：** Q4 验证器应尽量独立于 Q1 生成器，防止自我确认。
8. **Audit Before Interpretation：** 确定性 error/warning 先于 LLM，且不得被自然语言覆盖。
9. **Stock Claim Constraint：** 只有固定库存命中才能标记 in stock；实时供应证据单独标记时间与地区。
10. **Evidence Separation：** 预测、计算、外部证据、专家观察和 LLM 假设使用不同 source label。
11. **No Fabricated Conditions or Business Data：** 无数据时输出 unknown，不编造收率、价格、交期、PMI 或设备能力。
12. **Deduplication Transparency：** 合并路线保留 signature、数量和来源；相似路线补位公开 `diversity_relaxed`。
13. **Model Provenance：** 记录模型/版本、checkpoint ID/SHA-256、训练数据、运行依赖和失败信息。
14. **Failure Honesty：** 任一问题未解决时返回结构化 failure/unknown，不用下游 LLM 文本包装成完整成功。

## 参考边界

- AiZynthFinder 4.0 将单步模型、树搜索、stock 和 route scoring 作为不同组件：https://pmc.ncbi.nlm.nih.gov/articles/PMC11112899/
- Retro* 将多步规划形式化为神经引导的 AND-OR tree search：https://arxiv.org/abs/2006.15820
- PaRoutes 分开评测 solved targets、参考路线质量和路线多样性：https://doi.org/10.1039/D2DD00015F
- 单步逆合成 seq2seq 工作明确把前体预测作为多步系统中的模块：https://doi.org/10.1021/acscentsci.7b00303
- 条件推荐研究将 catalyst、solvent、reagent 和 temperature 视为独立预测问题：https://doi.org/10.1021/acscentsci.8b00357
