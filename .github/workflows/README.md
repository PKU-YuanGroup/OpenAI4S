# GitHub workflows

[中文说明](README_zh.md)

Everything CI does to this repository is in these three files: the offline gate
every pull request has to pass, plus release publication and Scorecard. They run
against the code but are not shipped as part of the Python package.

Credential scanning lives in `ci.yml`'s source-credential-scan job
([`scripts/source_secret_scan.py`](../../scripts/source_secret_scan.py)), which
reads the working tree with named provider detectors.

A separate Gitleaks full-history scan used to sit alongside it and was retired.
Not because it was broken — #57 had just fixed it, replacing commit-SHA
fingerprints (which squash-only merging duplicates out from under you) with
anchored value allowlists that survive a rewrite.

It was retired because of the cost that fix could not touch. A generic entropy
rule over *all history* fires on synthetic fixtures, and a fixture that has to
look real in order to be found by the code under test is exactly the kind this
repository keeps needing. Each one becomes another allowlist row a reviewer
must argue for, and the list only grows: #57 curated two values, and #63 added
a third within the day — for a string the working tree already suppressed
inline, because an inline `gitleaks:allow` cannot cover the commit that
introduced the line before the comment existed. Each of those suppressions is
correct, and none of them is free.

The detectors that remain are named rather than entropy-based, so they need no
allowlist to stay quiet on placeholders while still catching a real key in the
same file. What is given up: a credential committed and later removed is no
longer flagged. If that matters again, run gitleaks over history once by hand
rather than reinstating a scheduled job with a list to feed.

## Files

| File | Purpose |
| --- | --- |
| `ci.yml` | The default offline gate. Checks branch naming, runs pre-commit, verifies bilingual per-directory documentation coverage, type-checks the core orchestration boundary, scans tracked sources for credentials, builds the wheel and the sdist and checks what is inside both, then installs the wheel alone into a clean venv and exercises the CLI it puts there, runs the offline suite on Python 3.10 and 3.12 alongside the deterministic harness contracts, and drives the real workbench in Chromium. The macOS job that requires enforced Seatbelt isolation runs only on the schedule or on manual dispatch. |
| `release.yml` | Fires when a non-prerelease `v*` GitHub Release is published. Builds the distributions from the tag, matches the tag against both version declarations, rescans the sources, and publishes to PyPI through OIDC from the `pypi` environment. |
| `scorecard.yml` | Runs OpenSSF Scorecard on pushes to `main` and weekly, publishes the results, and uploads the SARIF to code scanning. |

The default test suite must remain offline. Live providers, GPU, SSH, package
publication, and credentials stay in separately authorized paths.
