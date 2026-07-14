# fair-esm2 Skill

Meta's ESM-2, driven through the external `fair-esm` package: per-residue and per-sequence embeddings, masked-language-model scores, mutation effects, and contacts. ESM-2 reads sequence and only sequence. It is never handed a structure and it does not inverse-fold — the contact map it returns is inferred from the residues alone, which is the opposite direction from the MPNN skills. One package trap sits in front of all of it: `fair-esm` and Biohub's ESM fork both import as `esm` and are different libraries. This recipe is the Meta one; the fork is covered by `esmfold2`.

No checkpoints are bundled here, and nothing in the recipe reserves the CPU or GPU capacity it asks for. The likelihoods and contacts a model hands back are computational predictions, and whether they hold for your task is something task-appropriate validation has to establish.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Picks a checkpoint (8M for a smoke test, 650M as the default, 3B when the embeddings have to be good), then walks through the alphabet and batch conversion, which representation layer to pull, pooled versus per-residue embeddings, mask-based mutation scoring, and contacts. Batching, memory, and what the model cannot tell you close it out. |
