# Scenario 4：反应物与试剂给定时的正向产物预测

## Scenario Overview

该 Scenario 面向完整前体侧已知、产物未知的反应结果预测。Harness 获得匿名反应物和试剂字段，在固定 Top-K 预算内预测主要产物。它既可独立评价 forward model，也可作为逆合成提案的 round-trip 诊断；但主 Benchmark 不把逆合成模型生成的前体混入测试集，以免同时测量两个模型的错误。

默认数据使用 Molecular Transformer 官方 USPTO_MIT 的 separated test split，默认模型为 ReactionT5v2-forward。产品标签在 Harness 执行期间完全隐藏。由于专利记录通常只列报告主产物，Top-K exact match 衡量对记录结果的恢复，不代表未记录副产物一定错误，也不代表预测命中即可在实验中实现。

立体化学是本场景的重要组成。主指标保留 isomeric SMILES；另行报告忽略立体化学的 connectivity accuracy，用于区分骨架错误和立体错误，不能用后者替代主指标。

## 数据可获取性与 Benchmark 构建方案

### 已验证的数据与模型来源

- **USPTO_MIT / USPTO_STEREO**：Molecular Transformer 官方仓库提供数据下载和 separated/mixed 预处理，代码以 MIT 许可证发布。
- **ReactionT5v2-forward**：作者官方仓库与 Hugging Face 发布 MIT 代码和 0.2B safetensors 权重，明确要求 `REACTANT`、`REAGENT`、`PRODUCT` 字段并支持 beam generation。
- **Molecular Transformer**：可作为经典开源基线；若使用其 checkpoint，必须冻结具体版本和训练 split。

USPTO 派生数据的再发布条款需要独立审计。正式发布只提供从固定上游快照构建的匿名 test inputs 与私有 labels，不宣称 MIT 代码许可证覆盖原专利数据。

### 测试集冻结规则

1. 使用官方 `USPTO_MIT/MIT_separated/test.csv`，不重新随机切分。
2. 固定 RDKit 后去除 atom map，分别 canonicalize reactants、reagents 和 product；保留 isomeric 信息。
3. 排除无产品、多个无法确定主产品、产品解析失败或输入前体为空的记录；所有排除理由进入审计。
4. 以无 map 的 canonical full-reaction signature 去重；同一 precursor input 对应多个记录产品时保存为多参考答案，或在预注册规则下标为 ambiguous，不随机留一条。
5. 与模型训练集执行 exact reaction 去重审计。ReactionT5v2 官方训练脚本提供 `USPTO_test_data_path` 去除重叠，正式 checkpoint 仍需记录其训练 manifest 证明该逻辑实际使用。
6. 对合格反应按 `SHA256(input_signature + benchmark_seed)` 冻结固定数量，匿名为 `forward_target_0001...`。
7. Harness 只见 reactants/reagents；product、reaction class、原始 ID 和专利来源进入 private evaluator。

## Scenario 流程与基础流程的对比

```text
严格隐藏产物的正向预测场景                    基础 round-trip 检查

冻结 reactants + reagents                     输入候选前体
             │                                    │
             ▼                                    ▼
角色、组分顺序和立体规范冻结                    调用 forward model
             │                                    │
             ▼                                    ▼
固定 checkpoint + Top-K 生成                   看目标是否出现
             │
             ▼
保留全部 beam、无效与重复输出
             │
             ▼
isomeric 与 connectivity 两套规范化
             │
             ▼
冻结后由 evaluator 读取真实产品评分
```

## Science Query

给定一组匿名反应的反应物和试剂/催化剂/溶剂字段，在不能访问产物、反应类别和原始专利上下文的情况下，请预测每条反应的 Top-5 主要产物，区分结构无效、骨架错误和立体化学错误，并保存完整 beam 与模型 provenance。

## 阶段介绍

### Stage 1. Input and Role Validation

**目标：** 确认 reactant/reagent 边界与模型格式，不因预处理差异泄漏产物。

**输入：** `reaction_inputs.csv` 和 config。

**输出：** 规范化输入、字段缺失与解析报告。

**技术：** 分别解析 `REACTANT` 和 `REAGENT`；组分内部按固定策略 canonicalize/sort。不得把 product 片段、产物 atom map 或反应类别留在输入。

### Stage 2. Checkpoint and Leakage Admission

**目标：** 证明 checkpoint 身份与 test reaction 隔离。

**输入：** model manifest、checkpoint、训练数据说明。

**输出：** admission report。

**技术：** 校验权重哈希、代码 commit、模型 input template、训练去重清单和许可证。无法证明排除 test 的模型只能作为非正式探索结果。

### Stage 3. Fixed-Budget Product Generation

**目标：** 在一致 beam 与硬件预算下生成 Top-K。

**输入：** 规范化前体、model、beam config。

**输出：** raw product strings、sequence scores、rank、耗时。

**技术：** ReactionT5v2 使用固定 `REACTANT:...REAGENT:...` 模板、最大输入/输出长度、beam size 和返回数。截断必须记录；不得在看到 Ground Truth 后增大 beam。

### Stage 4. Product Normalization and Error Taxonomy

**目标：** 公平比较等价 SMILES，并区分无效、骨架和立体错误。

**输入：** raw beams。

**输出：** isomeric canonical product、non-isomeric connectivity signature、parse/duplicate 状态。

**技术：** sanitize 后生成两种签名；多组分产品的 salt/主产物处理规则必须与数据构建一致。非法输出保留，不用低 rank 自动补足后隐藏失败。

