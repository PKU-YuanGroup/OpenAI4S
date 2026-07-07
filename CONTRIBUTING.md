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

## Branch naming

| Prefix | Use for |
| --- | --- |
| `feature/runtime/<name>` | Agent loop, kernel, host API, store |
| `feature/web/<name>` | Gateway, REST/WebSocket API, web UI |
| `feature/science/<name>` | Skills, science recipes, envs |
| `feature/harness/<name>` | Test harness, evals, fakes, golden traces |
| `feature/platform/<name>` | Compute providers, endpoints, worker runtime |
| `fix/<name>` | Bug fixes |
| `docs/<name>` | Documentation-only changes |

External PRs should target `main` (or a specific, announced `next` branch
during a coordinated integration window — never anything else).

## Before opening a PR

Run locally and make sure everything is green:

```bash
uv run pytest                       # full offline suite — no network, no keys
uv run pre-commit run --all-files   # black · isort --profile black · ruff
```

The default test suite is **offline by design**: `tests/conftest.py` redirects
`~/.openai4s` to a tmp dir and configures a fake LLM provider. Do not add tests
that require live LLM calls, network access, GPUs, SSH hosts, Docker, lab
hardware, or real API keys to the default suite. Such tests belong behind
explicit opt-in markers or manual workflows (see the refactor roadmap in
[`docs/refactor-plan.md`](docs/refactor-plan.md)).

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
  `uv run pre-commit run --all-files`, and docs that match behavior.
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
- Mark the CI workflow (once added) as a required status check.
- Replace the placeholder usernames in `.github/CODEOWNERS` with real GitHub
  usernames and enable "Require review from Code Owners".
- Ensure repository/organization secrets are **not** exposed to workflows
  triggered by external PRs.
