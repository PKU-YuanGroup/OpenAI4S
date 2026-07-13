# OpenAI4S Refactor Plan

> Historical planning record. The backend migration described here and in the
> later hybrid architecture has been implemented. For current ownership and
> extension rules, use [`architecture.md`](architecture.md),
> [`backend-refactor-architecture.md`](backend-refactor-architecture.md), and
> [`backend-extension-guide.md`](backend-extension-guide.md). The blocker list
> below records the pre-refactor baseline; it is not current status.

This document is an architecture exploration and staged refactor plan. It is not
an implementation patch. The intended workflow is:

1. Codex explores and audits.
2. Claude Code implements one small PR-sized step.
3. Codex reviews the diff against `main` before merge.

## Current Merge Blockers

These items should be fixed before structural refactors or large feature work.

1. `host.query` can read secret-bearing settings.
   - Evidence: `openai4s/store.py` denies only `memories`, `host_call_log`,
     and `permission_rules`, while `settings` stores API keys and model
     profiles.
   - Risk: an agent can query persisted secrets through the read-only SQL API.

2. `credentials_set` may be logged in `host_call_log`.
   - Evidence: `host_dispatch.py` documents credentials as not persisted, but
     `store.py` excludes only `credentials_get` and `credentials_list` from
     host-call logging.
   - Risk: credentials can be serialized into SQLite logs or test artifacts.

3. Provider import-time secret scrubbing is overclaimed.
   - Evidence: docs/comments say provider imports happen after scrub, but
     `openai4s_compute_provider/__main__.py` imports provider code before
     `ByocResident._prologue()` runs.
   - Risk: a provider module can inspect inherited environment variables at
     import time.

4. Security docs and prompts overstate isolation in a few places.
   - Evidence: `docs/security.md` correctly says there is no OS sandbox, but
     `openai4s/security/classifier.py` still describes seatbelt/bubblewrap-like
     isolation. Shell execution is cwd-scoped, not OS-sandboxed.
   - Risk: reviewers and future contributors may rely on guarantees that the
     runtime does not actually provide.

5. There is no `harness/` directory and no `.github/workflows/` CI yet.
   - This is not a code safety blocker for the current clean branch, but it is
     a governance blocker for multi-person refactors.

Non-blockers confirmed during this pass:

- `git status --short` produced no output.
- `git diff --stat` produced no output before this document was added.
- `git ls-files --deleted` produced no output.
- `git ls-files --others --exclude-standard` produced no output.
- No untracked replacement files were found.
- No tracked tests were deleted.
- `docs/webapp-api.md` does not currently exist, so there is no stale API
  contract document to block on.

## A. Executive Summary

The biggest architecture problem is boundary drift, not a single broken module.
OpenAI4S has a strong core design: a pure-stdlib Code-as-Action runtime with an
outer agent loop and inner synchronous host-RPC loop. The risk is that several
large files now carry too many contracts at once:

- `openai4s/host_dispatch.py` mixes host API dispatch, permissions, artifacts,
  delegation, compute, credentials, and step logging.
- `openai4s/server/gateway.py` mixes HTTP routing, WebSocket streaming, session
  orchestration, kernel lifecycle, artifact capture, settings, and UI contract.
- `openai4s/store.py` is both schema and repository layer.
- `openai4s/server/webui/app.js` is tightly coupled to implicit `/api/*` and
  WebSocket payload shapes.
- `openai4s_compute_provider` is named like a provider but behaves like a remote
  worker runtime/protocol package.
- `skills/remote-compute-*` currently blur science recipes with trusted
  platform/provider integration.

Do not do a large direct refactor. The kernel protocol, host API, gateway
streaming, artifact provenance, and security gates are behaviorally coupled and
only partly documented. A wholesale rewrite would silently drop contracts that
tests may not yet cover. The safe sequence is: first write the plan, then add
compatibility tests and security gates, then move small pieces behind stable
facades, and only then let Claude implement narrow changes that Codex audits.

Claude should implement because many steps are mechanical and PR-sized. Codex
should check because the highest risks are contract drift, hidden imports,
secret exposure, false docs, and accidental wholesale rewrites.

## Command Baseline

Commands requested by the user were run or attempted in this workspace.

| Command | Result |
| --- | --- |
| `git status --short` | Passed; no output before planning doc edit. |
| `git diff --stat` | Passed; no output before planning doc edit. |
| `git ls-files --deleted` | Passed; no output. |
| `git ls-files --others --exclude-standard` | Passed; no output. |
| `uv run pytest --collect-only -q` | Passed; collected 192 tests. |
| `uv run pytest -q` | Passed; 191 passed, 1 skipped. |
| `uv run pre-commit run --all-files` | Passed; EOF, mixed line ending, whitespace, JSON, merge conflict, isort, black, and ruff hooks all passed. |
| `uv run python -m compileall -q openai4s openai4s_compute_provider tests harness` | Exit code 0 for existing paths, but printed `Can't list 'harness'` because `harness/` does not exist. |

Agent 4 also ran `uv run pytest tests/test_skills.py tests/test_methodology_skills.py tests/test_compute_nvidia.py -q`, which passed with 35 passed and 1 skipped.

## 1. Subagent Findings

### Agent 1: Codebase Mapper

- `.github/` and `harness/` are absent.
- `docs/` contains architecture, compute, configuration, security, skills, and
  web app docs.
- `envs/` contains conda kernel environment definitions.
- `openai4s/` contains the active runtime, server, store, LLM client, security,
  compute manager, and skills loader.
- `openai4s_compute_provider/` is a resident remote worker runtime/protocol,
  despite the provider name.
- `skills/` contained 24 bundled skills when this audit was recorded.
- `tests/` is an offline pytest suite with tmp data-dir isolation and fake LLM
  configuration.
- Hotspot files: `gateway.py`, `app.js`, `host_dispatch.py`, `store.py`,
  `sdk/host.py`, `style.css`, and `openai4s_compute_provider/_resident.py`.

Main risk: new host capabilities must update SDK, dispatcher, permissions,
logging, tests, and possibly UI together.

### Agent 2: Runtime/Core Architect

- Runtime/core is the code required for the Code-as-Action dual loop:
  `agent/`, `kernel/`, `sdk/host.py`, `host_dispatch.py`, `permissions.py`,
  `egress.py`, `security/`, and `llm.py`.
- Stable interfaces include kernel frames, `Kernel.execute` semantics,
  `host.*`, `HostDispatcher.__call__`, permission broker APIs, `llm.chat()`
  normalized returns, and exported security verdict APIs.
- `gateway.py` duplicates some outer-loop behavior now found in `agent/loop.py`,
  so a future shared session runner should be extracted only after contract
  tests exist.
