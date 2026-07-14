# scGPT Skill

This progressive-disclosure recipe covers external scGPT checkpoints for single-cell embeddings, annotation, and gene representations. No checkpoint, vocabulary, `AnnData`, or GPU environment is included in this directory.

Checkpoint layout, vocabulary alignment, count preprocessing, batch metadata, and label validation must be verified. Zero-shot/fine-tuned annotations remain model outputs rather than ground truth.

## Direct files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Documents raw checkpoint/vocabulary loading, gene-token alignment, embedding and annotation flows, batching, output placement in `AnnData`, resource needs, and biological/technical caveats. |

## Direct subdirectories

None.
