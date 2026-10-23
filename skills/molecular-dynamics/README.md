# Molecular Dynamics Skill

Molecular dynamics workflows with OpenMM and MDAnalysis: prepare protein and small-molecule systems, minimize and equilibrate solvated systems, run staged NVT/NPT simulations, and analyze trajectories with RMSD, RMSF, contacts, PCA, hydrogen bonds, and relative free-energy surfaces. The recipe keeps force-field choices, periodic geometry, sampling limits, and provenance explicit; a stable trajectory is not by itself evidence of binding free energy or residence time.

The upstream recipe was imported from [K-Dense-AI/scientific-agent-skills](https://github.com/K-Dense-AI/scientific-agent-skills/tree/main/skills/molecular-dynamics) at revision `a1043ef544e3a100c28e70a736b6cf32554d0677` (2026-10-16). The bundled references are reviewed for OpenMM 8.6.1 and MDAnalysis 2.10.0; installation, force-field assets, GPU support, and optional PDBFixer/OpenFF dependencies remain separate.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and `git` on `PATH` (npm resolves a
`github:` spec through it), and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install molecular-dynamics --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name. The resolved absolute path is printed before anything is
written there, and `--dry-run` stops at that plan. A reinstall refuses to
overwrite a copy you have edited or one it did not install, and `uninstall`
removes only the files it wrote. npm 0.2.0 does not include this recipe. Use the
GitHub installation command above.

Without Node, take the directory itself — and turn it into a `.zip` if an upload
field wants one. The tarball is the whole repository (over 100 MB), and the pipe
is a POSIX shell recipe (macOS, Linux, WSL) that extracts only this directory:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/molecular-dynamics
python3 -m zipfile -c molecular-dynamics.zip molecular-dynamics
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip),
which is also the route to take on Windows: extract it and copy
`skills/molecular-dynamics/` out
(`unzip main.zip 'OpenAI4S-main/skills/molecular-dynamics/*'` unpacks only that
directory). If you already run OpenAI4S there is nothing to install — the wheel
ships every bundled Skill, and a bundled Skill takes precedence over a
same-named copy in `<data_dir>/user-skills`. Targets, provenance, and what the
installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Offline recipe checks

From the repository root, after creating the dedicated environment described in
`SKILL.md`, run:

```bash
uv pip install --python .venv-md/bin/python pytest
.venv-md/bin/python -m pytest tests/test_molecular_dynamics_skill.py
```

These tests execute the documented code with OpenMM 8.6.1 and MDAnalysis 2.10.0
on small synthetic fixtures: minimization, NVT/NPT stages, log-derived trajectory
timestamps, RMSF indexing, and periodic contacts including invalid geometry.
They use the Reference platform and no network or GPU. The default offline suite
checks syntax and skips execution when the optional packages are absent. This is
API/geometry evidence, not validation of a protein-ligand model or convergence.

## Files

| File | Responsibility |
| --- | --- |
| [`LICENSE.md`](LICENSE.md) | The upstream MIT license for this imported Skill. |
| [`SKILL.md`](SKILL.md) | The OpenMM simulation runbook and MDAnalysis trajectory-analysis workflow, including preparation, staged equilibration, validation, periodic geometry, and provenance limits. |

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`references/`](references/) | Read on demand for MDAnalysis analysis patterns and conservative system-preparation and force-field guidance. |
