# Output, checkpoint and delivery contract

## Stages

Persist `preflight.json`, `01_qc.h5ad`, `02_embedding.h5ad`,
`03_clustering.h5ad`, `04_annotation.h5ad`, and final `analysis.h5ad` as stages
complete. `run_manifest.json` records state for preflight, QC, embedding,
clustering, annotation and statistics. Checkpoints `03`, `04`, and
`analysis.h5ad` are derived files: they share the compressed matrix payload of
their upstream stage and rewrite only the slots that stage changed (`obs` and
`uns`; plus `obsm` for `analysis.h5ad`), because clustering, annotation, and
the final stamp never modify X, layers, raw, var, obsp, or varm.

Each stage records the SHA-256 of its effective configuration slice and input
fingerprint. `resume(run_dir)` reloads `config.resolved.json`, rehashes inputs,
and resumes from the earliest invalid or incomplete stage. A changed source or
configuration is never combined with a stale downstream checkpoint.

## Structured return

All public calls use JSON-friendly values. `run()` and `resume()` return:

- `status`: `completed`, `completed_with_warnings`, or `failed`;
- `run_dir` and `manifest` as absolute paths;
- `featured_files`: existing files for Artifact registration;
- `warnings`;
- `analysis_mode`;
- `annotation_status`;
- `statistics_status`, with independent `de` and `da` values.

## Analysis bundle

The complete run includes `analysis.h5ad`, `config.resolved.json`,
`run_manifest.json`, `report.md`, QC/marker/annotation/pseudobulk/DE/DA tables,
and generated QC/UMAP/resolution/marker/DE/DA figures when applicable. The
manifest records input summaries, package versions, seed, thresholds, stage
status, warnings and SHA-256 for every delivered file.

Descriptive runs include the base h5ad, QC, embedding, clustering, annotation,
marker tables and figures. They intentionally omit pseudobulk, DE and DA files;
the manifest records both statistics as `not_applicable_descriptive`. When an
output directory is reused, the workflow removes known comparative-only tables,
error files and figures before publishing the descriptive bundle; delivery and
manifest generation also exclude those paths defensively.

The Agent calls `host.save_artifact(path)` once for every returned
`featured_files` entry, then submits the structured result. Missing optional
statistics files must agree with the recorded skipped/failed status and must
not be described as successful inference.
