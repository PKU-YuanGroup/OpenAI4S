# Scenario test cases

[中文](README_zh.md)

This directory owns evaluation examples, dataset provenance, installation, and
private scoring. It is deliberately separate from `../pipelines/`. The bundled
JSON cases are CC0 synthetic protocol checks; they verify schemas, isolation,
budgets, hashes, and evaluator wiring but make no model-accuracy claim.

Install one case in a single command:

```bash
uv run python skills/retrosynthesis_planning/scenarios/test_cases/install.py \
  --case skills/retrosynthesis_planning/scenarios/test_cases/01_single_step_retrosynthesis.json \
  --workspace /tmp/openai4s-retro-case
```

The installer creates disjoint `public/`, `private_evaluator/`, and `results/`
directories plus an `installation.json` containing every installed file hash.
Run the matching public pipeline, freeze its output, and only then evaluate:

```bash
uv run python skills/retrosynthesis_planning/scenarios/pipelines/01_single_step_retrosynthesis.py \
  --workspace /tmp/openai4s-retro-case
uv run python skills/retrosynthesis_planning/scenarios/test_cases/evaluate.py \
  --scenario single_step --workspace /tmp/openai4s-retro-case
```

`database_sources.json` is the production-data registry. Entries marked
`not_frozen` must not be silently downloaded or republished. A maintainer must
first freeze the source revision, license decision, split, and SHA256; until
then, only the bundled protocol fixture is installable.

## Files

| File | Purpose |
| --- | --- |
| `install.py` | Boundary-preserving one-command test-data installer. |
| `evaluate.py` | Private-side evaluator entrypoint. |
| `database_sources.json` | Production dataset requirements and release state. |
| `01_single_step_retrosynthesis.json` | Scenario 1 synthetic evaluation case. |
| `02_multistep_route_planning.json` | Scenario 2 synthetic evaluation case. |
| `03_atom_mapping.json` | Scenario 3 synthetic evaluation case. |
| `04_forward_prediction.json` | Scenario 4 synthetic evaluation case. |
| `05_condition_recommendation.json` | Scenario 5 synthetic evaluation case. |
| `06_yield_estimation.json` | Scenario 6 synthetic evaluation case. |
| `README_zh.md` | Chinese directory guide. |
