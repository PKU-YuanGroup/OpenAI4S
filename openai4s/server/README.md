# Server

[中文说明](README_zh.md)

The Web application lives here. This package wires the provider-neutral agent engine, the persistent Python and R kernels, the Host capability boundary, and the SQLite repositories into one HTTP/WebSocket server built out of nothing but the standard library. Domain logic belongs in the focused services in this directory; [`gateway.py`](gateway.py) is the compatibility and transport facade they are composed through.

## Where this fits

```text
browser
  |  REST requests + WebSocket events
  v
gateway.py
  |-- session-domain services and read projections
  |-- AgentEngine adapter (agent_run.py)
  |-- FIFO execution ownership (execution_coordinator.py)
  `-- lazy, session-owned Python and R kernel slots
         |
         `-- HostDispatcher -> permissions, tools, artifacts, data and delegation
```

- **Gateway composition.** [`gateway.py`](gateway.py) builds the stdlib `ThreadingHTTPServer` and wires everything into it: routing, REST handlers, WebSocket framing and resume, session runners, the services, storage, and static assets. [`daemon.py`](daemon.py) is a different thing: a legacy minimal compatibility server exposing `/`, `/health`, and `/run`, and no part of the Gateway composition. A new algorithm normally belongs in a focused module rather than in the facade.
- **REST and WebSocket.** REST does the bounded request/response work and serves the session-domain read models. The WebSocket channel carries the live stream: agent prose, action and cell lifecycle, approvals, Notebook updates, and terminal events, buffered so that a reconnecting browser can resume.
- **Session services and projections.** The mutation services own plans, reviews, artifacts, branching, recovery, packages, Skills, and deletion. The projection services turn canonical ledger, execution, lineage, context, and security state into redacted DTOs a browser can safely hold. A projection is a view; it is never the underlying terminal or transactional signal.
- **Kernel ownership.** A Web session owns an independent lazy Python slot and R slot through its `SessionRunner`. [`execution_coordinator.py`](execution_coordinator.py) hands out FIFO tickets so the Agent, the user REPL, recovery, and lifecycle writers never run over each other, and an interrupt reaches only the exact owner holding the exact lease. Tool-only routing does not start the foreground session slot, though an individual tool may manage a dedicated worker of its own.
- **Persistence boundary.** Durable facts are written through `Store` repositories. WebSocket state and live kernel namespaces are process-local. No transaction spans SQLite, workspace files, a kernel process, and a socket delivery.

## Completion, Notebook, and recovery boundaries

