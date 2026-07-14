# Maintainer and release scripts

[中文说明](README_zh.md)

Maintainer-facing scripts: environment setup, release validation, secret
scanning, directory-documentation coverage, the contributor wall, and one
opt-in scientific operation. None of them is a native Agent tool, and the
normal daemon loop never imports them.

## Files

| File | Purpose |
| --- | --- |
| `build_macos_dmg.sh` | Packages a macOS `.app` and `.dmg`. The kernel spawns its worker through `sys.executable`, so freezing the app would break it; instead the bundle embeds a relocatable standalone CPython, ships the source tree as loose `.py` files, and pre-bakes the CORE science stack into the runtime so the first launch needs no network. The signature is ad-hoc only, with no Apple Developer credentials. |
| `check_directory_readmes.py` | The CI check that this file has to pass. Every maintained directory needs a `README.md` and a `README_zh.md` with the same heading sequence and the same table-row count, a backticked mention of each direct file and child directory, and relative links that actually resolve on disk. |
| `fold_remote.sh` | Protenix single-sequence folding on a pre-provisioned trusted GPU host, offline and without MSA. Writes `model.pdb`, `model.cif`, `confidence.json` and `plddt.csv`, then prints a one-line JSON manifest and the deliverables base64-encoded on stdout, so the caller can harvest everything from the log. Opt-in. |
| `release_import_smoke.py` | Imports the installed dependency-free wheel with the isolated environment's interpreter from outside the checkout, and refuses to pass if the import resolved back to the source tree. It then checks what a plain import test misses: the packaged R worker, the compute templates and the Web UI, the four environment specs, the Skill catalog, a working `python -m openai4s --help`, and a core that still declares no non-extra dependencies. |
| `setup_envs.sh` | A thin `sh` wrapper that execs `python -m openai4s setup` for the four conda environments. It forwards its arguments, so `--only python` and `--dry-run` work through it. |
| `source_secret_scan.py` | Scans the release source tree for credential-shaped material and fails closed. It prints the detector name, path and line number, never the matched value. Dependency-free: git selects the candidate files, and a deterministic filesystem walk takes over where git is unavailable, such as an unpacked source archive. |
| `update_contributors.py` | Rebuilds the Community Contributors wall. Fetches contributors from the GitHub API with the repository's own token, crops each avatar into a circular PNG under `.github/contributors/`, and rewrites the block between the `CONTRIBUTORS` markers in both root READMEs. Requires Pillow. |
| `verify_release_artifacts.py` | Stdlib-only inspection of a built wheel and sdist. Checks that the required packaged files are present and that nothing unsafe rode along (symlinks, bytecode, caches, `.env` files), then reads the wheel metadata for the MIT license, the four Project-URL entries, the `openai4s` console entry point, the platform-independent `py3-none-any` tag, the absence of the test suite, and the absence of core dependencies. |
| `verify_release_tag.py` | Fails closed unless a `vMAJOR.MINOR.PATCH` tag matches both literal version declarations: `[project] version` in `pyproject.toml` and `openai4s.__version__`. |

## Where this fits

The release and security scripts check the control plane from outside it; none
of them is part of it. `fold_remote.sh` is deliberately not a general
deployment guarantee either. The registered remote-science services still run
their own capability checks, and they must return an explicit error when the
required remote installation is unavailable.
