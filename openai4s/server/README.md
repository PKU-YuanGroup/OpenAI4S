# Server

[中文](README_zh.md)

This package is the Web composition layer of OpenAI4S. It turns the provider-neutral agent engine, persistent Python/R kernels, the Host capability boundary, and SQLite repositories into one stdlib HTTP/WebSocket application. The package deliberately keeps domain logic in focused services while [`gateway.py`](gateway.py) remains the compatibility and transport facade.

## Place in the architecture

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

- **Gateway composition.** [`gateway.py`](gateway.py) creates and composes the supported stdlib `ThreadingHTTPServer`, routing, REST handlers, WebSocket framing/resume, session runners, services, storage, and static assets. [`daemon.py`](daemon.py) is a separate legacy minimal `/`, `/health`, and `/run` compatibility server, not part of the full Gateway composition. New algorithms should normally live in a focused module rather than enlarging the facade.
- **REST and WebSocket.** REST endpoints perform bounded request/response operations and expose session-domain read models. The WebSocket channel streams agent prose, action/cell lifecycle, approvals, notebook updates, and terminal events, with reconnect/resume buffering.
- **Session services and projections.** Mutation services own plans, reviews, artifacts, branching, recovery, packages, skills, and deletion. Projection services turn canonical ledger, execution, lineage, context, and security state into redacted browser-safe DTOs; a projection is not the underlying terminal or transactional signal.
- **Kernel ownership.** A Web session owns independent lazy Python and R slots through its `SessionRunner`. [`execution_coordinator.py`](execution_coordinator.py) serializes Agent, REPL, recovery, and lifecycle writers by FIFO ticket and permits interruption only for the exact owner/lease. Tool-only routing does not start the foreground session slot; an individual tool may manage a dedicated worker.
- **Persistence boundary.** Durable facts are written through `Store` repositories. WebSocket state and live kernel namespaces are process-local; there is no transaction spanning SQLite, workspace files, a kernel process, and a socket delivery.

## Completion, Notebook, and recovery boundaries

- A Cell result is an outer-loop observation, not task completion by itself. Success requires a sole valid Engine-owned `finalize_response` or `host.submit_output(...)` from inside a Python Cell; R Cells cannot complete a task.
- A protocol-only `host.submit_output` Cell remains in the raw execution/audit history but is filtered from the live and reopened Notebook projections. The current `.ipynb` exporter reads immutable execution history without that filter, so its output is a raw/audit export and may include the system Cell.
- Recovery execution is wired through REST/UI, FIFO ownership, and Python/R candidate pipelines, but remains **Partial**: unsafe or nondeterministic Cells are classified `never`, arbitrary historical namespaces are not serialized, and a language candidate that cannot become active can stop the overall recovery with an explicit Partial result.

