---
name: protein-mutation-enhancement
description: >
  Deterministic protein gain-of-function mutation workflow: build single,
  double, and higher-order mutant libraries; merge ESM sequence-effect scores,
  structure metrics from ESMFold-class models, property/function scores; rank
  candidates; and decide whether to stop or start the next design round.
origin: openai4s
category: workflow
requirements: [gpu]
---

# Protein Mutation Enhancement Workflow

Use this skill when the task is to improve a target protein by iterative
mutation design. It is an orchestration layer: deterministic library
construction, score merging, ranking, and loop-control run locally with
stdlib-only helpers; heavyweight model calls run through existing model skills
such as `fair-esm2` and `esmfold2`.

## Workflow Contract

1. Validate the wild-type protein sequence and define mutable positions.
2. Build a deterministic mutant library:
   - round 1 usually uses single mutants over selected positions;
   - later rounds expand top candidates to doubles or higher-order combinations;
   - every variant has a stable ID such as `A12V+G47D`.
3. Score sequence effect with an ESM masked-LM model:
   - retrieve `fair-esm2` or `esmfold2` for concrete GPU recipes;
   - produce a table keyed by `id`, commonly with `esm_delta` where higher is
     better.
4. Predict structures for promising mutants:
   - retrieve `esmfold2` for ESMFold2 / ESMFold2-Fast recipes;
   - produce structure metrics keyed by `id`, commonly `plddt`, `ptm`, `pae`,
     `rmsd_to_wt`, or task-specific interface metrics.
5. Merge sequence, structure, property, and functional assay/proxy metrics.
6. Rank by weighted normalized score and apply acceptance thresholds.
7. If any candidate passes thresholds, call `host.submit_output(...)`. If none
   pass, use the top ranked variants to seed the next loop and expand the
   library.

## Import

The directory name contains hyphens, so import via `importlib`:

```python
import importlib

pm = importlib.import_module("protein-mutation-enhancement.kernel")
```

## Build a Mutation Library

```python
wt = "MKTAYIAKQRQISFVKSHFSRQ"

library = pm.enumerate_mutants(
    wt,
    positions=[3, 5, 8, 12],
    substitutions={3: ["A", "L", "F"], 5: ["V", "L"], 8: ["R", "K"]},
    max_order=2,
    limit=500,
)

pm.write_fasta(library, "round1_mutants.fasta")
```

`positions` are 1-indexed. If `substitutions` omits a position, all 20 natural
amino acids except the wild-type residue are used. Candidate IDs are stable and
sorted by position, so downstream score tables can safely join on `id`.

## Merge Scores and Rank

After running ESM and structure jobs, load or construct score tables keyed by
variant ID:

```python
esm_scores = {
    "T3L": {"esm_delta": 1.8},
    "Y5V": {"esm_delta": 0.3},
    "T3L+Y5V": {"esm_delta": 2.1},
}
structure_scores = {
    "T3L": {"plddt": 86.0, "rmsd_to_wt": 0.8},
    "Y5V": {"plddt": 71.0, "rmsd_to_wt": 2.4},
    "T3L+Y5V": {"plddt": 82.0, "rmsd_to_wt": 1.1},
}

round_result = pm.run_selection_round(
    library,
    score_tables=[esm_scores, structure_scores],
    weights={
        "esm_delta": 0.45,
        "plddt": 0.25,
        "rmsd_to_wt": 0.15,
        "property_score": 0.15,
    },
    directions={"rmsd_to_wt": "low"},
    acceptance_thresholds={
        "composite_score": 0.72,
        "esm_delta": 1.0,
        "plddt": 75.0,
    },
    top_k=20,
)

best = round_result["ranked"][0]
print(best["id"], best["composite_score"], round_result["should_continue"])
```

`property_score` is computed locally from conservative amino-acid-class,
hydropathy, charge, and size-change heuristics unless an external table
provides it. External assay or task-proxy metrics can be added as extra columns
and weights.

## Loop Pattern

```python
current_library = pm.enumerate_mutants(wt, positions=active_positions, max_order=1)

for round_idx in range(1, 6):
    # 1. Score `current_library` using fair-esm2 / ESMC.
    # 2. Fold the promising subset using esmfold2 / ESMFold2-Fast.
    # 3. Merge the resulting tables.
    result = pm.run_selection_round(
        current_library,
        score_tables=[esm_scores, structure_scores, function_scores],
        weights=weights,
        directions=directions,
        acceptance_thresholds=thresholds,
        top_k=50,
    )
    if not result["should_continue"]:
        host.submit_output({"accepted": result["accepted"], "round": round_idx})
        break

    active_positions = pm.suggest_next_positions(result["ranked"], max_positions=10)
    current_library = pm.enumerate_mutants(
        wt,
        positions=active_positions,
        max_order=min(round_idx + 1, 4),
        seeds=result["next_round_seeds"],
        limit=2000,
    )
else:
    host.submit_output({"accepted": [], "ranked": result["ranked"][:20]})
```

## Practical Defaults

- Start with curated active-site, binding-site, stability, or conservation
  positions instead of all positions when possible.
- Use `max_order=1` for the first pass, then expand top single mutants into
  doubles/combinations with `seeds=...`.
- Keep the model jobs separate from ranking artifacts:
  - `mutants.fasta`
  - `esm_scores.csv`
  - `structure_scores.csv`
  - `ranked_candidates.json`
- Prefer hard acceptance thresholds for safety-critical metrics such as
  minimum pLDDT or maximum RMSD, and use weighted composite score only after
  those gates pass.

## Notes

- This skill does not claim a mutation is biologically validated. It ranks
  candidates for follow-up validation using deterministic, inspectable rules.
- Core helpers are pure stdlib and safe for the default offline test suite.
- GPU model installation, weights, and remote execution remain in the model
  skills (`fair-esm2`, `esmfold2`) and compute skills.