- Do not wholesale rewrite `worker.py`, `manager.py`, `sdk/host.py`,
  `host_dispatch.py`, `gateway.py`, `store.py`, `app.js`, `llm.py`,
  `permissions.py`, `egress.py`, or `security/*`.

### Agent 3: Platform / Compute / Lab Architect

- The compute stack currently mixes provider discovery, SSH transport, job
  lifecycle, remote GPU capability registry, and endpoint-like behavior.
- `openai4s_compute_provider` is not really a provider. It is a worker runtime
  plus control protocol and resident process.
- `skills/remote-compute-nvidia/provider.py` combines Docker compute and NVIDIA
  hosted endpoint behavior.
- `skills/using-model-endpoint/provider.py` already represents endpoint
  behavior but imports the compute-provider runtime.
- Recommendation: distinguish `ComputeProvider`, `ModelEndpointProvider`,
  `LabProvider`, `Worker Runtime`, and transport/protocol layers.
- Recommended migration: preserve old `byoc:*` and `ssh:*` strings while adding
  resource/provider kinds and compatibility adapters.

### Agent 4: Science Skills Architect

- `skills/` should contain model-facing science recipes, workflows, examples,
  and lightweight helpers.
- Trusted platform code, transport, secrets handling, scheduler logic, and job
  lifecycle should not live in science skills.
- `envs/` is shared runtime environment configuration, not skill-private deps.
- Current skill loader risks:
  - Many skills use `description: >`, but the loader parses only inline scalar
    values, making catalog summaries become literal `>`.
  - Hyphenated skills with `kernel.py` produce invalid import hints such as
    `from pdf-explore.kernel import *`.
  - Sidecar gate is currently compile-only, not a full AST policy gate.
  - `remote-compute-ssh` references a missing `compute-env-setup` skill.
- Proposed future skill package shape: one skill per directory, optional
  `skill.json`, `kernel.py`, `references/`, `examples/`, and local tests.

### Agent 5: Web/API Architect

- `docs/webapp-api.md` does not exist, so there is no stale API doc blocker.
- The real API contract is implicit in `gateway.py` and `app.js`.
- REST is under `/api/*`; WebSocket is `/api/ws`.
- Artifact routes are mixed: many routes return JSON, while artifact downloads
  return raw bytes.
- Uploads are JSON/base64, not multipart.
- Frontend currently reads `j.detail` on errors, while backend commonly returns
  `{error: ...}`.
- `artifact_created` WebSocket payload shape is not fully uniform.
- `/projects?limit=100&offset=0` is sent by the frontend, but the backend does
  not implement real pagination semantics.
- Recommendation: document actual API shape first, then add consumer-driven
  contract tests and only later extract helpers.

### Agent 6: Harness / Test / CI Architect

- Current tests support continued refactoring but need stronger structure.
- There is no `harness/`, no `.github/workflows/`, and no pytest marker policy.
- Default tests are offline and use fakes, but live/network/GPU/SSH/lab tests
  should be guarded by strict markers.
- Recommended markers: `unit`, `integration`, `e2e`, `security`, `platforms`,
  `skills`, `slow`, `external`, `network`, `live_llm`, `gpu`, `ssh`, `lab`,
  `docker`, `browser`, `golden`, and `smoke`.
- Default PR CI must run without secrets, network, live LLM, GPU, SSH, or lab
  hardware.

### Agent 7: Security / Secrets Reviewer

- Found real blockers:
  - `host.query` can read `settings`.
  - `credentials_set` is not excluded from host-call logging.
  - provider import happens before resident prologue scrub.
  - several child processes inherit too much environment by default.
  - docs/prompts overstate sandboxing.
- Required security gates:
  - SQL allowlist/denylist for secret-bearing tables.
  - secret log redaction/exclusion for credential-setting paths.
  - provider import-time synthetic-secret test.
  - clean env builder for kernel, bash, MCP, compute helper, and Docker/SSH
    helpers.
  - docs truth gate for security claims.
  - external PRs never receive secrets.

### Agent 8: Contributor Experience / Governance Reviewer

- README and README_zh explain quickstart well, but contribution workflow is not
  actionable.
- Missing: `CONTRIBUTING.md`, `.github/CODEOWNERS`,
  `.github/pull_request_template.md`, CI workflows, labels, review policy, and
  release checklist.
- Since there is no GitHub org/team owner yet, CODEOWNERS should use real
  personal usernames or obvious placeholder usernames until replaced.
- Minimal governance: short-lived feature branches, main protection, required
  CI, CODEOWNERS routing, PR template, and release tags.

## 2. Consolidated Architecture Recommendation

Keep the current runtime shape stable while creating clearer boundaries around
it. Do not move code first. First add tests, docs, compatibility facades, and
governance gates.

Recommended sequencing:

1. Fix immediate secret leakage and false security claims.
2. Add CI/governance so every later refactor has a safe lane.
3. Document real web/API and host/kernel contracts.
4. Add harness skeleton and pytest markers for offline/default versus external
   scenarios.
5. Fix skill loader correctness and add manifest/lint rules.
6. Introduce `platforms` architecture with compatibility adapters, but leave
   old paths working.
7. Reclassify `openai4s_compute_provider` as worker runtime, with a gradual
   rename/alias plan.
8. Extract large-file helpers only after contract tests lock behavior.

## B. Current Repository Map

### Top-Level Directories

| Path | Current responsibility |
| --- | --- |
| `.github/` | Absent. No visible Actions workflow, CODEOWNERS, or PR template. |
| `docs/` | Architecture, compute, configuration, security, skills, and web app documentation. |
| `envs/` | Conda kernel environment definitions used by `host.env`. |
| `harness/` | Absent. Should become scenario/eval/fake-provider/golden-trace home. |
| `openai4s/` | Main stdlib core package: agent, kernel, host, store, server, LLM, security, compute, skills loader, MCP, CLI. |
| `openai4s_compute_provider/` | Remote worker runtime/protocol/resident process package currently named as a provider. |
| `scripts/` | Setup, remote folding, and macOS packaging helper scripts. |
| `skills/` | Bundled model-facing science and workflow skills, plus some platform/provider code that should eventually move. |
| `tests/` | Offline pytest suite using fake LLM/config and tmp data directories. |

### `openai4s/` Internal Modules

