# Repository governance

[中文说明](CONTENTS_zh.md)

GitHub's own policy and automation live here: who reviews which paths, how
dependency updates arrive, and what a pull request has to account for. None of
it runs inside the OpenAI4S daemon, the Agent Engine, or a kernel. It guards
changes before they reach those runtime surfaces.

## Files

| File | Purpose |
| --- | --- |
| `CODEOWNERS` | Maps paths to reviewers: a catch-all default, then rules for the runtime core, security-sensitive paths, the web app, compute, science skills, tests, and governance. The last matching rule wins, so the specific entries override the default. |
| `codeql-config.yml` | Narrow exclusion for one exact, byte-for-byte arXiv test fixture that is parsed but never executed. Executable bioSkills remain scanned even when an individual synthetic example needs an alert-level dismissal. The repository's `github-codeql-config-file` property must point here; wildcards are deliberately forbidden by an offline governance test. |
| `dependabot.yml` | Weekly Monday dependency-update proposals for the `uv`, `npm`, `docker`, `pre-commit`, and `github-actions` ecosystems, each with a cap on how many PRs stay open. The npm and Docker entries keep the browser driver's integrity-locked graph and the container's digest-pinned base current. Action bumps are batched into a single PR; `uv` batches minor and patch bumps of development dependencies, `pre-commit` batches minor and patch hook bumps, and `npm` and `docker` batch theirs for the same reason — one PR per package is one full CI matrix per package. Majors are deliberately outside every group, and production dependencies are not grouped at all, so both still arrive one PR at a time — a major hook bump is a lint or style policy change that has to be read on its own rather than merged as one row of a version table. |
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
