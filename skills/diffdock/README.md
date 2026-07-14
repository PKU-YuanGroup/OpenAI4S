# DiffDock Skill

This progressive-disclosure recipe guides external DiffDock-L blind small-molecule pose prediction. It does not bundle the DiffDock repository, weights, receptor preparation, or a GPU environment.

DiffDock confidence ranks pose plausibility, not binding affinity. Poses require chemistry checks and usually downstream scoring/refinement; sequence-only receptor folding adds another model-dependent uncertainty layer.

## Direct files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Main runbook for single-complex CLI input, SMILES/SDF/PDB handling, ranked pose/confidence outputs, interpretation, resource needs, and separation of geometry from affinity. |

## Direct subdirectories

| Directory | Responsibility |
| --- | --- |
| [`references/`](references/) | On-demand batch/library and sequence-only receptor workflows. |
