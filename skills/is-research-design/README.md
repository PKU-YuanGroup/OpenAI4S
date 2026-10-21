# Information Systems Research Design Skill

Connect an IT-specific mechanism or design principle to a defensible study.
This adaptation combines the theory-development and methods recipes from
Bryce Wang's Awesome-Journal-Skills. It supplies a local planning workflow and
an unfilled claim–evidence worksheet; it runs no estimator and requires no
additional Python package.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and `git` on `PATH` (npm resolves a
`github:` spec through it), and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install is-research-design --target claude
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
  | tar -xz --strip-components=2 OpenAI4S-main/skills/is-research-design
python3 -m zipfile -c is-research-design.zip is-research-design
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip),
which is also the route to take on Windows: extract it and copy
`skills/is-research-design/` out
(`unzip main.zip 'OpenAI4S-main/skills/is-research-design/*'` unpacks only that
directory). If you already run OpenAI4S there is nothing to install — the wheel
ships every bundled Skill, and a bundled Skill takes precedence over a
same-named copy in `<data_dir>/user-skills`. Targets, provenance, and what the
installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Scope and reuse

Use it for behavioral, economics-of-IS, organizational or design-science
questions. Literature retrieval, panel preparation and DiD execution stay in
the existing `literature-review`, `panel-data-preprocessing` and `did-analysis`
skills. ML evaluation uses the existing experiment and evaluation skills.
The methodology checklist is guidance, not an enforced completion gate.

## Source and license

The source revision and original file hashes are recorded in
[`UPSTREAM.json`](UPSTREAM.json); [`LICENSE`](LICENSE) retains Bryce Wang's
MIT notice unchanged. The adaptation removes upstream journal page-budget
rules and unavailable StatsPAI/Stata MCP calls, adds OpenAI4S routing and an
honest empty worksheet, and merges overlapping theory/design guidance.
`origin: openai4s` means bundled distribution, not original authorship.
This is community-maintained guidance, not an official MIS Quarterly skill.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Discovery, study-design workflow, existing-skill routing and a runnable artifact example. |
| [`design-template.json`](design-template.json) | Unfilled design and claim–evidence worksheet with explicit missing evidence. |
| [`UPSTREAM.json`](UPSTREAM.json) | Pinned upstream revision, source hashes and adaptation record. |
| [`LICENSE`](LICENSE) | Byte-for-byte upstream MIT license. |

The recipe reads resources with `host.skills.read` and writes its draft with
`host.write_file`. Saving a worksheet does not validate a theory, preregister
a study or establish a causal effect.
