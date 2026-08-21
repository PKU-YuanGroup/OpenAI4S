# 逆合成规划的模型化科学问题

[English](SCENARIO.md)

## Scenario Overview

该 Scenario 面向“给定一个目标分子，使用可本地执行的模型组件提出并评审合成路线”的任务。代码或模型 artifact 可公开取得，并不等于 checkpoint 已获准使用；每个 artifact 在使用前都必须满足相应的准入和条款政策。逆合成规划不是一个单模型问题，也不应被写成一条固定 pipeline。不同模型实际回答的是不同的科学问题：一步前体是什么、怎样把一步提案组合成多步路线、一个已知反应中的原子如何对应、给定前体会生成什么产物、固定反应需要什么条件，以及完整反应可能得到多少收率。

本文只保留当前有公开代码、可取得权重和脚本化推理入口的模型任务，不把去重、画图、日志、证据展示等工程功能硬凑成科学问题。共定义六个可分别输入、分别输出、分别评测的问题：

1. **单步逆合成前体生成**；
2. **多步逆合成路线规划**；
3. **反应原子映射与反应中心识别**；
4. **正向反应产物预测与 round-trip 验证**；
5. **反应条件推荐**；
6. **反应收率估计**。

其中问题 1 和问题 2 构成狭义逆合成规划的必要核心。问题 3–6 是可独立评测的反应理解、验证和实验执行问题，可以增强路线评审，但并非每种 planner 都必须串行调用，也不能用来把一条模型路线宣称为实验可行。

本文是总览与边界说明。六份按照 Benchmark 数据构建、Ground Truth 隔离、阶段检查点、输入/私有目录、评测指标和失败案例完整展开的规范位于 [`scenarios/`](scenarios/README_zh.md)：

| ID | 独立详细规范 | 当前成熟度 |
| --- | --- | --- |
| P1 | [`scenarios/01_single_step_retrosynthesis.md`](scenarios/01_single_step_retrosynthesis.md) | 设计完成；发布前需冻结 USPTO 派生数据许可与 checkpoint split 审计 |
| P2 | [`scenarios/02_multistep_route_planning.md`](scenarios/02_multistep_route_planning.md) | 设计完成；PaRoutes 数据与自动指标明确，可优先实现 |
| P3 | [`scenarios/03_atom_mapping.md`](scenarios/03_atom_mapping.md) | 设计完成；正式 Ground Truth 的独立人工核验与许可仍是阻塞项 |
| P4 | [`scenarios/04_forward_prediction.md`](scenarios/04_forward_prediction.md) | 设计完成；需冻结 USPTO_MIT 快照与 checkpoint 去重证明 |
| P5 | [`scenarios/05_condition_recommendation.md`](scenarios/05_condition_recommendation.md) | 设计完成；v1 限于类别条件，数据许可待审计；任何 checkpoint 下载或推理前须审核其条款并在 manifest 记录允许/拒绝决定 |
| P6 | [`scenarios/06_yield_estimation.md`](scenarios/06_yield_estimation.md) | 设计完成；以 Buchwald–Hartwig OOD split 为主，需冻结数据许可与去重清单 |

## 科学问题之间的关系

```text
                         ┌──────────────────────────┐
                         │ P1 单步逆合成前体生成     │
                         │ product → precursor sets │
                         └────────────┬─────────────┘
                                      │ 作为 expansion policy
                                      ▼
                         ┌──────────────────────────┐
                         │ P2 多步逆合成路线规划     │
                         │ target + stock → routes  │
                         └────────────┬─────────────┘
                                      │ 固定候选反应/路线
          ┌───────────────────────────┼───────────────────────────┐
          │                           │                           │
          ▼                           ▼                           ▼
┌──────────────────┐       ┌────────────────────┐       ┌──────────────────┐
│ P3 原子映射/中心  │       │ P4 正向产物预测     │       │ P5 条件推荐       │
│ known reaction   │       │ precursors → product│       │ fixed reaction   │
└──────────────────┘       └────────────────────┘       └─────────┬────────┘
                                                                  │ 完整条件上下文
                                                                  ▼
                                                        ┌──────────────────┐
                                                        │ P6 收率估计       │
                                                        │ reaction → yield │
                                                        └──────────────────┘
```