### Stage 5. Prediction Uncertainty and Candidate Diversity

**目标：** 描述模型内部不确定性，不把 beam score 伪装成校准概率。

**输入：** Top-K 与 raw scores。

**输出：** score margin、unique products、结构多样性和不确定性标签。

**技术：** 报告 Top-1/2 margin、候选数量、fingerprint diversity。只有在 public validation 上完成预注册校准时才输出概率。

### Stage 6. Private Product Evaluation

**目标：** 结果冻结后对隐藏产品评分。

**输入：** predictions、private products。

**输出：** Top-K accuracy、MRR、立体/骨架错误分层和资源指标。

**技术：** 先逐 reaction 评分，再总体和按反应类别汇总。多参考产品按任一合法参考命中计算。

## Input Data 与 Ground Truth 组织

### Harness 可见输入

```text
input/
├── reaction_inputs.csv
├── scenario_config.json
├── model_manifest.json
└── environment_manifest.json
```

公开 CSV 只含 `reaction_id,reactants,reagents`。若某条记录无 reagent，使用明确空字段而非缺列。

### Evaluator 私有数据

```text
private_evaluator/
├── products.jsonl
├── reaction_classes.csv
├── source_provenance.json
├── split_and_dedup_audit.json
└── evaluation_config.json
```

## `intermediate_results.json` 最低要求

```json
{
  "scenario_id": "forward_prediction_uspto_mit_separated_v1",
  "dataset_snapshot": "<snapshot>",
  "input_hashes": {},
  "model": {"id": "<model>", "checkpoint_sha256": "<sha256>"},
  "generation": {"top_k": 5, "num_beams": 5},
  "canonicalization": {},
  "reactions": [],
  "aggregate_diagnostics": {},
  "warnings": []
}
```

每条 reaction 保存格式化模型输入的哈希、全部 raw/canonical beam、score、rank、parse/duplicate/truncation 状态与耗时。

## 建议的 Reference Repository 结构

```text
reference_repository/
├── README.md
├── environment.yml
├── run.sh
├── input/
├── src/
│   ├── validate_inputs.py
│   ├── admit_model.py
│   ├── run_forward.py
│   ├── normalize_products.py
│   └── build_outputs.py
├── results/
│   ├── predictions.jsonl
│   ├── diagnostics.csv
│   └── intermediate_results.json
└── analysis/
    └── figures.py
```

## 评估自动化实现难度

全流程可离线自动化：角色校验 ✓ → checkpoint/泄漏审计 ✓ → Top-K 生成 ✓ → 两级产品规范化 ✓ → 私有标签评价 ✓。正式发布的主要阻塞项是 USPTO 派生数据许可记录与 checkpoint 训练去重证明。

## 评测指标

### Scientific Task Accuracy

- isomeric Top-1/3/5 exact-match accuracy；
- connectivity-only Top-1/3/5 accuracy；
- Mean Reciprocal Rank；
- invalid、empty、duplicate 和 unique-product rate；
- stereochemistry-only error rate；
- 按 reaction class、产品大小和输入长度分层的 accuracy；
- latency、throughput、peak memory/GPU memory。

### Scientific Workflow Completeness

检查角色边界、测试泄漏审计、固定生成预算、完整 beam、isomeric/connectivity 双重诊断、不确定性语义与私有标签隔离。

### Scientific Reproducibility

检查输入/checkpoint 哈希、模型模板、tokenizer/依赖版本、截断参数、随机性和单一重跑入口。

## 代码与数据的硬性约束

1. **Product Isolation**：Harness 不得读取真实产品、产物 map、类别或 evaluator 配置。
2. **Role Consistency**：reactants 与 reagents 不得按模型表现重新分配。
3. **Fixed Official Split**：不得重切 test 或删除模型不擅长的反应。
4. **Checkpoint Leakage Audit**：正式模型必须证明 test reaction 未用于训练。
5. **Stereochemistry Preservation**：主指标不得去除手性获得虚高结果。
6. **Fixed Top-K**：不得对失败样本扩大 beam 或换模型补答案。
7. **Full Beam Preservation**：invalid/duplicate 输出必须计入诊断。
8. **Score Semantics**：sequence score 不是实验概率，除非另有公开校准。
9. **Round-Trip Boundary**：逆向/正向模型一致只算模型一致性，不是实验验证。
10. **No Outcome Overclaim**：记录产品命中不保证选择性、收率或条件可实现。

## Domain-Specific Failure Cases

- 在 input CSV 中保留 `PRODUCT` 列或产物 atom map；
- 用 mixed 数据格式模型测 separated 输入却不记录角色变化；
- 去除立体化学后把 connectivity 命中报告成完整命中；
- checkpoint 在完整 USPTO/ORD 中见过 test reaction；
- 只保留可解析结果并补足 Top-K，隐藏 invalid rate；
- 用 intended product 参与 beam reranking；
- 将 forward/retro 同家族模型的 round-trip 称为独立验证；
- 把专利主产物当作所有可能反应产物的穷尽真值。

## 参考文献与一手资源

- Schwaller P, et al. *Molecular Transformer: A Model for Uncertainty-Calibrated Chemical Reaction Prediction*. ACS Central Science (2019). 官方实现与 USPTO_MIT 数据入口：<https://github.com/pschwllr/MolecularTransformer>
- Sagawa T, Kojima R. ReactionT5v2 官方实现与数据/推理协议：<https://github.com/sagawatatsuya/ReactionT5v2>
- ReactionT5v2-forward 官方权重：<https://huggingface.co/sagawa/ReactionT5v2-forward>
