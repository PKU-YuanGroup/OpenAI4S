# Single-cell RNA Analysis Skill

An OpenAI4S-maintained workflow for human or mouse, cell-called 10x GEX
scRNA-seq and snRNA-seq matrices. It provides a versioned configuration
contract, an executable Scanpy pipeline, conservative scientific gates,
restartable checkpoints, and an auditable output bundle. It does not modify or
vendor the pinned `bioSkills` collection.

## Files

| Path | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Short agent entry point: scope, public calls, stage routing, failure behavior, Artifact handoff, and interpretation boundaries. |
| [`kernel.py`](kernel.py) | Lazy-imported implementation of `preflight(config)`, `run(config, output_dir)`, and `resume(run_dir)`. |
| [`references/`](references/) | Detailed input, scientific, annotation, statistical, and output contracts, with its own bilingual directory documentation. |

The workflow is evidence preserving: raw counts remain isolated in
`layers["counts"]`, Harmony changes an embedding only, cluster markers never
stand in for condition DE, and unconfirmed labels may remain `Unknown`.