该图表示输入依赖，不表示必须执行的流水线。例如，P3 需要一个已知反应的两侧结构，不能接受只有目标分子的输入；P6 需要反应物、试剂和产物，不能直接评价一棵尚未补全条件的路线树。

## 问题与模型总览

| ID | 独立科学问题 | 最小输入 | 主要输出 | 入选模型 | 当前工程状态 |
| --- | --- | --- | --- | --- | --- |
| P1 | 单步逆合成前体生成 | 目标产物 SMILES | Top-K 前体集合 | RetroChimera 1 | 已有隔离 worker、checkpoint 校验和结构化返回 |
| P2 | 多步逆合成路线规划 | 目标、单步策略、库存、预算 | solved/unsolved 路线树 | AiZynthFinder | 已有命令构造、路线导入、规范化、审计和排序 |
| P3 | 原子映射与反应中心识别 | 完整 reaction SMILES | mapped reaction、变化键 | RXNMapper | 已写独立 Skill；需可选模型环境 |
| P4 | 正向反应产物预测 | 反应物、试剂 | Top-K 产物 | ReactionT5v2-forward | 已写独立 Skill；需可选模型环境 |
| P5 | 反应条件推荐 | 固定反应两侧 | 条件组合、可选温度 | Parrot（有条件候选） | 已写条件型 Skill；任何 checkpoint 下载或推理前须完成条款审核并在 manifest 记录允许决定 |
| P6 | 反应收率估计 | 反应物、试剂、产物 | 预测收率 | ReactionT5v2-yield | 已写独立 Skill；只允许域内筛选解释 |

## Problem 1. 单步逆合成前体生成

### Science Query

给定一个目标产物分子，在不查看参考答案或完整路线 Ground Truth 的情况下，生成能够通过一步反应形成该产物的候选前体集合。

### 目标

发现合理的键断裂和前体组合，为化学家提供一步 disconnection 假设，并为多步 planner 提供 expansion policy。

### 输入

- canonical target SMILES；
- Top-K 上限；
- 可选反应类别、禁止断键、保护基和立体化学约束；
- 固定的模型版本与 checkpoint。

### 输出

- 有序但化学组分内部无序的 precursor sets；
- reaction SMILES；
- raw model score 及 score type；
- 可选 reaction center；
- parse/duplicate 状态；
- 模型、checkpoint、训练集和许可证 provenance。

### 技术

默认使用 RetroChimera 1。它组合 edit-based 与 de-novo 子模型，通过 Syntheseus 接口返回单步提案。OpenAI4S 使用独立 Python/conda 进程加载模型，主进程只发送一个有版本的 JSON 请求并接收一个 JSON 响应。目标和输出使用 RDKit 做解析、canonicalization 和无序组分归一化；同一前体集合的重复 beam 被合并，但原始排名和来源必须保留。

ReactionT5v2-retrosynthesis 可作为序列模型多样性对照，但不能把不同模型的 raw score 直接平均。默认最多请求 5–10 个提案，因为低排名生成更容易出现幻觉。

### 模型实现

- 默认：RetroChimera 1，MIT 代码与公开 checkpoints；
- 备选：ReactionT5v2-retrosynthesis，MIT 权重，可直接 Transformers 推理；
- 仓库 Skill：`single-step-retrosynthesis`；
- 已有 adapter：`retrosynthesis_planning.external_backends.SyntheseusBackend`。

### 独立评测指标

1. precursor-set Top-1 / Top-K exact match；
2. 多参考答案下的 Top-K recall；
3. invalid、empty、duplicate prediction rate；
4. reaction-center bond precision / recall / F1，仅适用于显式 center 模型；
5. 候选结构多样性；
6. 单分子推理延迟、显存和吞吐量。

### 硬性约束

1. 评测参考前体和完整路线不得进入生成 prompt、模型输入、过滤器或重排器。
2. 前体集合必须按“无序分子集合”比较，不能因 SMILES 顺序不同误判。
3. 未经部署域校准的模型分数不得解释为实验成功概率。
4. 模型没有输出 reaction center 时，不得由 LLM 伪造。
5. 结构过滤只能剔除非法结果，不能利用 Ground Truth 选择正确 beam。

