# Scenario 3：完整反应的原子映射与反应中心识别

## Scenario Overview

该 Scenario 面向反应物和产物均已知、但原子对应关系未知的反应理解任务。Harness 需要为每个完整 reaction SMILES 分配守恒且一致的 atom-map number，并从映射结果确定形成键、断裂键和键级变化。

这是一个独立科学问题，不接受只有目标产物的输入，也不预测前体或反应条件。映射的核心真值是“哪些反应物原子对应哪些产物原子”，不是某个软件产生的 mapped SMILES 字符串；不同 map number 命名可能表达同一个对应关系，必须在原子置换等价类下比较。

默认模型 RXNMapper 是工程上成熟的本地开源实现，但它的输出不能反过来充当自己的 Ground Truth。正式 Benchmark 只允许使用具有人工校正、明确来源和可审计映射的反应集。若官方 RXNMapper 数据包中的具体文件、许可与人工核验状态尚未冻结，本场景保持“设计完成、数据发布阻塞”，不得用 RXNMapper 批量映射 USPTO 后自评。

## 数据可获取性与 Benchmark 构建方案

### 已验证的模型与数据入口

- **RXNMapper**：IBM Research/rxn4chemistry 官方仓库以 MIT 许可证发布代码，支持本地 `RXNMapper` 与 `BatchedMapper`，输出 mapped reaction 和 confidence；论文公开了 attention-guided atom mapping 方法。
- **官方评测数据入口**：RXNMapper 仓库提供官方数据包下载链接，论文报告在人工/规则核验的 benchmark 上评价。维护者必须从该数据包冻结明确文件，而不能只引用下载首页。
- **补充挑战集**：可加入论文中明确来源的 Golden、Jaworski 等人工审查集合，但每个集合必须单独确认再发布条款、映射真值来源、重复项和是否被 checkpoint 训练使用。

### 正式发布前的数据准入规则

1. 每条 reaction 必须同时具有未映射原始反应和独立于被测模型得到的参考 atom correspondence。
2. 排除只由 RXNMapper、LocalMapper 或其他被测 mapper 自动生成且未经独立核验的标签。
3. 保存 source dataset、原始 reaction ID、许可文本、下载日期、文件 SHA256 和参考映射产生方式。
4. 对人工争议、多种最大公共子结构映射均合理或反应记录不守恒的样本设置 `ambiguous`，不进入唯一映射 accuracy；可进入错误检测子任务。
5. 去除完全重复反应，并按无 atom-map canonical reaction signature 分组，防止同一反应跨 public/private split。
6. 以 reaction group 做确定性划分；若 checkpoint 训练集可得，执行 exact 与近重复去泄漏审计。
7. 至少冻结 1,000 条唯一、可评分、覆盖形成/断裂/键级变化的反应；不足则构建失败，不以模型预测补标签。
8. public input 只含匿名 ID 和未映射 reaction；参考 correspondence、变化键、来源和 ambiguity decision 位于 private evaluator。

### 标签表示

参考标签使用与 map number 名称无关的原子对应表：反应物原子由 `(component_canonical_id, atom_index)` 标识，产物同理。对称原子允许保存多个等价 correspondence 或 symmetry orbit。变化键由参考 correspondence 推导，不从文本反应类别猜测。

## Scenario 流程与基础流程的对比

```text
可审计原子映射场景                         基础 mapping 调用

未映射完整反应 + 冻结参考（私有）            输入 reaction SMILES
             │                                  │
             ▼                                  ▼
反应两侧解析、角色与原子索引冻结               调用 mapper
             │                                  │
             ▼                                  ▼
运行 mapper，保留 confidence 和失败             返回 mapped SMILES
             │
             ▼
检查 map 唯一性、元素/电荷/同位素守恒
             │
             ▼
由 map number 确定变化键
             │
             ▼
在对称等价下与私有 correspondence 比较
```

## Science Query

给定一组匿名、反应物与产物均已知但没有 atom map 的完整反应，请在不访问参考映射和变化键标签的情况下，恢复原子对应关系，识别形成、断裂和键级变化，并对不守恒、低置信度或存在多解的反应给出显式诊断。

## 阶段介绍

### Stage 1. Reaction Parsing and Role Freezing

**目标：** 固定进入映射的两侧结构，阻止通过移动或删除组分提高表面守恒率。

**输入：** `reactions.jsonl`、角色规则和环境 manifest。

**输出：** 组件、原子索引、规范化反应和解析错误。

**技术：** 解析 `reactants>reagents>products` 或显式字段；反应物/产物角色由输入冻结。去除已有 map number 但不改变键、立体化学、同位素、电荷或组分归属。

### Stage 2. Mapper Admission and Batch Isolation

**目标：** 确认模型身份与批处理失败边界。

**输入：** mapper code、checkpoint、配置。

**输出：** checkpoint 哈希、软件版本和 admission report。

**技术：** RXNMapper 记录模型版本、attention multiplier、canonicalization 和 CPU/GPU。批量执行时单条非法反应不得使剩余批次丢失；retry 规则预先固定。

### Stage 3. Atom Correspondence Inference

**目标：** 为反应两侧原子分配一一对应的 map identity。

**输入：** 冻结反应。

**输出：** mapped reaction、raw confidence、运行状态。

**技术：** 只映射输入中已有的反应两侧，不增删分子。所有输出同时保留原始未映射 reaction，确保可追溯。

### Stage 4. Mapping Integrity Audit

**目标：** 在看不到 Ground Truth 时发现明显无效映射。

**输入：** mapped reaction。

