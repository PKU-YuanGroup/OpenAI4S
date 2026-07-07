<!--
Thanks for contributing to OpenAI4S!
Please read CONTRIBUTING.md first. Keep the PR small and single-purpose.
Branch naming: feature/{runtime,web,science,harness,platform}/<name>, fix/<name>, docs/<name>.
-->

## What & why

<!-- One or two sentences: what changes, and why it is needed. Link issues. -->

## Type of change

- [ ] Bug fix
- [ ] New feature / Skill
- [ ] Documentation
- [ ] Security fix
- [ ] Refactor (no behavior change)

## Checklist (required)

### Tests — offline by design

- [ ] `uv run pytest` passes locally with **no network and no API keys**.
- [ ] `uv run pre-commit run --all-files` passes.
- [ ] No new test requires live LLM, network, GPU, SSH, Docker, lab hardware,
      or secrets in the default suite.
- [ ] No tests were deleted without tracked replacements.

### Core dependency policy

- [ ] No hard third-party import added to the core (engine, LLM client,
      web server stay pure stdlib).
- [ ] Optional science imports are guarded by `try/except ImportError` at
      every in-tree use site.

### Security

- [ ] No secrets, API keys, tokens, or real credentials in code, tests,
      fixtures, logs, or docs.
- [ ] If this touches secret-handling paths (`store.py`, `host_dispatch.py`,
      `security/`, `permissions.py`, `egress.py`, `openai4s_compute_provider/`):
      synthetic-secret regression tests are included.
- [ ] Docs do not overstate isolation/sandboxing or other guarantees the
      runtime does not provide.

### Surgical-edit policy

- [ ] Hotspot files (`gateway.py`, `host_dispatch.py`, `store.py`, `app.js`,
      `worker.py`, `manager.py`) were edited surgically — **no wholesale
      rewrite**.
- [ ] `openai4s/server/webui/vendor/` and `tests/fixtures/` were not
      reformatted.
- [ ] Kernel / host-RPC / gateway changes include focused contract tests and
      `tests/test_kernel.py` / `tests/test_agent.py` / `tests/test_gateway.py`
      were re-run.

### Docs

- [ ] Docs match actual code behavior.
- [ ] `README.md` and `README_zh.md` stay in sync where the change affects
      translated sections.

## Files that most need human review

<!-- List the riskiest files/hunks so reviewers focus there first. -->

## How this was verified

<!-- Paste the exact commands you ran and their results (pytest / pre-commit /
     manual browser check against ./start.sh where relevant). -->
