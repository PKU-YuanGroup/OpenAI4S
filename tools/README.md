# Tools

[中文说明](README_zh.md)

Developer- and user-facing tooling that is not part of the Python package.

Everything under `openai4s/` ships in the wheel and is imported at runtime.
What lives here does not: it is delivered through a different channel — today,
npm — and is deliberately kept out of `[tool.setuptools.packages.find]` so a
`pip install openai4s` carries no JavaScript.

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`skills-installer/`](skills-installer/) | The `openai4s-skills` command behind `npx openai4s-skills`. It copies the bundled Skill library out of this repository and into Claude Code, an OpenAI4S data directory, or any directory the user names — with a manifest that makes uninstall exact and overwrite refuse by default. |

## Where this fits

The repository root's `package.json` declares the npm package: its `bin` points
at `skills-installer/cli.mjs`, and its `files` list ships that directory
alongside `skills/`. Placing the manifest at the root is what makes
`npx github:PKU-YuanGroup/OpenAI4S` work without anything being published
first, which is the shortest path from "our address" to a working install.

Nothing here is imported by the daemon, the kernel, or any test in `tests/`.
The installer's own gate is `node tools/skills-installer/selftest.mjs`, run as
its own CI job.
