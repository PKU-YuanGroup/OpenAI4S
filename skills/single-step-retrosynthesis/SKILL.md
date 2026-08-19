---
name: single-step-retrosynthesis
description: Generate and compare ranked one-step precursor sets for a product SMILES with open local models. Use RetroChimera as the primary model and ReactionT5v2-retrosynthesis only as a diversity baseline; use this Skill for disconnection ideation, expansion-policy calls, or single-step benchmark work, not for complete route planning.
license: MIT
metadata:
  third_party:
    - kind: model
      name: RetroChimera 1
      license: MIT
      terms_url: https://github.com/microsoft/retrochimera/blob/main/LICENSE
---

# Single-step retrosynthesis

Answer one scientific question: given one product, which precursor sets could
produce it in one reaction? Do not recurse, check stock, invent conditions, or
call the output a synthesis route. Hand accepted candidates to
`retrosynthesis_planning` for multi-step search.

Use RetroChimera 1 as the default. Its ensemble combines edit-based and de-novo
components, exposes a direct Syntheseus-compatible Python API, and publishes
Pistachio, USPTO-FULL, and USPTO-50K checkpoints. The OpenAI4S adapter already
runs it in an isolated process so PyTorch and model dependencies never enter the
stdlib core.

## Run through the checked adapter

Create a separate environment and install the model:

```bash
conda create -n retrochimera python=3.10 -y
conda run -n retrochimera python -m pip install retrochimera
```

Acquire and verify a reviewed checkpoint with
`retrosynthesis_planning/model_deployment.py`; keep weights outside git. Then:

```python
from retrosynthesis_planning.external_backends import SyntheseusBackend

backend = SyntheseusBackend(
    model="RetroChimera",
    model_dir="/models/retrochimera_pistachio",
    manifest="/models/retrochimera_pistachio/manifest.json",
    python_command=("conda", "run", "-n", "retrochimera", "python"),
)
result = backend.single_step("Oc1ccc(OCc2ccccc2)c(Br)c1", num_results=5)
for proposal in result["predictions"]:
    print(proposal["rank"], proposal["reactants_smiles"], proposal["score"])
```

Require a path-free manifest containing model version, checkpoint ID and hash,
training dataset, and code/weight licenses. Leave automatic model download off.
The adapter caps requests at ten candidates because low-ranked beams become
increasingly hallucination-prone.

## Compare candidates correctly

- Canonicalize each molecule, sort dot-separated components, and collapse exact
  duplicate precursor sets before comparing models.
- Preserve raw rank and raw model score. Do not calibrate a probability without
  a held-out set matching the deployment domain.
- Reject unparsable outputs and obvious atom/charge pathologies, but label this
  as structural screening rather than feasibility validation.
- Use `reaction-forward-prediction` for round-trip product recovery and
  `reaction-atom-mapping` only after both sides of a proposed reaction are known.
- Keep disagreements between edit-based and sequence-based models as review
  diversity; do not average scores from unlike models.

## Optional diversity model

Use `sagawa/ReactionT5v2-retrosynthesis` when a second sequence model is useful.
It is MIT, 0.2B parameters, and loads directly through Transformers. Record
whether the checkpoint is the ORD-pretrained model or the USPTO-50K fine-tune:
their benchmark meanings are very different. It is not the default proposal
model.

## Output contract

Return product SMILES, ordered precursor sets, model/checkpoint provenance, raw
scores, parse status, duplicate group, and explicit caveats. A precursor set is
a hypothesis for chemist review, not evidence of literature precedent,
selectivity, available conditions, yield, safety, or experimental success.

## Failure modes

| Symptom | Action |
| --- | --- |
| `model_dir is required` | Install a reviewed checkpoint and pass its directory; do not enable an implicit download. |
| backend timeout or OOM | Lower `num_results`, use the smaller USPTO-50K checkpoint for a smoke test, or move the isolated worker to a GPU environment. |
| many invalid or repeated beams | Stop expanding the beam; report low candidate diversity and try an independent model. |
| high score but failed forward recovery | Keep it as a disagreement requiring chemistry review; never overwrite either raw result. |

Primary model source: <https://github.com/microsoft/retrochimera>. Deployment
details and reviewed checkpoint metadata live in
`../retrosynthesis_planning/MODEL_BACKENDS.md`.
