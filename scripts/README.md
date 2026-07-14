# Maintainer and release scripts

[简体中文](README_zh.md)

These scripts support setup, release validation, security checks, documentation
coverage, contributor rendering, and opt-in scientific operations. They are not
native Agent tools and are not imported into the normal daemon loop.

## Files

| File | Purpose |
|---|---|
| `build_macos_dmg.sh` | Builds an ad-hoc-signed macOS app/DMG with relocatable CPython, source, resources, and the optional science stack. |
| `check_directory_readmes.py` | Verifies that every maintained directory has structurally paired bilingual READMEs covering each direct file and child directory, with resolvable local Markdown links. |
| `fold_remote.sh` | Opt-in Protenix single-sequence folding wrapper for a pre-provisioned trusted GPU host; emits structured fold artifacts. |
| `release_import_smoke.py` | Imports an installed dependency-free wheel outside the checkout and checks packaged runtime resources. |
| `setup_envs.sh` | Thin shell wrapper around `python -m openai4s setup` for the four conda environments. |
| `source_secret_scan.py` | Dependency-free, fail-closed scan for credential-shaped material without echoing matched secrets. |
| `update_contributors.py` | Fetches GitHub contributors, generates circular PNG avatars, and updates bilingual README blocks. |
| `verify_release_artifacts.py` | Validates wheel/sdist paths, metadata, permissions, and required packaged resources. |
| `verify_release_tag.py` | Ensures a release tag matches all literal package version declarations. |

## Framework relationship

Release and security scripts validate the control plane from outside it.
`fold_remote.sh` is deliberately not a general deployment guarantee: the
registered remote-science services still perform capability checks and must
return explicit errors when the required remote installation is unavailable.