## Problem 2. 多步逆合成路线规划

### Science Query

给定目标分子、固定单步扩展策略、固定起始物库存和有限搜索预算，能否找到从目标到库存原料的完整多步合成路线？

### 目标

把局部单步提案递归组合成满足 AND-OR 逻辑的路线树，并区分完整 solved route、部分路线和搜索失败。

### 输入

- canonical target SMILES；
- 固定 expansion/filter policies；
- 冻结的 stock snapshot；
- 搜索算法、最大深度、扩展次数、墙钟时间和并行预算；
- 用户硬约束。

### 输出

- AND-OR route trees；
- 每条路线的 solved 状态；
- 反应步骤、分子节点、叶节点和库存命中；
- 搜索深度、扩展数、模型调用数和耗时；
- 原始 planner score/rank 与完整配置 provenance。

### 技术

默认使用 AiZynthFinder。分子节点表示 OR 选择，一次反应的全部前体构成 AND 条件；只有所有叶节点满足固定库存终止规则时路线才是 solved。搜索必须限制循环、重复状态、最大深度、扩展次数和墙钟时间，并保存原始 JSON 与可选 search checkpoint。

OpenAI4S 负责安全构造 `aizynthcli` 命令、导入并规范化路线树、保留未解决叶节点、去除完全重复路线并生成审计结果。路线排序和 dashboard 是 planner 输出后的工程评审功能，不另算一个科学预测问题。

### 模型实现

- 默认 planner：AiZynthFinder；
- 默认公开 policy：AiZynthFinder `download_public_data` 提供的 expansion/filter assets；
- 可选单步 policy：RetroChimera/Syntheseus backend；
- 仓库 Skill：`retrosynthesis_planning`。

### 独立评测指标

1. solved target rate；
2. Top-N reference-route recovery；
3. reaction、intermediate 和 route-tree similarity；
4. 平均步骤数和未解决叶节点数；
5. 扩展数、单步模型调用数、时间和内存；
6. timeout rate、重放一致性和每个 solved target 的成本。

### 硬性约束

1. 比较 planner 时必须冻结单步 policy、filter、库存和预算。
2. `solved=False` 的部分树不得展示为完整合成路线。
3. 任一前体到达库存不能代替同一步所有前体均已解决的 AND 条件。
4. 搜索过程中不得读取参考路线来选择分支或修改预算。
5. 实时供应商网页结果不得静默改变固定 benchmark 的库存。

## Problem 3. 反应原子映射与反应中心识别

### Science Query

给定一个反应物和产物均已知的完整反应，反应两侧的原子如何对应，哪些键被形成、断裂或改变了键级？

### 目标

为固定反应建立可审计的 atom correspondence 和 bond-change 表示，用于反应中心分析、数据清洗、模板抽取和路线步骤检查。

### 输入

- 包含反应物和产物两侧的 reaction SMILES；
- 固定 mapper 版本；
- 明确的 reagent/participant 分离规则。

### 输出

- atom-mapped reaction SMILES；
- mapping confidence；
- formed、broken 和 bond-order-changed bonds；
- 未映射原子、重复 map number 和守恒警告；
- mapper provenance。

### 技术

默认使用 RXNMapper 的 attention-guided atom mapping。批量任务使用 `BatchedMapper`，避免单个非法反应中断整批。映射后由 RDKit 根据 atom-map number 构造反应物和产物的键表，再计算形成键、断裂键和键级变化。

该问题不是 target-only retrosynthesis：只有一个目标产物时没有可映射的反应物侧，必须转到 P1。低 mapping confidence 也不等于反应不可行，只表示当前原子对应不可靠。

### 模型实现

- 默认：RXNMapper，MIT，本地 Python API，支持 CPU/GPU；
- 仓库 Skill：`reaction-atom-mapping`。

### 独立评测指标

1. atom-mapping accuracy；
2. changed-bond precision / recall / F1；
3. atom conservation pass rate；
4. invalid/empty mapping rate；
5. 置信度与映射错误的 calibration；
6. batch throughput 和失败隔离率。

### 硬性约束

