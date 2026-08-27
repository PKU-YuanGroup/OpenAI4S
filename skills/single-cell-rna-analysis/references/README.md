# Single-cell RNA Analysis References

Detailed contracts are split by decision point so the agent only loads the
material needed for the current stage.

## Files

| File | Use it for |
| --- | --- |
| [`input-contract.md`](input-contract.md) | Versioned configuration, sample-sheet and h5ad requirements, count validation, identifiers, reference consistency, contrast and confounding checks. |
| [`scientific-workflow.md`](scientific-workflow.md) | Per-sample QC, Scrublet, preserved representations, optional Harmony, resolution sweep, clustering and descriptive markers. |
| [`annotation-contract.md`](annotation-contract.md) | Marker-panel evidence, conflicts, reference compatibility, `Unknown`, and user-confirmed mappings. |
| [`statistics-contract.md`](statistics-contract.md) | Donor-aware pseudobulk DE, pairing, explicit design formulas, replication gates and Milo DA. |
| [`output-contract.md`](output-contract.md) | Checkpoints, resume invalidation, structured statuses, report, manifest, checksums and Artifact registration. |
| [`README_zh.md`](README_zh.md) | Chinese directory guide with the same structure as this file. |

These documents describe scientific and execution contracts, not biological
ground truth or a substitute for study-specific review.
