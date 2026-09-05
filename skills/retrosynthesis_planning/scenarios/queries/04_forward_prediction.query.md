# Query: forward reaction-product prediction

Create a reusable Python CLI at
`skills/retrosynthesis_planning/scenarios/openai4s_codebases/04_forward_prediction.py`.
It accepts `--workspace PATH` and atomically produces
`PATH/results/intermediate_results.json` from public inputs only.

## Installed public inputs

- `PATH/installation.json`: require
  `scenario_id == "forward_prediction_uspto_mit_separated_v1"`.
- `PATH/public/inputs.json`: rows with `reaction_id`, `reactants`, and
  `reagents`; the product is intentionally hidden.
- `PATH/public/model_outputs.json`: frozen raw predictions. Every row has
  `reaction_id`, ranked `predictions`, and `error`; each prediction contains
  `rank`, `product_smiles`, and `score`.
- `PATH/public/config.json`: contains integer `top_k` and `random_seed`.

Validate complete reaction coverage, normalize predictions, retain both
isomeric and connectivity diagnostics, enforce Top-K, and build the benchmark
intermediate artifact with deterministic `trajectory_sha256`. The bundled CC0
fixture uses identity isomeric canonicalization and removes `@` for its explicit
connectivity-only comparison; this is a protocol test, not chemical inference.

Do not access `PATH/private_evaluator/`, product references, `gt_codebase.py`,
or `scenarios/gt_codebases/`. Do not import GT code. Include `--help` and fail
nonzero on invalid input.
