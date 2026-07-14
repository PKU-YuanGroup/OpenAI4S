# Agent outer loop

[中文说明](README_zh.md)

The outer-loop state machine lives here, together with the adapters that compose it for local/CLI runs. The machine itself knows nothing about any particular provider. The Web session runner builds its own composition inside the server package, but it routes actions and drives the Engine through the same contracts defined here.

## Where this fits

Each model reply is routed to at most one action kind:

1. an ordered batch of provider-native JSON control-tool calls;
2. a sole valid Engine-owned `finalize_response` action; or
3. the first complete fenced Python/R cell.

Native calls take priority over code. A mixed or malformed finalizer is not completion. `host.submit_output(...)` is the only completion that can fire inside a Python Cell; a later sole valid `finalize_response` may still close the Engine after earlier Cells. Plain prose, ordinary tool observations, R cells, cancellation, and max-turn exhaustion remain non-completing outcomes.

The outer loop reaches for its foreground inner-loop kernel manager only when the action is code. A tool-only or finalizer-only turn therefore never starts that worker. An individual control tool can still manage a dedicated worker of its own, as part of what that tool does.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](./__init__.py) | The package surface: the Engine, the local `Agent` facade and `run_task`, the result values, and the finalization helpers. |
| [`actions.py`](./actions.py) | The one place a reply becomes an action. It normalizes native calls and recognizes Python/R fences; where both appear, the native calls win. A `finalize_response` is picked out as the Engine finalizer only when it is the sole native call. Both outer loops route through this module, so they cannot drift apart. |
| [`compaction.py`](./compaction.py) | Decides when the context is too large and what leaves it. Text, images, native calls, and provider wire state are budgeted separately, and every action/result pair stays indivisible. An oversized output moves into a digest-addressed archive and leaves a bounded preview plus a SHA-256 reference in its place. The slice that gets compacted away becomes a structured handoff; the raw slice is also archived as its own record, carrying the branch, ledger, and recovery metadata that ties it back to the run. |
| [`control.py`](./control.py) | Runs one native-tool batch and closes every declaration with exactly one result, cancellation included. A leading run of read-only calls with non-conflicting resources may go in parallel; the first mutating or unclassified call is a barrier, and results are always written back in the provider's original order. |
| [`delegation.py`](./delegation.py) | The sub-agent tree behind `host.delegate`. The tree owns the fan-out, session, and depth budgets; each runner owns only its direct children, their executor, and their collected results. Cancelling reaches a child and exactly its descendants, and a stopped child can never publish a late output. Steering messages wait in memory and are consumed at a child's next turn boundary. |
| [`engine.py`](./engine.py) | The state machine itself. Pure and provider-neutral, it speaks only to ports: model, context, action executor, completion, cancellation, reply interceptor, and events. |
| [`events.py`](./events.py) | The typed lifecycle events `AgentEngine` emits. |
| [`finalize.py`](./finalize.py) | Owns the `finalize_response` schema. Providers see a metadata-only spec, the Host revalidates the same closed schema before accepting anything, and one valid sole call becomes a structured completion record. It is deliberately not registered as a control `Tool`. |
| [`ledger.py`](./ledger.py) | Writes typed engine events into the append-only Action Ledger, redacting declared secrets on the way in. Reading back, it reduces groups into a restart history a provider will accept, closing any tool call that a crash left without a result. |
| [`loop.py`](./loop.py) | The backward-compatible local `Agent` facade, and the owner of local process lifecycle. It wires the Engine to the model, the dispatcher, the ledger, delegation, and persistent kernels that only start once a turn actually runs code. |
| [`models.py`](./models.py) | The provider-neutral values that cross the Engine: a normalized model reply, mutable run state, one execution outcome, and the final result. |
| [`ports.py`](./ports.py) | The protocols the Engine depends on, plus a no-op default for each. This is what keeps `engine.py` from importing a concrete model, storage, kernel, or UI. |
| [`runtime.py`](./runtime.py) | The local side of those ports. A blocking LLM client, compaction, native tools, Python/R cell execution, the CLI transcript projection, and the read-back of a completion — one adapter each, and the Engine sees none of them directly. |

## Extension and verification contract

- A new action kind goes through `actions.py`, the typed models and events, and both compositions, local and Web. Routing order must stay deterministic.
- Keep `engine.py` free of concrete provider, kernel, Store, and Gateway dependencies.
- The ledger must keep every provider tool call paired with its result, including the synthetic results it writes to close a group after a crash.
- Re-run the agent tests after any change to routing, completion, compaction, or delegation. A change to the execution protocol needs the kernel tests as well.
