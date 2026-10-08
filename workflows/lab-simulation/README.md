# `workflows/lab-simulation/`

Offline simulation through the real Store and LabManager. Only the device and
LLM reply boundary are fake. No external equipment or ChemGymRL is required.

| File | Purpose |
| --- | --- |
| `workflow.json` | Versioned success, refusal, recovery and policy comparison cases. |

## Cases

Five cases cover an applied action, stale-revision failure, denied Host approval,
response-loss reconciliation, and fixed/random/scripted-LLM comparison.
The optional `backend: toy` input exercises the stdlib subprocess device for
the success case. Every case stops its runs and closes the manager, including
exception paths. Failure and permission-denied cases assert the refusal itself.

## Policy comparison

Seeds 3, 17 and 41 are paired across all three policies with the same fake
profile and budgets, capped at 16 attempts. The explicit toy goal is at least
0.5 mol fictional `toy_solute` in `beaker_1`, purity at least 0.9. This is an
invented toy objective, not a chemical or ChemGymRL success claim. The injected
LLM emits a scripted 200 mL transfer from extraction vessel to beaker 1, then
ends; it is not a live model and measures no model capability.

Raw evaluation rows stay private. `evaluate_run` consumes complete snapshots,
then only `compare` aggregates and public action/evidence counts leave the step.
Wall times and identities are omitted from the comparison output.

Reproduce the comparison with a private directory:

```python
import json
from pathlib import Path
from openai4s.benchmark.steps import make_context, STEPS
ctx = make_context(Path('../_data/W4-C/policy-comparison'))
try:
    step = STEPS['lab_compare_policies']
    print(json.dumps(step(ctx, {}), sort_keys=True))
finally:
    ctx.store.close()
```

Measured locally with seeds 3, 17, 41 (three episodes per policy):

| Policy | Goal met | Mean attempted actions | Applied actions | Observation coverage |
| --- | --- | --- | --- | --- |
| Fixed rule | 3/3 | 13 | 30 | 30/30 |
| Random valid | 0/3 | 8 | 11 | 11/11 |
| Scripted LLM | 1/3 | 2 | 6 | 6/6 |

All three cohorts had zero illegal-action rejections and zero unresolved outcomes.
Fixed-rule commands included 9 provider failures; random commands included 12
provider failures and 1 host rejection. One random episode reached the 16-attempt
runner cap and was explicitly stopped. The fake LLM was called 6 times in total.
These results characterize these scripts on the fake device; they do not rank
live LLMs or establish performance on ChemGymRL or physical experiments.
