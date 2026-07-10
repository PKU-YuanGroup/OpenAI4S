# Backend refactor architecture

This document records the agreed target architecture for the OpenAI4S backend
refactor. It is a compatibility-preserving reorganization, not a reduction of
the product to CoreCoder's function-calling loop.

## Confirmed decisions

1. OpenAI4S remains a hybrid agent with two deliberately separate action
   planes:

   - **Native JSON tool calling is the control plane.** It is limited to
     orchestration, permission checks, external services, and operations that
     require human approval. Providers must expose these calls through their
     native structured tool-call protocol; fenced ```` ```tool ```` JSON is not the
     target representation.
   - **Python/R Code-as-Action is the scientific execution plane.** Data
     loading, transformation, numerical analysis, simulation, plotting, model
     execution, and other scientific computation continue to run as real code
     in persistent Python or R kernels.

2. The Python kernel retains synchronous mid-cell `host.*` RPC. A cell may use
   host services while its scientific program is running; native JSON tool
   calls do not replace this inner loop.

3. `host.submit_output(...)`, called from a Python cell, is the **only** task
   completion signal. Plain assistant text, an R cell, a native tool result,
   an empty action, or exhaustion of work does not mean success. Maximum turns,
   cancellation, refusal, timeout, and failure are non-success stop reasons.

4. All current capabilities are retained, including persistent Python/R
   kernels, delegation, skills, MCP, web access, compute/folding, environment
   management, artifacts and lineage, replay/review, security controls,
   permissions, the CLI, the HTTP/WebSocket server, and session persistence.

5. Public contracts remain compatible. This includes documented Python APIs,
   `host.*`, CLI behavior and environment variables, REST and WebSocket
   payloads, persisted sessions and database data, and user-visible behavior.
   Internal module paths and private implementation details may change. Public
   imports that move must keep compatibility re-exports for the supported
   deprecation period.

6. The refactor is delivered as a sequence of reviewable commits. Each phase
   must preserve a runnable system and pass the relevant offline and end-to-end
   checks before the next phase starts.

## Action model

The outer loop consumes one normalized assistant reply and chooses one action
kind:

```text
assistant reply
    |
    +-- native JSON tool call(s) --> control-plane executor
    |                                orchestration / permission / external
    |                                service / human approval
    |
    +-- one Python or R cell ------> scientific executor
    |                                persistent language kernel
    |
    +-- no valid action -----------> corrective observation
