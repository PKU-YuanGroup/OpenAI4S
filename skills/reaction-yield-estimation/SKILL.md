---
name: reaction-yield-estimation
description: Estimate yield for a fully specified reactant/reagent/product record with ReactionT5v2-yield. Use for in-domain screening, not route success; flag domain shift and uncalibrated uncertainty.
license: MIT
origin: openai4s
metadata:
  third_party:
    - kind: weights
      name: ReactionT5v2-yield
      license: MIT
      terms_url: https://huggingface.co/sagawa/ReactionT5v2-yield
---

# Reaction-yield estimation

Answer one scientific question: for a fully specified reaction string, what
yield does a trained regression model predict? Only after the exact deployment
passes its canaries and held-out validation may the number rank comparable
in-domain reactions or prioritize experiments. Do not call it a calibrated
probability of step success, and never multiply step predictions into a route
success probability.

Use `sagawa/ReactionT5v2-yield`, a 2025 MIT checkpoint trained on Open Reaction
Database records and distributed with a direct local inference example. The
model takes reactants, reagents, and product; a target alone is not valid input.

**Deployment status:** the currently pinned released checkpoint is quarantined
for quantitative use. With the upstream wrapper, canonicalization, sorted
mixture components, fixed 400-token padding, and the upstream Transformers
version, the published model-card canary was expected to return about 19.1666%
but returned 65.924858%. Until that discrepancy is resolved against a
deployment-matched held-out set, the backend may be exercised for protocol
testing only and its values must not rank reactions or support scientific
conclusions.

## Install and run

This Skill is self-contained; it does not require access to the
`reaction-forward-prediction` Skill. Create the isolated environment with all
direct and batch dependencies:

```bash
conda create -n reactiont5 python=3.11 -y
conda run -n reactiont5 python -m pip install \
  "torch" "transformers==4.40.2" "tokenizers==0.19.1" \
  "huggingface_hub[cli]==0.35.0" \
  sentencepiece rdkit datasets accelerate pandas
```

From an operator terminal whose current directory is the writable session
workspace, acquire the reviewed source commit and immutable yield-model
snapshot. Do not replace either revision with `main`; a different revision
requires a new review and provenance record.

```bash
set -eu

REACTIONT5_ROOT="$PWD/models/reactiont5"
SOURCE_COMMIT="76eb08068e10fe255cae5d563a91e1c1e9abac54"
YIELD_REVISION="f0658bfd360bceaaf560f11b850781c50221fe0b"

mkdir -p "$REACTIONT5_ROOT"
if [ ! -d "$REACTIONT5_ROOT/source/.git" ]; then
  git clone https://github.com/sagawatatsuya/ReactionT5v2.git \
    "$REACTIONT5_ROOT/source"
fi
git -C "$REACTIONT5_ROOT/source" cat-file -e "${SOURCE_COMMIT}^{commit}"
git -C "$REACTIONT5_ROOT/source" checkout --detach "$SOURCE_COMMIT"
test "$(git -C "$REACTIONT5_ROOT/source" rev-parse HEAD)" = "$SOURCE_COMMIT"
SOURCE_STATUS="$(git -C "$REACTIONT5_ROOT/source" status \
  --porcelain --untracked-files=all)"
test -z "$SOURCE_STATUS"

conda run -n reactiont5 hf download sagawa/ReactionT5v2-yield \
  --revision "$YIELD_REVISION" \
  --local-dir "$REACTIONT5_ROOT/yield-$YIELD_REVISION"
```

The final assertion must remain empty; if a reused checkout has modified or
untracked files, stop instead of importing it as reviewed source.

Record both revisions and hashes of the downloaded regular files, and keep the
snapshot outside version control. Select the environment in its own OpenAI4S
Python Cell:

```python
host.env.use("reactiont5")
```

After the switch succeeds, import the reviewed wrapper from the pinned local
source checkout and load only the reviewed local snapshot in a new Cell:

```python
import os
import sys
from pathlib import Path

import torch
from transformers import AutoTokenizer

source = Path.cwd() / "models" / "reactiont5" / "source"
reviewed_revision = "f0658bfd360bceaaf560f11b850781c50221fe0b"
snapshot = Path.cwd() / "models" / "reactiont5" / f"yield-{reviewed_revision}"
if not source.is_dir() or not snapshot.is_dir():
    raise FileNotFoundError("reviewed ReactionT5 source or yield snapshot is missing")
os.environ["HF_HUB_OFFLINE"] = "1"
sys.path.insert(0, str(source))
from models import ReactionT5Yield2

model = ReactionT5Yield2.from_pretrained(snapshot, local_files_only=True)
tokenizer = AutoTokenizer.from_pretrained(snapshot, local_files_only=True)
model.eval()
text = "REACTANT:<reactants>REAGENT:<reagents>PRODUCT:<product>"
inputs = tokenizer([text], return_tensors="pt")
with torch.inference_mode():
    raw_predicted_percent = float(model(inputs).detach().cpu().reshape(-1)[0])
display_percent = min(100.0, max(0.0, raw_predicted_percent))
```