| Module | Current responsibility |
| --- | --- |
| `agent/` | Outer REPL loop, context compaction, delegation. |
| `kernel/` | Persistent kernel subprocess, worker protocol, host-RPC loop, background execution, provenance, environment selection. |
| `sdk/host.py` | In-kernel `host` singleton facade; agent-visible ABI. |
| `host_dispatch.py` | Host-side implementation of `host.*` calls. Large dispatcher and policy integration point. |
| `store.py` | SQLite schema, persistence, query API, execution/artifact/provenance data model. |
| `server/` | Gateway daemon, API routes, WebSocket streaming, session/kernel lifecycle, UI serving. |
| `server/webui/` | Static frontend served from working tree. |
| `security/` | Pre-exec classifier, biosecurity, injection scanner, audit hook helpers. |
| `llm.py` | Pure-stdlib provider client for OpenAI/Anthropic/Gemini-style wires. |
| `permissions.py` | Permission broker and decision lifecycle. |
| `egress.py` | Host-side network allowlist gate. |
| `compute/` | Current compute manager and remote GPU registry; should evolve into platform compute. |
| `skills_loader/` | Skill discovery, parsing, search, and sidecar checks. |
| `mcp_client.py` and `mcp_servers/` | MCP integration. |
| `cli/` | CLI entrypoints. |
| `config.py` | Config and `.env` loading. |

### `openai4s_compute_provider` Real Responsibility

It is a remote worker runtime and control protocol:

- provider contract classes;
- fd/control-channel helpers;
- resident process lifecycle;
- oneshot/repl execution;
- worker process reuse;
- artifact/job harvest;
- partial secret scrubbing.

It is not the right long-term name for all future compute providers, because
providers such as SSH, SLURM, Modal, Kubernetes, endpoints, and lab instruments
need separate lifecycle and trust boundaries.

### Tests / Harness / Skills / Platforms Status

- `tests/` exists and is currently offline-friendly.
- `harness/` does not exist.
- `skills/` exists but contains both science recipes and provider/platform code.
- `platforms/` does not exist yet; platform concepts are spread across
  `openai4s/compute/`, `skills/remote-compute-*`, `host_dispatch.py`, and
  `openai4s_compute_provider/`.

## C. Target Architecture

This is the recommended destination. It should be approached through adapters
and tests, not a single move.

```text
openai4s/
  agent/
    loop.py
    compaction.py
    delegation.py
  kernel/
    manager.py
    worker.py
    protocol.py              # future explicit frame contract
    environments.py
    background.py
    provenance.py
    guards.py
  host/                       # future host API equivalent; may begin as facade
    api.py                    # stable host method registry/schema
    dispatcher.py             # HostDispatcher facade
    handlers/
      artifacts.py
      files.py
      web.py
      credentials.py
      skills.py
      compute.py
      query.py
  sdk/
    host.py                   # keep compatibility for in-kernel host facade
  server/
    gateway.py                # keep, but shrink through helpers over time
    daemon.py
    api_contract.py           # future route/event names and serializers
    session_runner.py         # future shared web/CLI loop orchestration
    webui/
      app.js
      style.css
      vendor/
  security/
    classifier.py
    biosecurity.py
    injection.py
    audit_hook.py
    redaction.py              # future shared secret redactor
    env.py                    # future clean env builder
  llm/
    client.py                 # future package form; keep openai4s.llm compat
    providers/
  skills_loader/
    loader.py
    manifest.py
    lint.py
  platforms/
    resources.py
    permissions.py
    compute/
      manager.py
      providers/
        ssh.py
        slurm.py
        modal.py
        kubernetes.py
        docker.py
      runtime/
        worker_contract.py
        resident_adapter.py
    lab/
      providers/
      protocols/
    model_endpoints/
      providers/
      client.py
  compute/                    # compatibility facade during migration
  storage/
    store.py                  # future split only after schema tests
    migrations/

openai4s_compute_provider/     # keep import-compatible during transition
openai4s_worker_runtime/       # optional future alias/package

skills/
  <skill-slug>/
    SKILL.md
    skill.json                # future strict manifest
    kernel.py                 # optional
    references/
    examples/
    tests/

harness/
  scenarios/
  providers/
  golden_traces/
  evals/
  smoke/

tests/
  unit/
  integration/
  e2e/
  security/
  platforms/
  skills/

docs/
  architecture.md
  webapp-api.md
  package-architecture.md
  refactor-plan.md
  refactor-pr-roadmap.md
```

Notes:

- `openai4s/host/` is a target boundary, not a required immediate rename.
  `openai4s/sdk/host.py` must remain available to kernels.
- `openai4s/llm.py` can stay as a compatibility module even if future provider
  code becomes a package.
- `openai4s/compute/` can become a compatibility facade while
  `openai4s/platforms/compute/` grows behind it.

## D. Module Boundaries

### Runtime/Core

Owns:

- Code-as-Action outer loop and completion semantics.
- Persistent kernel subprocess lifecycle.
- JSON-per-line kernel protocol and synchronous `host_call` RPC.
- `host.*` SDK/dispatcher ABI.
- Permissions, egress, classifier, injection/biosecurity/audit gates.
- LLM provider abstraction.
- Artifact/provenance contracts that are part of execution semantics.

Does not own:

- Specific science workflows.
- Real cloud/GPU/lab provider implementation details.
- UI rendering.
- Live external evaluation infrastructure.

### Web App

Owns:

- HTTP and WebSocket transport.
- Browser UI state and rendering.
- Session control and live stream presentation.
- API serializers and contract tests.

Does not own:

- Runtime policy decisions.
- Secret authorization policy.
- Kernel protocol semantics.

### Science Skills

Own:

- Model-facing recipes.
- Domain runbooks.
- Example inputs/outputs.
- Lightweight optional helpers.
- Scientific interpretation guidance.

Must not contain:

- Secret storage.
- SSH credentials.
- Scheduler/job lifecycle implementation.
- Cloud SDK side effects at import time.
- Lab hardware control.
- Default tests requiring GPU, network, API keys, SSH, or lab devices.

### Harness

Owns:

- Scenarios.
- Fake providers.
- Golden traces.
- Offline evals.
- Smoke scripts.

Does not own:

- Runtime implementation.
- Production provider code.
- Default live external calls.

### Platforms

Own:

- Trusted host-side integrations.
- Compute resources, model endpoints, lab hardware, and automation platforms.
- Resource registry and provider manifests.
- Scheduler/transport adapters.
- Secret allowlists and permission hooks for external execution.
- Job/protocol lifecycle and audit metadata.

### Worker Runtime

Owns:

- How code runs inside a controlled remote/container process.
- Resident process protocol.
- Timeout, stdout/stderr, status, and harvest.
- Environment scrubbing and redaction guarantees.

Does not own:

- Which cloud, scheduler, GPU service, endpoint, or lab platform launches it.
- Science workflow semantics.

### Default CI Exclusions

Default CI must not include:

- live LLM/API calls;
- network-dependent tests;
- SSH or SCP to real hosts;
- GPU or Docker requirements unless fully faked;
- lab hardware;
- self-hosted runner-only jobs;
- tests requiring secrets;
- large binary/vendored asset rewrites.

## E. `openai4s_compute_provider` Decision

### Option 1: Keep `openai4s_compute_provider` Independent

Pros:

