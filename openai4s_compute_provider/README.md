# BYOC worker runtime

[中文说明](README_zh.md)

Despite the legacy package name, this is a stdlib-only **worker runtime**, not a concrete compute provider. Provider-specific shims live in `skills/remote-compute-<id>/provider.py`, implement the protocol here, and are the only layer expected to import a third-party provider SDK. The runtime supplies shared authentication, secret-environment scrubbing, ownership checks, lifecycle operations, staging, output caps, and error normalization.

## Place in the architecture

[`ComputeManager`](../openai4s/compute/manager.py) launches [`__main__.py`](__main__.py) in isolated Python mode for BYOC operations. In the currently used oneshot path, request/reply files cross a private staging directory and credentials arrive on stdin, never in the child environment. The runtime loads one provider shim, verifies ownership before operating on an existing sandbox, and delegates actual sandbox creation/exec/list/terminate behavior to that provider.

The runtime also implements a long-lived REPL mode with an fd-3 control/auth channel and the common Python Cell protocol. That support surface should not be read as proof that every host path or UI wires a persistent provider kernel end to end.

## Files

| File | Responsibility |
|---|---|
| [`__init__.py`](__init__.py) | Documents the package contract and exports the provider protocols, resident, channel helpers, limits, error kinds, paths, and secret-scrub function. |
| [`__main__.py`](__main__.py) | Generic isolated entry point: performs baseline environment scrubbing before importing `provider.py`, loads `PROVIDER` by exact file path, then starts oneshot or REPL mode. |
| [`_channel.py`](_channel.py) | Implements capped newline-framed fd-3 ready/event/auth messages, stdin/fd-3 authentication parsing, byte formatting, and a courtesy stdout/stderr token scrubber. |
| [`_constants.py`](_constants.py) | Centralizes stream/harvest caps, idle timeout, stage/work paths, protocol exit codes, fd/line limits, recognized credential-name patterns, provider-secret prefixes, and normalized error kinds. |
| [`_protocol.py`](_protocol.py) | Defines the `ByocProvider` and running `ExecResult` structural contracts plus typed `ByocError`; optional persistent-store browsing methods may be omitted by providers. |
| [`_resident.py`](_resident.py) | Runs the hardened prologue and oneshot/REPL lifecycles; handles create, submit, wait/harvest, batch probe, reconcile, tail, browse/read, and terminate operations with owner-tag checks, bounded transfers, redaction, deadlines, and structured replies. |

## Subdirectories

There are no tracked child directories in this package.

## Lifecycle and trust boundaries

- Secret scrubbing is two-stage: a provider-agnostic baseline runs **before** provider import, then provider-declared prefixes are scrubbed before credentials are read. It is a name-based heuristic; a secret stored under an unrecognized name is not removed.
- The credential is intentionally passed to `provider.apply_auth`. A provider shim therefore has the authority represented by that credential. The stdout scrubber is courtesy protection against accidental printing, not a control against a malicious provider.
- Isolated mode (`python -I`) prevents provider sibling files from shadowing imports, but it is not an OS sandbox. Confinement must be supplied by the launching host and verified. Oneshot mode fails with exit 71 only when the caller requests `expect_confined` and the runtime probe fails; callers that do not request it have not established this boundary.
- Linux confinement probing compares network-namespace identity; macOS probing relies on denied home-directory reads. A successful probe verifies those invariants, not complete isolation.
- Sandbox owner tags bind operations to one OpenAI4S installation. The runtime refuses mismatches and best-effort terminates a newly created sandbox whose ownership cannot be read back correctly.
- Request/reply staging paths must resolve under the expected temp prefix. Transfer and log tails are capped, but harvested provider bytes remain untrusted and need safe host-side extraction/Artifact handling.
- REPL idle/auth expiry exits the resident. Oneshot signals and protocol violations use dedicated exit codes; failures are normalized into a bounded `ByocError` kind/message where possible.
- This runtime supports a provider contract; it does not make `host.compute` scheduler-grade or durable. Host job records and warm-sandbox handles may still be in memory, and provider/cloud behavior can fail independently.

## Related documentation

- [Compute backend](../openai4s/compute/README.md)
- [Remote compute](../docs/compute.md)
- [Security model](../docs/security.md)
- [Accurately named alias](../openai4s_worker_runtime/README.md)
