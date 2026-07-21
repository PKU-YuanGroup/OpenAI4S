# scGPT Skill

scGPT is a pretrained transformer over single-cell expression: cell embeddings, cell-type annotation, gene-level representations, all from an external checkpoint. Reach for it when you want a foundation model's view of a dataset rather than a generative model fitted to it — for the latter, `scvi-tools` trains scVI/scANVI on your own counts. Nothing scGPT needs in order to run is kept in this directory. No checkpoint, no vocabulary, no `AnnData`, no GPU environment.

The recipe cannot check your data for you. Confirm the checkpoint layout, that your gene names line up with the vocabulary, how the counts were preprocessed, and that batch metadata and labels are what you think they are. Zero-shot or fine-tuned, an annotation is a model output, not ground truth.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | A scGPT checkpoint is a raw directory (`args.json`, `best_model.pt`, `vocab.json`), not a Hugging Face repo, so the loader takes a filesystem path and a hub id will not do. The gene names in `adata.var` have to line up with the checkpoint's vocabulary; the ones that do not are dropped without a word, so `gene_col` is worth checking before you read anything into the result. `embed_data` leaves the per-cell embedding in `.obsm["X_scGPT"]`, and annotation goes on from there. Then the batch and resource needs, the remote-compute path, and the two defaults that actually bite: `use_fast_transformer` is `True` and resolves to a FlashAttention path that may not import, and a stale torchtext `Vocab` shim shows up as a missing attribute rather than a clean failure. |
