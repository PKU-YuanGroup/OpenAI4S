# GitHub workflows

[中文说明](README_zh.md)

Everything CI does to this repository is in these four files: the offline gate
every pull request has to pass, plus release publication, secret scanning, and
Scorecard. They run against the code but are not
shipped as part of the Python package.

## Files

| File | Purpose |
| --- | --- |
| `ci.yml` | The default offline gate. Checks branch naming, runs pre-commit, verifies bilingual per-directory documentation coverage, type-checks the core orchestration boundary, scans tracked sources for credentials, builds the wheel and the sdist and checks what is inside both, then installs the wheel alone into a clean venv and exercises the CLI it puts there, runs the offline suite on Python 3.10 and 3.12 alongside the deterministic harness contracts, and drives the real workbench in Chromium. The macOS job that requires enforced Seatbelt isolation runs only on the schedule or on manual dispatch. |
| `release.yml` | Fires when a non-prerelease `v*` GitHub Release is published. Builds the distributions from the tag, matches the tag against both version declarations, rescans the sources, and publishes to PyPI through OIDC from the `pypi` environment. |
| `scorecard.yml` | Runs OpenSSF Scorecard on pushes to `main` and weekly, publishes the results, and uploads the SARIF to code scanning. |
| `secret-scan.yml` | Runs Gitleaks over every reachable commit in Git history, not just the diff, on pushes, pull requests, a weekly schedule, and manual dispatch. The binary is checksum-pinned and matches are redacted in the log. |

The default test suite must remain offline. Live providers, GPU, SSH, package
publication, and credentials stay in separately authorized paths.