- A Cell result comes back to the outer loop as an observation; on its own it does not complete the task. Completion requires either a sole valid Engine-owned `finalize_response` or a `host.submit_output(...)` from inside a Python Cell. An R Cell cannot complete a task at all.
- A Cell whose only content is the `host.submit_output` protocol call still runs, and it stays in the raw execution and audit history, but the live and reopened Notebook projections filter it out. The `.ipynb` exporter reads immutable execution history without that filter, so its output is a raw/audit export and may include that system Cell.
- Recovery execution is wired through REST/UI, FIFO ownership, and the Python/R candidate pipelines, but it remains **Partial**: unsafe or nondeterministic Cells are classified `never`, no attempt is made to serialize an arbitrary historical namespace, and a language candidate that cannot become active can stop the overall recovery with an explicit Partial result.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | The stable package facade. Exports `build_server` and `serve`. |
| [`action_timeline.py`](action_timeline.py) | Projects the canonical Action Ledger into the Timeline the UI actually shows. An entry carries enough to say what ran, how it ended, which permissions it needed, what it cost, and which artifacts it referenced, all of it bounded and redacted. Provider `wire_state` and raw argument strings are left out on purpose, so a debugging endpoint cannot become a credential or protocol dump. |
| [`agent_run.py`](agent_run.py) | Adapts `AgentEngine` to the Web contracts. It streams safe prose and code drafts, emits Web events, honours cancellation, and runs native actions or Cells through injected ports. |
| [`artifacts.py`](artifacts.py) | A workspace file the agent writes becomes a versioned Artifact here. The same service backs edit, rename, upload, restore, and promote from the UI, keeping snapshots, provenance, and broadcasts in step as versions move. |
| [`cell_run.py`](cell_run.py) | Runs one Python/R Cell in a fixed order: admission, safety, kernel execution, live output, artifact capture, execution logging, terminal projection. Finishing that transaction is an observation; it never decides that the agent's task is done. |
| [`completions.py`](completions.py) | Writes the narration a user sees. Progress and outcome prose are localized, and a structured completion is rendered against the real Artifact-version delta rather than against a claim about it. Hidden reasoning never reaches it. |
| [`daemon.py`](daemon.py) | The legacy minimal threaded HTTP server, exposing `/`, `/health`, and `/run` for compatibility. It is not the Gateway and owns none of the Gateway's WebSocket, origin/auth checks, or singleton lifecycle. |
| [`execution_coordinator.py`](execution_coordinator.py) | The Web adapter over session-scoped FIFO execution ownership. Ticket state becomes WebSocket events. An admitted ticket is bound to its cancellation event and to the kernel lease current at that moment, and an interrupt reaches only the exact lease held by the exact execution id. |
| [`execution_views.py`](execution_views.py) | Reads immutable cell history and answers what the Notebook asks of it: which runtime generation ran a cell, what it depended on, whether it has gone stale since, how it was retried, where its data came from. |
| [`gateway.py`](gateway.py) | The main HTTP/WebSocket composition facade. Protocol framing, the hub and its resume buffers, `SessionState` and `SessionRunner`, REST routes, static serving, and the security checks all live here, and so does the wiring of every service in this table. |
| [`global_views.py`](global_views.py) | Composes the project-wide research Timeline and the artifact-lineage views that reach across sessions. |
| [`model_discovery.py`](model_discovery.py) | Probes a small fixed catalogue of loopback URLs for OpenAI-compatible model servers, with proxies disabled and redirects refused, so no caller can turn it into a general SSRF primitive. The result is a profile suggestion only: it mutates no model settings and stores no credentials. |
| [`model_profiles.py`](model_profiles.py) | A model-provider profile passes through here on the way in, where it is validated and migrated, and again when it is persisted, activated, or removed. Credentials are stripped out of anything that becomes public. It also builds the header model selector, which lists the live model and the saved profiles and nothing else — an endpoint nobody configured must not be offered, since choosing it would only fail at send time. |
| [`notebook_export.py`](notebook_export.py) | Deterministically exports raw immutable Python or R execution history as read-only `.ipynb` files and checksum-described bundles. It does not apply the Notebook projection's filter, so a protocol-only completion Cell can still show up in the export. |
| [`plans.py`](plans.py) | Owns the structured-plan lifecycle. A planner response is parsed and normalized, the draft and its JSON artifact are persisted, the public review shape is exposed, and an approved plan is carried into execution. Live `host.plan_update` mutations stay in `HostDispatcher`. |
| [`recovery_control.py`](recovery_control.py) | Projects recovery journal and generation state, and composes the validated, redacted plan of recovery actions currently possible. It never calls a checkpoint restorable unless both a workspace tree and a complete bootstrap manifest are present. |
| [`recovery_execution.py`](recovery_execution.py) | Runs one recovery mutation under exact execution ownership. Every language candidate runs under a single recovery id, the run stops after the first incomplete candidate, and it ends in one durable session terminal event. |
| [`recovery_recipe.py`](recovery_recipe.py) | Compiles immutable Cell facts, dependency closure, environment needs, sidecars, and determinism checks into a recovery recipe. It is conservative by design: a state-affecting Cell that cannot pass those checks stays in the recipe as a `never` replay step, so validation reports Partial instead of quietly claiming the old namespace survived. |
| [`recovery_runtime.py`](recovery_runtime.py) | Where the recovery pipeline meets real infrastructure. For one session it brings up candidate Python and R kernels, probes the environment, bootstraps, verifies, then commits or rolls back. |
| [`renderers.py`](renderers.py) | The registry from artifact kind, content type, and extension to a safe scientific renderer, plus the public renderer descriptors. Metadata only: it imports no scientific library and executes no artifact content. |
| [`reviews.py`](reviews.py) | Assembles the bounded evidence packet a scientific review runs on, then drives that review to a result. The run stays cancellable, and it lands in persistence, usage accounting, and the public review events. |
| [`session_branching.py`](session_branching.py) | Everything that makes a session branch: taking a checkpoint, forking it in isolation, previewing a revert, activating a branch, and recording revert/undo history append-only. A revert never rewrites an old checkpoint. It first records the current state as the undo target, and if external files changed after the current head the operation is recorded as `conflict` and no bytes move. |
| [`session_deletion.py`](session_deletion.py) | Cleans up after a durable session delete. Session aggregates, workspaces, snapshot/CAS references, and process-local state all go, and nothing outside that session's own scope is touched. |
| [`session_domain.py`](session_domain.py) | The high-level session-domain composition that route handlers call instead of assembling repositories themselves. It fronts checkpoints and cursor checkpoints, branches, timelines, export, renderers, package operations, and recovery. |
| [`session_package.py`](session_package.py) | Creates and imports session ZIP packages, deterministically and with checksums. Secret filtering, traversal checks, and a quarantine staging area guard the transfer. Import validates the whole archive before it creates anything, and the imported session lands on an ended kernel generation, an explicit view-only/recovery boundary. |
| [`session_recovery.py`](session_recovery.py) | Reconciles stale runtime state at startup and sweeps idle kernels deterministically, subject to activity and recovery blockers. A generation an older daemon left live is marked `abandoned` and stays auditable; nothing here deserializes objects or claims that memory survived. |
| [`session_runtime.py`](session_runtime.py) | Holds the session's control-plane objects, such as the dispatcher, delegation trees, and dynamic capabilities, so a language worker can be started, replaced, or stopped without discarding them. |
| [`share_projection.py`](share_projection.py) | Builds one frozen, flattened `ShareProjection` of a session (single synthetic root, no checkpoints, no memories/policy) and serializes it two ways: an `import_bytes`-compatible bundle and a redacted viewer document. Reuses the session-package fail-closed secret gate. |
| [`share_router.py`](share_router.py) | The read-only public request handler for one share: GET/HEAD only, exactly two read roots (in-memory viewer assets + the current leased snapshot), strict CSP, single-range support, and a uniform 404. It never touches the kernel, dispatcher, or a gateway route. |
| [`share_service.py`](share_service.py) | Two-phase publish for web shares (DB state machine + immutable version dirs + a `current.json` pointer) with SnapshotLease refcount GC, crash recovery, expiry sweeping, and revoke. FIFO admission and the tunnel client are injected. |
| [`skill_sidecars.py`](skill_sidecars.py) | Records the Skill sidecars a worker actually loaded against that exact kernel generation, merging them into the content-addressed bootstrap manifest with compare-and-swap so recovery can replay what was really observed. The Host process never imports or executes a sidecar. |
| [`skills.py`](skills.py) | The Web Customize lifecycle for user-authored Skill documents. It covers CRUD and import, the catalog projection the UI reads, and capability enablement. |
| [`titles.py`](titles.py) | Generates a session title in the background from the first message. Model configuration is bound late, and persistence and broadcast are race-safe. |
| [`variable_inspector.py`](variable_inspector.py) | Reads a live, idle Python/R namespace through a narrow manager request and returns bounded, sanitized variable previews. It creates no session and no worker, and it never enters the Cell transaction. |
| [`workbench_state.py`](workbench_state.py) | Projects the Context and Security panels from persistent and live state. It exposes no message content, and it does not claim an OS sandbox exists before a real worker has reported its self-test result. |
| [`ws_frames.py`](ws_frames.py) | The hardened RFC 6455 frame codec shared by the gateway WebSocket and the share tunnel. Role-aware reads validate mask direction, FIN, RSV, opcode, canonical length, the 64-bit high bit, control-frame size, and a payload cap; the gateway keeps its old call sites through aliases. |

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`webui/`](webui/) | The hand-written browser client and the scientific artifact renderers, served as static files by the gateway. There is no build step and no npm. The client's one third-party library is 3Dmol, injected on demand from `webui/vendor/`, with a fallback to the `3Dmol.org` CDN if the vendored copy fails to load. |

## Change guidance

- Keep [`gateway.py`](gateway.py) a composition and compatibility facade, edited surgically. New domain behaviour goes into the service that owns it.
- Any change to kernel lifecycle, WebSocket streaming, execution ownership, or artifact capture needs an end-to-end browser run on top of the focused tests.
- Browser DTOs stay bounded and redacted. Raw provider payloads, tool arguments, credentials, and unrestricted filesystem paths do not belong in a projection.

See the repository [architecture guide](../../docs/architecture.md), [Web application guide](../../docs/webapp.md), and the [`webui/` README](webui/README.md).

- [`security_headers.py`](security_headers.py) — the hash-based CSP and hardened response headers applied to every response.
- [`contract.py`](contract.py) — the versioned surface's shared envelope, error codes, and route/event inventory.
