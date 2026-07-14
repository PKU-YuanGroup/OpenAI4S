# Evo 2 Skill

Evo 2 is a long-context DNA language model, and this is the operating guidance for it: likelihood scoring, embeddings, generation, and variant comparison. It answers questions about the sequence itself — how likely is this base, this window, this edit. When the question is instead what an assay would have measured, that is `borzoi`, and a two-axis variant prioritization runs both. The model code, the checkpoints, and the accelerator runtime are all external.

GPU capacity, usable context length, checkpoint access, and generation quality depend on the environment you actually run in, so confirm them there instead of assuming them from this page. And a likelihood is a model score: it is not a measured variant effect.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Sequences go in as plain `list[str]`; hand `score_sequences` a tensor and it dies on a dtype mismatch, because the API tokenizes for you. It gives back one mean log-likelihood per sequence, and a variant is scored as `Δll = ll_alt - ll_ref` over a fixed window. `generate` returns `.sequences`, `.logits` and `.logprobs_mean`, all populated with no flag to set. Embedding genomic windows is in the declared scope too. Then the model table — 7B in roughly 22 GB, 40B in roughly 78 GB, both at a million nucleotides of context — the remote-compute path for those checkpoints, and the failure modes worth recognizing early, including an `HF_HOME` on a read-only mount that only breaks when the loader tries to write `refs/`. |
