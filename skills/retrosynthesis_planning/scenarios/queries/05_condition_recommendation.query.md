# Query: categorical reaction-condition recommendation

Create a reusable Python CLI at
`skills/retrosynthesis_planning/scenarios/openai4s_codebases/05_condition_recommendation.py`.
It accepts `--workspace PATH`, reads only the installed public boundary, and
atomically writes `PATH/results/intermediate_results.json`.

## Installed public inputs

- `PATH/installation.json`: require
  `scenario_id == "reaction_condition_uspto_categorical_v1"`.
- `PATH/public/inputs.json`: rows with `reaction_id`, `reactants`, and
  `product`; condition labels are hidden.
- `PATH/public/vocabulary.json`: allowed categorical values for exactly
  `catalyst1`, `solvent1`, `solvent2`, `reagent1`, and `reagent2`.
- `PATH/public/model_outputs.json`: each row has `reaction_id`, ranked
  `predictions`, and `error`; each prediction has `rank`, `score`, and one
  complete five-slot `conditions` object.
- `PATH/public/config.json`: integer `top_k` and `random_seed`.

Validate one output per reaction, complete tuples rather than a Cartesian mix
of marginal labels, vocabulary membership, duplicate tuples, and Top-K. Use
identity molecule canonicalization only for the bundled CC0 protocol fixture.
Build the standard intermediate artifact with deterministic
`trajectory_sha256`.

Never read `PATH/private_evaluator/`, hidden condition sets, `gt_codebase.py`,
or `scenarios/gt_codebases/`; do not import GT code. Provide `--help` and fail
nonzero on malformed input.
