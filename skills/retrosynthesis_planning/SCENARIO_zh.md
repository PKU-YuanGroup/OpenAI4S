# 逆合成领域任务分解：从断键预测到可审阅路线决策

## Scenario Overview

“给定目标分子，生成合成路线”并不是一个单一问题。它至少包含断哪里、生成什么前体、怎样递归到可得原料、路线是否可信、需要什么条件，以及多条路线如何取舍等相互关联但可独立评测的子问题。

如果把这些问题全部压缩成一个端到端输出，只检查最终路线是否“看起来合理”，就无法判断失败来自单步模型、树搜索、库存、验证器还是排序逻辑，也容易让 LLM 用流畅文字掩盖上游模型没有真正解决的问题。

因此，本 Scenario 不规定一条固定 pipeline，而是把逆合成拆成八个领域子问题。每个子问题都有独立的 Science Query、输入、输出、可执行解法、评测指标和失败边界。一个实际任务可以只调用其中一个问题，也可以按目标组合多个问题；后续问题不能反过来篡改前面问题的确定性结果。

## 逆合成问题地图

```text
                         目标分子 SMILES
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
       Q1 断键位置预测    Q2 单步前体生成     用户约束 / 固定库存
              │                │                │
              └────────┬───────┘                │
                       ▼                        │
                 Q3 多步路线搜索 ◀──────────────┘
                       │
              ┌────────┴────────┐
              ▼                 ▼
       Q4 库存终止判断     Q5 路线可行性验证
              │                 │
              └────────┬────────┘
                       ▼
              Q6 条件、选择性与风险
                       │
                       ▼
              Q7 路线排序与多样性
                       │
                       ▼
              Q8 证据、置信度与报告

Q8 的证据与不确定性边界同时约束 Q1–Q7，而不是只在最后补一段免责声明。
```

## 子问题总览

| 子问题 | 核心问题 | 主要输出 | 可独立评测 |
| --- | --- | --- | --- |
| Q1. 断键位置预测 | 目标分子中优先断哪些键 | 排序后的 reaction center / bond set | 是 |
| Q2. 单步前体生成 | 一步逆合成可能得到哪些前体 | Top-K 前体集合与反应假设 | 是 |
| Q3. 多步路线搜索 | 如何递归组合单步提议到完整路线 | AND-OR 路线树 | 是 |
| Q4. 库存终止判断 | 叶节点是否真的属于允许库存 | 库存命中与未解决叶节点 | 是 |
| Q5. 路线可行性验证 | 路线在结构和反应层面是否自洽 | error / warning / plausibility evidence | 是 |
| Q6. 条件、选择性与风险 | 候选步骤如何执行、风险在哪里 | 条件候选、选择性与安全提示 | 是 |
| Q7. 路线排序与多样性 | 多条路线如何比较且避免重复 | Pareto 排名和差异化路线集 | 是 |
| Q8. 证据、置信度与报告 | 哪些是模型输出、证据或假设 | provenance、置信边界和审阅报告 | 是 |

## Q1. 反应中心与断键位置预测

### Science Query

给定一个目标分子，在不查看参考合成路线的情况下，识别最值得优先尝试的一个或多个断键位置。

### 输入与输出

- **输入：** 规范化目标 SMILES，可选的保护基、禁止断键和最大断键数约束。
- **输出：** 排序后的 bond set；每项包含原子索引、键类型、模型分数、策略来源和适用约束。

### 可执行解法

1. 用 RDKit 解析并 canonicalize 目标，建立稳定原子/键索引。
2. 使用模板分类模型、图模型或由单步模型输出反推 reaction center，生成 Top-K 候选。
3. 合并不同 policy 的重复 bond set，并保留来源，不能只保留最高分而丢失模型分歧。
4. 应用明确的硬约束，例如禁止破坏指定药效团、必须保留手性中心或只允许单键断裂。
5. 输出候选而不是直接宣称“正确断键”；断键是否合理还要由 Q2、Q3 和 Q5 验证。

### 验证与指标

- Top-K reaction-center recall；
- bond-level precision / recall / F1；
- 与隐藏参考路线关键断键的匹配；
- 候选中心的去重率和模型间一致性；
- 约束违反数。

### 当前能力与缺口

RetroChimera / Syntheseus 单步输出可间接提供断键假设，AiZynthFinder policy 也携带模板或反应身份。当前仓库尚未把 reaction center 提取成独立公共结果，需要新增 atom-mapping / bond-diff 适配层和专门 evaluator。

## Q2. 单步逆合成前体生成

### Science Query

给定目标分子或指定断键，生成一步反应能够到达该产物的候选前体集合。

### 输入与输出

