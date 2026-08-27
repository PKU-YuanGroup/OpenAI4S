# Scenario 5：固定反应两侧下的反应条件组合推荐

## Scenario Overview

该 Scenario 面向反应物和目标产物均已确定、但实验条件未知的反应执行准备。Harness 获得固定 reaction SMILES，在有限 Top-K 预算内推荐离散的催化剂、溶剂和试剂组合，并说明模型覆盖范围与不确定性。

与让模型自由生成一段条件文本的基础任务相比，本场景冻结五个类别槽位、联合组合预算和 train-derived vocabulary。难点是生成内部一致的 condition tuple，并在 test-only 类别或训练域外反应上明确 OOV/abstention；不能把五个独立 Top-K 列表任意做笛卡尔积，也不能用不可评分的温度叙述掩盖类别恢复失败。

默认模型 Parrot 在 USPTO-Condition 上预测类别型条件槽位。该公开数据不可靠地支持连续温度评测，因此本场景 v1 只评价 `catalyst1, solvent1, solvent2, reagent1, reagent2`；温度、时间、浓度、加料顺序和气氛不在本场景 Ground Truth 中。若模型或报告生成温度，只能作为未评分假设，不能混入条件 exact-match。

专利条件并非唯一最优条件：一条记录只证明某组条件被报告过，未命中不等于建议不可行。因此主指标称“记录条件恢复”，并辅以槽位命中、组合 Top-K、多样性和覆盖率；不把模型推荐称为实验验证方案。

## 数据可获取性与 Benchmark 构建方案

### 已验证的数据与模型来源

- **Parrot**：作者官方仓库以 MIT 发布代码；第一作者 Hugging Face 仓库 `xiaoruiwang/ChemEnzyRetroPlanner_metadata` 也明确声明 MIT，并把 `USPTO_condition.mar` 描述为 Parrot 条件预测器。本场景只准入固定 revision `b9ef6049d341bfc62d835f09ad6ce33b6f86b047` 及经过大小/SHA256 校验的 MAR 和 metadata。
- **USPTO-Condition**：官方 `download_data.py` 提供处理后数据归档入口，适合类别型条件 benchmark。
- **Reaxys 条件数据**：需要受限原始数据库许可，不能随开源 Benchmark 再发布，因此不作为 v1 数据源。

Parrot 原官方下载器中的 Google Drive 归档仍没有与代码许可证等价的独立机器可读授权声明，因此保持阻塞；Reaxys 数据也不准入。已准入的第一作者 HF 固定快照必须继续按文件名、revision、大小与 SHA256 校验，不能静默替换。模型 artifact 可用于工程部署 canary；若其训练集与本场景 test split 的重叠无法审计，则不得据此报告正式 hidden-test 科学指标。

### 数据冻结规则

1. 从 Parrot 官方 USPTO-Condition 归档读取其正式 split 或 index；若上游只给随机 index，保存原始 index 和 seed，不重新挑选测试样本。
2. 以去 atom-map、canonical reactant+product signature 分组去重，防止同一反应的重复专利记录跨 train/test。
3. 角色字段固定为反应物、产物以及五个条件槽位。空槽使用显式 null，不用字符串拼接猜角色。
4. 同一 canonical reaction 对应多个报告条件时，全部作为合法 reference combinations；同时保留记录频次，不随机选一个。
5. 低频类别只在预注册阈值下映射到 `UNK`，阈值只能由 train split 确定；test-only 类别必须报告 out-of-vocabulary。
6. public train/validation 可含条件标签供方法选择；public test input 只含 reaction，两侧结构固定；test conditions 进入 private evaluator。
7. 模型训练数据必须与 split 对齐。官方 checkpoint 无法证明无 test overlap 时，只能作为部署演示，正式分数须用 public train 从头训练或使用有可审计 manifest 的 checkpoint。
8. 保存类别词表、标准化映射、重复组、split audit、代码/数据/权重哈希。

## Scenario 流程与基础流程的对比