1. 被评测 reaction 的两侧必须在映射前固定。
2. 不得为提高守恒率而静默移动 reagent、删除分子或重写反应。
3. 原始 reaction SMILES 必须与 mapped reaction 同时保存。
4. 变化键只能从 map number 推导，不能由 LLM 猜测。
5. mapping confidence 不得解释为反应成功概率。

## Problem 4. 正向反应产物预测与 round-trip 验证

### Science Query

给定候选反应物和试剂，模型预测会生成哪些产物；预期目标是否出现在 Top-K 产物中？

### 目标

独立评价一个逆合成前体提案能否在正向模型中恢复目标，并暴露可能的竞争产物或模型分歧。

### 输入

- reactant SMILES；
- 与 reactants 分离记录的 reagent/catalyst/solvent 字段；
- 可选 intended product，仅用于固定后的比较；
- beam size 与 Top-K。

### 输出

- Top-K predicted product SMILES；
- 每个产物的原始序列分数和 canonical parse 状态；
- intended product 的 rank 或 `null`；
- top-k recovery；
- 模型与 checkpoint provenance。

### 技术

默认使用 ReactionT5v2-forward。模型输入严格采用 `REACTANT:...REAGENT:...` 格式，通过 Transformers 生成多个产物。所有产物在比较前用 RDKit canonicalize，非法结果保留原始字符串并标记失败。

round-trip recovery 只表示正向模型与逆向提案一致，不是实验可行性证明。如果正向和逆向模型共享训练数据或模型家族，必须标记为相关证据，不能称为独立实验验证。

### 模型实现

- 默认：`sagawa/ReactionT5v2-forward`，MIT，0.2B safetensors；
- 可选：RetroChimera 发布的 forward checkpoint；
- 仓库 Skill：`reaction-forward-prediction`。

### 独立评测指标

1. product Top-1 / Top-K accuracy；
2. intended-product reciprocal rank；
3. round-trip recovery rate；
4. invalid 和 duplicate product rate；
5. stereochemistry-sensitive accuracy；
6. latency、吞吐量和显存。

### 硬性约束

1. 正向预测不得读取 intended product；intended product 只能在输出固定后比较。
2. 比较不同逆合成模型时必须使用同一个正向模型、checkpoint 和 beam 配置。
3. missing reagent 必须记录为 unknown，不能静默补成模型最有利的条件。
4. backward score 与 forward score 未联合校准时不得相乘成“可行概率”。
5. top-k recovery 不得命名为 `feasible=True`。

## Problem 5. 反应条件推荐

### Science Query

给定一个反应物和产物均固定的反应，哪些 catalyst、reagent、solvent 组合以及模型支持时的 temperature 最值得优先验证？

### 目标

为一个已确定的 transformation 生成有序条件假设，缩小文献/ELN 检索和实验筛选空间。

### 输入

- 固定 reaction SMILES；
- 模型配置和 condition label dictionary；
- Top-K；
- 明确是否使用支持 temperature 的 checkpoint。

### 输出

- Top-K condition sets；
- 分开的 catalyst、reagent、solvent、temperature 字段；
- raw label IDs 与 decoded names；
- checkpoint/config provenance；
- `model_only`、`literature_analog`、`exact_precedent` 或 `eln_verified` 验证状态。

### 技术

当前候选模型为 Parrot。它提供公开推理代码、condition label 数据、CPU/GPU 环境和 CLI。USPTO checkpoint 主要输出类别条件；只有适当的 Reaxys 配置才涉及温度预测，不能宣称所有 checkpoint 都支持 temperature。

Parrot 代码为 MIT，但官方下载器指向的外部 checkpoint 压缩包没有单独声明的机器可读许可证。因此，在任何 checkpoint 下载或推理前，必须审核所选 checkpoint 的条款，并在模型 manifest 中记录明确的允许/拒绝决定；记录缺失或决定为拒绝时必须停止。模型输出只作为检索与实验假设；文献或 ELN 验证必须另行记录。

### 模型实现

- 有条件候选：Parrot（非默认实现）；
- 仓库 Skill：`reaction-condition-recommendation`；
- 当前状态：有公开代码、checkpoint 下载器和 CLI，但任何下载或推理前都须完成所选 checkpoint 的条款审核，并在模型 manifest 中记录允许决定。

### 独立评测指标