Do not load this regression checkpoint as a plain seq2seq model. Record the
model ID, reviewed revision, local file hashes, source commit, package versions,
device, and input string. Never fall back from a missing snapshot to a moving
Hub model ID.

The upstream `task_yield/prediction_with_PreTrainedModel.py` script is not
audit-compliant unchanged: it overwrites its prediction column with values
clipped to 0–100. If adapting it for batches, preserve two columns before
writing the CSV:

```python
test_ds["prediction_raw"] = prediction
test_ds["prediction_percent"] = test_ds["prediction_raw"].clip(0, 100)
```

Run only that reviewed adaptation from the checkout's `task_yield` directory;
never relabel the clipped column as the raw model result. Pass its
`--model_name_or_path` argument the local
`models/reactiont5/yield-f0658bfd360bceaaf560f11b850781c50221fe0b`
directory and set `HF_HUB_OFFLINE=1`; do not pass the Hub model ID.

For OpenAI4S, use `reaction_model_deployment.py` to install the pinned shared
ReactionT5v2 environment, download `sagawa/ReactionT5v2-yield` at revision
`f0658bfd360bceaaf560f11b850781c50221fe0b`, snapshot the complete local model,
and call `ReactionModelBackend("reactiont5_yield", ...)`. The committed worker
contains the model-card regression head, requires a local checkpoint, disables
implicit downloads, preserves raw un-clipped output, and reports package and
manifest provenance.

The worker also reproduces the pinned upstream preprocessing: each molecular
mixture is RDKit-canonicalized component-wise, components are sorted, an absent
reagent is encoded as one blank character, and inputs are padded/truncated to
400 tokens by default. `input_max_length` is recorded and bounded to 32--1024.
Matching preprocessing did not remove the canary discrepancy above.

Copy the wrapper exactly from the official model card or repository rather than
loading the checkpoint as a plain seq2seq model. Pin the Hugging Face revision
and record package versions, device, input string, and checkpoint hash.

## Scenario 6 benchmark contract

Use `../retrosynthesis_planning/yield_benchmark.py` for the frozen random test
and four molecular-framework OOD groups. Submit the raw predicted percentage;
never clip it before evaluation. Intervals must be either fully specified or
explicitly absent, and every prediction carries a domain-status label. The
evaluator reports per-group MAE/RMSE/R2/ranking and interval diagnostics,
macro-OOD MAE, and worst-group MAE rather than hiding shift behind one pooled
score.

## Domain gate

Before quoting the number, record:

- whether reagents, catalyst, solvent, and temperature are known or missing;
- whether the reaction class and substrate family resemble the validation data;
- whether the value comes from the base checkpoint or a deployment-specific
  fine-tune;
- held-out MAE/RMSE and calibration diagnostics for that deployment domain;
- an uncertainty estimate, if and only if one was actually computed by a
  validated ensemble or conformal procedure.

If these checks are absent, label the output `screening_only`. The published
benchmark includes strong C-N coupling results, but that does not establish
uniform accuracy across arbitrary chemistry or laboratory protocols.

## Output contract

Return reaction fields, predicted yield percent, raw unclipped value, model and
revision, domain status (`matched`, `uncertain`, `out_of_domain`), missing-input
flags, optional validated uncertainty interval, and evaluation provenance. Clip
only for presentation; preserve any raw prediction outside 0–100 for audit.

## Failure modes

| Symptom | Action |
| --- | --- |
| product or reagent context missing | Refuse quantitative interpretation; request a complete reaction record. |
| raw prediction outside 0–100 | Preserve it, flag extrapolation, and show a clipped display value only if needed. |
| released model-card canary mismatch | Quarantine the checkpoint; do not rank reactions until independently resolved. |
| no deployment-matched held-out set | Label `screening_only`; do not state expected experimental error. |
| multiple route steps | Score steps separately and report the weakest/most uncertain steps; never multiply percentages. |

Primary sources: <https://github.com/sagawatatsuya/ReactionT5v2> and
<https://huggingface.co/sagawa/ReactionT5v2-yield>.
