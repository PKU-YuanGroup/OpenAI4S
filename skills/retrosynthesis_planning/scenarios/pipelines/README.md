# Legacy Scenario pipeline aliases

[中文](README_zh.md)

These six entrypoints are deprecated compatibility aliases for the reviewed GT
runtime. They are not OpenAI4S-generated code. New comparisons must use the
matched `../gt_codebases/`, `../openai4s_codebases/`, and `../queries/`
directories.

Run an entrypoint after installing its matching case, for example:

```bash
uv run python skills/retrosynthesis_planning/scenarios/pipelines/01_single_step_retrosynthesis.py \
  --workspace /tmp/openai4s-retro-case
```

`generation_manifest.json` records only the legacy alias mapping. The
authoritative generation provenance is
`../openai4s_codebases/generation_manifest.json`.

## Files

| File | Purpose |
| --- | --- |
| `01_single_step_retrosynthesis.py` | Scenario 1 public pipeline. |
| `02_multistep_route_planning.py` | Scenario 2 public pipeline. |
| `03_atom_mapping.py` | Scenario 3 public pipeline. |
| `04_forward_prediction.py` | Scenario 4 public pipeline. |
| `05_condition_recommendation.py` | Scenario 5 public pipeline. |
| `06_yield_estimation.py` | Scenario 6 public pipeline. |
| `generation_manifest.json` | One-to-one Science Query, Scenario ID, and entrypoint mapping. |
| `README_zh.md` | Chinese directory guide. |
