# Protein Mutation Enhancement Skill

An iterative protein mutation campaign: build a library, score it, rank it, decide whether to run another round. This is the orchestration layer, not a model. Library enumeration, score merging, ranking and loop control are deterministic and pure stdlib; the ESM, folding and assay numbers come from elsewhere — `fair-esm2` for sequence effect, `esmfold2` for structure — and are joined in on a stable variant ID like `A12V+G47D`. A variant ranking first is not evidence of gain of function.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | The input contracts, how single, double and higher-order libraries are built, where the external scores come from (`fair-esm2` for sequence effect, `esmfold2` for structure), thresholded ranking, the rule for continuing a round or stopping, the practical defaults, and the validation the output still needs. |
| [`kernel.py`](kernel.py) | Optional sidecar, pure stdlib. Validates sequences and `A12V`-style mutation notation, normalizes and applies variants, and enumerates a library deterministically with stable position-sorted IDs, so a score table can safely join on `id`. It scores the substitution itself with a local heuristic over amino-acid class, hydropathy, charge and volume; reads score tables and writes the library out as FASTA; merges and normalizes the metrics; ranks by weighted composite score; runs a selection round against acceptance thresholds and reports whether to continue; suggests the positions worth opening next; and persists the ranked result as JSON. |

The built-in property score is a heuristic term in the composite, not a functional predictor. Whatever comes out on top still needs independent computational and experimental validation.
