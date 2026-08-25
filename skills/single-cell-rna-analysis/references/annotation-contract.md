# Annotation contract

## Marker evidence

The marker CSV columns are `cell_type,gene,direction,weight`; `direction` is
`positive` or `negative` and weight is a positive number. Gene matching uses
the declared namespace/symbol column and is case-sensitive after surrounding
whitespace is removed.

For each cluster, score positive marker enrichment and subtract negative
marker enrichment. Export all candidate scores, supporting genes, opposing
genes, missing genes and conflicts. Assign the top candidate only when it has
positive support and clears the configured evidence margin; otherwise assign
`Unknown`. A gene supporting multiple incompatible candidates is explicit
conflict evidence, not silently discarded.

## Reference evidence

A reference h5ad is optional candidate evidence. It must share a sufficient
gene space, have a declared label column, and meet its documented preprocessing
contract. Validate overlap before transfer. Transfer failure is a warning and
cannot erase marker evidence or base clustering.

## Confirmation boundary

A mapping CSV contains exactly `cluster,cell_type`. It is user confirmation,
not a model prediction. Unmapped clusters remain `Unknown`. Without a mapping,
statistics group by stable cluster. With it, the run records the mapping hash
and may aggregate and rerun statistics by `confirmed_cell_type`.
