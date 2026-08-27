# Scenario 6：分布外 Buchwald–Hartwig 反应收率估计

## Scenario Overview

该 Scenario 面向反应物、试剂和目标产物均已知，需要预测 isolated yield 并识别模型外推失败的反应优选任务。Benchmark 使用 Ahneman 等人的 Buchwald–Hartwig C–N 偶联高通量实验数据及后续公开的 random/MFF Test1–4 划分。该数据具有成体系的真实实验收率，适合评价回归和分布外泛化；不使用噪声更大、实验尺度和缺失机制复杂的 USPTO 抽取收率作为主 Ground Truth。

与随机 IID split 上拟合一个回归器的基础任务相比，本场景把未见反应组分组合的 Test1–4 作为主评价条件，并要求 public validation 校准的不确定性、macro-average 与 worst-group 同时报告。模型不能依靠近重复反应获得高 R²，也不能用随机测试集的平均表现掩盖某一类组分外推完全失效。

候选部署模型为 ReactionT5v2-yield；Yield-BERT/rxn_yields 提供经典开源基线和官方训练模型。当前固定的 ReactionT5v2-yield release 虽能加载，但其 model-card canary 在复现上游 wrapper 与预处理后仍出现显著不一致（期望约 19.1666，实测 65.924858），因此必须保持隔离，不能作为默认科学结果模型。正式比较的核心不是只在随机拆分上获得高 R²，而是在固定的 Test1–4 留组分外推设置下，能否保持较低误差、合理排序并识别不确定性。

收率是反应、条件、操作和测量协议的联合属性。模型输入中缺失的时间、浓度或加料信息会形成不可约不确定性，因此输出必须称“在该数据定义和输入字段下的预测收率”，不能扩展为路线可行性或跨实验室绝对收率保证。

## 数据可获取性与 Benchmark 构建方案

### 已验证的数据与模型来源

- **Buchwald–Hartwig HTE**：Ahneman 等人公开的 Pd 催化 C–N 偶联实验矩阵，共约 3,955 条反应；rxn4chemistry 官方 `rxn_yields` 仓库提供预处理、random split、MFF 外推设置和模型代码。
- **ReactionT5v2**：作者官方仓库明确链接 Buchwald–Hartwig 数据，提供 yield 训练/预测代码，并通过 `CN_test_data_path` 从 ORD 预训练数据删除测试反应以降低泄漏。
- **ReactionT5v2-yield**：MIT 权重和推理入口可本地加载，但当前固定 release 未通过其公开 canary；仅可做协议测试，重新通过独立验证前不得进入正式评分或候选排序。
- **Yield-BERT**：`rxn_yields` 官方实现与已训练模型以 MIT 发布，论文同时指出 USPTO 收率受反应尺度等偏差影响。

原始 HTE 数据、派生 split 和模型权重的再发布条款必须分别冻结。若上游下载链接未附清晰数据许可证，Reference Repository 只能提供构建脚本与哈希，不能擅自重新打包数据。

### 数据与划分冻结规则

1. 从官方 `rxn_yields`/ReactionT5v2 链接获取原始表和 MFF Test1–4，冻结 commit、文件 SHA256、列名、单位和缺失值处理。
2. 验证收率范围、百分数单位和每条反应的 aryl halide、amine、ligand、base、additive 以及 product 字段；不对异常值静默 clipping。
3. 保留官方 random splits 作为 IID 参考，但主榜以 MFF Test1–4 的 OOD 平均和最坏组性能排序。
4. 重复反应按完整组分与条件 signature 聚合；聚合方式、重复数和标准差写入审计。近重复组合不得跨 train/test。
5. public train/validation 发布真实 yield；test input 隐藏 yield。每个 Test1–4 维护独立 split manifest，不把四个 test 合并后重新随机划分。
6. 若使用 ORD 预训练 checkpoint，必须执行 canonical full-reaction exact 去重，并记录 `CN_test_data_path` 清单。无法证明去重的 checkpoint 只能作非正式 baseline。
7. 模型选择、缩放、ensemble 和不确定性校准只使用 public train/validation，不访问任何 Test1–4 yield。
8. 输入字段不足以表示的实验条件明确列入 `known_missing_covariates`，不让 LLM 从论文或网络补全。

## Scenario 流程与基础流程的对比

```text
分布外收率科学场景                         基础收率调用

冻结反应、条件字段 + train/valid             输入 reaction SMILES
               │                                 │
               ▼                                 ▼
单位、重复和 split 审计                       调用回归模型
               │                                 │
               ▼                                 ▼
在 public validation 选择/校准方法              输出一个数字
               │
               ▼
对 Random + MFF Test1–4 批量预测
               │
               ▼
输出点估计、区间、域距离和失败标记
               │
               ▼
私有 evaluator 分别计算 IID/OOD 指标
```