```text
可评分的条件组合推荐场景                    基础条件生成

固定 reactants >> product                  输入一个反应
             │                                  │
             ▼                                  ▼
角色、类别词表和 split 冻结                  模型/LLM 给条件
             │                                  │
             ▼                                  ▼
在 public train/validation 选择方法            返回一段文本
             │
             ▼
对 hidden test 生成固定 Top-K 组合
             │
             ▼
槽位合法性、OOV、重复与多样性诊断
             │
             ▼
私有 evaluator 做多参考条件恢复评分
```

## Science Query

给定一组反应物和目标产物均已确定的匿名有机反应，以及由公开训练集冻结的条件类别词表，请在不能访问测试反应记录条件的情况下，为每条反应推荐最多 10 组催化剂、溶剂和试剂类别组合，并明确模型分歧、OOV 风险和未被该数据集评价的实验变量。

## 阶段介绍

### Stage 1. Reaction and Label-Schema Validation

**目标：** 固定反应身份与五个可评分条件槽位。

**输入：** train/validation labels、test reactions、label vocabulary。

**输出：** canonical reactions、词表统计和字段质量报告。

**技术：** 检查反应两侧可解析、目标产物存在、槽位名称/空值一致。温度等非 v1 字段不进入标签或综合分数。

### Stage 2. Split, Duplicate, and Checkpoint Audit

**目标：** 阻止重复反应和 checkpoint 训练泄漏。

**输入：** split manifest、canonical signatures、model manifest。

**输出：** overlap report 和模型准入结论。

**技术：** 按 reaction group 检查 exact overlap；记录 checkpoint 训练语料。无法审计的预训练条件模型不得提交正式 hidden-test 分数。

### Stage 3. Public Validation and Method Selection

**目标：** 在不查看 test labels 时冻结模型、解码和 Top-K 规则。

**输入：** public train/validation。

**输出：** validation metrics、选定 checkpoint、温度缩放或解码参数。

**技术：** 评价 full-combination Top-K、各槽位 recall、coverage 和 rare-class 性能。超参数只在 validation 选择，所有尝试写入日志。

### Stage 4. Top-K Condition Combination Generation

**目标：** 为 hidden test 输出联合条件方案，而非互不一致的独立槽位列表。

**输入：** fixed reactions、selected model、beam budget。

**输出：** 每 reaction 的 Top-K condition tuples 和 raw scores。

**技术：** Parrot 以自回归/组合方式产生槽位；保持空槽语义，输出类别必须属于冻结 vocabulary 或显式 `UNK`。不同组合去重但保留原 rank。

### Stage 5. Applicability and Diversity Diagnosis

**目标：** 标出模型训练域外、低置信度和组合塌缩。

**输入：** reaction embeddings/类别频次、Top-K outputs。

**输出：** OOV、nearest-domain distance、score margin、unique catalysts/solvents/reagents。

**技术：** applicability 规则只由 train/validation 构建。不得因为 test 参考条件罕见而删除样本。

### Stage 6. Frozen Output and Private Evaluation

**目标：** 在查看 test 条件前冻结所有推荐。

**输入：** recommendations 与运行日志。

**输出：** `condition_recommendations.jsonl`、`intermediate_results.json`、诊断。

**技术：** evaluator 对多参考条件 tuple 计算 Top-K exact recovery，并分别评价各槽位。OOV test labels 既报告 closed-vocabulary 性能，也单独报告可评分覆盖。

## Input Data 与 Ground Truth 组织

### Harness 可见输入

```text
input/
├── train_reactions_conditions.csv
├── validation_reactions_conditions.csv
├── test_reactions.csv
├── condition_vocabulary.json
├── split_manifest.json
├── scenario_config.json
└── environment_manifest.json
```

公开 test 只含 `reaction_id,reactants,product`。训练/验证标签只含五个 v1 槽位，不伪造温度。

### Evaluator 私有数据

```text
private_evaluator/
├── test_condition_references.jsonl
├── test_oov_audit.csv
├── source_provenance.json
├── deduplication_audit.json
└── evaluation_config.json
```

## `intermediate_results.json` 最低要求

```json
{
  "scenario_id": "reaction_condition_uspto_categorical_v1",
  "dataset_snapshot": "<snapshot>",
  "input_hashes": {},
  "label_slots": ["catalyst1", "solvent1", "solvent2", "reagent1", "reagent2"],
  "model": {"id": "<model>", "checkpoint_sha256": "<sha256>"},
  "validation": {"methods": [], "selected_method": "<id>"},
  "test_recommendations": [],
  "warnings": []
}
```

