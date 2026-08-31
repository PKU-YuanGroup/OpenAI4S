# Scenario pipelines

[中文](README_zh.md)

These six entrypoints are the reviewed OpenAI4S-generated codebases corresponding
one-to-one with the six Science Queries in the parent directory. They read only
an installed workspace's `public/` boundary and write
`results/intermediate_results.json`. They never open `private_evaluator/`.

Run an entrypoint after installing its matching case, for example:

```bash
uv run python skills/retrosynthesis_planning/scenarios/pipelines/01_single_step_retrosynthesis.py \
  --workspace /tmp/openai4s-retro-case
```

`generation_manifest.json` is the authoritative query-to-code mapping. The
shared, pure-stdlib contract implementation is `../../gt_codebase.py`; model
inference remains behind the existing external backend adapters.

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
