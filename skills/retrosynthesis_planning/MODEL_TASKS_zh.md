# 有模型实现的逆合成任务图谱

[English](MODEL_TASKS.md)

本文记录逆合成 Skills 背后的模型审计。科学任务按输入、输出和可独立检验的
假设来划分。路线渲染、去重、来源记录和报告生成仍然重要，但不计作科学模型
任务。

## 准入条件

默认模型必须同时具备：

1. 公开的推理代码；
2. 可取得的预训练权重；
3. 明确的代码和权重许可证；
4. 可脚本化的本地推理入口；
5. 足以说明分数适用范围的任务与训练数据披露。

论文精度本身不是准入条件。只公开训练代码、暗中依赖商业服务，或无法标明
结果来自哪个 checkpoint 的模型，都不能成为默认实现。

## 入选任务和模型

| 独立任务 | 输入 | 输出 | 入选实现 | 工程结论 |
| --- | --- | --- | --- | --- |
| 原子映射与反应中心提取 | 完整的反应物/产物 reaction SMILES | 原子映射反应、映射置信度、变化键 | **RXNMapper** | 成熟的本地包，MIT，含预训练模型，可用 CPU/GPU。它不能处理只有目标产物的查询，因为反应两侧都必须已知。 |
| 单步前体生成 | 一个产物 SMILES | 一次断键的有序前体集合 | **RetroChimera 1** | 首选提案模型：MIT 代码与权重，公开 Pistachio/USPTO checkpoints，有直接 Python/Syntheseus API。只保留 5–10 个候选，概率不得解释为实验成功率。 |
| 多步路线搜索 | 目标 SMILES、扩展策略、库存 | 已解/未解路线树 | **AiZynthFinder** | 成熟的 MIT planner，可下载公共策略和库存；代码许可证本身不能覆盖单独下载的 artifact 条款。它是由学习到的扩展/过滤策略引导的搜索，不是一个端到端神经网络。 |
| 正向产物与 round-trip 验证 | 反应物和试剂 | 有序产物 SMILES | **ReactionT5v2-forward** | 2025 年同行评议模型，Hugging Face 上有 MIT 0.2B safetensors，可直接 Transformers 推理。RetroChimera 的公开 forward checkpoint 可作同数据源替代，但不算独立证据。 |
| 反应条件推荐 | 完整 reaction SMILES | 催化剂/试剂/溶剂类别，以及合适 checkpoint 下的温度 | **Parrot（有条件候选；非默认实现）** | MIT 代码，有官方 checkpoint 下载器和支持 CPU 的 CLI；但外部 checkpoint 压缩包没有单独声明的机器可读许可证，因此在任何 checkpoint 下载或推理前，必须审核所选 checkpoint 的条款，并在模型 manifest 中记录明确的允许/拒绝决定。输出是数据集标签类别；温度支持和词表取决于 checkpoint。 |
| 反应收率估计 | 反应物、试剂和产物 | 预测的分离收率百分比 | **ReactionT5v2-yield（当前 release 已隔离）** | 2025 MIT checkpoint 可本地加载，但固定 release 在复现上游预处理后仍未通过公开 canary（期望约 19.1666，实测 65.924858）。独立解决并验证前只允许协议测试。 |

这六项构成了实用的模型化规划与评审界面，但 Parrot 仍是有条件候选。只有完成
checkpoint 条款审核且模型 manifest 记录了允许决定后，才可下载或运行对应
checkpoint；记录缺失或决定为拒绝时必须停止。这些任务也不会使合成方案自动变成
“实验完整”。采购、EHS、放大、后处理、纯化、分析放行和文献/ELN 核验仍需数据库、
规则、实验和化学家。

## 为什么它们是独立科学问题

- 原子映射研究已给定反应中两侧原子的对应关系。
- 单步逆合成研究哪组前体可能生成一个目标。
- 多步规划研究在有界搜索目标下，反复断键能否到达指定库存。
- 正向预测研究给定反应输入会形成哪个产物。
- 条件推荐研究固定转化所需的实验上下文。
- 收率估计研究完整反应上下文下可能分离出多少目标产物。

因此这些任务可分别训练和评测，也不能把它们的输出压成一个“总置信度”。映射
置信度、beam likelihood、搜索分数、产物排名、条件 top-k 准确率和收率回归
误差的含义不同。

## 保留为备选、但不作为默认的模型

- **ReactionT5v2-retrosynthesis** 很容易部署，适合作为序列模型多样性检查。
  但 ORD 预训练 checkpoint 在 USPTO-50K 上的 zero-shot 结果远弱于任务微调
  结果，因此必须记录 checkpoint，且不作为首选单步模型。
- **RXN-Sandbox** 是很方便的 2026 容器包，支持 forward、单步和 tree 推理，
  权重基于 2025Q2 Pistachio。它很有潜力，但公开仓库太新，且采用
  OpenMDW-1.1 而非默认模型使用的简单宽松许可证；本地回归测试完成前只作候选。
- **Chemformer** 仍有科学价值，但原仓库已在 2026 年归档并由
  `aizynthmodels` 取代；新部署不应从旧 Python 3.7 栈开始。
- **Yield-BERT** 能复现其 HTE 结果，但官方环境从 Python 3.6 起步，且其文档
  自己指出专利收率噪声很大。ReactionT5v2 的运行栈更易维护，但固定 release
  的 canary 差异解决前不能成为默认选择。

## 已核验的第一方来源

- RetroChimera：<https://github.com/microsoft/retrochimera>
- AiZynthFinder：<https://github.com/MolecularAI/aizynthfinder>
- ReactionT5v2：<https://github.com/sagawatatsuya/ReactionT5v2>
- ReactionT5v2 forward/yield 模型卡：
  <https://huggingface.co/sagawa/ReactionT5v2-forward>、
  <https://huggingface.co/sagawa/ReactionT5v2-yield>
- RXNMapper：<https://github.com/rxn4chemistry/rxnmapper>
- Parrot：<https://github.com/wangxr0526/Parrot>
- RXN-Sandbox：<https://github.com/rxn4chemistry/rxn-sandbox>

来源最后核验日期为 2026-08-19。受监管或长期部署必须固定仓库 revision 和模型
文件哈希；一个可移动的模型名不能构成 provenance。