## Direct files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Stable package facade exporting `build_server` and `serve`. |
| [`action_timeline.py`](action_timeline.py) | Builds a bounded, redacted Timeline projection from canonical Action Ledger groups, events, attempts, permissions, usage, and artifact references. |
| [`agent_run.py`](agent_run.py) | Adapts `AgentEngine` to Web contracts: streams safe prose/code drafts, emits Web events, handles cancellation, and executes native actions or cells through injected ports. |
| [`artifacts.py`](artifacts.py) | Captures, versions, edits, renames, uploads, restores, and promotes workspace artifacts while coordinating snapshots, provenance, and broadcasts. |
| [`cell_run.py`](cell_run.py) | Orchestrates one Python/R cell from execution admission through kernel execution, live output, artifact capture, execution logging, and terminal projection. |
| [`completions.py`](completions.py) | Produces localized, public progress/outcome narration and renders structured completion plus actual artifact deltas without exposing hidden reasoning. |
| [`daemon.py`](daemon.py) | Legacy minimal threaded HTTP compatibility server exposing `/`, `/health`, and `/run`; it is not the full Gateway and does not own the Gateway's WebSocket, origin/auth, or singleton lifecycle. |
| [`execution_coordinator.py`](execution_coordinator.py) | Web adapter over session-scoped FIFO execution ownership, exact ticket/lease cancellation, admission state, and cleanup. |
| [`execution_views.py`](execution_views.py) | Projects immutable cell history, runtime generations, dependencies, stale state, retries, and lineage into Notebook/execution DTOs. |
| [`gateway.py`](gateway.py) | Main HTTP/WebSocket composition facade: protocol framing, hub/resume buffers, `SessionState`/`SessionRunner`, REST routes, static serving, security checks, and service wiring. |
| [`global_views.py`](global_views.py) | Produces project-wide research Timeline and artifact-lineage views across sessions. |
| [`model_discovery.py`](model_discovery.py) | Performs bounded, redirect-resistant discovery of OpenAI-compatible model endpoints on loopback only. |
| [`model_profiles.py`](model_profiles.py) | Validates, migrates, persists, selects, and removes model-provider profiles while cleaning credentials from public results. |
| [`notebook_export.py`](notebook_export.py) | Deterministically exports raw immutable Python or R execution history as read-only `.ipynb` files and checksum-described bundles; unlike the Notebook projection, it currently may include a protocol-only completion Cell. |
| [`plans.py`](plans.py) | Owns structured plan parsing, normalization, draft/final lifecycle, review transition, execution, public projection, and plan artifacts. |
| [`recovery_control.py`](recovery_control.py) | Projects recovery journal/generation state and composes validated, redacted recovery action plans. |
| [`recovery_execution.py`](recovery_execution.py) | Runs one recovery mutation through exact execution ownership and verified Python/R recovery pipelines. |
| [`recovery_recipe.py`](recovery_recipe.py) | Conservatively compiles immutable cell facts, dependency closure, environment needs, sidecars, and determinism checks into a recovery recipe. |
| [`recovery_runtime.py`](recovery_runtime.py) | Provides concrete Python/R candidate kernels, environment probes, bootstrap, verification, commit, and rollback for session recovery. |
| [`renderers.py`](renderers.py) | Defines the safe artifact-kind/content-type/extension to scientific renderer registry and public renderer descriptors. |
| [`reviews.py`](reviews.py) | Builds bounded evidence packets and orchestrates cancellable scientific review, persistence, usage, and public review events. |
| [`session_branching.py`](session_branching.py) | Coordinates checkpoints, isolated forks, revert previews, append-only revert/undo history, workspace conflict checks, and branch activation. |
| [`session_deletion.py`](session_deletion.py) | Cleans durable session aggregates, workspaces, snapshots/CAS references, and process-local state without crossing ownership scopes. |
| [`session_domain.py`](session_domain.py) | High-level session-domain composition for checkpoints, cursor checkpoints, branches, timelines, export, renderers, package operations, and recovery. |
| [`session_package.py`](session_package.py) | Creates and imports deterministic, checksum-verified, secret-filtered, traversal-safe session ZIP packages through quarantine. |
| [`session_recovery.py`](session_recovery.py) | Reconciles stale runtime state at startup and performs deterministic idle-kernel sweeping subject to activity and recovery blockers. |
| [`session_runtime.py`](session_runtime.py) | Holds session-scoped control-plane objects such as delegation trees and dynamic capabilities independently of language workers. |
| [`skill_sidecars.py`](skill_sidecars.py) | Records successfully loaded Skill sidecars against an exact kernel generation so recovery can replay the observed immutable manifest. |
| [`skills.py`](skills.py) | Implements the Web Customize lifecycle for validated user-authored Skill documents and capability enablement. |
| [`titles.py`](titles.py) | Generates safe background session titles with late-bound model configuration and race-safe persistence/broadcast. |
| [`variable_inspector.py`](variable_inspector.py) | Reads a live, idle Python/R namespace through a narrow protocol and returns bounded, sanitized variable previews. |
| [`workbench_state.py`](workbench_state.py) | Projects Context and Security panels from persistent and live state without leaking message content or overstating sandbox guarantees. |

## Direct subdirectories

| Directory | Responsibility |
| --- | --- |
| [`webui/`](webui/) | Dependency-free browser client and scientific artifact renderers served by the gateway. |

## Change guidance

- Preserve [`gateway.py`](gateway.py) as a surgical composition/compatibility facade; place new domain behavior in the relevant service.
- Any change to kernel lifecycle, WebSocket streaming, execution ownership, or artifact capture needs an end-to-end browser run in addition to focused tests.
- Browser DTOs must stay bounded and redacted. Raw provider payloads, tool arguments, credentials, and unrestricted filesystem paths do not belong in projections.

See the repository [architecture guide](../../docs/architecture.md), [Web application guide](../../docs/webapp.md), and the [`webui/` README](webui/README.md).
