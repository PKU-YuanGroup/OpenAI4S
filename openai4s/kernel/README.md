# Kernel runtime

[中文说明](README_zh.md)

This directory implements the persistent scientific execution plane. The outer agent loop selects at most one complete Python or R Cell; this package starts the selected worker lazily, drives its language-neutral JSON-lines protocol, and keeps the namespace alive between Cells. Python additionally supports the inner synchronous Host RPC loop.

## Place in the architecture

1. [`agent/engine.py`](../agent/engine.py) routes a Cell action but does not depend on a concrete kernel.
2. CLI and Web composition create a lazy [`Kernel`](manager.py) or a supervised Python/R slot.
3. The manager sends one `execute` frame. A Python worker may send `host_call`; the manager dispatches it and returns the matching `host_response` before continuing to the final Cell response. Compatibility acknowledgements are not the normal completion path.
4. The worker returns captured output, error/interrupt details, guards, and resource usage, and exposes a separate bounded namespace-inspection request. A Cell result becomes another outer-loop observation; only Python `host.submit_output(...)` can complete from inside a Cell.

The manager must remain the only frame reader for its worker. The worker's protocol-write lock serializes frames, while its Host-call transaction lock permits only one outstanding synchronous RPC. The Web execution coordinator and [`KernelSupervisor`](supervisor.py) coordinate writers and lifecycle around this protocol rather than proxying it.

## Files

| File | Responsibility |
|---|---|
| [`__init__.py`](__init__.py) | Public exports for `Kernel`, `KernelBusyError`, `KernelLease`, and `KernelSupervisor`. |
| [`background.py`](background.py) | Runs `host.exec_background` Cells in dedicated worker processes and keeps thread-safe in-memory status/output for peek and interrupt operations; these jobs do not share the foreground namespace or durable job storage. |
| [`environment.py`](environment.py) | Builds a child environment from an explicit allowlist so daemon credentials, agent sockets, and loader-injection variables are not inherited by kernels or their subprocesses. |
| [`environments.py`](environments.py) | Discovers and caches selectable Python/R environments, resolves interpreters and installed-package metadata, and always exposes the active interpreter as synthetic `base`. |
| [`guards.py`](guards.py) | Best-effort cross-Cell probes for matplotlib figure leakage and selected process-global registry mutations; absent optional libraries make the relevant probe a no-op. |
| [`lazy.py`](lazy.py) | Thread-safe, one-shot lazy worker ownership for tool/finalize-only paths that should not spawn an interpreter; publishes candidates early enough for cancellation and detaches failed bootstraps. |
| [`manager.py`](manager.py) | Host-side subprocess owner and sole JSON-lines frame reader; handles execute/response routing, synchronous Python Host RPC, interrupts, restart generations, output chunks, and OS-sandbox wrapping. |
| [`preinstall.py`](preinstall.py) | Reports and optionally installs the scientific package baseline or requested packages, then relies on caller-controlled kernel restart; it is package-management support, not a hard import dependency of the stdlib core. |
| [`provenance.py`](provenance.py) | Installs best-effort object-level lineage instrumentation inside Python, tagging supported reads and propagating input version IDs to reported writes. |
| [`r_kernel.py`](r_kernel.py) | Resolves a real `Rscript` and constructs the file-descriptor-safe command used to run the R sibling through the common manager; it never silently substitutes Python. |
| [`r_worker.R`](r_worker.R) | Persistent R analysis worker with the common execute/response contract, output capture, interruption, trace, and resource accounting. It has no `host` object, mid-Cell RPC, or completion signal. |
| [`recovery.py`](recovery.py) | Builds and validates replacement kernels from content-addressed, canonical bootstrap recipes and conservatively classified replay steps; publishes a candidate only after validation and reports `partial` when state cannot safely be reconstructed. |
| [`supervisor.py`](supervisor.py) | Owns durable Python/R session slots, exact generation leases, lifecycle timestamps, and ABA-safe interrupt/watchdog replacement without reading protocol frames. |
| [`worker.py`](worker.py) | Persistent Python worker: isolates protocol file descriptors from stdout, captures Cell output, enforces single Host-call transactions, injects `host`, records source lines, handles SIGINT, installs guards/audit hooks/provenance, and returns bounded inspection/usage data. |

## Subdirectories

There are no tracked child directories in this package.

## Security and failure boundaries

- Environment filtering, the pre-execution classifier, OS sandbox, in-worker audit hook, durable approval, and the one-shot shell capability are independent layers. Presence of one does not imply the others succeeded.
- [`manager.py`](manager.py) uses [`security/sandbox.py`](../security/sandbox.py). `enforce` fails closed when confinement is unavailable; `auto` may continue with a visible degraded/unavailable status after the real self-test fails.
- Python code is intentionally powerful inside its granted workspace. [`environment.py`](environment.py) prevents inheritance of recognized secrets but cannot make arbitrary Cell code trustworthy.
- The R worker depends on `jsonlite` for inbound requests. It emits a structured error if that package is missing and remains analysis-only.
- Background execution uses a dedicated worker, but its job registry and accumulated streamed output are process memory rather than durable or size-bounded storage; very verbose long jobs can increase daemon memory.
- Provenance and guards are observational and best-effort. Unsupported objects, libraries, native transformations, or explicit opt-out can leave lineage incomplete.
- Recovery does not serialize a live Python/R namespace. It builds a new generation, replays only conservatively accepted steps, validates manifests, and may truthfully return partial recovery.
- Supervisor interrupt/restart calls must use the exact lease and the session execution barrier. Bypassing those ownership rules can race the manager's single frame reader.

## Related documentation

- [System architecture](../../docs/architecture.md)
- [Security model](../../docs/security.md)
- [Jupyter and kernel behavior](../../docs/jupyter.md)
- [Web runtime](../../docs/webapp.md)
