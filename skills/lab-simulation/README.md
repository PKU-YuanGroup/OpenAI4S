# Lab simulation Skill

[中文说明](README_zh.md)

A recipe for simulation-only experiments in the OpenAI4S Web daemon: discover
available devices, inspect capabilities, choose one bounded action, analyze
public observations, and report evidence tied to the exact run. Unknown
command outcomes are queried without resending. Completion is independently
checked by the Host; this recipe grants no permissions and controls no real
hardware. It has no Python sidecar or additional package dependency.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and `git` on `PATH` (npm resolves a
`github:` spec through it), and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install lab-simulation --target claude
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
  | tar -xz --strip-components=2 OpenAI4S-main/skills/lab-simulation
python3 -m zipfile -c lab-simulation.zip lab-simulation
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip),
which is also the route to take on Windows: extract it and copy
`skills/lab-simulation/` out
(`unzip main.zip 'OpenAI4S-main/skills/lab-simulation/*'` unpacks only that
directory). If you already run OpenAI4S there is nothing to install — the wheel
ships every bundled Skill, and a bundled Skill takes precedence over a
same-named copy in `<data_dir>/user-skills`. Targets, provenance, and what the
installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Capability discovery, one-action execution, unknown-outcome reconciliation, complete-array analysis, and evidence-based completion/reporting recipes. |
| [`README.md`](README.md) | English overview, installation instructions, and directory inventory. |
| [`README_zh.md`](README_zh.md) | Chinese overview, installation instructions, and directory inventory. |
