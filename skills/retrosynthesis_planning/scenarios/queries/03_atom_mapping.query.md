# Query: reaction atom mapping and bond-change normalization

Create a reusable Python CLI at
`skills/retrosynthesis_planning/scenarios/openai4s_codebases/03_atom_mapping.py`.
It accepts `--workspace PATH`, reads only public files, and atomically writes
`PATH/results/intermediate_results.json`.

## Installed public inputs

- `PATH/installation.json`: require
  `scenario_id == "reaction_atom_mapping_curated_v1"` and inspect
  `dataset_profile`.
- `PATH/public/inputs.json`: array with `reaction_id` and unmapped
  `reaction_smiles`. Public reactions must not already contain atom-map labels.
- `PATH/public/model_outputs.json`: frozen pre-analyzed mapping rows for the
  synthetic protocol fixture. Each row contains exactly `reaction_id`,
  `correspondence`, `bond_changes`, `valid`, and `issues`.
- `PATH/public/config.json`: contains `random_seed`.

Validate complete one-to-one reaction coverage and the exact fixture schema;
arrays must remain arrays and identifiers must be unique. Build the standard
atom-mapping intermediate artifact and deterministic `trajectory_sha256`.
This fixture tests the protocol boundary and does not pretend to run RDKit or
RXNMapper. A production run replaces the frozen raw rows with outputs from the
declared mapper while preserving this public contract.

Never read `PATH/private_evaluator/`, equivalent hidden correspondences,
`gt_codebase.py`, or `scenarios/gt_codebases/`; do not import the GT runtime.
Provide `--help` and return nonzero for malformed input.
