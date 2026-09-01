# Repository governance

[中文说明](CONTENTS_zh.md)

GitHub's own policy and automation live here: who reviews which paths, how
dependency updates arrive, what a pull request has to account for, and the
community health files GitHub surfaces (the contributing guide, the code of
conduct, the security policy). None of it runs inside the OpenAI4S daemon, the
Agent Engine, or a kernel. It guards changes before they reach those runtime
surfaces.

## Files

| File | Purpose |
| --- | --- |
| `CODEOWNERS` | Maps paths to reviewers: a catch-all default, then rules for the runtime core, security-sensitive paths, the web app, compute, science skills, tests, and governance. The last matching rule wins, so the specific entries override the default. |
| `CODE_OF_CONDUCT.md` | The community code of conduct GitHub links from the repository's community profile. |
| `CONTRIBUTING.md` | Governance: branch naming, the PR/review/release policy, the offline-test policy, and the numbered harness invariants. The technical conventions live in the root `CLAUDE.md` / `AGENTS.md`; this file is the process side. |
| `SECURITY.md` | The private vulnerability-reporting process GitHub links from the Security tab. Suspected vulnerabilities go through it, never through a public issue. |
| `dependabot.yml` | Weekly Monday dependency-update proposals for the `uv`, `npm`, `docker`, `pre-commit`, and `github-actions` ecosystems. The `routine-dependencies` multi-ecosystem group consolidates four uv development tools' minor/patch bumps, the non-Black pre-commit hooks, and GitHub Action minor/patch bumps into one cross-ecosystem PR. Complementary regular entries for those same ecosystems ignore exactly the batched update classes, so uv majors and production dependencies, Black, and Action majors remain covered once on their focused review paths. npm and Docker deliberately keep their independent Monday schedules and per-ecosystem browser-tooling/base-image groups because they were not part of the repeated hand-built consolidation. |
| `pull_request_template.md` | The checklist a PR fills in: branch policy, what changed, which commands were actually run (and which were not, and why), the core dependency policy, and what must never appear in a public repository. |

## Subdirectories

| Directory | Purpose |
| --- | --- |
| `ISSUE_TEMPLATE/` | The structured issue forms, plus the policy for what belongs in a public issue. |
| `contributors/` | Contributor avatars, cropped to circles and committed here for the root READMEs to embed. |
| `workflows/` | The five GitHub Actions workflows: the offline CI gate, bounded protocol fuzzing, container publication, the draft-first release pipeline, and OpenSSF Scorecard. Credential scanning is a job inside CI rather than a workflow of its own. |

## Where this fits

A change to routing, persistence, the kernel protocol, permissions, or
sandboxing has to get past the checks defined here first. That does not make
this directory a security boundary. GitHub Actions validate source; the
enforcement that matters at runtime stays in `openai4s/security/`,
`openai4s/host/`, and the kernel manager.
