# Statistical analysis contract

This contract applies only to `analysis_mode: comparative`. In descriptive
mode, pseudobulk construction, DE and DA are not run; both structured statuses
are `not_applicable_descriptive`, and no inferential tables or figures are
emitted.

## Unit of replication

Build integer pseudobulk counts from `layers["counts"]` for each
`sample × cluster` or `sample × confirmed_cell_type`. Cells are observations,
not biological replicates. Export library size and cell count for every
pseudobulk unit.

Each tested and reference level requires at least three independent donors. If
either level has fewer, clustering and annotation still complete but DE and DA
become `skipped_insufficient_replicates`; no inferential p-values are emitted.

## Differential expression

Run PyDESeq2 separately for eligible cell groups. The condition contrast is
explicitly `(condition_key, tested, reference)`. A paired design uses
`~ donor_id + condition`; an unpaired design uses declared covariates followed
by condition. Reject a rank-deficient design. Export baseMean, log2FoldChange,
standard error, statistic, p-value and adjusted p-value with group and contrast
metadata. Positive log2 fold change means tested greater than reference.

## Differential abundance

Milo counts neighborhoods by sample, not by cell. Use Pertpy's `pydeseq2`
solver. Include donor as a fixed design covariate for paired data and declared
covariates otherwise. Export logFC, PValue, FDR and SpatialFDR where available.
Milo failure is reported independently of pseudobulk DE and does not invalidate
the base analysis.

These models test the declared contrast only; they do not make causal claims.