```

Provider-specific streaming formats are normalized before they reach the
agent loop. Native tool-call IDs and their results remain paired as one
indivisible protocol group. Likewise, a code action and its observation remain
an indivisible turn group for interruption handling and context compaction.

The control plane and scientific plane share policy and observability, but not
responsibility:

- A native tool must not become an escape hatch for arbitrary scientific code
  or host-side shell execution.
- Scientific execution must not bypass permission, egress, injection, or
  safety policy when it crosses into a host service.
- Both native tools and in-kernel `host.*` calls route to the same capability
  registry and service implementations so authorization and auditing cannot
  drift.
- `submit_output` is intentionally absent from the native JSON tool registry.
  It remains an in-kernel host capability available only to the Python control
  channel.

Human approval is represented as a durable control-plane state. An operation
may pause with a pending approval and later resume the same session; approval
must not be inferred from model text.

## One authoritative agent engine

There will be one reusable `AgentEngine` for in-process, CLI, WebSocket, root,
and delegated runs. Server and CLI layers adapt events and persistence; they do
not implement their own model/action/execution loop.

The engine depends on small internal protocols:

```text
ModelClient       messages -> normalized reply + native tool calls + usage
ActionRouter      normalized reply -> CodeAction | NativeToolBatch | NoAction
ContextPolicy     complete turn groups -> retained/compacted context
ActionExecutor    action -> observation + optional submitted completion
EventSink         typed run events -> CLI / WebSocket / persistence adapters
Cancellation      reports whether the current run should stop
```

`AgentEngine` must not construct or import a concrete store, gateway, kernel,
dispatcher, skill loader, replay recorder, or WebSocket hub. Those dependencies
are assembled at the application boundary. The loop remains small enough that
its state transitions and terminal conditions can be reviewed in one place.

## Target package layout

The target is intentionally compact. A directory is introduced only when it
owns a real dependency boundary or replaces one of the current oversized
modules.

```text
openai4s/
  bootstrap.py                 application composition root
  config.py                    compatible configuration facade

  agent/
    engine.py                  the only outer agent loop
    actions.py                 normalized action types and routing rules
    context.py                 grouped compaction and archive policy
    events.py                  typed engine events
    prompts.py                 prompt assembly and capability descriptions
    delegation.py              bounded child-agent orchestration

  llm/
    client.py                  provider-neutral streaming and retry policy
    models.py                  normalized reply, delta, usage, tool-call types
    providers.py               OpenAI-compatible, Anthropic, and Gemini wires

  execution/
    executor.py                routes code actions and native tool batches
    runtime.py                 Python/R runtime ownership and environment switch
    completion.py              submit_output terminal-state handling

  kernel/
    protocol.py                single frame/response protocol definition
    manager.py                 worker lifecycle and synchronous host RPC
    worker.py                  persistent Python worker
    r_kernel.py                persistent R channel
    r_worker.R
    background.py
    environments.py
    guards.py
    provenance.py

  host/
    registry.py                capability metadata and handler registration
    dispatcher.py              routing, authorization, audit, soft-fail boundary
    services/                  filesystem, web, LLM, delegation, artifacts,
                               compute, skills, MCP, query, environment

  sdk/
    host.py                    compatible in-kernel facade
    rpc.py                     host_call transport
    namespaces/                modular host.env/skills/mcp/compute facades

  session/
    models.py                  session and message-job state
    service.py                 submit, resume, cancel, steer, kernel lifecycle
    artifacts.py               capture, versioning, restore, lineage integration
    review.py                  review orchestration
    events.py                  engine events to durable/UI events

  storage/
    database.py                connection, transaction, schema migration owner
    repositories.py            frames, messages, executions, artifacts, agents,
                               skills, memories, notes, and host-call logs

  tools/
    registry.py                native JSON control-tool schemas
    executor.py                tool-call pairing and dispatcher adapter

  security/                    classifier, permissions, egress, shell checks,
                               biosecurity, injection screening, audit hook
  compute/                     remote compute manager and provider registry
  skills/                      skill loading and validation
  mcp/                         MCP client and bundled servers
  replay/                      recording and playback

  server/
    app.py                     HTTP composition only
    routes.py                  REST adapters
    websocket.py               WebSocket transport and event presentation
    daemon.py
    webui/                     existing static frontend

  cli/
    main.py
    renderer.py                engine-event presentation