1. catalyst / reagent / solvent Top-K recall；
2. complete condition-set exact match 或 set similarity；
3. temperature MAE，仅对支持温度的 checkpoint；
4. label decoding failure rate；
5. out-of-vocabulary / abstention rate；
6. 与 exact literature/ELN 条件的一致性。

### 硬性约束

1. 反应两侧必须在条件预测前固定，不能由条件模型同时选择反应。
2. label dictionary 必须与 checkpoint 配套并写入 provenance。
3. 不支持温度的 checkpoint 不得输出伪造温度。
4. LLM 补充的条件不得标记为 Parrot 输出。
5. 未完成 checkpoint 条款审核且模型 manifest 未记录允许决定时，不得下载或运行 Parrot checkpoint；记录缺失或决定为拒绝时必须停止。

## Problem 6. 反应收率估计

### Science Query

给定反应物、试剂和目标产物均已明确的完整反应上下文，模型预测的 isolated yield 是多少，该数值在当前部署域内是否可信？

### 目标

对可比较的固定反应做域内风险排序，识别最需要实验或专门微调的低收率/高不确定步骤。

### 输入

- reactants；
- reagents/catalysts/solvents；
- product；
- 可选 temperature、time 等完整上下文；
- 固定模型 revision 或域内 fine-tuned checkpoint。

### 输出

- raw predicted yield percent；
- 用于展示的 clipped value，但必须保留 raw value；
- `matched`、`uncertain` 或 `out_of_domain` 状态；
- missing-input flags；
- 只有经过验证时才提供 uncertainty interval；
- checkpoint 和评测 provenance。

### 技术

默认使用 ReactionT5v2-yield。输入编码为 `REACTANT:...REAGENT:...PRODUCT:...`，必须使用官方 `ReactionT5Yield` regression wrapper，而不能把权重当作普通 seq2seq 模型加载。

基础 checkpoint 训练于 Open Reaction Database，并报告了特定 reaction datasets 上的结果。论文 benchmark 不能自动转化成任意化学体系的实验误差。没有部署域 held-out set、MAE/RMSE 和 calibration 时，结果必须标为 `screening_only`。

### 模型实现

- 默认：`sagawa/ReactionT5v2-yield`，MIT；
- 仓库 Skill：`reaction-yield-estimation`。

### 独立评测指标

1. yield MAE / RMSE；
2. R² 和 Spearman，仅作为补充；
3. 不同 reaction class、scale 和时间切分下的误差；
4. uncertainty coverage / calibration，仅在实现相应方法时；
5. out-of-domain detection 和正确 abstention rate；
6. raw prediction 超出 0–100 的比例。

### 硬性约束

1. product、reactants 或 reagent context 缺失时不得做定量解释。
2. benchmark split 必须防止相同或近重复 reaction 泄漏。
3. 不同实验室、scale 或 reaction class 的结果不得无验证外推。
4. 超出 0–100 的 raw prediction 必须保留并标记 extrapolation，不能静默删除。
5. 多步路线的各步收率不得直接相乘成“路线成功概率”。
6. 没有校准的不确定区间不得由 LLM 生成。

## 六个问题是否覆盖完整逆合成规划

### 覆盖范围

- **狭义逆合成规划：** P1 + P2 已覆盖其必要模型问题，即单步 expansion 和多步 search。
- **模型化路线验证：** 加上 P3 + P4，可分析反应中心并进行正向一致性检查。
- **实验假设补全：** 加上 P5 + P6，可提出条件并做有边界的收率筛选。

### 未覆盖范围

这六项不是“从分子到工厂”的全部问题。以下功能目前没有被硬凑成模型任务：

- 实时采购、价格、交期和供应商资格；
- EHS、反应量热、热失控和危险工艺审查；
- work-up、purification、分析方法和质量标准；
- 工艺放大、设备约束、质量平衡和绿色化学指标；
- 专利/FTO、组织内部 ELN 经验和实验闭环优化。

这些是数据库、规则、实验、工艺与决策问题。将来只有在找到许可证、权重、推理接口和真实验证均合格的模型后，才应新增独立 Skill。

## 评估自动化实现难度

