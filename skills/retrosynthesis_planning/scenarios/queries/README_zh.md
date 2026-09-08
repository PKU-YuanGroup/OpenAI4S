# Scenario 查询文件

这里保存实际交给 OpenAI4S、用于生成 `../openai4s_codebases/` 独立实现的冻结
prompt。文件名与 GT 和生成代码入口严格对应。每份 prompt 都定义安装后的公开输入
文件、字段、输出要求，以及绝对禁止读取的私有路径。

## 文件

| 文件 | 用途 |
| --- | --- |
| `01_single_step_retrosynthesis.query.md` | 单步逆合成代码生成 prompt。 |
| `02_multistep_route_planning.query.md` | 有预算多步规划代码生成 prompt。 |
| `03_atom_mapping.query.md` | 原子映射代码生成 prompt。 |
| `04_forward_prediction.query.md` | 正向预测代码生成 prompt。 |
| `05_condition_recommendation.query.md` | 条件推荐代码生成 prompt。 |
| `06_yield_estimation.query.md` | 收率估计代码生成 prompt。 |
| `README.md` | 英文目录说明。 |
| `README_zh.md` | 中文目录说明。 |
