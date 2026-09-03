# Single-cell RNA Analysis Skill

An OpenAI4S-maintained workflow for human or mouse, cell-called 10x GEX
scRNA-seq and snRNA-seq matrices. It provides a versioned configuration
contract, an executable Scanpy pipeline for either single-sample descriptive or
donor-aware comparative analysis, conservative scientific gates, restartable
checkpoints, and an auditable output bundle. It does not modify or vendor the
pinned `bioSkills` collection.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install single-cell-rna-analysis --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name; `--dry-run` prints the resolved absolute path and writes
nothing. A reinstall refuses to overwrite a copy you have edited, and
`uninstall` removes only the files it wrote. The same command under the
package's published name, which is not on npm yet, is
`npx openai4s-skills install single-cell-rna-analysis`.

Without Node, take the directory itself — and turn it into a `.zip` if an
upload field wants one:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/single-cell-rna-analysis
python3 -m zipfile -c single-cell-rna-analysis.zip single-cell-rna-analysis
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip):
unzip it and copy `skills/single-cell-rna-analysis/` out. If you already run
OpenAI4S there is nothing to install — the wheel ships every bundled Skill, and
a bundled Skill takes precedence over a same-named copy in
`<data_dir>/user-skills`. Targets, provenance, and what the installer refuses
to do: [`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| Path | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Short agent entry point: scope, public calls, stage routing, failure behavior, Artifact handoff, and interpretation boundaries. |
| [`kernel.py`](kernel.py) | Lazy-imported implementation of `preflight(config)`, `run(config, output_dir)`, and `resume(run_dir)`. |
| [`references/`](references/) | Detailed input, scientific, annotation, statistical, and output contracts, with its own bilingual directory documentation. |

The workflow is evidence preserving: raw counts remain isolated in
`layers["counts"]`, Harmony changes an embedding only, cluster markers never
stand in for condition DE, and unconfirmed labels may remain `Unknown`.