- **输入：** 目标 SMILES、可选 reaction center、单步模型及 Top-K。
- **输出：** 排序后的前体 SMILES 集合、reaction SMILES、score 与 score type、模型 provenance。

### 可执行解法

1. 通过 AiZynthFinder expansion policy 或隔离的 `SyntheseusBackend` 调用 RetroChimera 等单步模型。
2. 对前体逐个做 SMILES 解析和 canonicalization；前体集合按规范化后的无序集合去重。
3. 保留 raw score 和 `score_type`，不同模型的分数不得直接当成同一概率。
4. 对模型返回的 mapped reaction、模板和元数据做 schema 校验；缺失字段标为 unknown，不能由 LLM 补造。
5. 可使用独立正向反应模型做 round-trip 排序，但正向模型只能提供额外证据，不能把生成模型的错误改写成成功。

### 验证与指标

- 前体集合 Top-K exact-match accuracy；
- canonical precursor-set recall；
- reaction-center consistency；
- 非法 SMILES、重复前体和空预测率；
- 正向 round-trip Top-K 命中率；
- 推理时间与每目标预测数。

### 当前能力与缺口

仓库已经实现版本化外部模型请求/响应、RetroChimera checkpoint provenance、Top-K 单步预测规范化和结构化错误。缺口是独立的正向验证后端，以及公开数据集上的真实 Top-K 化学精度评测；现有 replay 只验证工程协议，不验证模型科学精度。

## Q3. 多步路线搜索与组合

### Science Query

如何把单步前体提议递归组合成从目标分子到允许终止原料的完整多步路线，而不是只返回第一步预测？

### 输入与输出

- **输入：** 目标 SMILES、expansion policy、filter policy、库存、搜索预算和用户约束。
- **输出：** 一个或多个 AND-OR route tree，包含 solved 状态、步数、叶节点、搜索来源和原始后端排名。

### 可执行解法

1. 使用 AiZynthFinder 的 MCTS / 配置化搜索作为多步 planner。
2. 每个分子节点调用单步 policy 展开；每个反应节点表示一组必须同时满足的前体，保留 AND-OR 语义。
3. filter policy 用于剪枝，不得用 LLM 的自然语言判断替代确定性搜索状态。
4. 对循环、重复状态、最大深度、最大迭代和墙钟时间设置硬上限。
5. 保存原始路线导出和 checkpoint；`solved=False` 的部分路线可以用于诊断，但不能伪装成完整路线。

### 验证与指标

- solved target rate；
- 至少一条路线命中的 Top-N route success；
- 搜索节点数、扩展数、深度、墙钟时间和峰值内存；
- 与参考路线的 tree edit / reaction-step / intermediate similarity；
- 搜索重放一致性和超时率。

### 当前能力与缺口

仓库已经能安全构造 `aizynthcli` 命令、加载路线 JSON、规范化 route tree 并保留 solved 状态。搜索本体依赖独立 AiZynthFinder 环境；当前默认离线测试使用 fixture，不声称在 CI 中完成真实模型搜索。

## Q4. 可购原料与库存终止判断

### Science Query

路线叶节点是否真的属于本任务允许使用的库存，哪些叶节点仍未解决？

### 输入与输出

- **输入：** 路线叶节点、冻结的库存快照、统一的盐/互变异构体/立体化学匹配规则。
- **输出：** 每个叶节点的 `in_stock`、匹配库存、匹配规则、库存版本和未解决原因。

### 可执行解法

1. 在运行前冻结库存文件及摘要，所有模型和搜索方案复用同一快照。
2. 优先进行 exact canonical-SMILES 匹配；盐拆分、去保护、互变异构体归一化必须作为显式的分层规则。
3. 把“固定库存命中”和“网页上似乎可以买到”严格分开。供应商搜索只能作为带时间戳的外部证据。
4. 未命中库存的叶节点返回 unresolved，不得由 LLM 根据常识标成 purchasable。
5. 对库存匹配规则记录命中路径，以便 evaluator 重放。

### 验证与指标

- stock-membership precision / recall；
- 完整库存终止路线比例；
- unresolved leaf 数量；
- exact、salt-normalized、tautomer-normalized 各层命中率；
- 库存版本和匹配 provenance 完整率。

### 当前能力与缺口

AiZynthFinder 负责搜索时库存终止，当前报告也能展示起始原料和 solved 状态。仍需把逐叶节点的匹配规则与库存摘要提升为稳定输出 schema，避免只有一个不可审计的布尔值。

## Q5. 路线结构完整性与反应可行性验证

### Science Query

候选路线是否结构自洽，是否存在明显缺失反应物、无效分子、错误原子变化或低可信反应步骤？

### 输入与输出

- **输入：** 规范化 route tree、reaction SMILES、模板、可选正向模型和文献证据。
- **输出：** 确定性 error、warning、正向验证分数、证据状态和不可验证项。

