---
title: Testing and validation
description: Test layers and runtime checks required for OpenAI4S changes.
status: current
audience:
  - contributors
  - operators
verified_commit: a92e736
last_verified: 2026-07-14
owner: OpenAI4S maintainers
---

# Testing and validation

The default test suite is an offline gate: LLM calls are mocked, user data is
redirected to temporary directories, and live network, GPU, SSH, Docker, lab,
and browser markers are deselected.

## Local gates

```bash
uv run pytest
uv run pre-commit run --all-files
npm run docs:build
```

Run focused tests while developing, then run the complete offline suite before
handoff.

| Change | Minimum focused gate |
|---|---|
| Agent routing or completion | `tests/test_agent_engine.py`, `tests/test_actions.py`, `tests/test_structured_finalize.py` |
| Kernel/Host protocol | `tests/test_kernel.py`, relevant R/supervisor/sandbox tests |
| Permissions/security | permission, Host contract, egress, sandbox, and security suites |
| Store/repository | the owning repository tests plus Store compatibility tests |
| Gateway/session behavior | Gateway, session service, coordinator, and Web static-contract tests |
| Artifacts/recovery | artifact manager/repository, checkpoint, recovery, and branch tests |
| Skills | discovery, versions, product surface, and the specific Skill test |
| Packaging | release gates and artifact verification |
| Documentation | VitePress build, internal-link check, locale parity, Mermaid and search smoke |

## Runtime validation beyond unit tests

Tests are the floor for kernel, WebSocket, artifact, and browser behavior. When
those surfaces change:

1. start the real workbench with `./start.sh`;
2. run a model-free or mocked scenario where possible;
3. execute a Python cell and, when relevant, an R cell;
4. verify Notebook events, Artifact capture, cancellation, and reconnect state;
5. use the browser smoke test for user-visible changes.

The Jupyter adapter, remote compute, live LLM providers, SSH hosts, GPUs, and
real browsers are opt-in environments. Never add them to the default offline
gate.

## Protocol-sensitive review checklist

- Does one component still own the kernel frame reader?
- Does every native tool declaration receive one canonical result?
- Can cancellation target only the exact execution owner and kernel lease?
- Does a failure preserve a truthful Action Ledger and execution attempt?
- Are filesystem, SQLite, kernel, and WebSocket commit points described
  separately rather than presented as one transaction?
- Are secrets absent from worker environments, logs, fixtures, and generated
  documentation?
- Does the change preserve zero hard dependencies in the Python core?

See [Release validation](../release-validation.md) for wheel and source archive
checks.
