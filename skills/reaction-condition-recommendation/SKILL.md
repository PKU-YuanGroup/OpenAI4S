---
name: reaction-condition-recommendation
description: Recommend ranked catalysts, reagents, solvents, and checkpoint-supported temperatures for a fixed reaction with the open Parrot model. Use when both reaction sides are known and condition hypotheses are needed for literature search or route triage; do not use it to invent conditions for an unspecified transformation or to claim an executable laboratory procedure.
license: MIT
metadata:
  third_party:
    - kind: code
      name: Parrot inference code
      license: MIT
      terms_url: https://github.com/wangxr0526/Parrot/blob/main/LICENSE
---

# Reaction-condition recommendation

Answer one scientific question: for a fixed reaction, which condition labels
does a trained model rank highest? Conditions are hypotheses used to focus
literature/ELN retrieval. They are not an experimental procedure and must not be
generated before reactants and products are specified.

Use Parrot by default. Its official repository publishes code, checkpoints,
dataset label dictionaries, CPU/GPU environment files, a CLI, and a web app.
The repository code is MIT. The externally hosted checkpoint archives do not
carry a separate machine-readable license in the official downloader; review
their terms before organizational or commercial deployment and record the
decision in the model manifest.

## Install and run

Keep Parrot in its own environment because it pins an older Transformers stack:

```bash
git clone https://github.com/wangxr0526/Parrot.git /opt/models/Parrot
conda env create -n parrot -f /opt/models/Parrot/envs_cpu.yaml
conda run -n parrot python /opt/models/Parrot/preprocess_script/download_data.py
```

Review the downloader URLs and hash downloaded archives before extracting them.
Write one complete reaction SMILES per line, then run the official CLI:

```bash
conda run -n parrot python /opt/models/Parrot/inference.py \
  --config_path /opt/models/Parrot/configs/config_inference_use_uspto.yaml \
  --input_path reactions.txt \
  --output_path predicted_conditions.csv \
  --num_workers 2 --inference_batch_size 8 --gpu -1
```

The USPTO checkpoint recommends categorical condition components. Use the
Reaxys configuration only when its separately obtained data/checkpoint terms
have been reviewed and temperature prediction is required. Never imply that all
Parrot checkpoints predict temperature.

## Interpret the result

- Preserve the label dictionary and model configuration used to decode each
  categorical ID.
- Return top-k condition sets rather than combining marginal top-1 labels into
  a condition set the model never emitted.
- Keep catalyst, reagent, solvent, and temperature fields separate.
- Use predictions to construct targeted literature/ELN searches for the exact
  transformation and close substrate analogues.
- Mark missing condition classes as unknown. Do not let an LLM fill them and
  relabel the result as model output.

## Output contract

Return canonical reaction SMILES, ordered condition sets, raw component labels
and decoded names, checkpoint/config provenance, temperature support status,
and validation state (`model_only`, `literature_analog`, `exact_precedent`, or
`eln_verified`). Model-only is the default.

## Failure modes

| Symptom | Action |
| --- | --- |
| only target or only precursors are known | Stop; select a concrete reaction before recommending conditions. |
| label ID is absent from the dictionary | Preserve the raw ID, mark decoding failure, and do not guess a name. |
| requested temperature with USPTO config | Report unsupported and switch only to a reviewed temperature-capable checkpoint. |
| predicted combination is unsafe or incompatible | Preserve the prediction as rejected and route it to EHS/chemist review. |

Primary source: <https://github.com/wangxr0526/Parrot>. The source paper is
Wang et al., *Research* (2023), DOI 10.34133/research.0231.