- Minimal disruption.
- Existing imports and provider skills keep working.
- Keeps remote worker/runtime code separate from pure core package.
- Good fit if it remains a low-level runtime dependency for multiple platform
  provider kinds.

Cons:

- Name remains misleading.
- Future endpoint/lab providers may keep importing a "compute provider" package.
- Documentation must constantly explain the mismatch.

Migration risk:

- Low if no behavior changes.
- Medium if new docs imply semantics not enforced by tests.

User/API impact:

- None.

Suitable now:

- Yes as a short-term stabilization step, but not as final naming.

### Option 2: Rename to `openai4s_worker` or `openai4s_worker_runtime`

Pros:

- Name matches reality: worker runtime/protocol/resident process.
- Makes it clear that compute, endpoints, and labs can all use the runtime.
- Reduces conceptual confusion for contributors.

Cons:

- Package rename can break imports, docs, skills, and downstream scripts.
- PyPI/package metadata and local import paths need a compatibility story.

Migration risk:

- Medium. Safe only if old package remains as re-export/adapter for multiple
  releases.

User/API impact:

- New imports can use the clearer name.
- Old imports must continue to work.

Suitable now:

- Suitable only as an alias package or compatibility facade, not as a hard move.

### Option 3: Absorb into `openai4s/platforms/compute/runtime/`

Pros:

- Single repository/package namespace.
- Clear relationship to platform compute.
- Easier internal refactors if runtime is never consumed separately.

Cons:

- Makes worker runtime look compute-only again.
- Couples remote runtime to the core `openai4s` import graph.
- Risks violating the pure-stdlib core boundary if third-party provider code
  leaks inward.
- Harder for endpoint/lab providers to reuse without importing compute.

Migration risk:

- High if done before platform boundaries and env/secrets gates exist.

User/API impact:

- Existing imports break unless fully adapted.

Suitable now:

- No.

### Option 4: Keep Old Package, Add `openai4s_worker_runtime` Alias, and Move
Provider Kinds Elsewhere

Pros:

- Best compatibility/name clarity balance.
- Allows docs and new provider code to use worker-runtime terminology.
- Leaves compute, endpoint, and lab providers in `openai4s/platforms/*`.
- Old skills and scripts keep working.

Cons:

- Two names exist for a while.
- Requires tests to ensure both names expose the same contract.

Migration risk:

- Low to medium.

User/API impact:

- No immediate break.
- New docs can recommend `openai4s_worker_runtime`.

Suitable now:

- Yes, after immediate security blockers are fixed.

### Recommendation

Use Option 4.

Phased migration:

1. Fix provider import-time scrubbing and add tests.
2. Document the current package as worker runtime, not provider registry.
3. Add `openai4s_worker_runtime` as an alias/re-export package or module.
4. Keep `openai4s_compute_provider` import-compatible for at least two minor
   releases.
5. Add provider `kind` fields: `compute`, `model_endpoint`, `lab`.
6. Move provider implementation concepts under `openai4s/platforms/*` with old
   skill/provider entrypoints forwarding to the new manager.
7. Only consider absorbing code after real usage proves the runtime is compute
   only. Current evidence suggests it is not.

## F. Branch and Contribution Strategy

- Do not default to long-lived `science-dev`, `harness-dev`, or
  `front-backend-dev` branches. They will drift.
- `main` should be always green and always releasable.
- Release tags are the frozen artifacts, for example `v0.2.0`.
- Feature branches should be short-lived and scoped:
  - `feature/runtime/<name>`
  - `feature/web/<name>`
  - `feature/science/<name>`
  - `feature/harness/<name>`
  - `feature/platform/<name>`
  - `fix/<name>`
  - `docs/<name>`
- Use a temporary `next` branch only for a coordinated large integration window.
  It must have an owner, merge criteria, and a deletion date.
- External PRs should target `main` or a specific `next` branch only.
- External PR CI must not receive secrets and must not run external/GPU/SSH/lab
  jobs.
- Risk is controlled by CODEOWNERS, CI, tests, and review gates, not by keeping
  parallel long-lived development branches.

## G. Refactor Roadmap

### PR 01: Block Secret Reads And Secret Logs

- Goal: prevent `host.query` and host-call logs from exposing secrets.
- Why now: this is the highest merge blocker and affects all future work.
- Files involved: `openai4s/store.py`, `openai4s/host_dispatch.py` if needed,
  `tests/test_security.py`, `tests/test_permissions.py` or new focused tests,
  `docs/security.md`.
- Explicitly not involved: `gateway.py`, `app.js`, compute provider rename,
  directory moves.
- Expected diff size: 100-250 lines.
- Risk: High.
- Claude tasks: add denylist/allowlist for secret-bearing SQL tables/fields;
  exclude/redact `credentials_set` and API key settings from host-call logs;
  add regression tests.
- Tests: `uv run pytest tests/test_security.py tests/test_permissions.py -q`,
  `uv run pytest -q`, `uv run pre-commit run --all-files`.
- Rollback: revert the PR; no schema migration should be required.
- Codex check: prove synthetic secrets do not appear in query results,
  `host_call_log`, stdout, stderr, or artifacts.
- Parallelizable: no.
- Dependencies: none.

### PR 02: Make Provider Import-Time Scrubbing True Or Documented Truthfully

- Goal: align provider import behavior, environment scrubbing, and docs.
- Why now: current docs overclaim that provider imports cannot see secrets.
- Files involved: `openai4s_compute_provider/__main__.py`,
  `openai4s_compute_provider/_resident.py`, `openai4s_compute_provider/__init__.py`,
  `tests/test_compute_nvidia.py`, `docs/security.md`, `CLAUDE.md`.
- Explicitly not involved: platform directory moves, gateway, app.js.
- Expected diff size: 150-350 lines.
- Risk: High.
- Claude tasks: add a synthetic-secret import-time test; either scrub env before
  dynamic provider import or downgrade docs/comments to exactly match behavior.
- Tests: `uv run pytest tests/test_compute_nvidia.py tests/test_security.py -q`,
  `uv run python -m compileall -q openai4s_compute_provider tests`.
- Rollback: revert to previous behavior and keep docs conservative.
- Codex check: malicious provider top-level code cannot see synthetic secrets,
  or docs clearly state it can until fixed.
- Parallelizable: no.
- Dependencies: PR 01 preferred.

### PR 03: Add Governance Skeleton

- Goal: make multi-person contribution reviewable.
- Why now: later refactors need branch, review, and ownership rules.
- Files involved: `CONTRIBUTING.md`, `.github/CODEOWNERS`,
  `.github/pull_request_template.md`, README links.
- Explicitly not involved: production code, tests reorganization.
- Expected diff size: 150-300 lines.
- Risk: Low.
- Claude tasks: add branch naming, PR checklist, CODEOWNERS placeholders,
  review policy, release policy, and offline-test policy.
