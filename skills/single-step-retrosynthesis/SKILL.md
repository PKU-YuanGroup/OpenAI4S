---
name: single-step-retrosynthesis
description: Generate ranked one-step precursor sets for a product with RetroChimera; use for disconnection ideas or expansion-policy calls. Do not recurse, search stock, or call the result a complete route.
license: MIT
origin: openai4s
capabilities:
  network:
    mode: raw_required
    domains: []
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
conda run -n retrochimera python -m pip install "retrochimera==1.2.0"
```

The USPTO-50K checkpoint uses RetroChimera's Graphium architecture and requires
`"retrochimera[graphium]==1.2.0"` instead. Install that extra before using the
smaller checkpoint as a smoke test.

Acquire and verify a reviewed checkpoint with
`retrosynthesis_planning/model_deployment.py`; keep weights outside git. Then:

The checked adapter and deployment notes are deliberately owned by the
`retrosynthesis_planning` Skill. A delegated specialist that runs this recipe
must therefore be allowlisted for both `single-step-retrosynthesis` and
`retrosynthesis_planning`; loading a Skill never widens that allowlist. Load and
read the dependency through the Skill APIs before importing it:

```python
host.load_skill("retrosynthesis_planning")
backend_notes = host.skills.read("retrosynthesis_planning", "MODEL_BACKENDS.md")
```

If either call is refused, stop and ask the caller to add the dependency to the
specialist profile. Do not bypass the gate with workspace file reads or a `../`
resource path. Once access is confirmed, the USPTO-50K smoke-test checkpoint
created by those notes lives under the same workspace root. Run the adapter:

```python
from pathlib import Path

from retrosynthesis_planning.external_backends import SyntheseusBackend

workspace = Path.cwd().resolve()
model_dir = workspace / "models" / "retrochimera" / "uspto50k"
manifest = model_dir / "model-manifest.json"

backend = SyntheseusBackend(
    model="RetroChimera",
    model_dir=model_dir,
    manifest=manifest,
    python_command=(
        "conda",
        "run",
        "--no-capture-output",
        "-n",
        "retrochimera",
        "python",
    ),
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

For a class-unknown benchmark, run the deterministic protocol after model
inference. The protocol fails closed without RDKit because identity/string
fallbacks would corrupt exact-match science; install the repository's optional
chemistry environment first:

```bash
uv sync --extra chemistry
```

Then normalize the frozen public output:

```bash
uv run python skills/retrosynthesis_planning/single_step_benchmark.py normalize \
  --targets input/targets.csv \
  --predictions results/predictions.jsonl \
  --model-manifest input/model_manifest.json \
  --top-k 10 \
  --output results/intermediate_results.json
```

The public target CSV is intentionally strict: it accepts only `target_id` and
`product_smiles`, so a reaction class, reference precursor, patent identifier,
or accidental extra column fails closed. Run `evaluate` only in the separate
evaluator process after predictions are frozen:

```bash
uv run python skills/retrosynthesis_planning/single_step_benchmark.py evaluate \
  --targets input/targets.csv \
  --predictions results/predictions.jsonl \
  --references private_evaluator/reference_precursor_sets.jsonl \
  --top-k 10 \
  --output private_evaluator/metrics.json
```

The evaluator compares dot-separated precursor molecules as unordered
multisets, preserves invalid and duplicate beams, scores each target before
aggregation, and supports multiple recorded precursor sets per product. It does
not turn patent-record recovery into a feasibility label.

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

Primary model source: <https://github.com/microsoft/retrochimera>. Read deployment
details and reviewed checkpoint metadata with
`host.skills.read("retrosynthesis_planning", "MODEL_BACKENDS.md")` after the
dependency has been allowed and loaded.
