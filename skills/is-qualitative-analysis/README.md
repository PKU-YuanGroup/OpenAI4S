# IS qualitative analysis

An MIT-licensed adaptation of AlterLab qualitative-analysis guidance for
Information Systems interviews and case studies. It preserves evidence
locators, negative cases and researcher reflexivity, distinguishes coding
reliability from reflexive thematic analysis, and provides a small stdlib
nominal-agreement helper. No optional packages or network access are needed.

The helper reports Krippendorff alpha, two-coder Cohen kappa, observed pairwise
agreement and missingness. It does not compute confidence intervals, label
themes automatically, or establish the validity of a study. Unsupported designs
and undefined coefficients remain explicit.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and `git` on `PATH` (npm resolves a
`github:` spec through it), and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install is-qualitative-analysis --target claude
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
  | tar -xz --strip-components=2 OpenAI4S-main/skills/is-qualitative-analysis
python3 -m zipfile -c is-qualitative-analysis.zip is-qualitative-analysis
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip),
which is also the route to take on Windows: extract it and copy
`skills/is-qualitative-analysis/` out
(`unzip main.zip 'OpenAI4S-main/skills/is-qualitative-analysis/*'` unpacks only
that directory). If you already run OpenAI4S there is nothing to install — the
wheel ships every bundled Skill, and a bundled Skill takes precedence over a
same-named copy in `<data_dir>/user-skills`. Targets, provenance, and what the
installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Discovery, method choice, runnable Artifact-based coding recipe and reporting. |
| [`kernel.py`](kernel.py) | Validated nominal point estimates and missing-data audit, with no I/O. |
| [`analysis-guide.md`](analysis-guide.md) | Approach distinctions, source locators, case comparisons and negative cases. |
| [`agreement-guide.md`](agreement-guide.md) | Coefficient definitions, unsupported designs, uncertainty and human/model comparison. |
| [`UPSTREAM.json`](UPSTREAM.json) | Exact upstream commit, source SHA-256 hashes and adaptation mapping. |
| [`LICENSE`](LICENSE) | Exact MIT license from AlterLab. |
| [`NOTICE.md`](NOTICE.md) | Attribution, local changes and inherited MIT notice. |

## Provenance and scope

The source is `AlterLab-IEU/AlterLab-Academic-Skills`, commit
`e4836c08a20da195a11f30f203a8cf23ec30aa95`, specifically the MIT-licensed
`skills/social-science-workflow/alterlab-qualitative-analysis` module.
This is an adapted bundled skill; `origin: openai4s` identifies distribution,
not original authorship. The adaptation removes suite-only routing, corrects
constant-category kappa and input validation, and does not copy the upstream
noncommercial pipeline modules. See [`NOTICE.md`](NOTICE.md).
