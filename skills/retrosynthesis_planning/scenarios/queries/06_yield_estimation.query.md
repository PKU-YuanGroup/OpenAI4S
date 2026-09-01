# Query: OOD reaction-yield estimation normalization

Create a reusable Python CLI at
`skills/retrosynthesis_planning/scenarios/openai4s_codebases/06_yield_estimation.py`.
It accepts `--workspace PATH`, reads public inputs only, and atomically writes
`PATH/results/intermediate_results.json`.

## Installed public inputs

- `PATH/installation.json`: require
  `scenario_id == "buchwald_hartwig_yield_ood_v1"`.
- `PATH/public/inputs.json`: rows with `reaction_id`, `split`, `reactants`,
  `reagents`, and `product`. Supported frozen splits are `random_test` and
  `mff_test1` through `mff_test4`; experimental yield is absent.
- `PATH/public/model_outputs.json`: one row per reaction containing
  `reaction_id`, raw `predicted_yield_percent`, nullable `interval_lower` and
  `interval_upper`, and `domain_status` (`matched`, `uncertain`, or
  `out_of_domain`). Preserve raw predictions even outside 0--100 rather than
  silently clipping them.
- `PATH/public/config.json`: contains `random_seed`.

Validate full unique coverage, split membership, numeric/nullable interval
fields and domain-status vocabulary. Use identity molecule canonicalization
only for this bundled CC0 protocol fixture. Build the standard intermediate
artifact, including the observed split list and deterministic
`trajectory_sha256`. Do not compute MAE without private labels.

Never read `PATH/private_evaluator/`, hidden yield values, `gt_codebase.py`, or
`scenarios/gt_codebases/`; do not import GT code. Provide `--help` and fail
nonzero on malformed input.
