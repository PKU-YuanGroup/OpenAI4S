# Annotation contract

## Marker evidence

The marker CSV columns are `cell_type,gene,direction,weight`; `direction` is
`positive` or `negative` and weight is a positive number. Gene matching uses
the declared namespace/symbol column and is case-sensitive after surrounding
whitespace is removed.

For each cluster, score positive marker enrichment and subtract negative
marker enrichment. Export all candidate scores, supporting genes, opposing
genes and missing genes; a gene supporting multiple candidates appears under
each of them, which is the explicit conflict evidence. Assign the top
candidate only when it has positive support and clears the configured evidence
margin; otherwise assign `Unknown`.

## Reference evidence

A reference h5ad is compatibility-screened only in this version: gene-space
overlap and the declared label column are validated, no label transfer is
performed, and reference labels are never applied to cells or clusters. The
screening outcome is reported as a warning and cannot change the annotation
status, marker evidence, or base clustering.

## Confirmation boundary

A mapping CSV contains exactly `cluster,cell_type`. It is user confirmation,
not a model prediction. Unmapped clusters remain `Unknown`. Without a mapping,
statistics group by stable cluster. With it, the run records the mapping hash
and may aggregate and rerun statistics by `confirmed_cell_type`.