```

The exact filenames may be adjusted during extraction, but the ownership and
dependency boundaries above are architectural constraints.

## Dependency rules

1. `agent.engine` imports only agent action/context/event abstractions and the
   injected protocols. It never imports `server`, `storage`, or concrete kernel
   and host implementations.
2. `llm` owns provider wire differences. No provider-specific response object
   crosses into the agent engine.
3. `execution` is the only layer that maps an agent action to either a
   persistent kernel or a native control-tool batch.
4. `host.dispatcher` is a thin policy-aware router. Capability business logic
   lives in `host.services`; both native tools and kernel RPC use it.
5. `session` owns durable run lifecycle. `server` and `cli` are adapters over
   session/engine services and cannot contain another agent loop.
6. `storage` owns transactions and schema migrations. Repositories do not open
   independent competing connections for one logical operation.
7. Kernel workers import the kernel-side `sdk` and security guards, never
   server or storage code. The host process continues to execute no shell on
   behalf of the model.
8. Capability restriction is structural. Leaf agents, reviewers, plan runs,
   and approval-pending runs receive restricted registries/executors instead of
   relying on prompt instructions alone.
9. Core runtime code remains Python standard-library only. The refactor does
   not introduce mandatory framework or SDK dependencies.

## Compatibility gates

Before moving behavior, the current contracts must be inventoried and protected
with characterization tests. At minimum the gates cover:

- provider streaming, native tool-call assembly, retries, and usage accounting;
- exactly one scientific cell per step and Python/R language routing;
- tool-call/result and code-action/observation pairing after interruption;
- `host.submit_output` as the sole successful completion path;
- host SDK method names, arguments, soft-fail behavior, and workspace jail;
- permission, approval, egress, injection, classifier, and audit behavior;
- kernel generation, RPC transaction locking, timeout recovery, and restart;
- delegation depth/fanout/session caps and child context isolation;
- artifact versions, capture, provenance, restore, and dependency mappings;
- REST/WebSocket event payloads, CLI behavior, configuration variables, and
  persisted-session resume;
- SQLite migrations that preserve existing user data.

Compatibility shims may forward old public imports or call signatures to new
implementations. They must not duplicate business logic.

## Migration phases

Each phase is a separate commit or short commit series with an explicit test
gate. Later phases do not begin while the preceding compatibility gate is red.

### Phase 1 — Architecture and contract baseline

- Record this decision document.
- Inventory public Python, CLI, environment, HTTP/WebSocket, host SDK, and
  persistence contracts.
- Add characterization tests for the current critical invariants.
- Make no runtime behavior change.

### Phase 2 — Normalize replies and actions

- Introduce provider-neutral reply, native tool-call, code-action, observation,
  usage, and stop-reason types.
- Move provider streaming reconstruction behind the LLM boundary.
- Introduce the single action router while preserving current behavior through
  adapters.

### Phase 3 — Introduce the single `AgentEngine`

- Extract the common state machine from the in-process agent and gateway loop.
- Route both existing entry points through the engine.
- Preserve current event payloads and persistence through adapters.
- Delete the duplicate loop only after parity tests and browser E2E pass.

### Phase 4 — Establish the native JSON control plane

- Register native tool schemas only for the agreed control-plane operations.
- Route their execution through the same host capability registry used by
  in-kernel RPC.
- Add durable human-approval pause/resume behavior.
- Retire fenced ```` ```tool ```` transport after provider and UI parity is proven;
  keep only a temporary compatibility parser if required for saved context.

### Phase 5 — Extract host, SDK, session, and storage boundaries

- Split `host_dispatch.py`, `sdk/host.py`, `store.py`, and gateway session logic
  surgically behind compatibility facades.
- Move one cohesive service/repository at a time; do not wholesale-rewrite the
  large files.
- Apply explicit SQLite migrations and verify old-session resume.

### Phase 6 — Thin transports and remove legacy internals

- Reduce CLI and server code to adapters over session/engine services.
- Remove private legacy modules only when no supported import or saved state
  depends on them.
- Run the full offline suite plus real browser tests for streaming, kernels,
  tools, approvals, artifacts, provenance, delegation, and resume.
- Update architecture, web, configuration, security, skills, and compute docs.

## Non-goals

- Replacing Code-as-Action with an all-tool-calling agent.
- Sending scientific computation through native JSON tools.
- Removing the R channel, persistent namespaces, mid-cell host RPC, or the
  structured `host.submit_output` completion contract.
- Dropping existing features to obtain a smaller codebase.
- Breaking public APIs, host SDK calls, CLI/configuration behavior, server
  payloads, or existing persisted data merely to simplify internals.
- Weakening workspace isolation, permission prompts, approval state, egress
  controls, injection screening, code safety, biosecurity, or auditing.
- Introducing third-party dependencies into the standard-library core.
- Rewriting the Web UI or changing product UX except where native approval and
  tool-call events require a backward-compatible presentation update.
- A big-bang rewrite. The final internal structure may change substantially,
  but it is reached through tested, reversible phases.
