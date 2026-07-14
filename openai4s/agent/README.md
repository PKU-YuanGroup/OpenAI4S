# Agent outer loop

[中文](./README_zh.md)

**Status: Implemented.** This package owns the provider-neutral outer-loop state machine and the adapters that compose it for local/CLI execution. The Web session runner uses the same action-routing and engine contracts through its own server composition.

## Architectural position

Each model reply is routed to at most one action kind:

1. an ordered batch of provider-native JSON control-tool calls;
2. a sole valid Engine-owned `finalize_response` action; or
3. the first complete fenced Python/R cell.

Native calls take priority over code. A mixed or malformed finalizer is not completion. `host.submit_output(...)` is the only completion that can fire inside a Python Cell; a later sole valid `finalize_response` may still close the Engine after earlier Cells. Plain prose, ordinary tool observations, R cells, cancellation, and max-turn exhaustion remain non-completing outcomes.

The outer loop invokes its foreground inner-loop kernel manager only for code. Tool-only and finalizer-only routing therefore does not start that worker; an individual control tool may still manage a separate dedicated worker as part of its own capability.

## Files directly in this directory

| File | Responsibility |
| --- | --- |
| [`__init__.py`](./__init__.py) | Re-exports the Engine, local `Agent` facade, result values, finalization helpers, and `run_task`. |
| [`actions.py`](./actions.py) | Single action parser/router: normalizes native calls, recognizes Python/R fences, enforces native-call priority, and identifies a sole Engine finalizer. |
| [`compaction.py`](./compaction.py) | Estimates context budgets, keeps action/result pairs indivisible, externalizes oversized outputs by digest, produces structured handoffs, and archives compacted slices. |
| [`control.py`](./control.py) | Validates and executes native-tool batches, including cancellation, resource-conflict checks, and safe read-only parallel waves while preserving ordered results. |
| [`delegation.py`](./delegation.py) | Implements bounded sub-agent trees, fan-out/session/depth budgets, exact descendant cancellation, result collection, and turn-boundary steering. |
| [`engine.py`](./engine.py) | Pure provider-neutral outer state machine over model, context, action-executor, completion, cancellation, interceptor, and event ports. |
| [`events.py`](./events.py) | Defines typed lifecycle events emitted by `AgentEngine`. |
| [`finalize.py`](./finalize.py) | Defines and validates the Engine-owned `finalize_response` schema and converts a valid sole call into a structured completion record. It is not registered as a control `Tool`. |
| [`ledger.py`](./ledger.py) | Persists typed engine events to the append-only Action Ledger, redacts declared secrets, and reduces incomplete groups into provider-safe restart history. |
| [`loop.py`](./loop.py) | Backward-compatible local `Agent` facade that composes the Engine with model, dispatcher, lazy persistent kernels, ledger, delegation, and process lifecycle. |
| [`models.py`](./models.py) | Provider-neutral immutable/mutable values for model replies, run state, execution outcomes, and final Engine results. |
| [`ports.py`](./ports.py) | Protocol definitions and no-op defaults that isolate the pure Engine from concrete models, storage, kernels, and UI code. |
| [`runtime.py`](./runtime.py) | Local adapters for the blocking LLM client, compaction, native tools, Python/R kernels, transcript projection, and completion capture. |

## Direct subdirectories

None.

## Extension and verification contract

- Add a new action kind only through `actions.py`, typed models/events, and both local and Web compositions; ordering must remain deterministic.
- Keep `engine.py` free of concrete provider, kernel, Store, and Gateway dependencies.
- Preserve provider tool-call/result atomicity in the ledger, including synthetic closure after crashes.
- Re-run agent tests after routing, completion, compaction, or delegation changes; kernel tests are also required when the execution protocol changes.
