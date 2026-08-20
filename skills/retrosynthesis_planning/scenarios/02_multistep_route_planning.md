# Scenario 2：固定库存与搜索预算下的多步逆合成路线规划

## Scenario Overview

该 Scenario 面向“给定目标分子，能否在有限计算预算内找到连接到固定可购库存的完整多步路线”。它评价的是搜索系统，不只是单步模型：相同 expansion policy、filter policy、stock snapshot 和预算下，不同搜索策略如何组合局部断键、处理 AND-OR 依赖、避免循环并返回多样路线。

Benchmark 采用 PaRoutes 的 n1/n5 目标、参考路线和库存。PaRoutes 是为多步逆合成规划设计的公开框架，提供 10,000 个目标的两种难度设置、两个固定 stock、参考路线和路线相似性/聚类工具。正式主榜只在冻结的公开子集上开发，在隐藏目标子集上最终评分；参考路线和 route fingerprints 不提供给 Harness。

`solved` 是严格的图语义：一条反应的全部前体分支都必须递归到达库存，才能称完整路线。搜索到一个库存分子、生成一条看似合理的部分树或 forward model 恢复目标，都不能替代 solved 判定。

## 数据可获取性与 Benchmark 构建方案

### 已验证的数据来源

- **PaRoutes**：MolecularAI 官方仓库以 Apache-2.0 发布评测代码；Zenodo 记录 6275421 发布 n1/n5 targets、stocks、reference routes 和约 150k 训练路线，并提供文件校验信息。
- **AiZynthFinder**：MolecularAI 官方开源 planner；公开数据下载器提供可本地部署的 expansion/filter policy 和 stock assets。
- **RetroChimera / Syntheseus**：可作为替代 expansion policy，但比较搜索器时必须固定同一个 policy，不允许 planner A 和 B 各自使用最有利模型。

### Benchmark 冻结规则

1. 固定 PaRoutes release 和所有文件 SHA256；保留上游 MD5 作为交叉核验，不只依赖下载 URL。
2. n1 与 n5 分别评分，不合并成一个平均数掩盖难度差异。
3. 在每个集合内按 `SHA256(target_smiles + benchmark_seed)` 划分 public-development 与 private-evaluation，目标不跨 split。
4. stock 以冻结文本快照规范化后建立 membership set；评测期间禁止联网查询供应商或修改可购状态。
5. expansion/filter checkpoints、模型代码 commit、RDKit 版本和配置文件哈希全部固定。
6. 对所有 Harness 设置同一 GPU/CPU 等级、最大迭代、最大深度、模型调用数、墙钟时间和并发数。
7. private evaluator 保存 reference route trees、route clusters 和相似性缓存；Harness 只见 target 与 stock。
8. 若修改 PaRoutes 原始划分或分子规范化导致 route 无法重建，构建必须失败并记录，不能静默删难例。

## Scenario 流程与基础流程的对比

```text
预算受控的多步规划场景                         基础路线生成

冻结目标 + stock + policy + budget             输入目标
                 │                                  │
                 ▼                                  ▼
       规范化与库存身份冻结                      调用 planner
                 │                                  │
                 ▼                                  ▼
      AND-OR 搜索与逐次 expansion                 返回若干路线
                 │
                 ├── 每次调用/扩展/状态写审计
                 ▼
      solved、partial、timeout 明确区分
                 │
                 ▼
      冻结所有候选树与资源使用
                 │
                 ▼
私有 evaluator 计算恢复率、相似性、多样性与成本
```

## Science Query

给定冻结的 PaRoutes n1/n5 匿名目标、固定库存、固定单步 expansion/filter policy 和统一计算预算，请设计并执行多步逆合成搜索，对每个目标返回完整 solved 路线、未解决部分树或明确失败，并在不访问参考路线的情况下优化求解率、参考路线覆盖、多样性与计算成本。

## 阶段介绍

### Stage 1. Target, Stock, and Configuration Validation

**目标：** 确认所有系统使用同一分子身份、库存和预算。

**输入：** `targets.csv`、`stock.smi`、policy manifests、scenario config。

**输出：** canonical target/stock、配置哈希和冲突报告。

**技术：** 用固定 RDKit 规范化、去 atom map 并保留立体化学。重复 stock 合并但记录来源；target 若已在 stock，按预注册规则单独统计 trivial solved，不混入路线搜索指标。

### Stage 2. Policy and Planner Admission

**目标：** 验证 expansion/filter policy 与 planner 可复现且无参考路线泄漏。

**输入：** checkpoints、代码 commit、配置。

**输出：** admission report。

**技术：** 校验 checkpoint 哈希、训练集声明、许可证、模型输入输出契约；阻止读取 private route 文件、预计算 target-specific cache 或在线检索。

### Stage 3. Budgeted AND-OR Search

**目标：** 在固定预算内递归组合单步提案。

**输入：** target、stock、policy、搜索算法与预算。

**输出：** 搜索事件流、开放/关闭状态、候选树和资源统计。

**技术：** 分子节点是 OR，反应的全部前体是 AND。每次 expansion 记录 parent、候选、policy score、filter 结果、累计调用数和时间。检测循环与重复状态，不把 transposition 当新发现重复计数。

### Stage 4. Route Completion and Failure Classification

**目标：** 用确定性规则区分完整路线、部分路线和系统失败。

**输入：** 搜索树与 stock membership。

**输出：** 每条 route 的 `solved`、未解决叶节点和终止原因。

**技术：** 自叶向根验证所有 AND 分支。终止原因至少包括 solved、budget_exhausted、timeout、no_expansion、invalid_output 和 backend_error。

### Stage 5. Route Normalization and Diversity Preservation

