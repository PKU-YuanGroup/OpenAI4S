# Scenario 1：未知反应类别下的单步逆合成前体生成

## Scenario Overview

该 Scenario 面向只有目标产物结构、需要提出一步断键方案的早期合成设计。Harness 获得一组匿名目标产物、固定推理预算和冻结模型环境，在不能访问参考反应物、反应类别和原始专利上下文的条件下，为每个产物生成有序的 Top-K 前体集合。

与“已知 reaction class”的基础单步任务相比，本场景隐藏类别不是简单少给一个 metadata 字段。已知类别时，模型可以先把搜索限制在某一反应家族，再只比较该家族内的断键、离去基和前体；类别未知时，有限的 Top-K 名额必须同时承担“判断可能属于哪个反应家族”和“在每个家族内选择具体断键与前体”两层不确定性。错误的类别先验会让正确断键在 beam search 开始前就被排除，因此本场景评价的是跨类别 disconnection search，而不是条件于真实类别的模板排序。Workflow 不需要因此增加人为阶段，但模型准入必须确认没有隐藏类别输入或 typed checkpoint shortcut。

科学问题不是“能否复现专利记录中的唯一字符串”，而是“模型能否在有限候选中恢复至少一个有文献记录的前体集合，并保持结构有效、候选不重复且具有断键多样性”。专利数据只记录实际执行过的一条反应，未命中该记录不证明替代前体不成立；因此 exact match 是可自动化的保守指标，不被描述为化学可行性的完整真值。

默认执行模型为 RetroChimera 1，ReactionT5v2-retrosynthesis 可作为结构不同的对照。两者的原始分数含义不同，只分别用于模型内排序，不做未经校准的数值平均。

## 数据可获取性与 Benchmark 构建方案

### 已验证的数据与模型来源

- **USPTO-50K / Schneider 50K**：GLN 官方仓库公开其使用的原始数据、固定 train/valid/test 划分和预处理脚本，并说明该划分继承自 RetroSim。正式构建须冻结 GLN commit、原始 CSV、预处理版本和文件 SHA256。
- **RetroChimera 1**：Microsoft 官方仓库以 MIT 许可证发布代码，并在 Figshare 发布 USPTO-50K checkpoint。仓库部署注册表已固定上游 article/file ID、归档字节数和 MD5，安装时还会计算 SHA256。
- **ReactionT5v2-retrosynthesis**：作者官方仓库和 Hugging Face 权重以 MIT 许可证发布，论文与代码明确给出 USPTO-50K 逆合成推理入口。

USPTO 专利反应的再发布许可、原始专利归属和派生数据条款必须在 Reference Repository 发布前由维护者完成书面审计。代码许可证不能替代数据许可证。

### 目标集合冻结规则

1. 只从标准 USPTO-50K `test` split 取样，不重新随机划分，也不从 train/valid 选择“容易样本”。
2. 用固定 RDKit 版本清理 atom map，拆分产品与记录前体，并执行 sanitize；解析失败、无产品、多主产品或产品含未定义原子者按预注册规则排除。
3. 以 canonical isomeric product SMILES 去重；同一产品对应多条测试反应时，全部合法前体集合保留为多参考答案，不随机丢弃。
4. 将 reaction class 从 Harness 输入中移除，仅由 evaluator 用于分层统计；正式任务采用 class-unknown 设置。
5. 对合格产品按 `SHA256(canonical_product + benchmark_seed)` 排序，冻结前 1,000 个为 `single_step_target_0001...1000`；若不足 1,000 个则构建失败。
6. 公共文件只保存匿名 ID 和产品；参考前体、反应类别、原始 reaction ID 和专利标识移入私有 evaluator。
7. 保存清洗前后数量、去重组、排除原因、数据 commit、RDKit 版本和所有文件 SHA256。

### 模型与数据泄漏审计

默认 checkpoint 必须明确以相同 USPTO-50K train split 训练且未使用 test 标签。若 checkpoint 的训练清单无法核验，允许作为 exploratory baseline，但不能进入正式榜单。不得使用以完整 USPTO、Pistachio 或在线专利语料训练且不能证明排除这些测试反应的模型与冻结 checkpoint 做无说明比较。

## Scenario 流程与基础流程的对比

```text
可审计的单步逆合成场景                     基础生成场景

冻结匿名产品 + Top-K 预算                   输入产品 SMILES
             │                                  │
             ▼                                  ▼
产品解析、去图号与规范化                      调用模型
             │                                  │
             ▼                                  ▼
运行冻结 checkpoint 的 beam search             返回字符串
             │
             ▼
前体集合解析、组分内无序规范化
             │
             ▼
保留 invalid / duplicate / raw score 诊断
             │
             ▼
输出完整 Top-K 后冻结结果
             │
             ▼
独立 evaluator 读取多参考答案并评分
```

