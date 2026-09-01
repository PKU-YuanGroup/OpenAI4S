# Query: single-step retrosynthesis, reaction class unknown

Create a reusable Python CLI at
`skills/retrosynthesis_planning/scenarios/openai4s_codebases/01_single_step_retrosynthesis.py`.
It must accept `--workspace PATH`, use only the Python standard library plus the
public benchmark modules under `retrosynthesis_planning`, and write
`PATH/results/intermediate_results.json` atomically.

## Installed public inputs

- `PATH/installation.json`: object containing `scenario`, `scenario_id`,
  `dataset_profile`, dataset metadata, and hashes of installed files. Require
  `scenario_id == "single_step_retrosynthesis_class_unknown_v1"`.
- `PATH/public/inputs.json`: array of targets. Each row has `target_id` and
  `product_smiles`. Reaction class, reference precursors, and patent context are
  deliberately absent.
- `PATH/public/model_outputs.json`: frozen raw model output for this protocol
  run. Each row has `target_id`, `predictions`, and `error`; each prediction has
  `rank`, `reactants_smiles`, and `score`. Treat scores as model ranking scores,
  not experimental probabilities.
- `PATH/public/config.json`: object with integer `top_k` and `random_seed`.
- `PATH/public/model_manifest.json`: public model/checkpoint provenance.

The bundled case uses identity string canonicalization because it is a CC0
synthetic protocol smoke fixture. Production data must use the benchmark's
chemical canonicalizer. Validate one output row per target, enforce the Top-K
budget, normalize duplicate/invalid candidates, and produce the benchmark
intermediate artifact including its deterministic `trajectory_sha256`.

Do not read `PATH/private_evaluator/`, any references/answers, `gt_codebase.py`,
or `scenarios/gt_codebases/`. Do not import the GT implementation. Do not write
an evaluation score. The independent evaluator runs only after output freezes.
Add a `--help` description and fail nonzero on malformed or mismatched input.
