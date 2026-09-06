<!--
Thanks for contributing to OpenAI4S.

Keep PRs small, focused, and reviewable.
Branch naming: feat/<name>, fix/<name>, docs/<name>, test/<name>,
refactor/<name>, chore/<name>, ui/<name>, harness/<name>, science/<name>,
release/<name>, hotfix/<name>.

OpenAI4S is a public repository. Do not include secrets, private data,
unpublished research plans, internal assignments, or unreleased results in
the PR title, description, branch name, commit messages, code, logs, or tests.
-->

## Summary

<!-- What changed, and why? Link related issues if applicable. -->

## Area

- [ ] Web UI / frontend
- [ ] Agent / loop / delegation
- [ ] Kernel / host RPC / runtime
- [ ] Server / gateway / API
- [ ] Store / provenance / artifacts
- [ ] Skills / science runtime
- [ ] Compute / remote execution
- [ ] CLI / config / packaging
- [ ] Tests / harness / CI
- [ ] Docs

## Change Type

- [ ] Bug fix
- [ ] Feature
- [ ] Refactor
- [ ] Test / harness
- [ ] Documentation
- [ ] Security-related change
- [ ] Release / packaging

## Verification

<!-- Paste exact commands and manual checks. If something was not run, say why. -->

Ran:

```text

```

Not run:

```text

```

Reason:

```text

```

## Risk and Reviewer Focus

<!-- What could break? Which files or behavior should reviewers inspect first? -->

## Checklist

### Diff Readiness

- [ ] I reviewed and understand the full diff.
- [ ] This PR is small and single-purpose.
- [ ] No unrelated formatting, broad rewrite, or drive-by refactor is included.
- [ ] Hotspot files were edited surgically if touched: `gateway.py`,
      `host_dispatch.py`, `store.py`, `worker.py`, `manager.py`.
- [ ] Workbench changes were made in `frontend/` (not legacy `app.js`)
      and `openai4s/server/webui/dist/` was rebuilt and committed with the
      source. `OPENAI4S_WEBUI=legacy` is an escape hatch, not a place to add
      features.
- [ ] `openai4s/server/webui/vendor/` and `tests/fixtures/` were not
      reformatted.

### Tests

- [ ] Relevant tests or manual checks were run and listed above.
- [ ] No new test requires live LLM, network, GPU, SSH, Docker, lab hardware,
      or secrets in the default offline suite.
- [ ] No tests were deleted without tracked replacements.
- [ ] Kernel / host-RPC / gateway changes include focused contract coverage or
      a documented smoke test.

### Core Dependency Policy

- [ ] No hard third-party import was added to the core engine, LLM client, or
      stdlib web server.
- [ ] Optional science imports are guarded by `try/except ImportError` at every
      in-tree use site.
- [ ] New dependencies, if any, are documented and justified.

### Public Disclosure and Security

- [ ] No secrets, API keys, tokens, real credentials, private data, model
      weights, or local absolute paths are included.
- [ ] No unpublished research roadmap, internal assignment, benchmark detail,
      or unreleased experimental result is disclosed.
- [ ] Security-sensitive changes include appropriate synthetic tests or manual
      verification notes.
- [ ] Docs do not overstate isolation, sandboxing, privacy, or security
      guarantees.

### Docs

- [ ] Docs match actual code behavior.
- [ ] User-facing behavior changes are reflected in docs or release notes where
      appropriate.
- [ ] `README.md` and `README_zh.md` stay in sync where translated sections are
      affected.
