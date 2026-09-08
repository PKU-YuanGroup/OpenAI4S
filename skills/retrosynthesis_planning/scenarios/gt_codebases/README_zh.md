# Ground Truth 参考 codebase

这里的六个同名入口调用经过仓库审查的 GT runtime，仅作为 evaluator 参考实现，
不是 OpenAI4S 生成结果。对应的生成实现位于 `../openai4s_codebases/`，冻结 prompt
位于 `../queries/`。

## 文件

| 文件 | Scenario |
| --- | --- |
| `01_single_step_retrosynthesis.py` | 单步逆合成。 |
| `02_multistep_route_planning.py` | 有预算多步规划。 |
| `03_atom_mapping.py` | 原子映射。 |
| `04_forward_prediction.py` | 正向预测。 |
| `05_condition_recommendation.py` | 条件推荐。 |
| `06_yield_estimation.py` | 收率估计。 |
| `README.md` | 英文目录说明。 |
| `README_zh.md` | 中文目录说明。 |
