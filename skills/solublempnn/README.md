# SolubleMPNN Skill

SolubleMPNN is not a package of its own. It is ProteinMPNN retrained on a soluble-PDB subset; the weights ship in the external ProteinMPNN repository and are also exposed by LigandMPNN. This progressive-disclosure recipe is about selecting that prior and running it; no runtime is bundled here.

The soluble prior costs a few points of native recovery to buy its surface bias, and that drop is the prior working rather than a bug. But the weights were trained on structures soluble enough to crystallise, which is not the same sentence as expresses solubly in E. coli at 37 °C. A SolubleMPNN sequence is therefore a better bet than a vanilla one, not a solved expression problem. It still has to fold, express, stay out of the inclusion bodies, and do the job it was designed for, and only the bench settles the last three.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Sets up the repository and shows both ways to reach the soluble weights: `--use_soluble_model` on the ProteinMPNN runner, `--model_type soluble_mpnn` on LigandMPNN's, which also threads designs back onto the backbone. Two hard edges get their own treatment. The repo ships soluble checkpoints at `v_48_010` and `v_48_020` only, so `--model_name v_48_002 --use_soluble_model` dies on a missing file — leave `--model_name` at its default. And a surface patch that keeps coming back hydrophobic is not the prior failing: that patch is probably load-bearing, and forcing it polar with `--omit_AAs` needs a re-fold to check the constraint was free. Around those: why the `cd` into the clone is load-bearing, why recovery against a native sequence drops a few points, and why a training set of crystallisable structures is not a promise about your expression host. |
