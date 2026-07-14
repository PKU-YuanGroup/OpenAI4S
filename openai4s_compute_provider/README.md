# BYOC worker runtime

[中文说明](README_zh.md)

The package name is a leftover. What lives here is a stdlib-only **worker runtime**, not a concrete compute provider. A provider is a shim at `skills/remote-compute-<id>/provider.py` that implements the protocol defined here, and it is the only layer expected to import a third-party provider SDK. Everything those shims share sits in this package: authentication, secret-environment scrubbing, ownership checks, lifecycle operations, staging, output caps, and error normalization.

## Where this fits

[`ComputeManager`](../openai4s/compute/manager.py) launches [`__main__.py`](__main__.py) in isolated Python mode for BYOC operations. The oneshot path is the one in use today: request and reply cross a private staging directory as files, and the credential arrives on stdin, never in the child environment. The runtime loads one provider shim, verifies ownership before it operates on an existing sandbox, and leaves the actual sandbox create/exec/list/terminate behavior to that provider.

There is also a long-lived REPL mode, with an fd-3 control/auth channel and the common Python Cell protocol. The support surface exists; do not read it as proof that every host path or the UI wires a persistent provider kernel end to end.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | The public surface. Documents the package contract and exports the provider protocols, the resident, the channel helpers, limits, error kinds, paths, and the secret-scrub function. |
| [`__main__.py`](__main__.py) | The one entry point every provider goes through. Runs the baseline environment scrub before it imports `provider.py`, loads `PROVIDER` by exact file path, then starts oneshot or REPL mode. |
| [`_channel.py`](_channel.py) | Transport plumbing that knows nothing about providers or ops: capped newline-framed fd-3 ready/event/auth messages, the authentication handshake read from stdin or fd-3, byte formatting, and a courtesy stdout/stderr token scrubber. |
| [`_constants.py`](_constants.py) | One place for the values the resident, the channel, and every shim have to agree on: stream and harvest caps, the idle timeout, stage and work paths, protocol exit codes, fd and line limits, recognized credential-name patterns, provider-secret prefixes, and the normalized error kinds. |
| [`_protocol.py`](_protocol.py) | The `ByocProvider` and running-`ExecResult` structural contracts plus the typed `ByocError`. A provider with no browsable persistent store simply omits the optional browsing methods. |
| [`_resident.py`](_resident.py) | The confined process that hosts the provider: the hardened prologue, then the oneshot or REPL lifecycle. It handles create, submit, wait/harvest, batch probe, reconcile, tail, browse/read, and terminate, with owner-tag checks, bounded transfers, redaction, deadlines, and structured replies. |

## Lifecycle and trust boundaries

- Secret scrubbing is two-stage: a provider-agnostic baseline runs **before** provider import, then provider-declared prefixes are scrubbed before the credential is read. It is a name-based heuristic; a secret stored under an unrecognized name is not removed.
- The credential is passed to `provider.apply_auth` on purpose, so a provider shim holds whatever authority that credential represents. The stdout scrubber guards against accidental printing, not against a malicious provider.
- Isolated mode (`python -I`) stops provider sibling files from shadowing imports, but it is not an OS sandbox. Confinement has to come from the launching host, and it has to be verified. Oneshot mode fails with exit 71 only when the caller asks for `expect_confined` and the runtime probe fails; a caller that does not ask for it has not established this boundary.
- Linux confinement probing compares network-namespace identity; macOS probing relies on denied home-directory reads. A probe that passes has verified those invariants and nothing more, not complete isolation.
- Sandbox owner tags bind operations to one OpenAI4S installation. The runtime refuses a mismatch, and if a newly created sandbox does not read its ownership back correctly, it tries to terminate that sandbox.
- Request/reply staging paths must resolve under the expected temp prefix. Transfers and log tails are capped, but harvested provider bytes are still untrusted and need safe extraction and Artifact handling on the host side.
- REPL idle or auth expiry exits the resident. Oneshot signals and protocol violations use dedicated exit codes, and failures are normalized into a bounded `ByocError` kind and message where that is possible.
- This runtime supports a provider contract. It does not make `host.compute` scheduler-grade or durable: host job records and warm-sandbox handles may still live only in memory, and provider or cloud behavior can fail on its own.

## Related documentation

- [Compute backend](../openai4s/compute/README.md)
- [Remote compute](../docs/compute.md)
- [Security model](../docs/security.md)
- [Accurately named alias](../openai4s_worker_runtime/README.md)
