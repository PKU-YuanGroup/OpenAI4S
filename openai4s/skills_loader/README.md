# Skill loading and versioning

[中文说明](README_zh.md)

Skill discovery and Skill versioning live here. The loader finds recipe-centric Skills and gives the outer loop nothing but their summaries until progressive disclosure asks for the full text; the version service keeps writable Skill packages as immutable versions in the Store and swaps their on-disk views into place atomically. Optional Python sidecars are compile-checked before either path lets a kernel import one.

## Where this fits

A Skill extends Code-as-Action; it is not a native JSON tool schema. A Skill directory holds `SKILL.md`, an optional `kernel.py` sidecar, and optional resources. The outer-loop prompt only ever sees the name and the one-line summary; [`../tools/skills.py`](../tools/skills.py) and the Host services pull the full recipe when a task calls for it. Agent-authored Python then imports the compile-checked sidecar inside the scientific worker.

Bundled Skills are read-only and win name collisions. Writable Skills live under the configured data and project roots, and the Store versions them. The default loader holds no repository of its own: it asks the current Store generation on every capability call, because a loader can outlive the Store that created it and would otherwise be left pointing at a closed connection.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](./__init__.py) | States the Skill directory contract in its docstring and re-exports the public names: `Skill`, `SkillLoader`, `SkillVersionService`, and `discover_skills`. |
| [`loader.py`](./loader.py) | Finds the Skills and decides how much of one to reveal. It parses `SKILL.md` frontmatter, scans the bundled, project and user roots, resolves capability state, and scores searches by keyword overlap. The system prompt gets summaries; the full recipe, the sidecar import hint, and the in-kernel bootstrap manifest are produced on demand. A `kernel.py` sidecar is compile-checked before anything imports it. |
| [`versions.py`](./versions.py) | Installs, upgrades, publishes, rolls back and deletes writable Skills. A package is validated first (bounded size, no symlinks, no paths escaping the directory) and stored as an immutable version; the personal or project directory on disk is only a materialized view, rebuilt off to the side and swapped in. Activation in the database is compare-and-swap, and if that switch fails the previous directory is restored before the error escapes. |

## Skill authoring and safety contract

- `SKILL.md` is a recipe the agent writes code from. It is not an executable control-tool declaration.
- The compile gate proves that a sidecar parses. It proves nothing about what the sidecar does: the kernel sandbox, the Host permissions, and the normal import rules all still apply when it runs.
- Unsafe paths, symlinks, oversized files or packages, and invalid canonical names are rejected before anything is materialized.
- Bundled roots stay read-only, and a bundled name always takes precedence over a writable one.
