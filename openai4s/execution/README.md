# Scientific cell execution policies

[中文说明](README_zh.md)

This is the shared execution layer. Once an outer-loop action or an explicit notebook request has become a scientific Python/R cell, the policies that apply to it live here. None of them know which provider produced the cell or which UI asked for it.

## Where this fits

The package sits between the outer-loop/Web adapters above and the persistent managers in [`../kernel/`](../kernel/) below. It parses no model replies, performs no Host RPC, and runs no code itself. What it does own: one scientific writer at a time per session, an exact owner/ticket/lease identity for every request, a projection of the namespace a cell depends on, the normalized request/result values both adapters share, and timeout supervision.

The FIFO coordinator covers Agent, user REPL, lifecycle, and recovery writers. Cancellation targets the exact ticket, and the adapter still has to deliver the interrupt through the matching kernel generation and lease. That is what keeps a stale cancellation from interrupting a newer owner.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](./__init__.py) | Re-exports the coordinator's tickets and errors, the cell request/result and capture values, and the watchdog policy. |
| [`coordinator.py`](./coordinator.py) | Per-session FIFO admission and the whole observable life of a ticket: queue position, the cancellation signal that only its exact holder sees, snapshots for UI and persistence, and the shutdown/recovery transitions. It admits and releases writers; it never executes code and never delivers a process signal, which the caller does through the kernel lease bound to the admitted ticket. |
| [`dependencies.py`](./dependencies.py) | Uses Python's `ast` and a deliberately small R lexer to record what each cell reads, writes, and deletes, and projects stale cells from those edges. The `visibility` and `replay_policy` defaults come from here too. A construct that can change the namespace without naming it is flagged uncertain rather than guessed at: this is a conservative projection, not a security boundary. |
| [`models.py`](./models.py) | The three dataclasses passed across the boundary: `CellRequest`, `CaptureResult`, and `CellExecutionResult`. No provider or UI types in any of them. |
| [`watchdog.py`](./watchdog.py) | The protocol-neutral timeout ladder for one frozen kernel lease: wait, interrupt the exact owner, kill if the interrupt does not land, then restart or abandon according to policy. A pending permission decision freezes the timeout budget, and a cancellation still cuts through it. |

## Concurrency and recovery contract

- Never bypass `SessionExecutionCoordinator` for a session-scoped writer.
- Carry the exact ticket and kernel generation through every interrupt and recovery path. An ID that merely looks related is not the same ID.
- Treat dependency metadata as a conservative projection. Dynamic imports, reflection, native extensions, and arbitrary side effects cannot be proven statically.
- Keep watchdog policy independent of Web sessions, Artifacts, completion, and persistence so adapters can reuse it safely.
