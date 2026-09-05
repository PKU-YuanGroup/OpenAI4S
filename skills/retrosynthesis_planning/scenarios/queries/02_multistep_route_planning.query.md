# Query: budgeted multistep route planning

Create a reusable Python CLI at
`skills/retrosynthesis_planning/scenarios/openai4s_codebases/02_multistep_route_planning.py`.
It accepts `--workspace PATH` and atomically writes
`PATH/results/intermediate_results.json` using only public inputs.

## Installed public inputs

- `PATH/installation.json`: require
  `scenario_id == "multistep_paroutes_budgeted_v1"`; it also declares the
  synthetic fixture profile and installed-file hashes.
- `PATH/public/inputs.json`: target rows with `target_id` and `target_smiles`.
- `PATH/public/stock.json`: JSON array of purchasable molecule strings. Every
  terminal molecule in a claimed solved route must be in this stock.
- `PATH/public/config.json`: contains `max_routes` and a `budget` object; the
  bundled case uses an `expansions` budget.
- `PATH/public/model_outputs.json`: frozen planner output. Each target row has
  `target_id`, `routes`, `termination_reason`, and `search_stats`. A route has
  `solved` and an AND/OR `tree` of molecule and reaction nodes.

Validate target coverage, normalize stock, enforce route and expansion budgets,
verify route-tree structure and stock closure, and create the standard
intermediate artifact with deterministic `trajectory_sha256`. For the CC0
synthetic fixture, molecule strings use identity canonicalization; do not claim
chemical accuracy from that fixture.

Do not access `PATH/private_evaluator/`, hidden reference routes,
`gt_codebase.py`, or `scenarios/gt_codebases/`. Do not import the GT
implementation. The program must have `--help` and fail nonzero on invalid,
over-budget, missing, or duplicate records.
