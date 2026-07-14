# Protein Mutation Enhancement Skill

This progressive-disclosure Skill defines a deterministic iterative protein mutation workflow that can combine sequence, structure, property, and function scores. Its sidecar builds and ranks candidate records; it does not run ESM/folding/assay models itself or establish gain of function.

## Direct files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Defines input contracts, single/double/higher-order library construction, external score generation, thresholded ranking, round continuation/stop rules, defaults, and validation requirements. |
| [`kernel.py`](kernel.py) | Optional sidecar: validates sequences/mutation notation; normalizes and applies variants; deterministically enumerates libraries; computes a simple property-conservation score; reads score tables/writes FASTA; merges/normalizes/ranks metrics; runs thresholded selection rounds; suggests next positions; and persists ranked JSON. |

## Direct subdirectories

None.

The built-in property score is a heuristic component, not a functional predictor. Final candidates require independent computational and experimental validation.