每条推荐保存完整 tuple、各槽位、rank、raw score、OOV/duplicate/applicability 状态和选择该模型的 public validation 依据。

## 建议的 Reference Repository 结构

```text
reference_repository/
├── README.md
├── environment.yml
├── run.sh
├── input/
├── src/
│   ├── validate_schema.py
│   ├── audit_splits.py
│   ├── train_or_load_model.py
│   ├── validate_model.py
│   ├── recommend_conditions.py
│   └── build_outputs.py
├── results/
│   ├── condition_recommendations.jsonl
│   ├── validation_metrics.csv
│   └── intermediate_results.json
└── analysis/
    └── diagnostics.py
```

## 评估自动化实现难度

类别型场景可离线自动化：schema 校验 ✓ → 去重/泄漏审计 ✓ → public validation ✓ → Top-K 组合生成 ✓ → OOV/域诊断 ✓ → private evaluation ✓。发布阻塞是数据与 checkpoint 的再分发许可和 checkpoint split provenance；连续温度不属于 v1 自动化范围。

仓库已提供 `retrosynthesis_planning.condition_benchmark`：冻结五个条件槽位及各自闭集词表，只接受模型实际发出的完整条件元组，禁止边际标签笛卡尔积；私有 evaluator 计算多参考 exact-tuple Top-K、slot recall、OOV、重复和预算利用率。

## 评测指标

### Scientific Task Accuracy

- full condition tuple Top-1/3/5/10 exact recovery；
- catalyst、solvent、reagent 各槽位 Top-K recall/F1；
- 任一合法多参考 tuple 的 Mean Reciprocal Rank；
- OOV rate、closed-vocabulary coverage 和 rare-class macro F1；
- unique tuple、catalyst/solvent/reagent diversity；
- confidence/error-detection calibration（仅经 validation 校准）；
- latency、throughput 与峰值显存。

### Scientific Workflow Completeness

检查反应两侧固定、标签 schema、分组去重、public validation、联合 tuple 输出、OOV/applicability 诊断、test label 隔离和未评分变量声明。

### Scientific Reproducibility

检查数据/词表/checkpoint 哈希、split、训练/解码参数、随机 seed、完整 Top-K 和一键重跑入口。

## 代码与数据的硬性约束

1. **Test-Condition Isolation**：Harness 不得读取测试条件或其频次提示。
2. **Fixed Reaction Sides**：不得修改反应物或目标产物来匹配条件。
3. **Grouped Deduplication**：同一 canonical reaction 不得跨 train/test。
4. **Vocabulary Freeze**：词表和 rare threshold 只能由 train 确定。
5. **Checkpoint Provenance**：无法证明 test 隔离的 checkpoint 不进入正式榜单。
6. **Joint Combination Output**：不得把各槽位独立 Top-K 拼成未评分的笛卡尔积。
7. **Categorical Scope**：v1 不评价温度、时间、浓度、气氛或操作顺序。
8. **Multi-Reference Evaluation**：同反应多个记录条件均应保留。
9. **No Optimality Claim**：命中记录条件不等于最优，未命中不等于不可行。
10. **Full-Trajectory Requirement**：保留 public validation、全部 test Top-K 和失败状态。

## Domain-Specific Failure Cases

- 用 Reaxys 受限数据构建“开源” Benchmark；
- 把代码 MIT 许可证当作外部数据和 checkpoint 的许可证；
- 官方 checkpoint 已见过 test 条件却直接评分；
- 同一专利反应重复项跨 train/test；
- 分别预测槽位后任意组合，导致化学上互不兼容；
- 把缺失字段当作某个具体溶剂或试剂；
- 从不含可靠温度标签的 USPTO-Condition 声称温度 accuracy；
- 把模型条件分数当作实验成功率或条件最优性。

## 参考文献与一手资源

- Wang X, et al. Parrot 官方实现、配置和数据下载脚本：<https://github.com/wangxr0526/Parrot>
- Wang X, et al. *Parrot: Prediction of Reaction Conditions with Transformer-Based Models*. Research (2023), DOI: 10.34133/research.0231.