**目标：** 去除完全重复路线，同时保留真正不同的合成策略。

**输入：** 原始 route trees。

**输出：** 规范化路线、重复来源和固定 Top-N 候选。

**技术：** 以规范化反应/分子树签名去重；使用 PaRoutes route fingerprint/cluster 计算路线差异。多样性选择不能改变 `solved` 状态或补入超预算路线。

### Stage 6. Frozen Output and Private Route Evaluation

**目标：** 在参考路线可见前冻结搜索轨迹和候选路线。

**输入：** 全部搜索结果。

**输出：** `routes.jsonl`、`search_events.jsonl`、`intermediate_results.json`。

**技术：** evaluator 计算 solved rate、Top-N route recovery、与参考路线最近距离、cluster coverage 和成本。参考未恢复不等于生成路线错误，因此 route quality 与 exact recovery 分开报告。

## Input Data 与 Ground Truth 组织

### Harness 可见输入

```text
input/
├── targets.csv
├── stock.smi
├── expansion_policy_manifest.json
├── filter_policy_manifest.json
├── planner_config.json
└── environment_manifest.json
```

### Evaluator 私有数据

```text
private_evaluator/
├── reference_routes.json
├── route_clusters.json
├── target_source_mapping.json
├── split_audit.json
└── evaluation_config.json
```

参考路线、cluster 和目标来源不得进入 Harness。公开 stock 可以读取，但不得替换或联网补充。

## `intermediate_results.json` 最低要求

```json
{
  "scenario_id": "multistep_paroutes_budgeted_v1",
  "paroutes_release": "<release>",
  "input_hashes": {},
  "planner": {"id": "<planner>", "commit": "<commit>"},
  "policies": {"expansion": {}, "filter": {}},
  "budget": {},
  "targets": [],
  "aggregate": {"solved": 0, "partial": 0, "failed": 0},
  "warnings": []
}
```

每个 target 至少记录 route 数、solved 数、最佳未解决叶数量、扩展数、模型调用数、耗时、终止原因和原始输出路径。

## 建议的 Reference Repository 结构

```text
reference_repository/
├── README.md
├── environment.yml
├── run.sh
├── input/
├── src/
│   ├── validate_benchmark.py
│   ├── run_planner.py
│   ├── verify_andor.py
│   ├── normalize_routes.py
│   └── freeze_results.py
├── results/
│   ├── routes.jsonl
│   ├── search_events.jsonl
│   └── intermediate_results.json
└── analysis/
    ├── resource_usage.csv
    └── diagnostics.py
```

## 评估自动化实现难度

全流程可离线自动化：输入/库存冻结 ✓ → policy 准入 ✓ → AND-OR 搜索 ✓ → 完整性验证 ✓ → 路线规范化 ✓ → PaRoutes 私有评分 ✓。最大工程成本是大规模模型推理和公平的资源计量，不是指标实现。

## 评测指标

### Scientific Task Accuracy

- solved target rate（n1、n5 分开）；
- Top-1/5/10 reference-route recovery；
- 最近 reference route 的 reaction/intermediate/tree similarity；
- route cluster coverage 与每 target 的独立 solved route 数；
- 平均步骤数、未解决叶节点数和 trivial-solved rate；
- 每 solved target 的 expansion、模型调用、秒数、内存/显存；
- timeout、backend error 和 deterministic replay rate。

### Scientific Workflow Completeness

检查 stock 冻结、policy 准入、AND-OR 语义、预算审计、失败分类、全路线保存和私有参考隔离。

### Scientific Reproducibility

检查全部输入/模型/config 哈希、planner commit、环境版本、event log、随机 seed 和单一重跑入口。

## 代码与数据的硬性约束

1. **Reference-Route Isolation**：搜索和排序不得读取参考路线或 cluster。
2. **Fixed Policy**：比较搜索器时 expansion、filter checkpoint 完全相同。
3. **Fixed Stock**：不得在线查询供应商或依据目标修改 stock。
4. **Strict AND Semantics**：一个前体命中库存不能让整步反应 solved。
5. **Fixed Budget**：迭代、深度、调用、时间和硬件约束统一。
6. **Partial-Route Honesty**：部分树必须列出未解决叶并标 `solved=false`。
7. **No Hidden Cache**：不得携带目标专属路线缓存或专利检索结果。
8. **Route Identity**：完全重复树不重复计 diversity；不同 SMILES 序列需先规范化。
9. **Raw Trajectory Preservation**：最终 Top-N 之外仍保留搜索审计摘要和所有候选索引。
10. **No Experimental Overclaim**：solved 只表示连接到库存，不表示条件、安全、收率或实验成功。

## Domain-Specific Failure Cases

- 只要任意叶节点在 stock 就把路线标 solved；
- 给不同 planner 使用不同单步模型或库存，再比较 solved rate；
- timeout 后继续运行直到找到路线；
- 用参考路线引导扩展、终止或排序；
- 将同一棵树的 SMILES 重排当作多样路线；
- 删除失败目标后只在成功子集报告平均成本；
- 把目标本身在 stock 的 trivial case 混入规划能力；
- 将 PaRoutes 未记录的替代路线自动判错，或将任意 solved 路线自动判为实验可行。

## 参考文献与一手资源

- Genheden S, et al. *PaRoutes: towards a framework for benchmarking retrosynthesis route predictions*. Digital Discovery (2022). 官方实现：<https://github.com/MolecularAI/paroutes>；官方数据：<https://zenodo.org/records/6275421>
- Genheden S, et al. AiZynthFinder 官方实现与文档：<https://github.com/MolecularAI/aizynthfinder>
- RetroChimera 官方实现：<https://github.com/microsoft/retrochimera>
