# Chai-1 Skill

The progressive-disclosure recipe for Chai-1, an all-atom diffusion co-folder that treats protein, RNA, DNA, and SMILES-ligand chains as first-class entities in one multi-entity FASTA. It walks the agent through the external `chai-lab` Python API and through the ranked candidates and scores that come back; the model itself is not shipped here. Chai-1 covers much the same ground as `boltz`, and the recipe leans on that: running both and keeping the designs that pass either is a common consensus filter, and Chai's Python entry point makes it the easier of the two to embed in a design loop.

The package, the weights, GPU resources, and optionally an external MSA service all have to be available separately. Running Chai-1 alongside Boltz-2 buys a second model, not a second experiment. Both are all-atom diffusion co-folders of the same family, so a complex they both like is a complex they can be wrong about together, and the ipTM they agree on is still the models talking about themselves. What survives both is a shorter list to take to the bench. That is all it is.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Most of it is about one choice: MSA-backed run or the ESM-embedding path. Skipping the MSA server is faster but typically lands a few ipTM points behind, and it saves no GPU memory, because the embedding path loads a traced 3-billion-parameter ESM2 next to the trunk. Around that decision sit the `>protein\|name=…` header syntax for the multi-entity FASTA, the `run_inference` arguments, the ranked `pred.model_idx_*.cif` files and their `scores.*.npz`, and why an unset `CHAI_DOWNLOADS_DIR` either re-pulls 5 GB on every cold start or dies mid-run with a `PermissionError`. Ends on running Chai-1 against a second model for consensus, plus licensing. |
