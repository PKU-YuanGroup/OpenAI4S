# Borzoi Skill

Borzoi predicts functional coverage tracks — RNA-seq, CAGE, DNase, ChIP — straight from DNA sequence. Reach for it to get predicted tracks across a locus, or to compare ref and alt windows around a variant when you want the assay-level consequence rather than a language model's likelihood; `evo2` is the skill for the likelihood, and the two answer different halves of the same variant question. What is here is guidance for driving an external PyTorch port. No model runtime and no checkpoint is bundled.

Whether any of it runs depends on the environment: compatible packages, weights already downloaded, the track metadata, and a substantial amount of GPU memory. A predicted track delta is model evidence you can prioritize on. It is not causal proof, and it is not clinical validation.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | The input window is fixed at 524,288 bp of one-hot DNA and the model exposes no attribute that says so, so a shape mismatch is the first thing you meet: pad or crop. Out comes a tensor of 7,611 human tracks over 32-bp bins, with the separate 2,608-track mouse head off unless you enable it and select it. From there: where the track metadata actually lives (`TRACKS_DF`, not a `targets` attribute the base model does not have) and how to line it up with the output, ref/alt variant scoring, the VRAM floor, and the CC-BY-4.0 terms on the ported weights, which do not match the Apache-2.0 code they came with. |