- Tests: `uv run pre-commit run --all-files`.
- Rollback: remove governance files.
- Codex check: CODEOWNERS uses personal usernames/placeholders, not unavailable
  org teams; PR template includes security/offline/core-dependency checks.
- Parallelizable: yes, after PR 01.
- Dependencies: none.

### PR 04: Add Default-Safe CI And Pytest Markers

- Goal: establish offline default PR CI and external-test marker policy.
- Why now: all later refactors need automated safety checks.
- Files involved: `pyproject.toml`, `.github/workflows/ci.yml`,
  selected tests for markers only if needed.
- Explicitly not involved: moving tests, creating live external jobs,
  production code.
- Expected diff size: 120-250 lines.
- Risk: Medium.
- Claude tasks: register markers; set strict marker policy; add CI with no
  secrets and read-only permissions; keep default tests offline.
- Tests: `uv run pytest --collect-only -q`, `uv run pytest -q`,
  `uv run pre-commit run --all-files`.
- Rollback: revert marker/CI changes.
- Codex check: no `pull_request_target`, no secrets, no self-hosted runner,
  external markers are excluded by default.
- Parallelizable: yes with PR 03.
- Dependencies: PR 03 recommended.

### PR 05: Create Harness Skeleton Without Moving Tests

- Goal: define harness boundaries without disrupting current pytest collection.
- Why now: future evals and platform fakes need a home.
- Files involved: `harness/README.md`, `harness/scenarios/.gitkeep`,
  `harness/providers/.gitkeep`, `harness/golden_traces/.gitkeep`,
  `harness/evals/.gitkeep`, `harness/smoke/.gitkeep`, `docs/refactor-plan.md`
  if updating status.
- Explicitly not involved: moving existing tests, changing runtime code.
- Expected diff size: 80-180 lines.
- Risk: Low.
- Claude tasks: add directory skeleton and docs explaining tests vs harness.
- Tests: `uv run python -m compileall -q openai4s openai4s_compute_provider tests harness`,
  `uv run pytest --collect-only -q`.
- Rollback: remove harness skeleton.
- Codex check: harness is not default-collected as live external tests.
- Parallelizable: yes.
- Dependencies: PR 04 preferred.

### PR 06: Document Real Web API Contract

- Goal: create truthful `docs/webapp-api.md` from `gateway.py` and `app.js`.
- Why now: frontend/backend refactors need a shared contract.
- Files involved: `docs/webapp-api.md`, possibly `tests/test_gateway.py` for
  small serializer assertions.
- Explicitly not involved: rewriting `gateway.py`, rewriting `app.js`.
- Expected diff size: 200-450 lines.
- Risk: Medium.
- Claude tasks: document REST routes, WebSocket events, JSON vs raw bytes,
  optional fields, error envelope reality, and known gaps.
- Tests: `uv run pytest tests/test_gateway.py -q`,
  `uv run pre-commit run --all-files`.
- Rollback: remove or revert doc; no behavior change.
- Codex check: every route/event claim maps to code; pagination and
  `artifact_created` are not overpromised.
- Parallelizable: yes.
- Dependencies: PR 03 recommended.

### PR 07: Fix Skill Loader Metadata And Add Skill Lint Tests

- Goal: fix skill frontmatter parsing and import-hint correctness.
- Why now: skill contribution quality depends on reliable catalog metadata.
- Files involved: `openai4s/skills_loader/loader.py`,
  `tests/test_skills.py`, maybe selected `skills/*/SKILL.md` only if tests
  require fixture correction.
- Explicitly not involved: moving skills, platform provider extraction.
- Expected diff size: 150-350 lines.
- Risk: Medium.
- Claude tasks: parse folded frontmatter descriptions correctly; avoid invalid
  import hints for hyphenated skill names; add lint tests.
- Tests: `uv run pytest tests/test_skills.py tests/test_methodology_skills.py -q`,
  `uv run pytest -q`.
- Rollback: revert loader and tests.
- Codex check: no hard third-party dependency added; summaries are no longer
  literal `>`.
- Parallelizable: yes after PR 01.
- Dependencies: none.

### PR 08: Introduce Platform Architecture Docs And Provider Kinds

- Goal: clarify compute, endpoint, lab, transport, and worker runtime concepts.
- Why now: prevents future provider additions from landing in `skills/`.
- Files involved: `docs/compute.md`, new `docs/package-architecture.md`,
  maybe provider manifests/docs under `skills/remote-compute-*`.
- Explicitly not involved: moving provider code, renaming packages.
- Expected diff size: 250-500 lines.
- Risk: Low.
- Claude tasks: add kind taxonomy and compatibility guarantees; identify legacy
  paths as supported adapters.
- Tests: `uv run pre-commit run --all-files`.
- Rollback: revert docs.
- Codex check: docs do not claim implemented SLURM/Kubernetes/Modal behavior
  unless code supports it.
- Parallelizable: yes.
- Dependencies: PR 02 recommended.

### PR 09: Add Worker Runtime Alias And Compatibility Tests

- Goal: begin renaming `openai4s_compute_provider` without breaking users.
- Why now: name confusion blocks platform architecture clarity.
- Files involved: new `openai4s_worker_runtime/` alias package or equivalent,
  `openai4s_compute_provider/__init__.py`, tests for import compatibility,
  docs.
- Explicitly not involved: deleting `openai4s_compute_provider`, moving runtime
  internals, changing provider behavior.
- Expected diff size: 100-250 lines.
- Risk: Medium.
- Claude tasks: add alias exports, tests, and docs; keep old package primary for
  now.
- Tests: `uv run pytest tests/test_compute_nvidia.py -q`,
  `uv run python -m compileall -q openai4s_compute_provider openai4s_worker_runtime tests`.
- Rollback: remove alias package.
- Codex check: old imports still pass; no new dependency; no secret regression.
- Parallelizable: no.
- Dependencies: PR 02 and PR 08.

### PR 10: Add Host/Kernel/Web Contract Tests Before Extraction

- Goal: lock key contracts before splitting large files.
- Why now: large-file refactors are unsafe without behavior tests.
- Files involved: `tests/test_kernel.py`, `tests/test_agent.py`,
  `tests/test_gateway.py`, maybe new `tests/test_host_contract.py`.
- Explicitly not involved: moving handlers out of `host_dispatch.py`, moving
  gateway routes, changing UI behavior.
- Expected diff size: 200-500 lines.
- Risk: Medium.
- Claude tasks: add focused tests for kernel frames, `host.submit_output`,
  dispatcher unknown-method soft-fail, error envelope, and key serializers.
- Tests: `uv run pytest tests/test_kernel.py tests/test_agent.py tests/test_gateway.py -q`,
  `uv run pytest -q`.
