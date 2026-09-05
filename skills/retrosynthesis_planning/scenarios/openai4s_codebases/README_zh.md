# OpenAI4S 生成 codebase

这里保存由 OpenAI4S 根据 `../queries/` 冻结 prompt 实际生成的六个独立实现。前两个
来自完整的 `openai4s run` Agent 轨迹；由于 Agent 网关处理长代码响应不稳定，其余四个
由 OpenAI4S LLM 客户端读取公开 Query 和 benchmark 源码生成。六个实现均经过明确记录
的生成后协议一致性修复。文件名与 `../gt_codebases/` 严格对应，但生成实现不得导入或
读取 GT runtime。

`generation_manifest.json` 冻结生成接口、未完整结束的 Agent 轨迹状态、修复披露、
prompt/源码哈希和验证产物。

## 文件

| 文件 | Scenario |
| --- | --- |
| `generation_manifest.json` | Query、GT、生成代码的 provenance 和哈希。 |
| `generate.py` | 根据冻结 query 运行 OpenAI4S 并冻结 provenance。 |
| `01_single_step_retrosynthesis.py` | 单步逆合成。 |
| `02_multistep_route_planning.py` | 有预算多步规划。 |
| `03_atom_mapping.py` | 原子映射。 |
| `04_forward_prediction.py` | 正向预测。 |
| `05_condition_recommendation.py` | 条件推荐。 |
| `06_yield_estimation.py` | 收率估计。 |
| `README.md` | 英文目录说明。 |
| `README_zh.md` | 中文目录说明。 |
