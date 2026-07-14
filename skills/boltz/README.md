# Boltz Skill

The progressive-disclosure recipe for Boltz-2, the open-weights diffusion co-folder that takes protein, DNA, RNA, and ligand chains and returns mmCIF plus confidences, with an optional small-molecule affinity head. The recipe tells the agent how to write the input YAML, drive the external `boltz` package, and read the structures and confidence files that come back. Boltz itself is not vendored here. Of the four co-fold Skills this one is the default for binder-validation campaigns: fully open MIT weights, and the fastest sampler of the set.

The package, the weights, a GPU, and any optional MSA service all have to be available separately. Boltz-2 is the one co-folder here that hands back a number in experimental units — `affinity_pred_value` is log10(IC50 in μM) — and that is exactly what makes it so easy to quote as though an assay had produced it. It orders candidates against each other; it does not measure them. A passing ipTM is the same kind of claim: the model placed the interface consistently. Neither number is evidence that the two chains ever meet.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Opens on the YAML entity blocks and the `boltz predict` flags, then gives most of its length to the affinity head, which no sibling co-folder has: a `properties:` block names one ligand chain as the binder, `affinity_pred_value` comes back as log10(IC50 in μM), and `affinity_probability_binary` is the score to rank hits by. One affinity ligand per input, capped at 128 atoms on Boltz v2.2.x, and a FASTA input cannot request it at all. Two traps get their own sections: `msa: empty` buys accuracy loss and no VRAM back, because the MSA search is CPU-side, and the `--no_kernels` fallback runs the reference PyTorch path, which the SKILL puts at roughly 2× slower and treats as correct rather than degraded — an unblock for a one-off, not a campaign. Nothing in this repo benchmarks that path or checks it against the fused kernels, so read it as guidance rather than as a measured claim of identical output. Then the confidence JSON, an error table, and licensing. |
