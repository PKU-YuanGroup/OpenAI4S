# Contributing to OpenAI4S

Thank you for helping keep the **Code-as-Action** paradigm open. This document
defines the minimal governance rules for multi-person contribution: branch
naming, the PR checklist, review policy, release policy, and the offline-test
policy. The technical conventions live in [`CLAUDE.md`](CLAUDE.md) /
[`AGENTS.md`](AGENTS.md) and are binding for all contributors, human or agent.

## Ground rules

1. **`main` is always green and always releasable.** Every commit on `main`
   must pass the full offline test suite and pre-commit. Never merge a red PR.
2. **Release tags are frozen artifacts** (e.g. `v0.2.0`). Once a tag is
   published it is never moved or rewritten; fixes go into a new tag.
3. **Feature branches are short-lived and scoped.** No long-lived parallel
   development branches (`science-dev`, `front-backend-dev`, ...) — they drift.
   A temporary `next` integration branch is allowed only with a named owner,
   explicit merge criteria, and a deletion date.
4. **The core is pure stdlib.** No hard third-party import may be added to the
   engine, LLM client, or web server. Optional science libraries must be
   guarded by `try/except ImportError` at every in-tree use site.
5. **External PRs never receive secrets.** CI for pull requests must run
   without API keys, tokens, or credentials of any kind, and must not run
   live LLM / network / GPU / SSH / lab jobs.

## Harness invariants

Harness changes must establish and preserve these project invariants. Until a
target contract lands, an interim change must not weaken the portions already
satisfied or claim that a known gap is enforced:

1. Compaction may change only the model-view context and append its archive and
   boundary records; it never rewrites execution, artifact, lineage, durable
   event, or host-call-tape truth.
2. Resume never replays a cell, tool, or remote job. A recovery probe is marked
   `system_recovery`, calls no taped `host.*` method, and is not published as an
   agent action.
3. Hooks and convenience rules may only tighten permissions. A standing deny
   is absolute.
4. Every executed cell produces an observation, including interruption and
   failure paths.
5. Secret fields never enter durable audit/events/goldens/errors or an
   unauthorized child process environment.
6. Cells execute serially within a persistent kernel namespace.
7. Each worker has at most one in-flight RPC transaction on its protocol
   channel; separate workers may dispatch concurrently.
8. Provenance failures never interrupt user code, and provenance never invents
   a lineage edge.
9. Artifact versions are append-only and immutable; rollback moves the latest
   pointer rather than changing stored bytes.
10. The egress fence remains read-fresh so a live policy toggle applies to the
    next checked call.
11. Every new Store table is reviewed for inclusion in `QUERY_DENYLIST`.
12. Noninteractive permission behavior is a typed capability decision
    (`deny`, `rules_only`, or `allow`); viewer presence is an answer channel,
    never authorization.
13. Every dispatcher `_m_*` method has an explicit capability specification;
    unknown methods fail closed.
14. Every side-effecting host action produces one canonical, redacted action
    and outcome shared by approval, activity, and durable audit projections.
15. Any SDK or skill that promises approval gating has contract tests for
    interactive, noninteractive, and durable decision-linkage behavior.

## Branch naming

| Prefix | Use for |
| --- | --- |
| `feat/<name>` | New features (runtime, platform, compute, ...) |
| `fix/<name>` | Bug fixes |
| `docs/<name>` | Documentation-only changes |
| `test/<name>` | Tests, contract coverage, fixtures |
| `refactor/<name>` | Refactors with no behavior change |
| `chore/<name>` | Tooling, CI, maintenance |
| `ui/<name>` | Web UI / frontend |
| `harness/<name>` | Test harness, evals, fakes, golden traces |
| `science/<name>` | Skills, science recipes, envs |
| `release/<name>` | Release preparation |
| `hotfix/<name>` | Urgent fixes on top of a release |

External PRs should target `main` (or a specific, announced `next` branch
during a coordinated integration window — never anything else).

Branch names are enforced by the `branch-name` CI job on every PR:
`^(main|next|(feat|fix|docs|test|refactor|chore|ui|harness|science|release|hotfix)/[a-z0-9][a-z0-9._-]{1,80})$`
(lowercase, 2–81 chars after the slash; Dependabot branches are exempt).

