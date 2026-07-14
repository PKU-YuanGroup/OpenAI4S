# GitHub workflows

[简体中文](README_zh.md)

These workflows form the repository validation and release control plane. They
exercise the code but are not shipped as part of the Python package.

## Files

| File | Purpose |
|---|---|
| `ci.yml` | Enforces branch naming, pre-commit, bilingual directory-documentation coverage, typed core boundaries, source secret scanning, wheel/sdist validation, offline Python 3.10/3.12 tests, browser smoke, and scheduled macOS sandbox smoke. |
| `contributors.yml` | Periodically regenerates contributor avatars and README contributor blocks. |
| `release.yml` | Builds verified distributions and publishes an approved GitHub Release to PyPI through OIDC. |
| `scorecard.yml` | Runs OpenSSF Scorecard analysis and publishes security findings. |
| `secret-scan.yml` | Runs Gitleaks against Git history on pushes, PRs, schedules, and manual dispatch. |

The default test suite must remain offline. Live providers, GPU, SSH, package
publication, and credentials stay in separately authorized paths.
