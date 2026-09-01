# 旧版 Scenario pipeline 兼容入口

[English](README.md)

这里的六个入口只是经过审查的 GT runtime 的废弃兼容别名，不是 OpenAI4S 生成
代码。新的对照必须使用同名的 `../gt_codebases/`、`../openai4s_codebases/` 和
`../queries/` 三组目录。

安装对应测试用例后可直接运行，例如：

```bash
uv run python skills/retrosynthesis_planning/scenarios/pipelines/01_single_step_retrosynthesis.py \
  --workspace /tmp/openai4s-retro-case
```

`generation_manifest.json` 只记录旧兼容入口映射；权威生成 provenance 位于
`../openai4s_codebases/generation_manifest.json`。

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