### 可执行解法

1. 先运行确定性结构审计：缺失树、非法 SMILES、无前体反应、重复前体、缺少反应身份和简单元素缺口。
2. 有 atom mapping 时计算反应中心和原子守恒；区分真正不守恒与导出中省略试剂/离子的情况。
3. 可选使用与生成模型独立的正向预测器检查前体是否能返回目标产物。
4. 检索相似反应或明确文献先例，并区分 exact precedent 与 similarity-based support。
5. 将结构审计、正向模型和文献证据并列展示，禁止把三者压缩成一个虚假的“可行概率”。

### 验证与指标

- 确定性结构 error / warning 数；
- atom-mapping 和守恒检查通过率；
- forward round-trip rank / score；
- 有文献先例步骤比例；
- 对 evaluator 标注的可行/不可行步骤的 AUROC、precision 和 recall；
- calibration error（仅在验证器分数确实校准时）。

### 当前能力与缺口

`structural_audit.py` 已实现确定性树结构、SMILES 和简单元素缺口检查。它明确不是正向反应模型。要真正解决“反应是否可行”，还需可部署的正向模型、atom-mapping 适配器和反应文献检索 evaluator。

## Q6. 反应条件、选择性、安全性与放大风险

### Science Query

对于一条已生成的反应步骤，哪些试剂、溶剂、温度和时间值得优先尝试，可能存在哪些化学选择性、安全和放大风险？

### 输入与输出

- **输入：** 反应物、产物、reaction center、可选文献/ELN 和条件预测模型。
- **输出：** 一个或多个条件候选、证据来源、适用范围、选择性风险、安全提示和验证实验建议。

### 可执行解法

1. 优先检索 exact 或高相似反应先例，记录来源、底物差异和检索时间。
2. 可选调用独立条件预测器，保留 Top-K 条件而不是只给一个点估计。
3. LLM 仅用于把模型和文献结果组织成假设、发现冲突并提出验证步骤，不能冒充条件数据库。
4. 单独检查官能团兼容性、化学/区域/立体选择性、保护基需求和已知危险试剂。
5. 对没有证据的条件明确输出 unknown，不得生成虚假收率区间。

### 验证与指标

- reagent / solvent / catalyst / temperature Top-K recall；
- 与文献条件的集合相似度和温度误差；
- 选择性风险召回率；
- 危险试剂识别 recall；
- 带可追溯证据的条件比例；
- 无证据时正确 abstain 的比例。

### 当前能力与缺口

当前 Skill 能通过 Host 检索文献并让 LLM 生成显式标注的条件和风险假设，也会在报告中保留免责声明。仓库没有把条件预测器作为已验证后端，因此不能把 LLM 条件文本视为该子问题已被科学解决。

## Q7. 路线排序、去重与多样性决策

### Science Query

面对多条候选路线，如何选出质量较高、约束满足且代表不同化学思路的审阅集合？

### 输入与输出

- **输入：** 候选路线、结构审计、库存命中、用户约束、成本/文献/条件证据。
- **输出：** 多目标排名、Pareto 诊断、重复路线来源和差异化 Top-N 路线集。

### 可执行解法

1. 先应用用户硬约束和确定性 error；不满足硬约束的路线不能靠模型高分翻盘。
2. 对 solved 状态、步骤数、库存终止、审计风险、证据覆盖、预估成本和搜索来源做多目标排序。
3. 使用稳定 route signature 合并完全相同的反应树，并保留 `duplicate_count` 和 `source_ranks`。
4. 基于反应身份、产物、前体和叶节点特征计算路线相似度，优先选择不同断键和不同起始原料的路线。
5. 权重必须在 evaluator Ground Truth 之外确定；性能接近时优先简单、短、证据更完整的路线。

### 验证与指标

- Top-N 中有效 / solved / 库存终止路线比例；
- 与化学家排序的 NDCG、Spearman 或 pairwise accuracy；
- exact duplicate removal rate；
- route-feature coverage 和候选间最大相似度；
- Pareto front 覆盖；
- 排名对小权重扰动的稳定性。

### 当前能力与缺口

`workflow.py`、`route_review.py` 已实现规范化排序、稳定签名去重和 Jaccard 多样性选择，并保留 `diversity_relaxed`。当前评分仍不等于经过化学家偏好数据校准的工业路线价值函数；成本、收率、设备和组织约束需要独立数据源。

## Q8. 证据、置信度、失败诊断与审阅输出

### Science Query

如何让使用者知道每个结论来自模型、确定性计算、外部证据还是 LLM 假设，并在系统没有解决问题时得到诚实诊断？

### 输入与输出