- Rollback: remove tests.
- Codex check: tests assert current behavior, not imagined future behavior.
- Parallelizable: yes after PR 01.
- Dependencies: PR 06 recommended.

## H. Claude Implementation Prompts

### Prompt for PR 01

目标：修复 secrets 被 `host.query` 或 `host_call_log` 暴露的 blocker。

背景：当前 `settings` 可能保存 API key/model profiles，`credentials_set`
也可能被记录到 host-call log。默认测试必须 offline。

允许修改：`openai4s/store.py`、必要的 `openai4s/host_dispatch.py`、
`tests/test_security.py`、`tests/test_permissions.py`、`docs/security.md`。

禁止修改：`gateway.py`、`app.js`、compute provider rename、目录迁移。

硬性约束：不新增核心第三方依赖；不打印真实 secret；不改 SQLite schema
除非绝对必要；保持 `host.query` 非 secret 表行为兼容。

必须新增/更新测试：合成 secret 写入 settings/credentials 路径后，确认
agent query 和 host-call log 不含明文。

必须运行：`uv run pytest tests/test_security.py tests/test_permissions.py -q`、
`uv run pytest -q`、`uv run pre-commit run --all-files`。

完成标准：测试证明 secret 不可通过 query/log 读出；文档描述和代码一致。

最终汇报格式：变更摘要、测试结果、残余风险、需要 Codex 重点审查的文件。

### Prompt for PR 02

目标：修正 provider import-time secret scrub 行为或保守化相关文档。

背景：当前 provider 模块导入早于 resident prologue，文档却声称 import 前已
scrub。这个说法必须由测试证明，或从文档中删除。

允许修改：`openai4s_compute_provider/__main__.py`、
`openai4s_compute_provider/_resident.py`、
`openai4s_compute_provider/__init__.py`、`tests/test_compute_nvidia.py`、
`tests/test_security.py`、`docs/security.md`、`CLAUDE.md`。

禁止修改：`gateway.py`、`app.js`、平台目录迁移、provider package 删除。

硬性约束：旧 provider 入口仍可用；不破坏 fake NVIDIA tests；无真实 GPU/SSH。

必须新增/更新测试：恶意 provider 顶层 import 尝试读取 synthetic secret，
测试必须证明读不到，或文档明确说明当前不能保证。

必须运行：`uv run pytest tests/test_compute_nvidia.py tests/test_security.py -q`、
`uv run python -m compileall -q openai4s_compute_provider tests`。

完成标准：代码与 docs 不再矛盾；没有过度承诺。

最终汇报格式：行为选择、测试证据、兼容性说明、残余风险。

### Prompt for PR 03

目标：新增最小多人协作治理文件。

背景：仓库缺少 `CONTRIBUTING.md`、CODEOWNERS 和 PR template。当前无 org
team 权限，所以 CODEOWNERS 使用个人 username 或 placeholder。

允许修改：`CONTRIBUTING.md`、`.github/CODEOWNERS`、
`.github/pull_request_template.md`、`README.md`、`README_zh.md`。

禁止修改：生产代码、测试代码、CI workflow。

硬性约束：明确 main always green、release tags frozen、短生命周期 feature
branches；外部 PR 无 secrets；核心 stdlib-only。

必须新增/更新测试：无代码测试；文档需通过 pre-commit。

必须运行：`uv run pre-commit run --all-files`。

完成标准：新人能看到分支命名、PR checklist、review policy、release policy。

最终汇报格式：新增文件列表、关键政策摘要、仍需仓库设置手动启用的项目。

### Prompt for PR 04

目标：新增默认安全的 PR CI 和 pytest marker policy。

背景：当前没有 `.github/workflows/`，也没有 markers。默认 PR CI 必须
offline、无 secrets、无 GPU/SSH/network/lab。

允许修改：`pyproject.toml`、`.github/workflows/ci.yml`、必要测试文件的
marker 标注。

禁止修改：生产代码、测试重排、external/live workflow。

硬性约束：不用 `pull_request_target` 执行 PR 代码；workflow permissions
只读；不注入 secrets；external markers 默认排除。

必须新增/更新测试：pytest marker collect-only 应通过。

必须运行：`uv run pytest --collect-only -q`、`uv run pytest -q`、
`uv run pre-commit run --all-files`。

完成标准：本地测试全绿，CI 文件只跑 default offline gate。

最终汇报格式：CI 触发条件、权限、marker 策略、测试结果。

### Prompt for PR 05

目标：新增 `harness/` 骨架并解释 tests/harness 边界。

背景：当前 `compileall ... harness` 提示 `Can't list 'harness'`，且未来
需要 fake providers、golden traces、evals、smoke scenarios。

允许修改：`harness/README.md`、`harness/scenarios/.gitkeep`、
`harness/providers/.gitkeep`、`harness/golden_traces/.gitkeep`、
`harness/evals/.gitkeep`、`harness/smoke/.gitkeep`。

禁止修改：现有 `tests/` 文件移动、生产代码、live eval 接入。

硬性约束：harness 不得引入默认 live network/GPU/SSH/lab 依赖。

必须新增/更新测试：无代码测试；compileall 必须不再提示 harness 缺失。

必须运行：`uv run python -m compileall -q openai4s openai4s_compute_provider tests harness`、
`uv run pytest --collect-only -q`、`uv run pre-commit run --all-files`。

完成标准：目录存在，边界说明清楚，默认测试收集不改变。

最终汇报格式：新增目录、边界说明、命令结果。

### Prompt for PR 06

目标：新增真实的 `docs/webapp-api.md`。

背景：当前 API contract 只隐含在 `gateway.py` 和 `app.js`。不要凭空设计
未来 API，只记录真实行为和已知 gaps。

允许修改：`docs/webapp-api.md`、可选 `tests/test_gateway.py` 中小型 contract
assertions。

禁止修改：`openai4s/server/gateway.py`、`openai4s/server/webui/app.js` 的
大规模改动或重写。

硬性约束：必须区分 JSON routes 和 raw bytes artifact routes；不得声明
offset pagination 已实现；`artifact_created` optional 字段要写清楚。

必须新增/更新测试：若改测试，只添加当前行为断言。

必须运行：`uv run pytest tests/test_gateway.py -q`、
`uv run pre-commit run --all-files`。

完成标准：每个 API 说法都能对应现有代码路径。

最终汇报格式：文档覆盖范围、未覆盖/不稳定 contract、测试结果。

### Prompt for PR 07

目标：修复 skill loader metadata 解析和 hyphenated skill import hint。

背景：多个 skill 使用 `description: >`，当前 loader 可能解析成字面 `>`；
带连字符的 skill 生成非法 import hint。

