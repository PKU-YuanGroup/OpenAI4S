# Tools

[中文说明](README_zh.md)

Developer- and user-facing tooling that is not part of the Python package.

Everything under `openai4s/` ships in the wheel and is imported at runtime.
What lives here does not: it is delivered through npm or `npx` directly from
this repository, and is deliberately kept out of
`[tool.setuptools.packages.find]` so a
`pip install openai4s` carries no JavaScript.

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`skills-installer/`](skills-installer/) | The `openai4s-skills` command — use `npx @pku-yuangroup/openai4s-skills@0.2.0 <command>` for the fixed npm release (603 Skills: 42 curated + 561 bioSkills), `npx @pku-yuangroup/openai4s-skills <command>` for the latest npm release, or `npx github:PKU-YuanGroup/OpenAI4S <command>` for the current repository catalog (604: 43 + 561). It copies the selected Skill library into Claude Code, an OpenAI4S data directory, or any directory the user names — with a manifest that makes uninstall exact and overwrite refuse by default. |

## Where this fits

The repository root's `package.json` declares the npm package: its `bin` points
at `skills-installer/cli.mjs`, and its `files` list ships that directory
alongside `skills/`. Placing the manifest at the root is what makes
`npx github:PKU-YuanGroup/OpenAI4S` run directly from the default branch,
independently of the version published on npm.

Nothing here is imported by the daemon, the kernel, or any Python test — the
language boundary makes that impossible in both directions. The installer's own
gates are `node tools/skills-installer/selftest.mjs` and
`node tools/skills-installer/check_package.mjs`, each its own CI job so that a
red one cannot hide the other: "the installer behaves" and "the published
package contains anything to install" are different questions. The half of the
contract that lives on the Python side — the shape of
`skills/` and the `package.json` manifest the published package is cut from —
is asserted in `tests/test_skills_installer_contract.py`, which deliberately
never shells out to `node`: a test that skips on a machine without Node reports
success for the wrong reason.