## Science Query

给定 1,000 个匿名目标产物的 canonical isomeric SMILES、每个目标最多 10 个候选的固定预算和冻结模型环境，请在不知道反应类别、参考前体和专利上下文的条件下，为每个目标生成一步可形成该产物的候选前体集合，并保存足以复现解析、去重和排序的完整证据。

以下阶段是必要科学检查点，不限制 Agent 采用 RetroChimera 或其他通过泄漏审计的开源模型。

## 阶段介绍

### Stage 1. Input Validation and Product Canonicalization

**目标：** 建立唯一产品身份，避免 atom map、盐组分顺序或 RDKit 版本差异污染评测。

**输入：** `targets.csv`、`scenario_config.json`、`environment_manifest.json`。

**输出：** 标准化产品表、输入哈希和失败记录。

**技术：** 检查匿名 ID 唯一性，移除 map number 后用固定 RDKit 生成 canonical isomeric SMILES；不移除立体化学，不对无法 sanitize 的产品静默修复。

### Stage 2. Model and Checkpoint Admission

**目标：** 确认模型可离线运行，且训练数据边界、许可证和权重身份可审计。

**输入：** 模型 manifest、checkpoint、环境信息。

**输出：** admission report 和 checkpoint SHA256。

**技术：** 校验归档/权重哈希、代码 commit、模型类别、训练 split 声明和许可证；记录 CPU/GPU、依赖版本和随机性设置。必须确认推理入口运行于 class-unknown 模式，不能从文件列、模型参数、缓存或 typed checkpoint 注入真实类别。无法证明测试集隔离时必须退出正式评测。

### Stage 3. Budgeted Precursor Generation

**目标：** 在相同 Top-K 和计算预算内生成候选，不因查看参考答案而延长搜索。

**输入：** 规范化产品、checkpoint、beam 和时间预算。

**输出：** 每个 target 的原始 beam、raw score、模型状态和耗时。

**技术：** 固定 beam size、返回数、最大 token/编辑数、batch size 和 timeout。模型崩溃或超时按失败记录，不用第二个模型只补失败样本。

### Stage 4. Precursor-Set Normalization

**目标：** 把字符串输出转换为可公平比较的无序分子集合。

**输入：** 原始 beam。

**输出：** canonical precursor-set signature、parse 状态和重复来源。

**技术：** 以 `.` 拆分分子、分别 sanitize 和 canonicalize，再对组分排序。保留立体化学；不因试剂/反应物角色不确定而删除小分子。完全相同集合只计一次，但保留所有原始 beam rank。

### Stage 5. Structural and Diversity Diagnosis

**目标：** 区分模型未命中、无效生成和候选塌缩。

**输入：** 规范化 Top-K。

**输出：** invalid、empty、duplicate、unique 数量以及候选间相似度。

**技术：** 计算有效率、唯一率、前体 Morgan fingerprint 距离或反应中心多样性。诊断不能利用隐藏前体进行重排。

### Stage 6. Frozen Output and Private Evaluation

**目标：** 在 Ground Truth 首次可见前冻结全部预测。

**输入：** 所有 target 的完整候选和日志。

**输出：** `predictions.jsonl`、`intermediate_results.json` 和诊断表。

**技术：** evaluator 对无序前体集合做多参考 exact match，按 target 先计分再汇总；同时报告反应类别分层结果，但不把类别返回 Harness。

## Input Data 与 Ground Truth 组织

### Harness 可见输入

```text
input/
├── targets.csv
├── scenario_config.json
├── model_manifest.json
└── environment_manifest.json
```

`targets.csv` 只含 `target_id,product_smiles`。配置冻结 Top-K、超时、canonicalization 和随机种子。模型 manifest 记录 checkpoint 身份、训练数据声明和许可证，不含参考反应。

### Evaluator 私有数据

```text
private_evaluator/
├── reference_precursor_sets.jsonl
├── reaction_class_labels.csv
├── source_provenance.json
├── target_selection_audit.json
└── evaluation_config.json
```

私有目录不得挂载给 Harness。`reference_precursor_sets.jsonl` 可为一个产品保存多个合法记录集合；原始分子顺序不参与评分。

## `intermediate_results.json` 最低要求

```json
{
  "scenario_id": "single_step_retrosynthesis_class_unknown_v1",
  "dataset_snapshot": "<commit-or-release>",
  "random_seed": 2026,
  "input_hashes": {},
  "model": {"id": "<model>", "checkpoint_sha256": "<sha256>"},
  "canonicalization": {"rdkit_version": "<version>", "isomeric": true},
  "budget": {"top_k": 10, "timeout_seconds_per_target": 0},
  "targets": [],
  "aggregate_diagnostics": {},
  "warnings": []
}
```

