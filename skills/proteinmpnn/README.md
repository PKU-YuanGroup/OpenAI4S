# ProteinMPNN Skill

ProteinMPNN designs sequence from backbone geometry and nothing else. Every ligand, nucleic acid and metal in the input PDB is invisible to it, which is why `ligandmpnn` exists; `solublempnn` keeps the same architecture but swaps in weights trained on soluble structures. So this is the right inverse-folding step for a protein-protein design surface, and the wrong one the moment a cofactor joins the interface. What is here is a runbook for the external repository: how to clone and drive it, which flags actually matter, and how to read the FASTA that comes back. The repo is cloned in-job — there is no PyPI dist, and the checkpoints ride inside the clone.

What comes out is a sequence and a likelihood. The `score=` in each FASTA header is a mean negative log-likelihood — how ProteinMPNN-like the sequence looks given that backbone — and `seq_recovery=` is agreement with the sequence the input already had. Neither says the design folds, expresses, or binds; that needs a fold model first and a bench after. Small runs are fine on CPU; real runtime, and whether a GPU buys you anything, depend on the size of the campaign and the environment it runs in.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install proteinmpnn --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name; `--dry-run` prints the resolved absolute path and writes
nothing. A reinstall refuses to overwrite a copy you have edited, and
`uninstall` removes only the files it wrote. The same command under the
package's published name, which is not on npm yet, is
`npx openai4s-skills install proteinmpnn`.

Without Node, take the directory itself — and turn it into a `.zip` if an
upload field wants one:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/proteinmpnn
python3 -m zipfile -c proteinmpnn.zip proteinmpnn
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip):
unzip it and copy `skills/proteinmpnn/` out. If you already run OpenAI4S there
is nothing to install — the wheel ships every bundled Skill, and a bundled
Skill takes precedence over a same-named copy in `<data_dir>/user-skills`.
Targets, provenance, and what the installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | The runbook, built around the two things that trip people first. `--sampling_temp` and `--pdb_path_chains` are space-separated strings inside one quoted argument, and a comma does not split them. And a `--fixed_positions_jsonl` file missing its outer PDB-stem key is read as "no PDB matched": every position gets redesigned, with no warning — the bundled helper script writes the right shape. Around those: the noise-level checkpoints (`v_48_002` for close-to-native redesign through `v_48_030` for rough backbones), when CPU is enough, and why the output is FASTA only — ProteinMPNN never threads the designed sequence back onto the backbone, which is one reason to run even protein-only jobs through the `ligandmpnn` runner. |
