# Kernel runtime

[中文说明](README_zh.md)

The persistent Python and R kernels live here, and with them the system's scientific execution plane. The outer agent loop hands this package at most one complete Cell at a time; the worker starts on first use, speaks a language-neutral JSON-lines protocol, and keeps its namespace alive across Cells. The inner synchronous Host RPC loop is Python-only.

## Where this fits

1. [`agent/engine.py`](../agent/engine.py) routes a Cell action without depending on any concrete kernel.
2. CLI and Web composition create a lazy [`Kernel`](manager.py) or a supervised Python/R slot.
3. The manager sends one `execute` frame. A Python worker may answer with `host_call`; the manager dispatches it and writes back the matching `host_response` before it goes on waiting for the Cell's final response. Compatibility acknowledgements are not the normal completion path.
4. The worker returns captured output, error and interrupt details, guard reports, and resource usage. Namespace inspection is a separate bounded request, not a synthetic Cell. A Cell result becomes another outer-loop observation; only Python `host.submit_output(...)` can complete a task from inside a Cell.

For each worker, the manager must be the only party that reads frames. The worker's protocol-write lock serializes frames on the wire, and its Host-call transaction lock allows only one synchronous RPC in flight at a time. The Web execution coordinator and [`KernelSupervisor`](supervisor.py) coordinate writers and lifecycle around that protocol; neither proxies it.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Public exports for `Kernel`, `KernelBusyError`, `KernelLease`, and `KernelSupervisor`. |
| [`background.py`](background.py) | The home of `host.exec_background`. A Cell that will run for a long time — a training run, a long simulation — gets a worker process of its own, so it never blocks the foreground kernel or the agent's turn. `exec_peek` reads its accumulated stdout without waiting; `exec_interrupt` sends one idempotent SIGINT. Such a job cannot see the foreground namespace, and nothing about it is written to disk. |
| [`environment.py`](environment.py) | Decides what a kernel is allowed to inherit. The child environment is built from a small explicit allowlist rather than copied from `os.environ`, so provider keys, cloud tokens, agent sockets, and loader-injection variables stop at the process boundary. Anything a Cell then launches, `host.bash` included, inherits that same filtered mapping. |
| [`environments.py`](environments.py) | Environment selection: how a task moves to an interpreter that already has what it needs instead of installing packages every time. Prebuilt conda environments are discovered under `OPENAI4S_ENV_ROOTS` or the usual install roots, probed for `bin/python` or `bin/Rscript`, and cached with their package sets. The daemon's own interpreter is always offered as a synthetic `base` environment, so a selection can never leave a session with no Python kernel. |
| [`guards.py`](guards.py) | Looks for state that leaks from one Cell into the next: pyplot figures a Cell opened and never closed, plus a few process-global registries pinned before the Cell and diffed after. These are cheap probes, not confinement. A probe whose optional library is missing does nothing, and `OPENAI4S_GUARDS_OFF=1` turns the whole bundle off. |
| [`lazy.py`](lazy.py) | Keeps a turn that only calls tools or only finalizes from starting an interpreter it never needs. One owner, one start, thread-safe. The candidate worker is published early enough that a cancellation can still reach it, and a failed bootstrap is detached and shut down rather than reused. |
| [`manager.py`](manager.py) | The host side of one worker. It spawns the subprocess, wraps the command in the OS sandbox, and is the only party that ever reads that worker's JSON-lines frames. One `execute` frame goes out; what comes back may be a streamed output chunk, the final response, or a `host_call` that has to be answered with a `host_response` before the blocked Cell can go on. Interrupts and restart generations are driven from here. |
| [`preinstall.py`](preinstall.py) | Package management for the kernel, held at arm's length: it is support tooling, and the stdlib core never takes a hard import dependency on it. It reports which of the scientific baseline is already importable, installs the rest at daemon startup, and installs whatever a caller names on demand. A newly installed package only reaches a fresh process, so restarting the kernel afterwards is the caller's job. |
| [`provenance.py`](provenance.py) | Installs object-level lineage instrumentation inside the Python worker. Objects read through a supported reader carry the source `version_id` of the artifact they came from, and a later write reports the input versions they accumulated. It sees what it can see, not everything. |
| [`r_kernel.py`](r_kernel.py) | Resolves a real `Rscript` and builds the file-descriptor-safe command that runs the R sibling through the common manager. Python is never silently substituted. |
| [`r_worker.R`](r_worker.R) | The persistent R worker, holding the same execute/response contract as Python: captured output, interruption, the failing line and call, resource usage. Inbound frames are parsed with `jsonlite`, but outbound JSON is escaped by hand, so an R install without `jsonlite` still reports a clean structured error instead of dying. It is an analysis channel: no `host` object, no mid-Cell RPC, no way to complete a task from inside a Cell. |
| [`recovery.py`](recovery.py) | Builds a replacement kernel from a content-addressed, canonical bootstrap recipe plus conservatively classified replay steps, and validates it before anyone else sees it. The candidate is published only once validation passes, and recovery reports `partial` when state cannot be safely reconstructed. |
| [`supervisor.py`](supervisor.py) | Owns the durable Python and R session slots, and stops there. A caller receives a lease naming the exact generation it acted on, and interrupt, restart, and watchdog replacement only fire while that lease still matches the live slot — so a late caller cannot kill the kernel that already replaced the one it meant. It never reads a protocol frame. |
| [`worker.py`](worker.py) | The persistent Python worker, and the file that has to get the fiddly parts right. The protocol file descriptors are moved off stdout, so a stray print from Cell code lands on stderr instead of corrupting the wire. `host` is injected into the namespace and held to one Host-call transaction at a time. Cell source is registered with `linecache`, so a traceback points at the line the researcher actually wrote. SIGINT handling, guards, the audit hook, provenance, and the bounded inspection and usage replies are all armed here. |

## Security and failure boundaries

- Environment filtering, the pre-execution classifier, the OS sandbox, the in-worker audit hook, durable approval, and the one-shot shell capability are independent layers. One of them being in place says nothing about whether the others succeeded.
- [`manager.py`](manager.py) wraps the worker with [`security/sandbox.py`](../security/sandbox.py). `enforce` fails closed when confinement is unavailable; `auto` may keep running after the real self-test fails, with a visible degraded or unavailable status.
- Python code is deliberately powerful inside the workspace it was granted. [`environment.py`](environment.py) prevents inheritance of recognized secrets, but it cannot make arbitrary Cell code trustworthy.
- The R worker parses inbound requests with `jsonlite`. Without that package it emits a structured error, and it stays analysis-only either way.
- Background execution gets a dedicated worker, but its job registry and accumulated streamed output live in process memory: not durable, not size-bounded. A long job that prints a lot keeps growing the daemon's memory.
- Provenance and guards are observational, and they do their best rather than guarantee coverage. Unsupported objects, unsupported libraries, native transformations, or an explicit opt-out can leave lineage incomplete.
- Recovery does not serialize a live Python or R namespace. It builds a new generation, replays only the steps it conservatively accepted, validates manifests, and will truthfully return partial recovery.
- Supervisor interrupt and restart calls must carry the exact lease and go through the session execution barrier. Bypassing those ownership rules races the manager's single frame reader.

## Related documentation

- [System architecture](../../docs/architecture.md)
- [Security model](../../docs/security.md)
- [Jupyter and kernel behavior](../../docs/jupyter.md)
- [Web runtime](../../docs/webapp.md)