每个 target 必须包含原始 beam、规范化集合、原始分数、rank、parse/duplicate 状态和耗时，数量不能保留占位值。

## 建议的 Reference Repository 结构

```text
reference_repository/
├── README.md
├── environment.yml
├── run.sh
├── input/
├── src/
│   ├── validate_inputs.py
│   ├── admit_checkpoint.py
│   ├── run_inference.py
│   ├── normalize_precursors.py
│   └── build_outputs.py
├── results/
│   ├── predictions.jsonl
│   ├── target_diagnostics.csv
│   └── intermediate_results.json
└── docs/
    ├── methods.md
    └── final_report.md
```

## 评估自动化实现难度

全流程可离线自动化：输入规范化 ✓ → checkpoint 审计 ✓ → 批量生成 ✓ → 前体集合规范化 ✓ → 诊断 ✓ → 私有 exact-match 评分 ✓。主要非计算阻塞项是 USPTO 派生数据的发布许可审计与 checkpoint 训练集隔离证明。

仓库已经提供 `retrosynthesis_planning.single_step_benchmark` 参考实现：公开阶段严格拒绝 `reaction_class`、参考前体和其他额外列；规范化阶段保留 invalid、empty、duplicate 和 unused budget；私有阶段按 target 计算多参考 Top-K、MRR 和候选集合诊断。该代码完成协议与离线 evaluator，不包含尚未获准再分发的 USPTO-50K 数据或大模型权重。

## 评测指标

### Scientific Task Accuracy

- Top-1、Top-3、Top-5、Top-10 precursor-set exact-match accuracy；
- 多参考答案 Top-K recall；
- Mean Reciprocal Rank；
- invalid、empty、duplicate 和 unique-candidate rate；
- 按反应类别和产品大小分层的 Top-K accuracy；
- 每 target 延迟、吞吐量、峰值内存/显存。

### Scientific Workflow Completeness

LLM Judge 检查产品规范化、模型准入、预算一致性、无序集合比较、全 beam 保存、失败诊断和 Ground Truth 冻结边界是否完整。

### Scientific Reproducibility

检查输入与 checkpoint 哈希、代码 commit、依赖版本、推理参数、随机种子、每 target 原始输出和单一重跑入口。

## 代码与数据的硬性约束

1. **Ground-Truth Isolation**：Harness 不得读取参考前体、反应类别、专利 ID 或 evaluator 配置。
2. **Class-Unknown**：不得把隐藏 reaction class 作为输入或用于重排。
3. **Fixed Test Split**：不得从训练/验证集补充目标，不得依据模型表现挑目标。
4. **Unordered Set Equality**：前体分子内部顺序不影响 exact match，但分子计数和立体化学必须保留。
5. **Fixed Budget**：所有模型使用相同 Top-K 与时间/硬件级别；超时不得追加预算。
6. **No Ground-Truth Filtering**：合法性过滤不得查看参考答案。
7. **Score Semantics**：raw score 只能称模型分数，不得称实验成功率。
8. **No Feasibility Overclaim**：未命中记录不等于化学错误，命中也不等于实验已验证。
9. **Full-Trajectory Requirement**：不得只提交最优一个候选。
10. **Deterministic Re-run**：相同环境和 seed 应产生相同候选与顺序；非确定算子必须披露。

## Domain-Specific Failure Cases

- 按 SMILES 字符串顺序比较多前体，误判等价集合；
- 去掉立体化学后获得虚高 exact match；
- 用隐藏反应类别运行 typed model；
- 用完整 USPTO 训练模型测 USPTO-50K 而不做重叠审计；
- 把无效 beam 删除后用更低 rank 补足，隐藏模型失败率；
- 将模型 log-probability 表述为反应成功概率；
- 只报告专利 exact match，并声称所有不匹配路线化学不可行；
- 查看 reference 后选择 beam size、模型或后处理规则。

## 参考文献与一手资源

- Dai H, et al. *Retrosynthesis Prediction with Conditional Graph Logic Network*. NeurIPS (2019). 官方实现与 Schneider 50K 固定划分：<https://github.com/Hanjun-Dai/GLN>
- Mikulak-Klucznik B, et al. RetroChimera 官方实现、权重与许可证：<https://github.com/microsoft/retrochimera>
- Sagawa T, Kojima R. *ReactionT5: a pre-trained transformer model for accurate chemical reaction prediction with limited data*. Journal of Cheminformatics (2025). 官方实现：<https://github.com/sagawatatsuya/ReactionT5v2>
