---
title: Backend extension guide
description: Where new control-plane, kernel, storage, and Web behavior belongs.
outline: deep
status: current
audience: [contributors]
verified_commit: a92e736
last_verified: 2026-07-14
owner: OpenAI4S maintainers
---

# Backend extension guide

> Verified against repository revision `a92e736` on 2026-07-14.

OpenAI4S has two action planes and one rule decides where new behaviour belongs:

- Use a native JSON `Tool` for orchestration, permissions, external services,
  metadata, or a human-approval boundary.
- Use Python/R Code-as-Action for computation, exploration, analysis,
  simulation, and long-running scientific execution.
- Engine-level completion uses the closed-schema `finalize_response` action;
  Python can also complete from inside a scientific Cell with
  `host.submit_output(...)`. Neither is a registry `Tool`, and ordinary
  prose/tool results must never be inferred as completion. The Engine does not
  reject a sole `finalize_response` merely because an earlier step ran a Cell.

## Dependency map

```text
provider wire -> AgentEngine -> action router -> append-only Action Ledger
                                  |       |             |
                                  |       |             +-> FinalizeAction
                                  |       +-> Python/R kernel
                                  |                 |
                                  +-> Tool class    +-> synchronous host RPC
                                         |                      |
                                         +--- HostDispatcher ---+
                                                    |
                                   host service classes / repositories
                                                    |
                                           Store compatibility facade
```

`HostDispatcher` owns the shared policy envelope: Host-RPC argument decoding,
permissions, human approval, audit/replay, injection screening, and UI activity
events.
Business behaviour belongs in a tool or service class. `Store` remains the
compatible public facade and connection/migration owner; SQL behaviour belongs
in domain repositories sharing that connection and lock.

## Add a native control tool

Create one module under `openai4s/tools/`. The class must contain its schema,
security policy, and behaviour so a maintainer can understand the capability by
opening one file.

```python
from openai4s.tools.base import Tool


class CreateExperimentTool(Tool):
    name = "create_experiment"
    host_method = "create_experiment"
    description = "Create an approved scientific workflow record."
    parameters = {
        "properties": {
            "type": {"type": "string"},
        },
        "required": ["type"],
    }
    read_only = False
    requires_approval = True
    permission_target_key = "type"
    side_effect_class = "metadata_write"
    resource_key_prefix = "experiment"
    resource_target_key = "type"

    def execute(self, context, arguments: dict) -> dict:
        return context.invoke(self.host_method, {"type": arguments["type"]})
```

Then add the class—not a pre-created instance—to `TOOL_TYPES` in
`openai4s/tools/registry.py`. The registry is the only built-in composition
point and creates the runtime instances in a deterministic order.

Registration and invocation contracts:

- `bash` and `submit_output` can never be native tools;
- tool names must be portable across supported providers;
- network tools must declare untrusted-result screening;
- model-originated calls enter through `Tool.invoke()` and the dispatcher;
  application code must not call `execute()` as a policy bypass.
- mutating tools declare a valid side-effect class and namespaced resource
  keys; unknown input properties are rejected unless a trusted extension
  explicitly opts into an open schema.

`requires_approval` defaults to true. A class that turns it off must document
and test its safe boundary; the registry preserves this class-owned policy,
while the dispatcher is responsible for enforcing it at invocation time.
`read_only` and `resource_keys()` also drive batch scheduling: only a leading
lane of non-conflicting read-only calls may run in parallel, while mutating or
unknown capabilities form a sequential barrier. Declare these fields
conservatively; missing resource identity is treated as a conflict.

Add direct tests for the class behaviour and policy metadata, plus an engine
test for the provider-neutral call/result group when the wire contract changes.
If the tool mutates workspace files, declare `writes_files = True`. The Web
control adapter then snapshots each individual native call and registers every
changed file/version as an Artifact. Do not add Artifact capture to the
dispatcher itself: kernel-side `host.write_file()` is already captured by the
Cell transaction and would otherwise be registered twice.

`finalize_response` is intentionally outside `TOOL_TYPES`. Its schema,
validation, and completion record live in `openai4s/agent/finalize.py`; do not
register another tool with that name or route plugin code around the Engine.

### Session-authored dynamic tools

Model/session-authored tools use the existing `DefineDynamicTool` control path,
not `register_tool()`. Definitions are schema-checked, content-hashed, tested in
an enforced OS sandbox with the kernel environment allowlist, and exposed
through a trusted proxy with a session TTL. Promotion to broader scope is a
separate approval operation. Model-authored code is never imported into the
Host process. If the OS sandbox is unavailable, definition fails closed.

## Add an in-kernel `host.*` capability

The worker-facing signature belongs in `openai4s/sdk/`. A cohesive namespace
such as compute should have its own module; `sdk/host.py` composes and
compatibly re-exports it.

The host-side implementation belongs in a class under `openai4s/host/`:

```python
class ExperimentService:
    def __init__(self, store_provider):
        self._store_provider = store_provider

    def create(self, spec: dict) -> dict:
        store = self._store_provider()
        return store.create_experiment(**spec)
```

Construct the service once in `HostDispatcher.__init__`, using small provider
callbacks when session state can be replaced at runtime. Keep the existing
`_m_<method>` method only as a thin compatibility adapter:

```python
def _m_create_experiment(self, spec: dict) -> dict:
    return self._experiment_service.create(spec)
```