## Science Query

给定 Buchwald–Hartwig C–N 偶联的公开训练/验证反应及收率，以及五组隐藏测试反应的完整结构化输入，请在不访问测试收率的条件下，预测每条反应的百分比收率，给出经公开验证校准的不确定性，并重点评价对 MFF Test1–4 未见组分组合的外推能力。

## 阶段介绍

### Stage 1. Schema, Unit, and Duplicate Audit

**目标：** 确保标签方向、百分数单位、组件身份和重复处理一致。

**输入：** train/validation/test tables 和 split manifests。

**输出：** 数据质量报告、canonical signatures、聚合记录和哈希。

**技术：** 收率固定为 0–100 percentage points；越高越好。检查反应组分与 product，可疑越界值停止构建而不是 clipping。重复实验按预注册均值聚合并保存方差。

### Stage 2. Split and Pretraining Leakage Audit

**目标：** 确认 OOD 组分隔离和预训练数据去重。

**输入：** split manifests、training corpus manifest、checkpoint。

**输出：** exact/near-duplicate overlap 与准入结论。

**技术：** 分别验证 aryl halide、ligand、base/additive 等 MFF holdout 逻辑；对 ORD/其他语料按 full-reaction signature 去重。只凭论文总体声明不足以替代 checkpoint manifest。

### Stage 3. Public Validation and Model Selection

**目标：** 不使用 hidden test 收率选择回归方法和参数。

**输入：** train/validation labels、候选模型。

**输出：** validation MAE/RMSE/R²/Spearman、选定模型和固定参数。

**技术：** 可比较 ReactionT5v2-yield、Yield-BERT 和简单 reaction-fingerprint baseline。任何 fine-tuning、ensemble 权重、early stopping 与校准都只看 validation。

### Stage 4. Yield Prediction

**目标：** 对全部隐藏测试反应一致产生点预测。

**输入：** fixed model 与 reaction fields。

**输出：** `predicted_yield_percent`、raw model output、运行状态。

**技术：** 模型输入严格记录 reactant/reagent/product 编码和最大长度。预测超出 0–100 时保留 raw value；用于指标的 clipping 规则若存在必须预注册并同时报告 unclipped error。

### Stage 5. Uncertainty and Applicability Diagnosis

**目标：** 识别 OOD 反应和高误差风险，而不是只给无依据置信度。

**输入：** train/validation 表征、模型 ensemble/dropout 或残差校准。

**输出：** prediction interval、domain-distance、uncertainty flag。

**技术：** 区间覆盖与尺度仅在 validation 校准；Test1–4 不参与调参。若模型不支持可信区间，输出 `uncertainty_unavailable`，不得编造。

### Stage 6. Frozen Output and Private OOD Evaluation

**目标：** 预测冻结后分别评价随机与四种 OOD split。

**输入：** predictions、private yields。

**输出：** split-level metrics、worst-group 指标、误差诊断。

**技术：** 每个 split 单独计算 MAE、RMSE、R²、Spearman、Top-decile enrichment 和区间覆盖；主排名使用 Test1–4 macro average 与 worst-group MAE，不由样本更多的 split 支配。

## Input Data 与 Ground Truth 组织

### Harness 可见输入

```text
input/
├── train_reactions_yields.csv
├── validation_reactions_yields.csv
├── random_test_reactions.csv
├── mff_test1_reactions.csv
├── mff_test2_reactions.csv
├── mff_test3_reactions.csv
├── mff_test4_reactions.csv
├── split_manifest.json
├── scenario_config.json
└── environment_manifest.json
```

测试表不含 yield。若为支持模型输入保留一个 `YIELD` 列，必须全为空且在送入模型前移除；禁止使用占位数值。

### Evaluator 私有数据

```text
private_evaluator/
├── random_test_yields.csv
├── mff_test1_yields.csv
├── mff_test2_yields.csv
├── mff_test3_yields.csv
├── mff_test4_yields.csv
├── source_provenance.json
├── leakage_audit.json
└── evaluation_config.json
```

## `intermediate_results.json` 最低要求

```json
{
  "scenario_id": "buchwald_hartwig_yield_ood_v1",
  "dataset_snapshot": "<snapshot>",
  "input_hashes": {},
  "yield_unit": "percent",
  "model": {"id": "<model>", "checkpoint_sha256": "<sha256>"},
  "validation": {"methods": [], "selected_method": "<id>"},
  "test_predictions": [],
  "known_missing_covariates": [],
  "warnings": []
}
```