允许修改：`openai4s/skills_loader/loader.py`、`tests/test_skills.py`、
必要的 fixture 或 skill docs 小修。

禁止修改：移动 `skills/`、迁移 compute providers、引入 PyYAML 或其他核心
依赖。

硬性约束：只能用 stdlib 解析；保持旧 frontmatter 兼容；非 stdlib science
依赖必须 lazy/guarded。

必须新增/更新测试：folded description、inline description、hyphenated
kernel skill import hint。

必须运行：`uv run pytest tests/test_skills.py tests/test_methodology_skills.py -q`、
`uv run pytest -q`、`uv run pre-commit run --all-files`。

完成标准：catalog summary 不再是 `>`；import hint 不再是非法 Python。

最终汇报格式：解析规则、兼容性、测试结果、残余限制。

### Prompt for PR 08

目标：补充 platform/provider 架构文档和 provider kind taxonomy。

背景：当前 compute、endpoint、worker runtime、lab 概念混用。先写清楚边界，
不要移动代码。

允许修改：`docs/compute.md`、`docs/package-architecture.md`、可选
`skills/remote-compute-*/SKILL.md` 的 wording。

禁止修改：生产代码、包名、provider 文件位置、`openai4s_compute_provider`
行为。

硬性约束：不能宣称 SLURM/Kubernetes/Modal/lab 已实现；必须标注 legacy
compatibility。

必须新增/更新测试：无代码测试；文档通过 pre-commit。

必须运行：`uv run pre-commit run --all-files`。

完成标准：ComputeProvider、ModelEndpointProvider、LabProvider、Worker
Runtime、Transport 边界明确。

最终汇报格式：新增/修改文档、实现状态声明、后续 PR 建议。

### Prompt for PR 09

目标：新增 worker runtime alias，保持 `openai4s_compute_provider` 兼容。

背景：推荐路线是保留旧包并新增更准确的 `openai4s_worker_runtime` alias。

允许修改：新 `openai4s_worker_runtime/`、`openai4s_compute_provider/__init__.py`、
相关 tests、docs。

禁止修改：删除旧包、移动 `_resident.py`/`_protocol.py`、改 provider 行为。

硬性约束：旧 import 继续工作；新 alias 不新增依赖；package metadata 如需
更新必须最小化。

必须新增/更新测试：旧包和新 alias 暴露相同关键 symbols。

必须运行：`uv run pytest tests/test_compute_nvidia.py -q`、
`uv run python -m compileall -q openai4s_compute_provider openai4s_worker_runtime tests`、
`uv run pre-commit run --all-files`。

完成标准：新旧 import 都通过；文档推荐新名但不破坏旧名。

最终汇报格式：新增 alias、兼容测试、用户影响、回滚方式。

### Prompt for PR 10

目标：在抽大文件前添加 host/kernel/web contract tests。

背景：`host_dispatch.py`、`gateway.py`、`app.js`、`store.py` 等都是多合同
热点。先锁当前行为，再做抽取。

允许修改：`tests/test_kernel.py`、`tests/test_agent.py`、`tests/test_gateway.py`、
可新增 `tests/test_host_contract.py`。

禁止修改：生产代码、web UI、gateway route 行为、host API 行为。

硬性约束：测试只断言当前真实行为，不设计未来行为；默认 offline。

必须新增/更新测试：kernel frame/host response、`host.submit_output`、
unknown host method soft-fail、关键 gateway serializer/error envelope。

必须运行：`uv run pytest tests/test_kernel.py tests/test_agent.py tests/test_gateway.py -q`、
`uv run pytest -q`、`uv run pre-commit run --all-files`。

完成标准：新增测试能在当前实现上稳定通过，并为后续 extraction 提供护栏。

最终汇报格式：新增合同点、测试结果、仍未覆盖合同。

## I. Codex Review Prompts

Use these after Claude completes each PR. Each review is read-only.

### Review Prompt for PR 01

只读审计当前 PR diff against `main`，不要编辑文件。重点检查：
`host.query` 是否仍能读 secret-bearing settings；`credentials_set`、API key、
model profiles 是否会进入 `host_call_log`、stdout/stderr、artifact metadata；
是否新增核心依赖；测试是否覆盖 synthetic secrets。运行或要求结果：
`git diff --stat main...HEAD`、`uv run pytest tests/test_security.py tests/test_permissions.py -q`、
`uv run pytest -q`、`uv run pre-commit run --all-files`。返回 `PASS` /
`BLOCK` / `NEEDS WORK`，并列 blocking issues、non-blocking concerns、
missing tests、files requiring human review。

### Review Prompt for PR 02

只读审计当前 PR diff against `main`，不要编辑文件。检查 provider module
top-level import 是否能看到 synthetic secrets；docs/comments 是否仍过度承诺；
旧 provider entrypoint 是否兼容；没有真实 GPU/SSH/API key 需求。运行或要求结果：
`uv run pytest tests/test_compute_nvidia.py tests/test_security.py -q`、
`uv run python -m compileall -q openai4s_compute_provider tests`。返回
`PASS` / `BLOCK` / `NEEDS WORK`，列 blocking issues、concerns、missing tests、
human-review files。

### Review Prompt for PR 03

只读审计治理文件 diff against `main`，不要编辑文件。检查 branch strategy 是否
明确 main always green/release tags frozen/short-lived feature branches；CODEOWNERS
是否使用个人 username 或 placeholders；PR template 是否包含 offline tests、
no secrets、core stdlib-only、大文件不重写、README_zh 同步。运行或要求：
`uv run pre-commit run --all-files`。返回 `PASS` / `BLOCK` / `NEEDS WORK`。

### Review Prompt for PR 04

只读审计 CI/marker diff against `main`，不要编辑文件。检查 workflow 是否不用
`pull_request_target` 执行 PR 代码；permissions 是否只读；是否无 secrets；
external/live/GPU/SSH/lab markers 是否默认排除；默认测试是否 offline。运行或要求：
`uv run pytest --collect-only -q`、`uv run pytest -q`、`uv run pre-commit run --all-files`。
返回 `PASS` / `BLOCK` / `NEEDS WORK`。

### Review Prompt for PR 05

只读审计 harness skeleton diff against `main`，不要编辑文件。检查是否移动了
现有 tests；harness 是否只包含 README/.gitkeep/离线说明；compileall 是否不再
提示 harness 缺失；默认 pytest collection 是否没有 live external scenarios。运行或要求：
`uv run python -m compileall -q openai4s openai4s_compute_provider tests harness`、
`uv run pytest --collect-only -q`。返回 `PASS` / `BLOCK` / `NEEDS WORK`。

### Review Prompt for PR 06

