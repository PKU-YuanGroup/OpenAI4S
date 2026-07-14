# Worker-side Host SDK

[中文说明](README_zh.md)

This is the `host` object that agent code touches inside a Python Cell, and the worker's half of the inner loop. [`worker.py`](../kernel/worker.py) calls `build_host(host_call, ...)` and injects the returned singleton as `host`. Most methods are thin and synchronous: encode the public Python arguments, send one `host_call` to the daemon, block until the matching `host_response` comes back, decode it, and either return the value or raise on a soft error.

## Where this fits

The SDK is not the authorization boundary, and it normally does not implement capability behavior at all. Validation, permissions, approvals, auditing, screening and the real work belong to [`HostDispatcher`](../host_dispatch.py) and the services under [`openai4s/host`](../host). Two pieces genuinely run in the worker, and both matter:

- `host.bash(...)` runs the subprocess inside the scientific worker, and only after the host has issued an exact, one-shot capability and atomically consumed it. Whether that worker is confined by the OS is a separate question: it depends on the sandbox mode and on whether confinement was actually established. The default mode is `auto`, which stays up and reports `state="unavailable"` when detection or the self-test fails, so the subprocess may well run unconfined.
- `host.compute` builds its Python handle objects locally. Provider discovery, job submission, status, cancellation and harvesting are all host calls.

The R analysis worker never imports this package and has no `host` singleton.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Exports `build_host`, the composition entry point the Python worker calls. |
| [`bash.py`](bash.py) | Runs shell commands in the worker, and only under an authorization it cannot grant itself. It proposes an exact command, cwd, kernel generation and challenge, then checks every binding on the capability that comes back and spends the token once. Around the run it snapshots bounded workspace file metadata. The bounded result and the file diff go back to the host, with secret-looking paths masked. |
| [`compute.py`](compute.py) | Backs the `host.compute` namespace and the local instance and job handles. It normalizes provider parameters and paths, maps each operation onto a `compute_<op>` RPC, and offers status/wait/result/cancel/close/attach on top. No provider transport lives here. |
| [`host.py`](host.py) | The public `host.*` facade. A strict wire codec sits at the top and maps snake_case against camelCase. Under it: the skills/query/lineage/endpoints/credentials/MCP/environments/science/compute namespaces, the file, network, delegation and session helpers, and `host.submit_output`. |

## RPC and completion contract

- Every call blocks inside the Python Cell. The worker's Host-call transaction lock allows only one request in flight, even if user code spins up threads.
- An optional field whose value is `None` is omitted from the wire rather than sent as JSON `null`. The strict Host schema distinguishes an absent field from an invalid null one, and rejects the null.
- A host soft-error object arrives back as a `RuntimeError`. Provider and compute errors may carry a structured error kind or concurrency detail, but they are still failures.
- `host.submit_output(...)` is the only SDK method that can mark a Python Cell as successfully complete. Printing, returning a Python value, producing an R result, or making an ordinary Host call that merely succeeds does not end the outer agent run.

## Security and failure boundaries

- The SDK is trusted code inside an already powerful Python process. Its argument checks help protocol integrity; they do not replace dispatcher permissions or the OS sandbox.
- Shell authorization binds the token to the command digest, canonical cwd, workspace root, worker generation, challenge and expiry. Worker validation and host consumption both fail closed, and a daemon restart invalidates every capability still outstanding in memory.
- Redaction of shell stdout/stderr is defensive and shape-based. It cannot guarantee that a deliberately transformed or simply unrecognized secret stays out of the output.
- Compute handles are convenience projections, not durable Python objects. After a Cell or kernel restart you have to rebuild the handle or attach by job ID, and whether that works depends on what the manager or provider still holds in memory or on disk.
- `host.compute` is an evolving integration surface. The existence of a public SDK method does not by itself guarantee a configured provider, working confinement, remote capacity, a successful harvest, or a second approval for every follow-up operation.

## Related documentation

- [System architecture](../../docs/architecture.md)
- [Security model](../../docs/security.md)
- [Remote compute](../../docs/compute.md)
