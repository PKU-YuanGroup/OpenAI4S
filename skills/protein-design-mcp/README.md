# Protein-design MCP Skill

[中文说明](README_zh.md)

This Skill teaches the agent to compose the bundled protein-design MCP tools
for general protein design and redesign work. It covers target-conditioned
binder backbones, constrained sequence design, monomer and complex prediction,
physical scoring and relaxation, sequence-naturalness scoring, minimization,
and reproducible candidate comparison.

It also states the current scientific boundary explicitly: the RFdiffusion
tool requires target hotspots and does not yet express epitope-free,
motif-scaffolding, unconditional or membrane-aware backbone generation.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and `git` on `PATH` (npm resolves a
`github:` spec through it), and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install protein-design-mcp --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name. The resolved absolute path is printed before anything is
written there, and `--dry-run` stops at that plan. A reinstall refuses to
overwrite a copy you have edited or one it did not install, and `uninstall`
removes only the files it wrote. Once the package is on npm,
`npx openai4s-skills install protein-design-mcp --target claude` is the short
form of the same command.

Without Node, take the directory itself — and turn it into a `.zip` if an upload
field wants one. The tarball is the whole repository (over 100 MB), and the pipe
is a POSIX shell recipe (macOS, Linux, WSL) that extracts only this directory:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/protein-design-mcp
python3 -m zipfile -c protein-design-mcp.zip protein-design-mcp
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip),
which is also the route to take on Windows: extract it and copy
`skills/protein-design-mcp/` out
(`unzip main.zip 'OpenAI4S-main/skills/protein-design-mcp/*'` unpacks only that
directory). If you already run OpenAI4S there is nothing to install — the wheel
ships every bundled Skill, and a bundled Skill takes precedence over a
same-named copy in `<data_dir>/user-skills`. Targets, provenance, and what the
installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | General tool-selection workflows, reproducibility controls, current capability gaps and model-evidence boundaries. |
| [`README.md`](README.md) | English directory boundary and inventory. |
| [`README_zh.md`](README_zh.md) | Chinese directory boundary and inventory. |

The model packages, weights and GPU environments are not vendored by this
Skill. Configure the connector and its external backends separately.
