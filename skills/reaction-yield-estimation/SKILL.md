---
name: reaction-yield-estimation
description: Estimate reaction yield with the open ReactionT5v2-yield checkpoint for a fully specified reactant/reagent/product record. Use for in-domain reaction ranking, yield-model benchmarking, or deciding which steps need experiments; refuse route-wide success claims and clearly flag domain shift, missing conditions, and absent uncertainty calibration.
license: MIT
metadata:
  third_party:
    - kind: weights
      name: ReactionT5v2-yield
      license: MIT
      terms_url: https://huggingface.co/sagawa/ReactionT5v2-yield
---

# Reaction-yield estimation

Answer one scientific question: for a fully specified reaction string, what
yield does a trained regression model predict? Use the number to rank comparable
in-domain reactions or prioritize experiments. Do not call it a calibrated
probability of step success, and never multiply step predictions into a route
success probability.

Use `sagawa/ReactionT5v2-yield`, a 2025 MIT checkpoint trained on Open Reaction
Database records and distributed with a direct local inference example. The
model takes reactants, reagents, and product; a target alone is not valid input.

## Install and run

Use the same isolated `reactiont5` environment as
`reaction-forward-prediction`. For batches, prefer the official
`task_yield/prediction_with_PreTrainedModel.py` script. The model card also
defines the required `ReactionT5Yield` wrapper:

```python
model = ReactionT5Yield.from_pretrained("sagawa/ReactionT5v2-yield")
tokenizer = AutoTokenizer.from_pretrained("sagawa/ReactionT5v2-yield")
text = "REACTANT:<reactants>REAGENT:<reagents>PRODUCT:<product>"
inputs = tokenizer([text], return_tensors="pt")
predicted_percent = float(model(inputs).detach().cpu().item())
```

Copy the wrapper exactly from the official model card or repository rather than
loading the checkpoint as a plain seq2seq model. Pin the Hugging Face revision
and record package versions, device, input string, and checkpoint hash.

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
| no deployment-matched held-out set | Label `screening_only`; do not state expected experimental error. |
| multiple route steps | Score steps separately and report the weakest/most uncertain steps; never multiply percentages. |

Primary sources: <https://github.com/sagawatatsuya/ReactionT5v2> and
<https://huggingface.co/sagawa/ReactionT5v2-yield>.
