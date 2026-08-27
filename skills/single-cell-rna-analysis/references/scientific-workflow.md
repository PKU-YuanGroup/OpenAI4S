# Scientific workflow contract

## Sample-aware QC

Compute total counts, detected genes, mitochondrial percentage and ribosomal
percentage separately by sample. The default flag is the union of robust MAD
outliers: low log-counts, low log-genes, high log-counts, high genes, and high
mitochondrial percentage. Zero-MAD metrics do not create artificial outliers.
There is no cross-tissue mitochondrial hard threshold; `qc.max_mt_pct` and
other hard cutoffs are honored only when explicitly configured.

Run Scrublet within each sample with the fixed run seed. Persist score,
prediction, effective threshold, all filter reasons and before/after counts.
Very small samples that cannot support Scrublet are retained with a warning.
SoupX and CellBender are out of scope. Record `qc.ambient_correction` as
`none`, `upstream`, or a specific upstream method; an uncorrected input creates
a limitation in the report.

In descriptive mode, a missing sample column is populated only with the
declared technical `input.sample_id`, so the same within-sample QC machinery can
run. No donor, condition, batch or replicate value is created or inferred.

## Data-space isolation

Immediately copy validated raw counts to `layers["counts"]` and never mutate
that layer. Normalize and log-transform `.X` for visualization and marker
ranking. Use count-layer HVGs and PCA for the neighbor representation. Store
the log-normalized full-gene representation in `.raw`.

If integration is `none`, build neighbors from `X_pca`. If Harmony was
explicitly requested and passed preflight, call Scanpy Harmony and store
`X_pca_harmony` while retaining `X_pca`; only the neighbor representation
changes. DE and pseudobulk always read `layers["counts"]`.

## Clustering and markers

With the fixed seed, run Leiden for the configured resolution sweep, default
`0.2, 0.4, 0.5, 0.6, 0.8, 1.0`. Store every assignment as
`leiden_<resolution>` and copy the selected resolution (default 0.5) to
`cluster`. The sweep is a sensitivity view; the workflow does not call a
resolution optimal from UMAP geometry.

Rank cluster markers on log-normalized values for description only. Export
gene, score, log-fold change and adjusted p-value where Scanpy supplies them.
Never reuse these values as tested-versus-reference condition DE.

For descriptive mode, these markers and embeddings are the terminal scientific
results. They describe within-sample structure only and cannot support condition
effects or population-level inference.