**输出：** duplicate/missing map、元素、电荷、同位素和立体化学警告。

**技术：** 检查每侧 map number 唯一性、成对出现情况和元素一致性。质量警告保留，不能静默改写输出获得更高分。

### Stage 5. Bond-Change Extraction

**目标：** 从 atom correspondence 确定反应中心。

**输入：** mapped reaction。

**输出：** formed、broken、order_changed bond sets。

**技术：** 以 map number 构造两侧键字典；仅一侧存在分别为 formed/broken，两侧均存在但键级不同为 order change。芳香键表示和 kekulization 策略固定。

### Stage 6. Symmetry-Aware Private Evaluation

**目标：** 在化学等价映射下公平评分并分析置信度。

**输入：** 冻结预测、私有 correspondence 和 symmetry orbits。

**输出：** atom accuracy、reaction exactness、bond-change F1 和校准指标。

**技术：** evaluator 在允许的对称置换中选择等价参考，不按 map number 数字直接比较。ambiguous 样本单独统计，不进入唯一答案主指标。

## Input Data 与 Ground Truth 组织

### Harness 可见输入

```text
input/
├── reactions.jsonl
├── role_policy.json
├── mapper_manifest.json
├── scenario_config.json
└── environment_manifest.json
```

### Evaluator 私有数据

```text
private_evaluator/
├── atom_correspondence.jsonl
├── bond_changes.jsonl
├── symmetry_orbits.jsonl
├── ambiguity_audit.csv
├── source_provenance.json
└── evaluation_config.json
```

私有映射不得以 mapped SMILES、缓存或日志形式泄漏。Evaluator 必须能证明 reference 与被测 mapper 独立。

## `intermediate_results.json` 最低要求

```json
{
  "scenario_id": "reaction_atom_mapping_curated_v1",
  "dataset_snapshot": "<snapshot>",
  "input_hashes": {},
  "mapper": {"id": "rxnmapper", "version": "<version>", "checkpoint_sha256": "<sha256>"},
  "role_policy": {},
  "reactions": [],
  "aggregate_diagnostics": {},
  "warnings": []
}
```

每条 reaction 保存原始输入、mapped 输出、confidence、完整性检查、变化键、耗时和失败原因。

## 建议的 Reference Repository 结构

```text
reference_repository/
├── README.md
├── environment.yml
├── run.sh
├── input/
├── src/
│   ├── validate_reactions.py
│   ├── run_mapper.py
│   ├── audit_mapping.py
│   ├── extract_bond_changes.py
│   └── build_outputs.py
├── results/
│   ├── mapped_reactions.jsonl
│   ├── bond_changes.jsonl
│   └── intermediate_results.json
└── analysis/
    └── diagnostics.py
```

## 评估自动化实现难度

模型推理与变化键提取可完全自动化。真正困难的是建立不由被测 mapper 自举、又处理对称多解的可信 Ground Truth。完成数据来源/许可证/人工核验审计前，本场景不能进入正式榜单。

## 评测指标

### Scientific Task Accuracy

- symmetry-aware atom correspondence accuracy；
- whole-reaction exact mapping rate；
- formed/broken/order-changed bond precision、recall、F1；
- element/isotope/charge conservation violation rate；
- missing/duplicate map rate 和 mapper failure rate；
- confidence 的 error detection AUROC、AUPRC 与 calibration error；
- batch throughput、延迟和峰值内存。

### Scientific Workflow Completeness

检查角色冻结、模型身份、原始反应保存、完整性审计、变化键确定性推导、对称性处理和 ambiguous 分流。

### Scientific Reproducibility

检查数据与 checkpoint 哈希、atom indexing 和 aromaticity 规则、依赖版本、完整逐反应输出和重跑入口。

## 代码与数据的硬性约束

1. **Independent Ground Truth**：不得用被测 mapper 输出创建或修正正式标签。
2. **Complete-Reaction Input**：只有产品时不得运行本场景并伪造反应物侧。
3. **Role Freeze**：不得移动 reagent、删除旁观组分或补分子来提高守恒。
4. **Map-Number Invariance**：评分比较 correspondence，不比较任意 map 数字名称。
5. **Symmetry Awareness**：化学等价原子置换不得错误扣分。
6. **Deterministic Bond Changes**：变化键只能由映射前后键表推导。
7. **Confidence Semantics**：mapping confidence 不是反应成功概率。
8. **Ambiguity Honesty**：无唯一参考的反应不能强塞入 exact accuracy。
9. **Raw Input Preservation**：必须同时保存原始和 mapped reaction。
10. **No Silent Repair**：完整性失败只能标记或按预注册规则剔除，不能事后人工改 map。

## Domain-Specific Failure Cases

- 用 RXNMapper 自己生成的 USPTO map 当 RXNMapper Ground Truth；
- 按 map number 数字逐字比较而忽略重编号等价；
- 未处理芳香对称原子导致虚假错误；
- 把 reagent 移到 reactant side 来提高 atom conservation；
- 从反应类别或 LLM 叙述猜变化键，而不从映射计算；
- 只报告平均 atom accuracy，隐藏整条反应严重错误；
- 将低 confidence 解释成低实验收率；
- 删除不守恒难例且不报告覆盖率。

## 参考文献与一手资源

- Schwaller P, et al. *Unsupervised attention-guided atom-mapping*. Science Advances 7, eabe4166 (2021). 官方实现与数据入口：<https://github.com/rxn4chemistry/rxnmapper>
- RXNMapper 官方数据包入口：<https://ibm.box.com/v/RXNMapperData>
