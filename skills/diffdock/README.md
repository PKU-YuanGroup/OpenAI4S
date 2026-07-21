# DiffDock Skill

DiffDock-L docks blind. It samples ligand placements across the whole protein surface with a diffusion model, without a search box or a declared pocket, then ranks the samples with a separately trained confidence head. Reach for it to dock a SMILES or an SDF against a PDB, or to get a starting pose for something that will rescore it. The DiffDock repository, the weights, receptor preparation and a GPU environment all have to be arranged separately; none of them are bundled here.

DiffDock confidence ranks pose plausibility, not binding affinity. A pose still needs chemistry checks and, usually, downstream scoring or refinement. Folding the receptor from sequence adds another layer of model-dependent uncertainty on top of that.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | The runbook for the single-complex CLI path: how to pass SMILES, SDF and PDB inputs, what the ranked pose files and their confidence logits do and do not tell you, what hardware the run needs, and which failures are worth recognizing on sight. Geometry and affinity are kept apart throughout. |

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`references/`](references/) | Read on demand: batch and library docking, and the sequence-only receptor path. |
