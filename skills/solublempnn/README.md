# SolubleMPNN Skill

SolubleMPNN is not a package of its own. It is ProteinMPNN retrained on a soluble-PDB subset; the weights ship in the external ProteinMPNN repository and are also exposed by LigandMPNN. This progressive-disclosure recipe is about selecting that prior and running it; no runtime is bundled here.

The soluble prior costs a few points of native recovery to buy its surface bias, and that drop is the prior working rather than a bug. But the weights were trained on structures soluble enough to crystallise, which is not the same sentence as expresses solubly in E. coli at 37 °C. A SolubleMPNN sequence is therefore a better bet than a vanilla one, not a solved expression problem. It still has to fold, express, stay out of the inclusion bodies, and do the job it was designed for, and only the bench settles the last three.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install solublempnn --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name; `--dry-run` prints the resolved absolute path and writes
nothing. A reinstall refuses to overwrite a copy you have edited, and
`uninstall` removes only the files it wrote. The same command under the
package's published name, which is not on npm yet, is
`npx openai4s-skills install solublempnn`.

Without Node, take the directory itself — and turn it into a `.zip` if an
upload field wants one:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/solublempnn
python3 -m zipfile -c solublempnn.zip solublempnn
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip):
unzip it and copy `skills/solublempnn/` out. If you already run OpenAI4S there
is nothing to install — the wheel ships every bundled Skill, and a bundled
Skill takes precedence over a same-named copy in `<data_dir>/user-skills`.
Targets, provenance, and what the installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Sets up the repository and shows both ways to reach the soluble weights: `--use_soluble_model` on the ProteinMPNN runner, `--model_type soluble_mpnn` on LigandMPNN's, which also threads designs back onto the backbone. Two hard edges get their own treatment. The repo ships soluble checkpoints at `v_48_010` and `v_48_020` only, so `--model_name v_48_002 --use_soluble_model` dies on a missing file — leave `--model_name` at its default. And a surface patch that keeps coming back hydrophobic is not the prior failing: that patch is probably load-bearing, and forcing it polar with `--omit_AAs` needs a re-fold to check the constraint was free. Around those: why the `cd` into the clone is load-bearing, why recovery against a native sequence drops a few points, and why a training set of crystallisable structures is not a promise about your expression host. |