每条测试记录至少保存 split、point prediction、raw prediction、区间或 unavailable 原因、domain-distance、输入截断状态和耗时。

## 建议的 Reference Repository 结构

```text
reference_repository/
├── README.md
├── environment.yml
├── run.sh
├── input/
├── src/
│   ├── validate_data.py
│   ├── audit_leakage.py
│   ├── train_baselines.py
│   ├── select_model.py
│   ├── predict_yield.py
│   ├── calibrate_uncertainty.py
│   └── build_outputs.py
├── results/
│   ├── predictions.csv
│   ├── validation_metrics.csv
│   └── intermediate_results.json
└── analysis/
    ├── diagnostics.py
    └── figures/
```

## 评估自动化实现难度

全流程可离线自动化：单位/重复审计 ✓ → OOD split/预训练泄漏审计 ✓ → public validation ✓ → 批量回归 ✓ → 不确定性校准 ✓ → private split-level 评价 ✓。关键发布阻塞是原始 HTE 数据再分发条款和每个 checkpoint 的精确训练/去重清单。

仓库已提供 `retrosynthesis_planning.yield_benchmark`：冻结 random_test 与四个 MFF 子组，保留未经裁剪的原始预测和显式区间缺失，计算每组误差、R2、Spearman、Top-decile enrichment、区间覆盖，以及 macro-OOD 和 worst-group MAE。它不包含受外部条款约束的数据或权重。

## 评测指标

### Scientific Task Accuracy

- Random 与 MFF Test1–4 分别的 MAE、RMSE、R²、Spearman；
- MFF Test1–4 macro-average MAE/RMSE；
- worst-group MAE；
- Top-10% high-yield enrichment、NDCG 和 pairwise ranking accuracy；
- prediction interval coverage、平均区间宽度和 calibration error；
- 高不确定性对大误差的 AUROC/AUPRC；
- latency、throughput、peak memory/GPU memory。

### Scientific Workflow Completeness

检查单位/方向、重复聚合、OOD split、预训练去重、public validation、点预测、区间或缺失说明、逐 split 评价和限制声明。

### Scientific Reproducibility

检查原始/派生文件和 checkpoint 哈希、split manifest、训练/选择/校准日志、seed、软件版本、完整逐样本预测和一键重跑入口。

## 代码与数据的硬性约束

1. **Test-Yield Isolation**：任何 test yield 均不得进入训练、选择、校准或提示。
2. **Fixed OOD Splits**：不得把 Test1–4 重新随机拆分或混合后只报总体指标。
3. **Unit and Direction**：标签为 0–100 百分比且越高越好，不得与 0–1 比例混用。
4. **Pretraining Deduplication**：ORD/USPTO 等预训练数据必须排除 test exact duplicates。
5. **Validation-Only Selection**：模型、权重、early stopping、clipping 和区间参数只由 public validation 确定。
6. **Raw Prediction Preservation**：clipping 前后结果均保存。
7. **Uncertainty Honesty**：未校准模型分数不得称置信区间。
8. **Worst-Group Reporting**：不得只用 random split 或 macro average 隐藏最差 OOD 组。
9. **Domain Scope**：结果只适用于该 C–N 偶联实验定义，不能泛化声称覆盖全部有机反应。
10. **No Route Feasibility Claim**：预测收率不是多步路线成功概率。

## Domain-Specific Failure Cases

- 用 test yield 调 ensemble 权重或选择最优 seed；
- 只报告 random split 高 R²，忽略 Test1–4 外推崩溃；
- 预训练 ORD 中含测试反应却称零样本泛化；
- 把 0.73 与 73% 混用或偷偷 clipping 提升 RMSE；
- 随机拆散同一反应的重复实验造成泄漏；
- 输出窄区间但没有 validation coverage 校准；
- 使用 USPTO 抽取 yield 替代 HTE 真值而不处理尺度/报告偏差；
- 将单步预测收率相乘当作多步路线成功概率。

## 参考文献与一手资源

- Ahneman DT, et al. *Predicting reaction performance in C–N cross-coupling using machine learning*. Science 360, 186–190 (2018), DOI: 10.1126/science.aar5169.
- Schwaller P, et al. `rxn_yields` 官方数据处理、划分、模型与已训练权重：<https://github.com/rxn4chemistry/rxn_yields>
- Sagawa T, Kojima R. ReactionT5v2 官方实现与 C–N split 去重入口：<https://github.com/sagawatatsuya/ReactionT5v2>
- ReactionT5v2-yield 官方权重：<https://huggingface.co/sagawa/ReactionT5v2-yield>
