# GitHub workflows

[中文说明](README_zh.md)

Everything CI does to this repository is in these four files: the offline gate
every pull request has to pass, plus release publication, container-image
publication, and Scorecard. They run
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
| `ci.yml` | The default offline gate, as independent jobs rather than steps. Checks branch naming, runs pre-commit, verifies bilingual per-directory documentation coverage, type-checks the core orchestration boundary, scans tracked sources for credentials, builds the wheel and the sdist and checks what is inside both, then installs the wheel alone into a clean venv and exercises the CLI it puts there, self-tests the npx Skill installer and — in a separate job — proves the published npm package still carries both the CLI and the Skill tree, because "the installer behaves" and "the package contains anything to install" are different questions and a red pytest must not be able to hide either, parses the shipped Windows launcher on a real Windows runner and proves it refuses — with guidance — where there is no WSL, and runs the offline suite on Python 3.10, 3.12, and 3.13 — the `requires-python` floor, the version every other job here uses, and the interpreter the macOS `.dmg` embeds. The suite runs under `pytest -n auto --maxprocesses=4 --dist loadfile`: it was 1094 of that job's 1122 seconds while every other job here finished inside two minutes, so it was the whole critical path. Parallelism is across files rather than across tests, because file independence is the boundary the suite was written to; the four-worker cap is the width measured here and prevents high-core hosts from multiplying kernel-capable test processes without bound. The deterministic harness contracts, the route response contract, and the frozen response shapes are three more jobs, split out because as steps behind `pytest` they could only run when the suite was already green, and "the gate did not get to run" and "the gate ran and passed" looked identical on the summary page. The frozen response shapes runs the suite a second time, with the capture installed, and is split the same way: the capture is written once per session, so each worker leaves its un-elided shapes beside the destination and the script merges them after pytest exits — through the same `merge` call `Recorder.observe` makes, so a route two workers both saw reaches the schema one process would have reached. Every share carries xdist's expected worker count and run ID, so a missing or stale share is rejected rather than published as a narrower contract. That equality and completeness are asserted in `tests/test_response_capture_assembly.py` rather than assumed, because a capture that silently held a fraction of the evidence would still look complete. The browser E2E runs the breadth matrix in Chromium, Firefox, and WebKit; the long workbench walk, the admission-fault case, and the P1 controls stay Chromium-only, where the value is depth rather than engine coverage. Stage 0 acceptance and team mode each get a browser job with a daemon of their own, because the smoke daemon can provide neither: Stage 0's whole subject is a disposable non-8760 daemon with the REPL off — its redaction/schema/projection self-check runs first, before Playwright is even downloaded — while team mode needs `OPENAI4S_TEAM_MODE=1` and two seeded accounts. The container image gets a job of its own, on every pull request rather than nightly: it builds the `Dockerfile` and then runs the daemon inside it — answering "does it work" rather than "did it build" — and it is the same [`scripts/container_smoke.sh`](../../scripts/container_smoke.sh) a contributor can run on a laptop. Every CI event also runs a real Linux bubblewrap Python/R persistent-kernel interrupt job: the job pins Ubuntu 24.04 and loads its packaged capability-stripping `bwrap-userns-restrict` profile; team read isolation and private PID/info-fd/procfs/pidfd adoption remain enforced, while raw worker networking is deliberately allowed so process-identity evidence is independent of private-network setup. That job proves SIGINT targeting and reuse, not Linux egress isolation. Three jobs are schedule- or dispatch-only: enforced Seatbelt isolation on macOS, the Linux app bundle plus the Windows package that wraps it, and the science connector canary, which fails on real schema drift and never on an upstream being unreachable. The broader Linux boundary smoke remains manual because it includes network denial and has not yet been re-evaluated on the hosted runner with that packaged profile loaded. |
| `publish-image.yml` | Publishes the daemon container image to GitHub Packages, as `ghcr.io/pku-yuangroup/openai4s:<version>` and `:latest` — automatically when a release is published, or by manual dispatch against an existing `v*` tag. The tag is first checked against both version declarations (`scripts/verify_release_tag.py`), so an image can never carry a tag its own `__version__` disagrees with; then the image is built and proven by the same [`scripts/container_smoke.sh`](../../scripts/container_smoke.sh) gate PR CI runs — the daemon must boot and serve inside it — and only a smoke-passing image is pushed. linux/amd64 only for now: the science stack under QEMU multiplies build time without a user on the other end, so an arm64 slice is a deliberate later addition rather than a flag flip. The push runs from the `ghcr` environment with the job's own `GITHUB_TOKEN`; no long-lived registry credential exists in the repository. |
| `release.yml` | Manual dispatch only, and draft-first. It used to trigger on `release: [created]` with every outward-facing job gated on the release being a draft — a combination GitHub never emits — so the pipeline was unreachable by construction. Now a maintainer creates a stable draft and dispatches this workflow against that tag; with `publish` unset everything is built and verified and nothing leaves. One job peels the tag to a single commit SHA that every later job checks out, because a tag is mutable and five jobs resolving it independently could gate one commit, build the wheel from a second, and package the app from a third. After it: the non-prerelease draft guard, the offline gates re-run at that SHA into a receipt staging verifies, enforced Seatbelt isolation on macOS (with the complete Linux filesystem-and-egress boundary explicitly recorded as unproven because it is not a current CI gate), the tag matched against both version declarations, a source rescan, the wheel and sdist, the macOS app image, the Linux bundle, the Windows package and a Windows-native parse of its launcher, assets staged onto the draft, publication to PyPI through OIDC from the `pypi` environment, and only then the GitHub release made public. The ordering and every check live in [`scripts/release_pipeline.py`](../../scripts/release_pipeline.py), so they run on a laptop and under pytest instead of only on a release event. |
| `scorecard.yml` | Runs OpenSSF Scorecard on pushes to `main` and weekly, publishes the results, and uploads the SARIF to code scanning. |

The default test suite must remain offline. Live providers, GPU, SSH, package
publication, and credentials stay in separately authorized paths.