- **输入：** Q1–Q7 的结果、模型 manifest、原始路线导出、检索来源和运行日志。
- **输出：** provenance 完整的结构化 JSON、HTML dashboard、Markdown 报告、置信边界和失败原因。

### 可执行解法

1. 给每个结果记录 source type：backend prediction、deterministic audit、database/literature evidence 或 LLM hypothesis。
2. 外部模型记录版本、checkpoint ID / SHA-256、训练数据集、运行环境和错误码。
3. 外部检索记录 URL、请求、时间和响应摘要；未检索到结果不等于否定证据。
4. Dashboard 合并相同分子节点但保留 AND-OR 路线语义、原始排名、重复来源和审计问题。
5. 没有 solved route、checkpoint 缺失、搜索超时或证据不足时，输出结构化 failure / unknown，而不是补造路线或条件。

### 验证与指标

- provenance 字段完整率；
- 可重放结果比例和规范化响应摘要一致性；
- 模型输出、证据和假设的标签准确率；
- 失败分类准确率；
- 应当 abstain 时的拒答率；
- 报告中的 unsupported claim 数量。

### 当前能力与缺口

仓库已经实现 path-free model manifest、checkpoint 哈希、版本化外部响应、结构化 backend error、路线 dashboard 和 Markdown 报告。还需要把 Q1–Q7 的独立置信度和 evaluator 指标统一进一个任务级结果 schema。

## 推荐的任务组合

这些子问题不是必须全部串行运行。可以按评测目标组合：

| 任务类型 | 使用的子问题 | 说明 |
| --- | --- | --- |
| 单步逆合成 benchmark | Q1 + Q2 + Q5 + Q8 | 评测断键和前体，不声称得到完整路线 |
| 多步规划 benchmark | Q2 + Q3 + Q4 + Q5 + Q8 | 评测搜索是否到达固定库存 |
| 路线优选 benchmark | Q5 + Q7 + Q8 | 输入固定候选路线，只评测验证和决策 |
| 条件推荐 benchmark | Q5 + Q6 + Q8 | 输入固定反应，不让搜索质量污染条件评测 |
| 端到端化学家审阅 | Q1–Q8 | 输出多条路线和证据，但仍不等于实验验证 |

## 自动化实现难度

```text
Q1 断键位置预测               △ 需要独立 reaction-center 适配与评测
Q2 单步前体生成               ✓ 已有外部后端协议；科学精度需 live benchmark
Q3 多步路线搜索               ✓ 依赖 AiZynthFinder 可选环境和模型资产
Q4 库存终止判断               △ 搜索可用；逐叶 provenance schema 待补
Q5 确定性结构审计             ✓
Q5 正向可行性 / 文献验证       △ 需要独立模型和证据 evaluator
Q6 条件与选择性预测            △ 当前主要是证据检索和 LLM 假设
Q7 排序、去重和多样性          ✓ 基础能力已实现；工业价值函数待校准
Q8 provenance 与审阅输出      ✓ 基础能力已实现
实验成功、真实收率与放大可行性    ✗ 必须由实验和专家确认
```

## 统一硬性约束

1. **Ground-truth Isolation：** 参考路线、真实前体、真实条件、人工断键和 evaluator 分数只能由 evaluator 在对应子问题完成后读取。
2. **Subproblem Isolation：** 评测某一子问题时，其输入必须固定。比如条件推荐评测应提供固定反应，不能让更好的搜索结果偷偷提高条件任务分数。
3. **Fixed Target and Stock：** 同一 case 的目标规范化规则和库存快照固定；盐、互变异构体和立体化学规则必须预先声明。
4. **Backend Score Separation：** 不同模型的 raw score 不直接比较，也不解释为实验成功概率。
5. **Route-source Integrity：** 路线节点只能来自声明后端；LLM 不得新增或修复步骤后冒充模型输出。
6. **Audit-before-interpretation：** 确定性审计先于 LLM 解释，error 和 warning 不得被自然语言覆盖。
7. **Stock Claim Constraint：** 只有固定库存命中才能标记 in stock；供应商网页只是时间敏感证据。
8. **Evidence Separation：** backend output、deterministic calculation、external evidence 和 LLM hypothesis 必须分别标记。
9. **No Fabricated Conditions：** 没有条件模型或文献证据时必须输出 unknown / hypothesis，不能伪造收率和操作事实。
10. **Deduplication Transparency：** 合并重复路线仍保留 signature、数量和来源；补回相似路线时标记 `diversity_relaxed`。
11. **Model Provenance：** 外部模型记录模型/版本、checkpoint ID 与 SHA-256、训练数据集、运行时依赖和失败信息。
12. **Failure Honesty：** 一个子问题未解决时返回结构化失败，不得依靠下游 LLM 文本把失败包装成完整答案。
