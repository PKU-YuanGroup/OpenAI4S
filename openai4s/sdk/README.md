# Worker-side Host SDK

[中文说明](README_zh.md)

This package is the Python-kernel side of the inner loop. [`worker.py`](../kernel/worker.py) calls `build_host(host_call, ...)` and injects the returned singleton as `host`. Most methods are thin, synchronous facades: encode the public Python arguments, send one `host_call` to the daemon, wait for the matching `host_response`, decode the result, and either return it or raise on a soft error.

## Place in the architecture

The SDK is not the authorization boundary and normally does not implement capability behavior. [`HostDispatcher`](../host_dispatch.py) and services in [`openai4s/host`](../host) own validation, permissions, approvals, auditing, screening, and host-side work. Two worker-local exceptions are important:

- `host.bash(...)` executes the subprocess inside the sandboxed scientific worker, but only after the host issues and atomically consumes an exact, one-shot capability.
- `host.compute` creates Python handle objects locally while all provider discovery, job submission, status, cancellation, and harvesting are host calls.

The R analysis worker does not import this package and has no `host` singleton.

## Files

| File | Responsibility |
|---|---|
| [`__init__.py`](__init__.py) | Exports `build_host`, the composition entry point used by the Python worker. |
| [`bash.py`](bash.py) | Worker-local shell executor: proposes an exact command/cwd/generation/challenge, validates the returned capability, consumes it once, snapshots bounded workspace metadata, launches the subprocess, and reports a redacted/bounded result and file diff to the host. |
| [`compute.py`](compute.py) | Implements the `host.compute` namespace and local instance/job handles, normalizes provider parameters and paths, maps operations to `compute_<op>` RPCs, and exposes status/wait/result/cancel/close/attach semantics without embedding provider transports. |
| [`host.py`](host.py) | Defines the public `host.*` facade, strict top-level snake_case/camelCase wire codec, namespaces for skills/query/lineage/endpoints/credentials/MCP/environments/science/compute, file/network/delegation/session helpers, and `host.submit_output`. |

## Subdirectories

There are no tracked child directories in this package.

## RPC and completion contract

- Each call is synchronous inside the Python Cell. The worker's Host-call transaction lock allows only one request in flight, even if user code creates threads.
- Optional fields whose value is `None` are omitted rather than encoded as JSON `null`, because the strict Host schema distinguishes omission from an invalid null value.
- A host soft-error object is converted into `RuntimeError`. Provider/compute errors may carry structured error-kind or concurrency information, but are still failures.
- `host.submit_output(...)` is the only SDK method that can mark a Python Cell as successfully complete. Printing, returning a Python value, an R result, or a successful ordinary Host call does not complete the outer agent run.

## Security and failure boundaries

- The SDK is trusted code inside an already powerful Python process. Its argument checks improve protocol integrity but do not replace dispatcher permission or the OS sandbox.
- Shell authorization binds token, command digest, canonical cwd, workspace root, worker generation, challenge, and expiry. Both worker validation and host consumption fail closed; daemon restart invalidates outstanding in-memory capabilities.
- Shell stdout/stderr redaction is defensive and shape-based. It cannot guarantee that an intentionally transformed or unrecognized secret will not appear in output.
- Compute handles are convenience projections, not durable Python objects. A restarted Cell/kernel must reconstruct or attach by job ID, and availability depends on the manager/provider's persisted or in-memory state.
- `host.compute` is an evolving integration surface. A public SDK method does not by itself guarantee a configured provider, confinement, remote capacity, successful harvest, or a second approval for every follow-up operation.

## Related documentation

- [System architecture](../../docs/architecture.md)
- [Security model](../../docs/security.md)
- [Remote compute](../../docs/compute.md)
