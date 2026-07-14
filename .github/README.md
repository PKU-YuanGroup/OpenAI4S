# Repository governance

[中文说明](README_zh.md)

GitHub's own policy and automation live here: who reviews which paths, how
dependency updates arrive, and what a pull request has to account for. None of
it runs inside the OpenAI4S daemon, the Agent Engine, or a kernel. It guards
changes before they reach those runtime surfaces.

## Files

| File | Purpose |
| --- | --- |
| `CODEOWNERS` | Maps paths to reviewers: a catch-all default, then rules for the runtime core, security-sensitive paths, the web app, compute, science skills, tests, and governance. The last matching rule wins, so the specific entries override the default. |
| `dependabot.yml` | Weekly Monday dependency-update proposals for the `uv`, `pre-commit`, and `github-actions` ecosystems, each with a cap on how many PRs stay open. Action bumps are batched into a single PR, and `uv` batches minor and patch bumps of development dependencies; everything else — production deps, majors, `pre-commit` — still arrives one PR at a time. |
| `pull_request_template.md` | The checklist a PR fills in: branch policy, what changed, which commands were actually run (and which were not, and why), the core dependency policy, and what must never appear in a public repository. |

## Subdirectories

| Directory | Purpose |
| --- | --- |
| `ISSUE_TEMPLATE/` | The structured issue forms, plus the policy for what belongs in a public issue. |
| `contributors/` | Contributor avatars, cropped to circles and committed here for the root READMEs to embed. |
| `workflows/` | The GitHub Actions: CI, release, the contributor wall, OpenSSF Scorecard, and secret scanning. |

## Where this fits

A change to routing, persistence, the kernel protocol, permissions, or
sandboxing has to get past the checks defined here first. That does not make
this directory a security boundary. GitHub Actions validate source; the
enforcement that matters at runtime stays in `openai4s/security/`,
`openai4s/host/`, and the kernel manager.
