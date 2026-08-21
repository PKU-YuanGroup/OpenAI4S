# 逆合成科学场景详细规范

[English](README.md)

本目录把逆合成相关能力拆成六个可独立运行、独立产生结果、独立评分的科学场景。它们不是一条强制流水线：狭义逆合成规划由场景 1 和场景 2 构成；场景 3–6 分别评价反应理解、正向验证和实验执行属性。

## 文件

| 文件 | 科学问题 |
| --- | --- |
| [`01_single_step_retrosynthesis.md`](01_single_step_retrosynthesis.md) | 固定 USPTO-50K 测试集上的目标产物到前体集合预测。 |
| [`02_multistep_route_planning.md`](02_multistep_route_planning.md) | 固定 PaRoutes 目标、库存和搜索预算下的多步路线搜索。 |
| [`03_atom_mapping.md`](03_atom_mapping.md) | 完整反应的原子对应和变化键识别。 |
| [`04_forward_prediction.md`](04_forward_prediction.md) | 固定专利测试集上的反应物/试剂到产物预测。 |
| [`05_condition_recommendation.md`](05_condition_recommendation.md) | 固定反应两侧下的 Top-K 类别型条件推荐。 |
| [`06_yield_estimation.md`](06_yield_estimation.md) | Buchwald–Hartwig 分布外划分上的收率回归与不确定性。 |
| [`README.md`](README.md) | 英文目录说明。 |

上级 [`../SCENARIO_zh.md`](../SCENARIO_zh.md) 保留为六问题总览和依赖图；本目录文件是 Benchmark 设计的详细草案。每个场景只有在数据快照、划分、许可证、文件校验和以及私有 evaluator 边界全部冻结后，才能从“设计完成”升级为“可发布 Benchmark”。模型输出、论文报告数字或另一个模型的预测不得充当 Ground Truth。
