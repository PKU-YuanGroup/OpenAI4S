# IS survey measurement

[中文](README_zh.md)

Information Systems survey analysis with explicit construct/item definitions,
reverse coding, missing-data decisions, ordinal CFA and evidence limits for
reliability and group comparisons. It reuses existing data-audit and literature
skills. It is a measurement recipe, not a validated questionnaire or causal model.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and `git` on `PATH` (npm resolves a
`github:` spec through it), and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install is-survey-measurement --target claude
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
  | tar -xz --strip-components=2 OpenAI4S-main/skills/is-survey-measurement
python3 -m zipfile -c is-survey-measurement.zip is-survey-measurement
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip),
which is also the route to take on Windows: extract it and copy
`skills/is-survey-measurement/` out
(`unzip main.zip 'OpenAI4S-main/skills/is-survey-measurement/*'` unpacks only
that directory). If you already run OpenAI4S there is nothing to install — the
wheel ships every bundled Skill, and a bundled Skill takes precedence over a
same-named copy in `<data_dir>/user-skills`. Targets, provenance, and what the
installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Runtime and scope

Scoring uses the Python standard library. The analysis example uses the independent
OpenAI4S R kernel and the optional `lavaan` package; ordinal score reliability
optionally uses `semTools`. Missing R/packages are reported, not installed or
replaced by invented output. The analysis has no network access requirements and
adds no core dependency. No `kernel.py` sidecar or external MCP is required.

## Attribution and adaptation

Adapted from AlterLab-IEU/AlterLab-Academic-Skills at the exact commit recorded in
`UPSTREAM.json`. Its individual SEM/psychometrics skill declares MIT. The retained
`LICENSE` is the upstream notice; `origin: openai4s` marks bundled distribution,
not original authorship. General fit cutoffs and loading-only omega shortcuts are
qualified by model assumptions. Claude-specific tools and unavailable sibling
skills are replaced with OpenAI4S reading, local files and existing skills.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Scope, item audit/scoring recipe, optional ordinal CFA, artifact and interpretation requirements. |
| [`method-notes.md`](method-notes.md) | Fit, model-based reliability, ordinal versus continuous invariance and unsupported comparisons. |
| [`UPSTREAM.json`](UPSTREAM.json) | Pinned repository, source SHA-256 hashes, license evidence and adaptation mapping. |
| [`LICENSE`](LICENSE) | Unmodified MIT notice from the upstream repository. |
| [`NOTICE.md`](NOTICE.md) | Upstream attribution, adaptation scope and inherited MIT notice. |
