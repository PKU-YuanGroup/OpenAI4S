# Scientific cell execution policies

[中文](./README_zh.md)

**Status: Implemented shared execution layer.** This package contains provider- and UI-neutral policies used when an outer-loop action or an explicit notebook request reaches a scientific Python/R cell.

## Architectural position

The package sits between outer-loop/Web adapters and the persistent managers in [`../kernel/`](../kernel/). It does not parse model replies, perform Host RPC, or execute code itself. Instead it serializes writers per session, gives every request an exact owner/ticket/lease identity, projects namespace dependencies, defines normalized request/result values, and supervises timeouts.

The FIFO coordinator covers Agent, user REPL, lifecycle, and recovery writers. Cancellation targets the exact ticket; adapters must still deliver interrupts through the matching kernel generation/lease. This prevents a stale cancellation from interrupting a newer owner.

## Files directly in this directory

| File | Responsibility |
| --- | --- |
| [`__init__.py`](./__init__.py) | Re-exports coordinator errors/types, cell request/result values, and capture metadata. |
| [`coordinator.py`](./coordinator.py) | Implements observable per-session FIFO admission, ticket lifecycle, exact cancellation signals, leases, queue snapshots, and shutdown/recovery transitions without executing code directly. |
| [`dependencies.py`](./dependencies.py) | Uses Python AST and a conservative R lexer to record best-effort namespace reads/writes/deletes, visibility, replay policy, and stale-cell projections. It is not a security boundary. |
| [`models.py`](./models.py) | Defines provider/UI-neutral `CellRequest`, `CaptureResult`, and `CellExecutionResult` dataclasses. |
| [`watchdog.py`](./watchdog.py) | Applies a protocol-neutral timeout ladder to one frozen kernel lease: wait, interrupt the exact owner, kill if necessary, then restart or abandon according to policy. |

## Direct subdirectories

None.

## Concurrency and recovery contract

- Never bypass `SessionExecutionCoordinator` for a session-scoped writer.
- Carry the exact ticket and kernel generation through interrupt/recovery paths; IDs that merely look related are insufficient.
- Treat dependency metadata as a conservative projection. Dynamic imports, reflection, native extensions, and arbitrary side effects cannot be proven statically.
- Keep watchdog policy independent of Web sessions, Artifacts, completion, and persistence so adapters can reuse it safely.
