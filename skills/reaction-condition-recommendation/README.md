# Reaction Condition Recommendation Skill

The Parrot recipe for ranked condition hypotheses on a fully specified
reaction, with checkpoint-specific temperature support and a strict boundary
between model output and literature/ELN evidence.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and `git` on `PATH` (npm resolves a
`github:` spec through it), and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install reaction-condition-recommendation --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name. The resolved absolute path is printed before anything is
written there, and `--dry-run` stops at that plan. A reinstall refuses to
overwrite a copy you have edited or one it did not install, and `uninstall`
removes only the files it wrote. For a recipe included in the npm release,
`npx @pku-yuangroup/openai4s-skills install reaction-condition-recommendation --target claude`
installs the published copy. The npm catalog can differ from this repository.

Without Node, take the directory itself — and turn it into a `.zip` if an upload
field wants one. The tarball is the whole repository (over 100 MB), and the pipe
is a POSIX shell recipe (macOS, Linux, WSL) that extracts only this directory:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/reaction-condition-recommendation
python3 -m zipfile -c reaction-condition-recommendation.zip reaction-condition-recommendation
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip),
which is also the route to take on Windows: extract it and copy
`skills/reaction-condition-recommendation/` out
(`unzip main.zip 'OpenAI4S-main/skills/reaction-condition-recommendation/*'`
unpacks only that directory). If you already run OpenAI4S there is nothing to
install — the wheel ships every bundled Skill, and a bundled Skill takes
precedence over a same-named copy in `<data_dir>/user-skills`. Targets,
provenance, and what the installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Isolated setup, official CLI, interpretation rules, evidence states, output contract, and failure modes. |
| [`README.md`](README.md) | English directory index. |
| [`README_zh.md`](README_zh.md) | Chinese directory index. |
