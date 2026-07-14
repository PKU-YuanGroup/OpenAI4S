# DiffDock References

One file, loaded only when a task outgrows the single-complex path in the main recipe.

## Files

| File | Responsibility |
| --- | --- |
| [`workflows.md`](workflows.md) | How to build the batch CSV and push a fragment library through it, and how to let DiffDock fold the receptor with ESMFold when only a sequence is available. It also covers reading confidence across a library: the logits are calibrated within one complex, not between ligands, so they are not an affinity ranking. |