## Before opening a PR

Run locally and make sure everything is green:

```bash
uv run pytest                       # full offline suite — no network, no keys
uv run mypy                         # strict agent core + typed Host dispatcher
uv run pre-commit run --all-files   # black · isort · ruff · mypy · hygiene
```

The default test suite is **offline by design**: `tests/conftest.py` redirects
`~/.openai4s` to a tmp dir and configures a fake LLM provider. Do not add tests
that require live LLM calls, network access, GPUs, SSH hosts, Docker, lab
hardware, or real API keys to the default suite. Such tests must carry one of
the opt-in pytest markers registered in `pyproject.toml` (`external`,
`network`, `live_llm`, `gpu`, `ssh`, `lab`, `docker`, `browser`) — the default
`uv run pytest` run and PR CI deselect them automatically, and
`--strict-markers` rejects unregistered markers. Opt in explicitly with e.g.
`uv run pytest -m gpu`.

## PR checklist

Every PR description must confirm (the template in
[`.github/pull_request_template.md`](.github/pull_request_template.md) asks
for each of these):

- Offline tests pass: `uv run pytest` with no network and no secrets.
- Lint/format passes: `uv run pre-commit run --all-files`.
- No hard third-party import was added to the core; optional science imports
  are guarded by `try/except ImportError`.
- No secrets, keys, tokens, or real credentials appear in code, tests,
  fixtures, logs, or docs. Security-sensitive changes include synthetic-secret
  regression tests.
- The large hotspot files — `openai4s/server/gateway.py`,
  `openai4s/host_dispatch.py`, `openai4s/store.py`,
  `openai4s/server/webui/app.js`, `openai4s/kernel/worker.py`,
  `openai4s/kernel/manager.py` — were edited **surgically, never wholesale
  rewritten**.
- `openai4s/server/webui/vendor/` and `tests/fixtures/` were not reformatted.
- No tests were deleted without tracked replacements
  (`git ls-files --deleted` is clean or explained).
- Docs match actual code behavior — no overpromised security or API claims.
- If `README.md` changed in a translated section, `README_zh.md` was updated
  to match (and vice versa).
- Files that most need human review are listed in the PR description.

## Review policy

- Every PR needs at least **one approving review from a code owner** of the
  touched paths (see [`.github/CODEOWNERS`](.github/CODEOWNERS)) before merge.
- Changes to security-sensitive paths (`openai4s/security/`,
  `openai4s/permissions.py`, `openai4s/egress.py`, `openai4s/store.py`,
  `openai4s/host_dispatch.py`, `openai4s_compute_provider/`) additionally
  require review by a security-focused owner and synthetic-secret tests where
  applicable.
- Kernel / host-RPC / gateway-streaming changes require focused contract tests
  in the same PR and an explicit re-run of `tests/test_kernel.py` /
  `tests/test_agent.py` / `tests/test_gateway.py`.
- Reviews are read-only audits: reviewers verify the diff against `main`,
  they do not push fixups onto contributor branches without agreement.
- Keep PRs small and single-purpose. Large mechanical diffs (formatting,
  vendored assets) must be separated from behavior changes.

## Release policy

- Releases are cut from `main` only, as annotated tags `vMAJOR.MINOR.PATCH`.
- A release requires: green `uv run pytest`, green
  `uv run pre-commit run --all-files`, green source/artifact/install gates
  described in [`docs/release-validation.md`](docs/release-validation.md), and
  docs that match behavior.
- Tags are immutable. A bad release is followed by a new patch release, never
  a force-pushed tag.
- Compatibility promises (e.g. `openai4s_compute_provider` import paths during
  the worker-runtime transition) are only dropped with a deprecation note in
  the release notes at least one minor release in advance.

## Repository settings (maintainers)

Some governance pieces cannot be expressed in-repo and must be enabled in
GitHub settings by an admin:

- Branch protection on `main`: require PR review, require status checks,
  forbid force pushes and deletions.
- Mark the CI workflow ([`.github/workflows/ci.yml`](.github/workflows/ci.yml))
  as a required status check.
- Keep `.github/CODEOWNERS` usernames current (owners need write access) and
  enable "Require review from Code Owners".
- Ensure repository/organization secrets are **not** exposed to workflows
  triggered by external PRs.
