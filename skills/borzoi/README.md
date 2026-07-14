# Borzoi Skill

This progressive-disclosure recipe covers Borzoi DNA-to-functional-track prediction for locus tracks and ref/alt variant comparisons. It explains how to operate an external PyTorch model; no model runtime or checkpoint is bundled here.

Execution is conditional on compatible packages, downloaded weights, track metadata, and substantial GPU memory. Predicted track deltas are model evidence for prioritization, not causal or clinical validation.

## Direct files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Documents model loading, 524-kb one-hot inputs, human/mouse heads, output bins/tracks, reverse-complement handling, ref/alt scoring, metadata alignment, memory constraints, and license provenance. |

## Direct subdirectories

None.
