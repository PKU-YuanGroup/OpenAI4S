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
| `ci.yml` | The default offline gate, as independent jobs rather than steps. Checks branch naming, runs pre-commit, verifies bilingual per-directory documentation coverage, type-checks the core orchestration boundary, scans tracked sources for credentials, builds the wheel and the sdist and checks what is inside both, then installs the wheel alone into a clean venv and exercises the CLI it puts there, parses the shipped Windows launcher on a real Windows runner and proves it refuses — with guidance — where there is no WSL, and runs the offline suite on Python 3.10, 3.12, and 3.13 — the `requires-python` floor, the version every other job here uses, and the interpreter the macOS `.dmg` embeds. The deterministic harness contracts, the route response contract, and the frozen response shapes are three more jobs, split out because as steps behind `pytest` they could only run when the suite was already green, and "the gate did not get to run" and "the gate ran and passed" looked identical on the summary page. The browser E2E runs the breadth matrix in Chromium, Firefox, and WebKit; the long workbench walk, the admission-fault case, and the P1 controls stay Chromium-only, where the value is depth rather than engine coverage. Three jobs are schedule- or dispatch-only: enforced Seatbelt isolation on macOS, the Linux app bundle plus the Windows package that wraps it, and the science connector canary, which fails on real schema drift and never on an upstream being unreachable. There is deliberately no Linux bubblewrap job — a GitHub-hosted runner will not let `bwrap` bring up loopback in a new network namespace, so it could never pass, and `docs/platforms.md` states what is proven instead of pointing at a job that was always red. |
| `release.yml` | Manual dispatch only, and draft-first. It used to trigger on `release: [created]` with every outward-facing job gated on the release being a draft — a combination GitHub never emits — so the pipeline was unreachable by construction. Now a maintainer creates a stable draft and dispatches this workflow against that tag; with `publish` unset everything is built and verified and nothing leaves. One job peels the tag to a single commit SHA that every later job checks out, because a tag is mutable and five jobs resolving it independently could gate one commit, build the wheel from a second, and package the app from a third. After it: the non-prerelease draft guard, the offline gates re-run at that SHA into a receipt staging verifies, enforced Seatbelt isolation on macOS (with the Linux sandbox boundary explicitly recorded as unproven because GitHub-hosted runners cannot execute it), the tag matched against both version declarations, a source rescan, the wheel and sdist, the macOS app image, the Linux bundle, the Windows package and a Windows-native parse of its launcher, assets staged onto the draft, publication to PyPI through OIDC from the `pypi` environment, and only then the GitHub release made public. The ordering and every check live in [`scripts/release_pipeline.py`](../../scripts/release_pipeline.py), so they run on a laptop and under pytest instead of only on a release event. |
| `scorecard.yml` | Runs OpenSSF Scorecard on pushes to `main` and weekly, publishes the results, and uploads the SARIF to code scanning. |

The default test suite must remain offline. Live providers, GPU, SSH, package
publication, and credentials stay in separately authorized paths.