Return `{"error": message}` only for the established soft-fail contract.
Uncaught exceptions are converted at the kernel protocol boundary. Do not
duplicate permission, audit, replay, or injection policy inside the service;
all calls already cross the dispatcher envelope.

## Add persisted data

Create a focused repository under `openai4s/storage/`. Repositories receive the
existing SQLite connection, the existing `RLock`, and a clock callback. They do
not open another connection for application writes.

```python
class ExperimentRepository:
    def __init__(self, connection, lock, *, clock_ms):
        self._connection = connection
        self._lock = lock
        self._clock_ms = clock_ms

    def get(self, experiment_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM experiments WHERE experiment_id=?",
                (experiment_id,),
            ).fetchone()
        return dict(row) if row else None
```

`Store` owns schema creation and migrations, constructs the repository with its
shared connection/lock, and exposes a thin forwarding method. Composite writes
that span several aggregates must keep their existing single-lock and commit
boundary. When old code dynamically called another `Store` method, inject a
late-bound lambda rather than freezing a bound method and silently breaking
subclass/monkeypatch compatibility.

Respect Store generations: `Store.close()` is idempotent and removes only that
exact instance from the `get_store(path)` cache. A long-lived service that may
outlive a Store must resolve the current Store/repository at operation time;
never retain a repository backed by a connection that configuration reload or
test teardown may close. The default `SkillLoader` capability adapter is the
reference pattern.

Repository tests should lock down SQL-visible results, commit/rollback
boundaries, ordering, timestamp evaluation, JSON fallback, and legacy error
shapes. Default tests remain offline.

## Extend the Skill lifecycle

Bundled recipes belong under `skills/` and are read-only. User-authored files
belong under `<data_dir>/user-skills`; reject symlink/path escapes and name
collisions rather than shadowing a bundled Skill. The in-kernel Host editor
uses `draft` and promotes explicitly to `personal`; Web Customize writes whole
documents with `user` origin. Discovery must preserve these user origins and
must never allow a user-space frontmatter value to claim `openai4s` trust.
Capability enablement is durable and scoped, and default loaders must follow
the current Store generation as described above.

## Extend the Action Ledger

`action_groups`, `action_events`, and `execution_attempts` are the canonical
runtime history. Chat messages, Notebook rows, activity cards, and the Action
Timeline are projections. Open a group before execution, append every ordered
result (including validation/permission failures), allocate a Cell attempt
before lazy runtime startup, and append a terminal event. Never update an old
event to make a retry look successful.

Provider wire metadata and raw arguments stay in the durable audit/replay
record. Researcher-facing services must use the redacted, field-bounded
`ActionTimelineService`; do not expose `wire_state`, raw argument strings,
credentials, or unrestricted results in a new REST/WS payload. Its page limit
is 1–500: an initial read returns the latest window, `before_ordinal` moves
older, and `after_ordinal` moves newer. The cursors are non-negative and
mutually exclusive, and the projection returns explicit truncation/cursor
metadata. Consumers must not treat per-field truncation as history pagination.

## Add Web session behaviour

HTTP and WebSocket code is an adapter. Stateful behaviour belongs in a service
under `openai4s/server/` with narrow protocols/callbacks for persistence,
kernel lifecycle, event broadcast, and configuration. `SessionRunner` may keep
a private forwarding method when tests or integrations depend on it, but the
algorithm should be visible in the service module.

Preserve event payload keys and order-sensitive lifecycle rules. Changes to
kernel execution, host RPC, artifact capture, review, streaming, or resume need
both focused tests and a real browser run against `./start.sh`.

Scientific execution must enter `WebExecutionCoordinator`: submit an owner
(`agent`, `user_repl`, `lifecycle`, or `recovery`), wait for FIFO admission,
bind the exact `KernelLease`, and mark finalizing before publication. Cancel and
interrupt adapters must require the exact execution ID and owner pair; never
reintroduce a session-global broad interrupt.

Checkpoint/revert, recovery projection, Timeline, Notebook export, and renderer
selection belong behind `SessionDomainService`. Their algorithms are already
implemented independently of HTTP, and the Gateway routes are thin adapters to
that service. Extend those adapters instead of duplicating CAS, journal,
`.ipynb`, Timeline, or renderer logic in `gateway.py`. Recovery status and all
five action descriptors are public. Only `restore`, `retry`, and
`restart_fresh` are mutating operations; they must continue to enter through
an exact coordinated Gateway execution ticket.

## Definition of done

For every backend extension or extraction:

1. The class file contains the behaviour; the registry/dispatcher/facade only
   composes or forwards.
2. Core imports remain standard-library-only.
3. Public SDK, `host.*`, CLI, REST/WebSocket, SQLite, and saved-session contracts
   remain compatible or receive an explicit migration.
4. Terminal behavior remains explicit: an Engine-owned `FinalizeAction`, or
   `host.submit_output()` as the only completion emitted from inside a Python
   Cell. Ordinary prose and normal tool results never complete a run.
5. Run focused tests, the full offline suite, and the browser flow when session,
   kernel, RPC, artifact, or UI behaviour is involved.
6. Commit one cohesive change at a time.

Avoid module-level tool singletons, duplicate agent loops, host-side shell
execution, independent repository connections, provider response types leaking
into `AgentEngine`, and scientific computation disguised as JSON tools.
