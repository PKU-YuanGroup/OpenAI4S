# fair-esm2 Skill

This directory contains the progressive-disclosure recipe for Meta ESM-2 embeddings, masked-language-model scores, mutation effects, and contact predictions through the external `fair-esm` package.

It does not bundle checkpoints or guarantee CPU/GPU capacity. Model likelihood/contact outputs are computational predictions and need task-appropriate validation.

## Direct files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Documents checkpoint selection, alphabet/batch conversion, representation layers, pooled/per-residue embeddings, mask-based mutation scoring, contacts, batching, memory, and model limitations. |

## Direct subdirectories

None.
