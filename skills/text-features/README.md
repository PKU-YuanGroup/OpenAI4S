# Text Features Skill

Calibrated numeric features from free text, for a supervised target, with a
frozen test split. The main model proposes questions; TypeSafe Jev answers
every selected row; this sidecar fits and evaluates. Features keep their
source question and measurement error. They are not a human gold standard.
Selected row text is sent to a US-hosted service when the experimental
capability is on. Loading the Skill may attach [`kernel.py`](kernel.py) to
the persistent Python kernel.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and `git` on `PATH` (npm resolves a
`github:` spec through it), and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install text-features --target claude
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
  | tar -xz --strip-components=2 OpenAI4S-main/skills/text-features
python3 -m zipfile -c text-features.zip text-features
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip),
which is also the route to take on Windows: extract it and copy
`skills/text-features/` out
(`unzip main.zip 'OpenAI4S-main/skills/text-features/*'` unpacks only that
directory). If you already run OpenAI4S there is nothing to install — the wheel
ships every bundled Skill, and a bundled Skill takes precedence over a
same-named copy in `<data_dir>/user-skills`. Targets, provenance, and what the
installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | When the skill fits (lots of free text, a supervised target) and when it does not (small samples, no label, sensitive data); how to turn on `text_features`; that row text leaves the machine; that Jev features are not a human gold standard; and how to call `propose_questions`, `featurize`, and `run_feature_study`. |
| [`kernel.py`](kernel.py) | Optional sidecar. `propose_questions` asks `host.llm` for Noul/Score questions and validates them. `featurize` calls `host.judge("features.custom")` per row: one P(yes) column per Noul, expected level and spread per Score, NaN on unavailable rows. `run_feature_study` reuses `audit-dataset`, `plan-ml-experiment`, and `evaluate-model`, edits questions on the development split only, and scores the test split once. numpy/pandas/sklearn are optional lazy imports. |
