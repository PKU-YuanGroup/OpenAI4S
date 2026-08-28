# DiffDock Skill

DiffDock-L docks blind. It samples ligand placements across the whole protein surface with a diffusion model, without a search box or a declared pocket, then ranks the samples with a separately trained confidence head. Reach for it to dock a SMILES or an SDF against a PDB, or to get a starting pose for something that will rescore it. The DiffDock repository, the weights, receptor preparation and a GPU environment all have to be arranged separately; none of them are bundled here.

DiffDock confidence ranks pose plausibility, not binding affinity. A pose still needs chemistry checks and, usually, downstream scoring or refinement. Folding the receptor from sequence adds another layer of model-dependent uncertainty on top of that.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install diffdock --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name; `--dry-run` prints the resolved absolute path and writes
nothing. A reinstall refuses to overwrite a copy you have edited, and
`uninstall` removes only the files it wrote. The same command under the
package's published name, which is not on npm yet, is
`npx openai4s-skills install diffdock`.

Without Node, take the directory itself — and turn it into a `.zip` if an
upload field wants one:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/diffdock
python3 -m zipfile -c diffdock.zip diffdock
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip):
unzip it and copy `skills/diffdock/` out. If you already run OpenAI4S there is
nothing to install — the wheel ships every bundled Skill, and a bundled Skill
takes precedence over a same-named copy in `<data_dir>/user-skills`. Targets,
provenance, and what the installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | The runbook for the single-complex CLI path: how to pass SMILES, SDF and PDB inputs, what the ranked pose files and their confidence logits do and do not tell you, what hardware the run needs, and which failures are worth recognizing on sight. Geometry and affinity are kept apart throughout. |

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`references/`](references/) | Read on demand: batch and library docking, and the sequence-only receptor path. |
