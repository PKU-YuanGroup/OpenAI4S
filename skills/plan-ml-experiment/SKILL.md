---
name: plan-ml-experiment
description: Plan reproducible machine-learning experiments with leakage-safe random, grouped, or chronological splits; deterministic configuration fingerprints; dataset checksums; seeds, baselines, ablations, and artifact manifests.
origin: openai4s
category: reproducibility
---

# Plan an ML experiment

Use this skill before training begins. A reproducible experiment is a falsifiable
question plus immutable inputs, a leakage-safe evaluation boundary, a declared
metric, and enough recorded state to rerun the comparison.

## Planning sequence

1. Write the hypothesis, intervention, baseline, primary metric, and decision
   rule before inspecting test performance.
2. Choose the unit that must remain independent. Use a grouped split for
   patients, molecules/scaffolds, sites, documents, or repeated measures; use a
   chronological split when deployment predicts the future.
3. Reserve the test set for the final comparison. Fit preprocessing and choose
   hyperparameters using training/validation data only.
4. Fix seeds, environments, input versions, and the exact configuration.
5. Run the baseline first, then one-factor ablations and the planned model.
6. Save per-example predictions and a manifest before drawing conclusions.

## Deterministic helpers

```python
from importlib import import_module

plan = import_module("plan-ml-experiment.kernel")
splits = plan.grouped_split(patient_ids, seed=42)
manifest = plan.experiment_manifest(
    config,
    data_paths=["data/cohort.csv"],
    seeds=[42, 43, 44],
    code_revision="<git commit>",
)
```

Use `random_split(size, ...)` only when rows are genuinely independent.
`grouped_split(groups, ...)` keeps each group in exactly one partition.
`chronological_split(timestamps, ...)` orders observations without shuffling.
All return original row indices under `train`, `validation`, and `test`.

## Minimum artifact set

- frozen config and `config_fingerprint`;
- source artifact/version plus SHA-256 checksums;
- code revision, runtime/environment versions, and random seeds;
- split indices or stable sample IDs and the grouping/time policy;
- baseline, ablation, and final per-example predictions;
- aggregate metrics with uncertainty and a failure analysis.

Determinism does not prove validity. Hardware kernels may remain
nondeterministic, and repeating one biased split does not repair leakage or
dataset shift. Report deviations from the plan rather than overwriting it.
