# OpenAI4S-generated codebases

This directory contains six independent implementations generated through
OpenAI4S from the frozen prompts in `../queries/`. The first two came from full
`openai4s run` Agent trajectories; the remaining four use OpenAI4S's LLM client
with the public Query and benchmark source as context after the Agent gateway
proved unreliable for long code responses. All six received an explicit
post-generation protocol-conformance repair. Filenames match the GT entry
points in `../gt_codebases/`, but generated implementations never import or
read the GT runtime.

Generation interface, incomplete Agent-run state, repair disclosure, prompt and
source hashes, and verification artifacts are frozen in
`generation_manifest.json`.

`generate.py --scenario NAME --overwrite` regenerates only that Scenario and
preserves the other provenance entries. Without `--overwrite`, an existing
source and its provenance remain unchanged. Verification stages only the public
fixture and reviewed benchmark modules, uses a clean environment and an OS
sandbox, and denies access to the repository, GT outputs, and network. It
requires usable macOS Seatbelt or Linux bubblewrap; unavailable isolation fails
verification. Child output and machine-local error details are not persisted
in the manifest.

Only `dataset_profile=synthetic_protocol_smoke` uses fixture string
canonicalization. Other profiles require RDKit for chemical normalization.
The offline tests execute every committed generated CLI, compare its artifact
bytes with GT, and check the recorded artifact hash.

## Files

| File | Scenario |
| --- | --- |
| `generation_manifest.json` | Query/GT/generated provenance and hashes. |
| `generate.py` | Runs OpenAI4S from the frozen queries and freezes provenance. |
| `01_single_step_retrosynthesis.py` | Single-step retrosynthesis. |
| `02_multistep_route_planning.py` | Budgeted multistep planning. |
| `03_atom_mapping.py` | Atom mapping. |
| `04_forward_prediction.py` | Forward prediction. |
| `05_condition_recommendation.py` | Condition recommendation. |
| `06_yield_estimation.py` | Yield estimation. |
| `README.md` | English directory guide. |
| `README_zh.md` | Chinese directory guide. |
