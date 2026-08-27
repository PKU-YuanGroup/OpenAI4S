---
name: reaction-condition-recommendation
description: Recommend ranked catalyst, reagent, and solvent labels for a fully specified reaction using the reviewed Parrot USPTO checkpoint. Not for unknown reactions or lab procedures.
license: MIT
origin: openai4s
metadata:
  third_party:
    - kind: code
      name: Parrot inference code
      license: MIT
      terms_url: https://github.com/wangxr0526/Parrot/blob/main/LICENSE
    - kind: checkpoint
      name: Parrot USPTO condition predictor
      license: MIT
      terms_url: https://huggingface.co/xiaoruiwang/ChemEnzyRetroPlanner_metadata
---

# Reaction-condition recommendation

Answer one scientific question: for a fixed reaction, which condition labels
does a trained model rank highest? Conditions are hypotheses used to focus
literature/ELN retrieval. They are not an experimental procedure and must not be
generated before reactants and products are specified.

Parrot is the implementation. The original repository code is MIT, but its
Google Drive archives do not carry separate machine-readable terms in the
official downloader and remain blocked. The approved deployment instead uses
the first author's separately published Hugging Face repository, whose
repository card declares MIT. Admission is limited to revision
`b9ef6049d341bfc62d835f09ad6ce33b6f86b047`, `USPTO_condition.mar` (SHA256
`4418693a91a7a3b5f2aa101a39d58702b154e58901ddbf1ac94edc4c28de8e7d`) and
`condition_predictor_metadata.zip` (SHA256
`dfdf7fff11fe2d52af49146b1080dd6304ddd2b51665907fa759ffd4c5fca820`).

## Install and run

Keep Parrot in its own environment because it pins an older Transformers stack.
This recipe is Linux-only: upstream states that Parrot was tested on Linux, and
`envs_cpu.yaml` contains Linux-specific packages such as
`ld_impl_linux-64` and `libgcc-ng`. On macOS or another non-Linux platform,
stop and route the task to a reviewed Linux container or remote host rather than
trying to solve that lock file locally.

From an operator terminal whose current directory is the writable session
workspace, clone the code under a workspace-owned model root and detach at the
reviewed commit. Do not run a moving branch:

```bash
set -eu

PARROT_ROOT="$PWD/models/parrot"
PARROT_COMMIT="0fb2325567e21011589641544e32427c8244e2a9"

mkdir -p "$PARROT_ROOT"
if [ ! -d "$PARROT_ROOT/source/.git" ]; then
  git clone https://github.com/wangxr0526/Parrot.git "$PARROT_ROOT/source"
fi
git -C "$PARROT_ROOT/source" cat-file -e "${PARROT_COMMIT}^{commit}"
git -C "$PARROT_ROOT/source" checkout --detach "$PARROT_COMMIT"
test "$(git -C "$PARROT_ROOT/source" rev-parse HEAD)" = "$PARROT_COMMIT"
SOURCE_STATUS="$(git -C "$PARROT_ROOT/source" status \
  --porcelain --untracked-files=all)"
test -z "$SOURCE_STATUS"
conda env create -n parrot -f "$PARROT_ROOT/source/envs_cpu.yaml"
```

The final assertion must remain empty; if a reused checkout has modified or
untracked files, stop instead of executing it as reviewed source.

That source revision and the approved Hugging Face snapshot have been verified
in the external deployment root. The repository-native
`../retrosynthesis_planning/parrot_mar_inference.py` adapter consumes a safely
expanded MAR through `model_location`; the OpenAI4S worker has completed a real
GPU canary and returned 15 joint condition beams. This is an engineering
inference check, not benchmark accuracy or experimental validation. Invoke the
snapshot through `ReactionModelBackend("parrot", ...)`; temporary files must
use an explicit external `workspace_dir`.

Do not execute the official `download_data.py` directly. At the reviewed
revision it constructs an unquoted `shell=True` extraction command and does not
propagate extraction failure, so a workspace path containing spaces can fail
silently and shell metacharacters are unsafe. Review the downloader URLs and
checkpoint terms and record an explicit `allow` decision in the model manifest
before acquiring anything; a missing or `deny` decision must stop. Only after
that decision, use an approved operator workflow that streams each archive to
private staging, verifies its recorded size and digest, and extracts it without
a shell while rejecting traversal and links. Place only the verified dataset,
label dictionaries, and checkpoint at the repository-relative paths named by
the reviewed configuration, and add the acquisition receipts to the manifest
before inference.

The Google Drive files remain unapproved. Do not substitute the MIT source-code
license for those artifacts or silently replace the admitted Hugging Face
revision. A missing/deny admission decision, an unexpected filename, size, or
digest, or a path/link-unsafe archive must stop before extraction or inference.

Write one complete reaction SMILES per line. For the reviewed MAR deployment,
pass the expanded model directory in the backend manifest. The legacy upstream
CLI example below applies only to a separately reviewed legacy snapshot:

```bash
SESSION_WORKSPACE="$PWD"
PARROT_ROOT="$SESSION_WORKSPACE/models/parrot"
conda run -n parrot --cwd "$PARROT_ROOT/source" python inference.py \
  --config_path configs/config_inference_use_uspto.yaml \
  --input_path "$SESSION_WORKSPACE/reactions.txt" \
  --output_path "$SESSION_WORKSPACE/predicted_conditions.csv" \
  --num_workers 2 --inference_batch_size 8 --gpu -1
```

Run these blocks from the session workspace root. Shell expansion makes both
input and output absolute before `conda run` changes to the repository working
directory; do not rely on Parrot's process directory for session I/O.

The USPTO checkpoint recommends categorical condition components. Use the
Reaxys configuration only when its separately obtained data/checkpoint terms
have been reviewed and temperature prediction is required. Never imply that all
Parrot checkpoints predict temperature.

## Scenario 5 benchmark contract

Use `../retrosynthesis_planning/condition_benchmark.py` with the checkpoint's
frozen label dictionaries. Submit ranked complete five-slot tuples
(`catalyst1`, two solvents, and two reagents), preserving explicit empty slots.
Do not form an unscored Cartesian product from independent marginal labels.
The evaluator scores multi-reference exact tuples, Top-1 slot recall, OOV
tuples, duplicates, and unused Top-K budget.

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
| checkpoint admission missing, denied, or hash-mismatched | Stop before extraction or inference, return `terms_review_required`, and do not substitute LLM-generated conditions. |
| only target or only precursors are known | Stop; select a concrete reaction before recommending conditions. |
| label ID is absent from the dictionary | Preserve the raw ID, mark decoding failure, and do not guess a name. |
| requested temperature with USPTO config | Report unsupported and switch only to a reviewed temperature-capable checkpoint. |
| predicted combination is unsafe or incompatible | Preserve the prediction as rejected and route it to EHS/chemist review. |

Primary sources: <https://github.com/wangxr0526/Parrot> and the first-author
checkpoint distribution
<https://huggingface.co/xiaoruiwang/ChemEnzyRetroPlanner_metadata>. The source
paper is Wang et al., *Research* (2023), DOI 10.34133/research.0231.