| 问题 | 离线接口测试 | 真实模型推理 | 科学精度评测 | 当前结论 |
| --- | --- | --- | --- | --- |
| P1 单步逆合成 | ✓ | 需 RetroChimera 环境/权重 | USPTO-50K 详细设计已写 | 已集成；数据许可/split provenance 待冻结 |
| P2 多步规划 | ✓ | 需 AiZynthFinder policies/stock | PaRoutes 详细设计已写 | 已集成；最接近可实现 Benchmark |
| P3 原子映射 | ✓ Skill 发现 | 需 RXNMapper 环境 | 独立人工真值设计已写 | recipe 完成；可信 Ground Truth 待冻结 |
| P4 正向预测 | ✓ Skill 发现 | 需 ReactionT5v2 权重 | USPTO_MIT 详细设计已写 | recipe 完成；数据/去重 provenance 待冻结 |
| P5 条件推荐 | ✓ Skill 发现 | 需 Parrot 环境及下载前条款决定 | USPTO 类别条件设计已写 | 条件型 recipe；推理前 manifest 必须记录允许决定 |
| P6 收率估计 | ✓ Skill 发现 | 需 ReactionT5v2 权重 | C–N 偶联 OOD 设计已写 | recipe 完成；数据许可/预训练去重待冻结 |

## 全 Scenario 的硬性约束

1. **Ground-truth Isolation：** 每个问题只能访问其声明输入；参考前体、参考路线、真实产物、真实条件和真实收率仅由对应 evaluator 在输出固定后读取。
2. **Task Isolation：** 评测某一个问题时固定其他问题的输入和模型。例如评测 P4 时固定反应物与试剂，P1 的候选召回率不能影响 P4 分数。
3. **Checkpoint Freeze：** 记录模型 ID、revision、checkpoint hash、训练集、许可证、依赖版本和推理参数；移动的模型名不构成 provenance。
4. **Representation Consistency：** SMILES parsing、canonicalization、盐/互变异构体、立体化学和无序组分规则必须在比较前固定，不能为某一模型单独改变。
5. **Search Fairness：** P2 的 policy、filter、stock、预算、硬约束和停止条件必须冻结；timeout 与 unsolved 必须作为结果保留。
6. **Held-out Information Isolation：** intended product、真实条件或真实收率不得参与对应模型的生成、候选选择、beam reranking 或超参数调整。
7. **No Self-confirmation：** 使用正向模型验证逆向模型时必须披露共享数据和模型家族；round-trip 一致不得表述为独立实验事实。
8. **Score Semantics Isolation：** mapping confidence、beam likelihood、planner score、product rank、condition probability 和 yield error 不得压缩成一个未经校准的总可信度。
9. **Evidence Isolation：** 模型输出、确定性计算、文献、ELN、供应商数据、专家判断和 LLM 假设必须使用不同 source type；LLM 不能把假设升级为证据。
10. **Domain and Abstention：** 条件与收率模型必须记录适用域；缺少输入、超出训练域或缺少校准时应输出 unknown、out_of_domain 或 screening_only。
11. **No Hidden Model Download：** 权重不得提交进仓库；下载必须显式授权、校验来源和哈希，并在隔离环境加载。对 Parrot，必须在任何下载或推理前将 checkpoint 条款的允许/拒绝决定记录到模型 manifest。
12. **Final Evaluation Constraint：** evaluator 只能计算预先声明的指标，不得根据测试集结果反向修改模型、过滤规则、搜索预算或排序权重。

## 当前仓库中的对应 Skills

| 问题 | 仓库相对目录 |
| --- | --- |
| P1 | [`../single-step-retrosynthesis/`](../single-step-retrosynthesis/) |
| P2 | [`./`](./) |
| P3 | [`../reaction-atom-mapping/`](../reaction-atom-mapping/) |
| P4 | [`../reaction-forward-prediction/`](../reaction-forward-prediction/) |
| P5 | [`../reaction-condition-recommendation/`](../reaction-condition-recommendation/) |
| P6 | [`../reaction-yield-estimation/`](../reaction-yield-estimation/) |

模型准入证据、备选模型和排除理由见同目录的 `MODEL_TASKS_zh.md`；各 Benchmark 的详细实现草案见 [`scenarios/README_zh.md`](scenarios/README_zh.md)。
