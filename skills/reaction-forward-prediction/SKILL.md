---
name: reaction-forward-prediction
description: Predict ranked products from reactants and reagents with the open ReactionT5v2-forward model, and use product recovery as a round-trip check for proposed retrosynthetic steps. Use for forward reaction outcome prediction, precursor-set validation, byproduct hypotheses, or reaction-model benchmarking; do not treat product rank as experimental feasibility.
license: MIT
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
  "torch" "transformers==4.40.2" "tokenizers==0.19.1" sentencepiece rdkit
```

Run the official repository CLI for batches, or use the direct model-card API:

```python
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

model_id = "sagawa/ReactionT5v2-forward"
tokenizer = AutoTokenizer.from_pretrained(model_id)
model = AutoModelForSeq2SeqLM.from_pretrained(model_id)
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

Pin the Hugging Face revision for reproducible work and record resolved commit,
model ID, package versions, device, beam settings, and input string.

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
