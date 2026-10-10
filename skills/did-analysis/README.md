# DiD Analysis

[中文说明](README_zh.md)

Analyze researcher-defined panel interventions in a fixed order: traditional
DiD, dynamic event study, fake-time/fake-group placebo checks, then applicable
staggered group-time ATT or common-time triple differences. Keep treatment-source
declarations, original observations, assignment clusters and all requested results.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and `git` on `PATH` (npm resolves a
`github:` spec through it), and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install did-analysis --target claude
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
  | tar -xz --strip-components=2 OpenAI4S-main/skills/did-analysis
python3 -m zipfile -c did-analysis.zip did-analysis
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip),
which is also the route to take on Windows: extract it and copy
`skills/did-analysis/` out
(`unzip main.zip 'OpenAI4S-main/skills/did-analysis/*'` unpacks only that
directory). If you already run OpenAI4S there is nothing to install — the wheel
ships every bundled Skill, and a bundled Skill takes precedence over a
same-named copy in `<data_dir>/user-skills`. Targets, provenance, and what the
installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Usage and scope

Load `did-analysis` through the Skill loader and import its `kernel` sidecar as
shown in `SKILL.md`. Estimation is pure standard library; optional figures require
matplotlib in the scientific environment. Input is a long panel with explicit
integer periods and observed exposure (or a fixed adoption cohort for staggered
analysis). Default incomplete-sample handling refuses estimation; an explicit
complete-case choice records excluded entities. Filled outcomes are excluded.

Traditional DiD uses per-entity pre/post means with cluster CR1 Student-t inference.
Event study retains joint covariance. Fake dates cannot include genuine treatment,
and fake groups are whole clusters selected only from never-treated controls.
Random fake-group distributions are descriptive diagnostics. Staggered estimates
are unconditional group-time contrasts with explicit comparison membership and
aggregation weights. DDD is restricted to unconditional common-time 2×2×2 designs.
Conditional/DR estimation, simultaneous bands and general staggered DDD require
additional justified estimators; the sidecar does not claim those capabilities.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Researcher intake, ordered executable analysis recipes, placebo rules, derivative selection and interpretation boundaries. |
| [`kernel.py`](kernel.py) | Audited stdlib estimation, cluster inference, dynamic/placebo/derivative results, provenance and deterministic exports; optional diagnostic figures. |
| [`method-notes.md`](method-notes.md) | Formulas, estimands, uncertainty conventions, comparison/weight rules and primary methodological sources. |

Development is recorded in [`doc/skill-development.md`](../../doc/skill-development.md).
The independent experiment is [`experiments/did-skill-validation/`](../../experiments/did-skill-validation/README.md).
These checks validate behavior and numerical calculations, not a real study's
identifying assumptions.
