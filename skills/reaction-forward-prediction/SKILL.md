---
name: reaction-forward-prediction
description: Predict ranked products from reactants and reagents with ReactionT5v2-forward; use for outcome prediction or round-trip recovery. Product rank is not reaction feasibility.
license: MIT
origin: openai4s
metadata:
  third_party:
    - kind: weights
      name: ReactionT5v2-forward
      license: MIT
      terms_url: https://huggingface.co/sagawa/ReactionT5v2-forward
---

# Forward reaction prediction

Answer one scientific question: given reactants and a separately declared
reagent/condition string, which product structures does the model rank highest?
For retrosynthesis review, test whether the intended product appears in the
forward model's top-k outputs. Call this **round-trip recovery**, not proof that
the reaction works.

Use `sagawa/ReactionT5v2-forward` by default. It is a 2025 peer-reviewed,
MIT-licensed 0.2B model distributed as safetensors and runs through ordinary
Transformers.

## Install and run

Install in a separate environment; do not add these packages to OpenAI4S core:

```bash
conda create -n reactiont5 python=3.11 -y
conda run -n reactiont5 python -m pip install \
  "torch" "transformers==4.40.2" "tokenizers==0.19.1" \
  "huggingface_hub[cli]==0.35.0" \
  sentencepiece rdkit datasets accelerate pandas
```

Acquire an immutable local model snapshot and a reviewed source checkout from an
operator terminal whose current directory is the writable session workspace.
The revisions below are the reviewed revisions for this recipe; do not replace
either with `main`. A future revision requires a new review and provenance
record before use.

```bash
set -eu

REACTIONT5_ROOT="$PWD/models/reactiont5"
SOURCE_COMMIT="76eb08068e10fe255cae5d563a91e1c1e9abac54"
FORWARD_REVISION="933114058cb2604dc1bf536dbebdfcefbe83d4fc"

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

conda run -n reactiont5 hf download sagawa/ReactionT5v2-forward \
  --revision "$FORWARD_REVISION" \
  --local-dir "$REACTIONT5_ROOT/forward-$FORWARD_REVISION"
```

The final assertion must remain empty; if a reused checkout has modified or
untracked files, stop instead of executing it as reviewed source.

Record the two revisions and hashes of the downloaded regular files. Keep the
snapshot outside version control. The batch CLI imports repository-local
modules, so run `prediction.py` with `task_forward` as its working directory and
pass only the reviewed local snapshot:

```bash
REACTIONT5_ROOT="$PWD/models/reactiont5"
FORWARD_REVISION="933114058cb2604dc1bf536dbebdfcefbe83d4fc"

HF_HUB_OFFLINE=1 conda run -n reactiont5 \
  --cwd "$REACTIONT5_ROOT/source/task_forward" \
  python prediction.py \
  --input_data "$PWD/reactions.csv" \
  --model_name_or_path "$REACTIONT5_ROOT/forward-$FORWARD_REVISION" \
  --input_max_length 150 --num_beams 5 --num_return_sequences 5 \
  --batch_size 16 --output_dir "$PWD/forward-output"
```

Run that block from the session workspace root so `$PWD` expands to absolute
workspace input/output paths. For a single record, select the environment in
its own OpenAI4S Python Cell:

```python
host.env.use("reactiont5")
```

After the switch succeeds, load only the reviewed local snapshot in a new Cell:

```python
import os
from pathlib import Path

from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

reviewed_revision = "933114058cb2604dc1bf536dbebdfcefbe83d4fc"
snapshot = Path.cwd() / "models" / "reactiont5" / f"forward-{reviewed_revision}"
if not snapshot.is_dir():
    raise FileNotFoundError(f"reviewed snapshot is missing: {snapshot}")
os.environ["HF_HUB_OFFLINE"] = "1"
tokenizer = AutoTokenizer.from_pretrained(snapshot, local_files_only=True)
model = AutoModelForSeq2SeqLM.from_pretrained(snapshot, local_files_only=True)
model.eval()
text = "REACTANT:CCBr.OCCREAGENT:"
inputs = tokenizer(text, return_tensors="pt")
generated = model.generate(
    **inputs,
    num_beams=5,
    num_return_sequences=5,
    return_dict_in_generate=True,
    output_scores=True,
)
products = [
    tokenizer.decode(row, skip_special_tokens=True).replace(" ", "").rstrip(".")
    for row in generated.sequences
]
```

Record the model ID, reviewed revision, local file hashes, source commit, package
versions, device, beam settings, and input string. Never fall back from a
missing local snapshot to a moving Hub model ID.

## Round-trip check

1. Keep precursors and reagents in different fields; missing reagents are an
   explicit unknown, not an empty condition claim.
2. Generate no more top-k products than the review can inspect.
3. Parse and canonicalize each predicted product with RDKit.
4. Compare canonical intended product against the top-k set and record its rank.
5. Preserve nonmatching top products as possible model disagreements or
   byproduct hypotheses.

Do not multiply a backward-model score by a forward-model score unless both
were calibrated together on a deployment-matched held-out set. If the backward
and forward checkpoints share training data, round-trip agreement is correlated
evidence rather than an independent experiment.

## Output contract

Return reactants, reagents, ranked canonical products, invalid outputs, intended
product rank or `null`, top-k recovery, raw sequence scores when available, and
model provenance. Do not emit a boolean `feasible` field.

## Failure modes

| Symptom | Action |
| --- | --- |
| intended product absent | Report failed top-k recovery; inspect reagent encoding, stereochemistry, salts, and candidate chemistry. |
| invalid SMILES | Retain the raw string for audit, mark parse failure, and exclude it from canonical matching. |
| all top products identical | Report low beam diversity instead of presenting duplicates as support. |
| CPU latency is high | Batch requests or move the isolated environment to a GPU; do not reduce provenance or validation. |

Primary sources: <https://github.com/sagawatatsuya/ReactionT5v2> and
<https://huggingface.co/sagawa/ReactionT5v2-forward>.
