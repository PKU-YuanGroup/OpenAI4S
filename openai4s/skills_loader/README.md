# Skill loading and versioning

[中文](./README_zh.md)

**Status: Implemented.** This package discovers recipe-centric Skills, exposes only summaries until progressive disclosure is requested, validates optional Python sidecars structurally, and manages immutable user Skill versions with atomic materialized views.

## Architectural position

Skills are an extension plane for Code-as-Action, not native JSON tool schemas. A Skill directory contains `SKILL.md`, optionally `kernel.py`, and optional resources. The outer-loop prompt sees name/summary metadata; [`../tools/skills.py`](../tools/skills.py) and Host services load full recipes on demand. Agent-authored Python can then import a validated sidecar inside the scientific worker.

Bundled Skills are read-only and win name collisions. Writable user Skills live under configured data/project roots and are versioned through the Store. Capability state is resolved against the current Store generation rather than retained from a closed Store.

## Files directly in this directory

| File | Responsibility |
| --- | --- |
| [`__init__.py`](./__init__.py) | Documents the Skill directory contract and re-exports discovery, loader, value, and version-service APIs. |
| [`loader.py`](./loader.py) | Parses frontmatter, discovers bundled/user Skills, computes summaries/search matches, resolves capability state, exposes full recipes progressively, builds import metadata, and compile-checks `kernel.py` sidecars. |
| [`versions.py`](./versions.py) | Validates bounded, symlink-free Skill packages; stores immutable versions; materializes personal/project views off to the side; and activates or rolls back them with filesystem and database compare-and-swap recovery. |

## Direct subdirectories

None.

## Skill authoring and safety contract

- Treat `SKILL.md` as a recipe for generated code, not as an executable control-tool declaration.
- Compile-checking a sidecar proves only Python syntax/structure; normal kernel sandbox, permission, and import rules still apply at execution time.
- Reject unsafe paths, symlinks, oversized files/packages, and invalid canonical names before materialization.
- Keep bundled roots read-only and preserve their precedence over writable names.