只读审计 `docs/webapp-api.md` diff against `main`，不要编辑文件。逐条核对
文档中的 REST route、WebSocket event、payload 字段、error envelope、artifact
download/upload 描述是否与 `gateway.py` 和 `app.js` 一致；特别检查是否错误声称
offset pagination 或稳定 `artifact_created.artifact.id`。运行或要求：
`uv run pytest tests/test_gateway.py -q`。返回 `PASS` / `BLOCK` / `NEEDS WORK`。

### Review Prompt for PR 07

只读审计 skill loader diff against `main`，不要编辑文件。检查是否新增硬依赖；
folded `description: >` 是否正确；hyphenated skill import hint 是否合法；
sidecar compile behavior是否兼容；真实 skills 是否未被大规模重写。运行或要求：
`uv run pytest tests/test_skills.py tests/test_methodology_skills.py -q`、
`uv run pytest -q`。返回 `PASS` / `BLOCK` / `NEEDS WORK`。

### Review Prompt for PR 08

只读审计 platform architecture docs diff against `main`，不要编辑文件。检查文档
是否清楚区分 ComputeProvider、ModelEndpointProvider、LabProvider、Worker
Runtime、Transport；是否把未实现的 SLURM/Kubernetes/Modal/lab 写成 future；
是否保留 legacy compatibility；是否没有移动生产代码。运行或要求：
`uv run pre-commit run --all-files`。返回 `PASS` / `BLOCK` / `NEEDS WORK`。

### Review Prompt for PR 09

只读审计 worker runtime alias diff against `main`，不要编辑文件。检查旧
`openai4s_compute_provider` imports 是否仍通过；新 alias 是否只 re-export/adapter；
没有删除旧包；没有改 runtime behavior；没有 secret scrub regression。运行或要求：
`uv run pytest tests/test_compute_nvidia.py -q`、
`uv run python -m compileall -q openai4s_compute_provider openai4s_worker_runtime tests`。
返回 `PASS` / `BLOCK` / `NEEDS WORK`。

### Review Prompt for PR 10

只读审计 contract tests diff against `main`，不要编辑文件。检查新增测试是否只
断言当前行为；没有修改生产代码；没有 live external dependency；合同点覆盖 kernel
frames、host soft-fail、submit_output、gateway serializer/error envelope。运行或要求：
`uv run pytest tests/test_kernel.py tests/test_agent.py tests/test_gateway.py -q`、
`uv run pytest -q`。返回 `PASS` / `BLOCK` / `NEEDS WORK`。

## J. Merge Gates

Every PR must satisfy:

- `git status --short` is clean before merge.
- No untracked replacement files.
- No deleted tests without tracked replacements.
- `git ls-files --others --exclude-standard` has no accidental generated files.
- `git ls-files --deleted` has no unexplained deleted tracked files.
- `uv run pytest --collect-only -q`.
- `uv run pytest -q`.
- `uv run pre-commit run --all-files`.
- `uv run python -m compileall -q openai4s openai4s_compute_provider tests harness`
  once `harness/` exists; before PR 05, the harness warning is expected.
- Default tests are offline.
- No secrets required for default CI.
- No live LLM/network/GPU/SSH/lab dependency in default PR CI.
- No large-file wholesale rewrite.
- Docs match actual code behavior.
- No hard third-party import added to core.
- Optional science dependencies are guarded by `try/except ImportError` or lazy
  imports.
- Security-sensitive PRs include synthetic secret tests.
- Kernel/host/gateway PRs include focused contract tests.
- Human review files are listed in the PR description.

## K. Risk Register

| Risk | Mitigation |
| --- | --- |
| False API documentation | Generate docs from `gateway.py`/`app.js`; add contract tests; Codex reviews docs against code. |
| Tests accidentally dropped during move | `git ls-files --deleted`; no test moves without tracked replacements and collect-only proof. |
| Provider import sees secrets | Synthetic-secret import-time test; scrub before import or remove claim. |
| Full environment forwarded to compute job | Shared clean env builder; allowlist per target; redaction tests. |
| Skills/platforms boundary collapse | Keep science recipes in `skills/`; move trusted provider lifecycle to `platforms/`; lint manifests. |
| `app.js`/`gateway.py` wholesale rewrite | CODEOWNERS and PR template forbid; require small contract tests before extraction. |
| `openai4s_compute_provider` naming confusion | Add worker-runtime docs and alias while preserving old imports. |
| Long-lived dev branch integration drift | Use short-lived feature branches; temporary `next` only with owner and deletion date. |
| External PR triggers unsafe workflow | No `pull_request_target` execution; read-only permissions; no secrets; no self-hosted runner. |
| CI depends on GPU/SSH/API key | Mark external tests; default CI excludes `external`; manual protected workflows only. |
| Core gains hard third-party dependency | Pre-commit/review gate; pyproject review; optional imports guarded. |
| Kernel protocol deadlock | Contract tests for `host_call`/`host_response`; surgical edits only. |
| Store schema breaks UI or provenance | Schema/serializer tests; no schema moves without migration plan. |
| Security docs overpromise sandboxing | Docs truth gate; classifier prompt reviewed against actual isolation. |
| Host API facade and dispatcher drift | Tests assert SDK method has dispatcher implementation and soft-fail behavior. |
| WebSocket payload drift | Event sample tests and documented optional fields. |

## L. Immediate Next Step

The immediate next step should be PR 01: block secret reads and secret logs.

Rationale:

- The branch was clean before this planning doc, so there is no untracked
  replacement-file blocker to untangle.
- The test suite is green, so this is a safe base for a small security PR.
- The discovered secret exposure risks are more urgent than package layout,
  platform taxonomy, or governance polish.
- After PR 01 and PR 02, the project can safely add CI/governance and then
  proceed with docs/contracts.

Recommended action:

1. Commit this planning document as docs-only.
2. Ask Claude to implement PR 01 using the prompt above.
3. Have Codex run the PR 01 review prompt before merge.
4. Continue with PR 02, then PR 03/04 in parallel.

## 10. Whether This Branch Is Safe To Continue From

Yes for docs and small security fixes. No evidence of untracked replacement
files, deleted tracked tests, or dirty diff was found before this document was
created. However, do not start structural refactors from this branch until PR 01
and PR 02 address the current security blockers.

## 3. `docs/refactor-plan.md` Summary

This file is the master plan. It intentionally combines:

- subagent findings;
- consolidated architecture recommendation;
- current and target repository maps;
- module boundaries;
- `openai4s_compute_provider` decision analysis;
- branch/contribution strategy;
- 10 PR-sized roadmap steps;
- Claude implementation prompts;
- Codex read-only review prompts;
- merge gates;
- risk register;
- immediate next step.

If this document later becomes too large, split it mechanically into:

- `docs/package-architecture.md` for sections B-E;
- `docs/refactor-pr-roadmap.md` for sections G-K;
- keep `docs/refactor-plan.md` as the executive index.
