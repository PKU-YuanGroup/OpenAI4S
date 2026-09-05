# Scenario 测试用例

[English](README.md)

本目录统一管理评测示例、数据来源、安装和私有评分，并与 `../pipelines/` 严格
分离。随仓库提供的 JSON 用例是 CC0 合成协议检查，只验证 schema、隔离、预算、
哈希和 evaluator 接线，不声称代表模型科学精度。

一条命令安装一个用例：

```bash
uv run python skills/retrosynthesis_planning/scenarios/test_cases/install.py \
  --case skills/retrosynthesis_planning/scenarios/test_cases/01_single_step_retrosynthesis.json \
  --workspace /tmp/openai4s-retro-case
```

安装器生成互相分离的 `public/`、`private_evaluator/`、`results/`，并在
`installation.json` 记录所有安装文件的 SHA256。之后运行匹配的公开 pipeline，
冻结输出，再由 evaluator 读取私有 Ground Truth：

```bash
uv run python skills/retrosynthesis_planning/scenarios/pipelines/01_single_step_retrosynthesis.py \
  --workspace /tmp/openai4s-retro-case
uv run python skills/retrosynthesis_planning/scenarios/test_cases/evaluate.py \
  --scenario single_step --workspace /tmp/openai4s-retro-case
```

`database_sources.json` 是正式数据库注册表。标为 `not_frozen` 的条目不得静默下载或
重新发布；维护者必须先冻结来源 revision、许可证结论、split 和 SHA256。在此之前，
一键安装只允许使用随仓库发布的协议 fixture。

## 文件

| 文件 | 用途 |
| --- | --- |
| `install.py` | 保持数据边界的一键测试数据安装器。 |
| `evaluate.py` | 私有 evaluator 入口。 |
| `database_sources.json` | 正式数据库要求和发布状态。 |
| `01_single_step_retrosynthesis.json` | Scenario 1 合成评测用例。 |
| `02_multistep_route_planning.json` | Scenario 2 合成评测用例。 |
| `03_atom_mapping.json` | Scenario 3 合成评测用例。 |
| `04_forward_prediction.json` | Scenario 4 合成评测用例。 |
| `05_condition_recommendation.json` | Scenario 5 合成评测用例。 |
| `06_yield_estimation.json` | Scenario 6 合成评测用例。 |
| `README.md` | 英文目录说明。 |
