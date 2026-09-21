# Target Druggability Screening Skill

[中文说明](README_zh.md)

End-to-end biological target druggability assessment and lead candidate screening.
Combines protein-protein interaction (PPI) networks from STRING with quantitative
pharmacological binding affinities ($K_i, K_d, IC_{50}, EC_{50}$) from BindingDB,
and evaluates small-molecule drug-likeness using Lipinski's Rule of 5. The result is
a reproducible Markdown dossier compiled directly into session artifacts.

This recipe uses pure Python standard library utilities and native OpenAI4S science
connectors. It does not require external chemical modeling servers or GPU resources.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and `git` on `PATH` (npm resolves a
`github:` spec through it), and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install target_druggability_screening --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name. The resolved absolute path is printed before anything is
written there, and `--dry-run` stops at that plan. A reinstall refuses to
overwrite a copy you have edited or one it did not install, and `uninstall`
removes only the files it wrote. For a recipe included in the npm release,
`npx @pku-yuangroup/openai4s-skills install target_druggability_screening --target claude`
installs the published copy. The npm catalog can differ from this repository.

Without Node, take the directory itself — and turn it into a `.zip` if an upload
field wants one. The tarball is the whole repository (over 100 MB), and the pipe
is a POSIX shell recipe (macOS, Linux, WSL) that extracts only this directory:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/target_druggability_screening
python3 -m zipfile -c target_druggability_screening.zip target_druggability_screening
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip),
which is also the route to take on Windows: extract it and copy
`skills/target_druggability_screening/` out
(`unzip main.zip 'OpenAI4S-main/skills/target_druggability_screening/*'` unpacks
only that directory). If you already run OpenAI4S there is nothing to install —
the wheel ships every bundled Skill, and a bundled Skill takes precedence over a
same-named copy in `<data_dir>/user-skills`. Targets, provenance, and what the
installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | The agent-facing recipe driving the four-phase workflow, interactive cell code, and output artifact schema. |
| [`kernel.py`](kernel.py) | Pure-stdlib sidecar providing SMILES descriptor estimation, Lipinski Rule of 5 evaluation, composite lead scoring, and Markdown dossier formatting. |
