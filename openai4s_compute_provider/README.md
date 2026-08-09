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
| [`__main__.py`](__main__.py) | The one entry point every provider goes through. Runs the baseline environment scrub before it imports `provider.py`, loads `PROVIDER` by exact file path, then starts oneshot or REPL mode. It also loads *this* package by file location rather than by putting its parent on `sys.path`: a package import lists the directory it searches, so the old form required the parent — the repository root, in a source or editable install — to be readable from inside the confinement, handing an untracked `.env` and `.git` to a process that by design has the network. |
| [`_channel.py`](_channel.py) | Transport plumbing that knows nothing about providers or ops: capped newline-framed fd-3 ready/event/auth messages, the authentication handshake read from stdin or fd-3, byte formatting, and a courtesy stdout/stderr token scrubber. |
| [`_constants.py`](_constants.py) | One place for the values the resident, the channel, and every shim have to agree on: stream and harvest caps, the idle timeout, stage and work paths, protocol exit codes, fd and line limits, recognized credential-name patterns, provider-secret prefixes, and the normalized error kinds. |
| [`_protocol.py`](_protocol.py) | The `ByocProvider` and running-`ExecResult` structural contracts plus the typed `ByocError`. A provider with no browsable persistent store simply omits the optional browsing methods. |
| [`_resident.py`](_resident.py) | The confined process that hosts the provider: the hardened prologue, then the oneshot or REPL lifecycle. It handles create, submit, wait/harvest, batch probe, reconcile, tail, browse/read, and terminate, with owner-tag checks, bounded transfers, redaction, deadlines, and structured replies. |

## Lifecycle and trust boundaries

- Secret scrubbing is two-stage: a provider-agnostic baseline runs **before** provider import, then provider-declared prefixes are scrubbed before the credential is read. It is a name-based heuristic; a secret stored under an unrecognized name is not removed. A variable is dropped when its *name* matches the credential pattern (`*_API_KEY`, `*_TOKEN`, `*_SECRET`, …) or a baseline/provider prefix — which is also why the probe anchors have to keep surviving it: scrubbing one leaves not a broken variable but a probe that cannot verify the boundary, and that now fails closed.
- The credential is passed to `provider.apply_auth` on purpose, so a provider shim holds whatever authority that credential represents. The stdout scrubber guards against accidental printing, not against a malicious provider.
- Isolated mode (`python -I`) stops provider sibling files from shadowing imports, but it is not an OS sandbox. Confinement has to come from the launching host, and it has to be verified. The host does now supply one — Seatbelt or bubblewrap, from [`security/byoc_confinement.py`](../openai4s/security/byoc_confinement.py) — and asks for `expect_confined` whenever it actually wraps, because the two only make sense together: demanding the check without building the boundary would make the helper exit 71 having proved nothing. The unconfined fallback form deliberately does not ask, which is what makes `auto`'s degradation visible rather than fatal.
- The invariant the probe checks is the filesystem one on both platforms. macOS expects `listdir($HOME)` to raise `PermissionError`. Linux compares the home directory's device id against an anchor the host passes in (`OPENAI4S_HOST_HOME_DEV`): under bubblewrap's `--tmpfs` a home is readable and *empty*, and an empty home is a legitimate home, so emptiness cannot be the test. The network-namespace comparison the original design used survives only as a fallback for a host a release behind. A probe that passes has verified that invariant and nothing more — in particular, not network isolation, which is a separate capability and is not enabled.
- Every path through the probe that cannot actually perform the check now returns **False**. It used to answer "I could not verify" with `True`, and since the check is only consulted when the caller passed `expect_confined`, that `True` let an unconfined helper go on to read the credential and call the provider having proved nothing — the confinement theatre the anchor exists to prevent, reached by the one route no test trips. A missing `/proc` is not evidence of confinement, and `/proc/1/ns/net` is routinely unreadable to an unprivileged process.
- Sandbox owner tags bind operations to one OpenAI4S installation. The runtime refuses a mismatch, and if a newly created sandbox does not read its ownership back correctly, it tries to terminate that sandbox.
- Request/reply staging paths must resolve under the expected temp prefix. Transfers and log tails are capped, but harvested provider bytes are still untrusted and need safe extraction and Artifact handling on the host side.
- REPL idle or auth expiry exits the resident. Oneshot signals and protocol violations use dedicated exit codes, and failures are normalized into a bounded `ByocError` kind and message where that is possible.
- This runtime supports a provider contract. It does not make `host.compute` scheduler-grade: host job records are durable now and carry the receipt needed to reach a sandbox after a restart, but the warm-sandbox handle is still in-memory only, nothing polls in the background, and provider or cloud behavior can fail on its own.

## Related documentation

- [Compute backend](../openai4s/compute/README.md)
- [Remote compute](../docs/compute.md)
- [Security model](../docs/security.md)
- [Accurately named alias](../openai4s_worker_runtime/README.md)
