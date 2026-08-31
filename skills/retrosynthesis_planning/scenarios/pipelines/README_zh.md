# Scenario pipelines

[English](README.md)

这里的六个入口是经过审阅的 OpenAI4S 生成 codebase，与上级目录中的六个
Science Query 一一对应。入口只读取已安装 workspace 的 `public/` 边界，并写出
`results/intermediate_results.json`，绝不读取 `private_evaluator/`。

安装对应测试用例后可直接运行，例如：

```bash
uv run python skills/retrosynthesis_planning/scenarios/pipelines/01_single_step_retrosynthesis.py \
  --workspace /tmp/openai4s-retro-case
```

`generation_manifest.json` 是 query 到 code 的权威映射。共享的纯标准库协议实现位于
`../../gt_codebase.py`；模型推理继续通过已有的外部 backend adapter 隔离执行。

## 文件

| 文件 | 用途 |
| --- | --- |
| `01_single_step_retrosynthesis.py` | Scenario 1 公开 pipeline。 |
| `02_multistep_route_planning.py` | Scenario 2 公开 pipeline。 |
| `03_atom_mapping.py` | Scenario 3 公开 pipeline。 |
| `04_forward_prediction.py` | Scenario 4 公开 pipeline。 |
| `05_condition_recommendation.py` | Scenario 5 公开 pipeline。 |
| `06_yield_estimation.py` | Scenario 6 公开 pipeline。 |
| `generation_manifest.json` | Science Query、Scenario ID 与入口的一一映射。 |
| `README.md` | 英文目录说明。 |
