# Single-cell RNA Analysis Skill

An OpenAI4S-maintained workflow for human or mouse, cell-called 10x GEX
scRNA-seq and snRNA-seq matrices. It provides a versioned configuration
contract, an executable Scanpy pipeline for either single-sample descriptive or
donor-aware comparative analysis, conservative scientific gates, restartable
checkpoints, and an auditable output bundle. It does not modify or vendor the
pinned `bioSkills` collection.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and `git` on `PATH` (npm resolves a
`github:` spec through it), and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install single-cell-rna-analysis --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name. The resolved absolute path is printed before anything is
written there, and `--dry-run` stops at that plan. A reinstall refuses to
overwrite a copy you have edited or one it did not install, and `uninstall`
removes only the files it wrote. Once the package is on npm,
`npx openai4s-skills install single-cell-rna-analysis --target claude` is the
short form of the same command.

Without Node, take the directory itself — and turn it into a `.zip` if an upload
field wants one. The tarball is the whole repository (over 100 MB), and the pipe
is a POSIX shell recipe (macOS, Linux, WSL) that extracts only this directory:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/single-cell-rna-analysis
python3 -m zipfile -c single-cell-rna-analysis.zip single-cell-rna-analysis
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip),
which is also the route to take on Windows: extract it and copy
`skills/single-cell-rna-analysis/` out
(`unzip main.zip 'OpenAI4S-main/skills/single-cell-rna-analysis/*'` unpacks only
that directory). If you already run OpenAI4S there is nothing to install — the
wheel ships every bundled Skill, and a bundled Skill takes precedence over a
same-named copy in `<data_dir>/user-skills`. Targets, provenance, and what the
installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| Path | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Short agent entry point: scope, public calls, stage routing, failure behavior, Artifact handoff, and interpretation boundaries. |
| [`kernel.py`](kernel.py) | Lazy-imported implementation of `preflight(config)`, `run(config, output_dir)`, and `resume(run_dir)`. |
| [`references/`](references/) | Detailed input, scientific, annotation, statistical, and output contracts, with its own bilingual directory documentation. |

The workflow is evidence preserving: raw counts remain isolated in
`layers["counts"]`, Harmony changes an embedding only, cluster markers never
stand in for condition DE, and unconfirmed labels may remain `Unknown`.
