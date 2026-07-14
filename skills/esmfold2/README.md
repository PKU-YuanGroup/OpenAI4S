# ESMFold2 Skill

The progressive-disclosure recipe for Biohub's ESMFold2 and ESMFold2-Fast co-folding models, and for the ESMC protein language models from the same release. Nothing is vendored here: no code, no weights, no Hugging Face access, no GPU environment.

Which models, backends and versions actually exist has to be checked in the active environment. The paper's own FoldBench figure for antibody-antigen is a 50–55% DockQ pass rate, so about half of those interfaces are wrong, and the protocol behind numbers like that is 25 seeds by 5 diffusion samples with the best of the 125 taken as the answer — one fold ranked by its own ipTM is a thinner result than the headline suggests. The PDB training cutoff is September 2021; anything solved since, the model has never seen. Structures, mutation scores, contacts and interpretability features are computed predictions, and none of them should be presented as experimentally validated.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | The whole recipe: how to describe protein, DNA, RNA and ligand inputs, when single-sequence mode is enough and when to feed an MSA, and how many diffusion steps and trunk recycles it takes to reproduce the paper's numbers. It also explains what the fused kernel backend actually buys you: roughly a 1.5–6x speedup on the trunk, growing with sequence length, though short folds are diffusion-bound and only break even around L≈300–400. (The gotchas list quotes a flat ~12x against the paper's numbers; the detailed section is the one to trust.) Fused and reference structures agree to within noise. After that, how to read the confidence outputs, how to pick a model variant, and where the upstream weights and their license come from. |

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`references/`](references/) | Read on demand: notes on the experimental design hook, and the ESMC language-model recipes. |
