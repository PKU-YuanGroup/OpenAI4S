# Host services

[中文说明](README_zh.md)

This directory contains focused host-side capability services. They are composed by [`HostDispatcher`](../host_dispatch.py), which remains the shared RPC envelope for argument validation, permission/approval checks, audit records, untrusted-output screening, activity events, and soft-error routing. These services implement domain behavior; they are not independently exposed network endpoints.

## Place in the architecture

Python's worker-side [`host` facade](../sdk/host.py) emits a synchronous `host_call`. [`kernel/manager.py`](../kernel/manager.py) hands it to `HostDispatcher`, which applies policy and invokes one of the services below. The returned value is sent in the matching `host_response`, allowing the blocked Cell to resume. Native control tools also use the dispatcher where their capability overlaps, keeping control-plane and in-kernel policy consistent.

A service may return the single-key shape `{"error": message}` as a soft failure. The Python worker converts that result to `RuntimeError`; it is not a successful scientific result or task completion. Most services deliberately leave permission, replay, audit, and injection policy to the dispatcher rather than duplicating it.

## Files

| File | Responsibility |
|---|---|
| [`__init__.py`](__init__.py) | Re-exports the principal service classes used by composition code. |
| [`bash.py`](bash.py) | Authorizes kernel-local `host.bash`: classifies/redacts proposals, issues short-lived generation/challenge/command/cwd-bound capabilities, consumes each token once, and records a bounded worker-reported result. It never imports `subprocess` or executes the command. |
| [`completion.py`](completion.py) | Validates the sole in-Cell success contract (`output` plus 1–4 completed-action bullets and an optional schema) and stores one submission for the active dispatch context. |
| [`credentials.py`](credentials.py) | Keeps session-local credentials and short-lived, action-bound, single-use leases in memory; rotation invalidates leases and no raw value is persisted here. |
| [`data.py`](data.py) | Store-backed read-only SQL/schema access, scoped Artifact metadata/version/path/save/restore operations, image projection, frame browsing, and provenance/lineage reads and reports. |
| [`delegation.py`](delegation.py) | Applies stored agent-profile overrides, injects built-in specialist context, and forwards delegate/children/collect/stop/message/stat operations to the session's delegation runtime. |
| [`delegation_policy.py`](delegation_policy.py) | Parses and freezes child-Agent method/capability policy, including aliases, per-method decisions, and tool visibility; explicitly restricted policies allowlist operations, while the separate unrestricted mode remains explicit in the projection. |
| [`endpoints.py`](endpoints.py) | Allocates loopback ports, stores endpoint metadata/start-stop scripts, and probes readiness. Registration does not execute the stored lifecycle scripts or introduce a separate egress policy. |
| [`files.py`](files.py) | Resolves the late-bound session workspace, confines relative paths to it, rejects secret basenames, and provides compatibility dispatch to class-based file tools where concrete I/O behavior lives. |
| [`llm.py`](llm.py) | Performs synchronous configured-model calls from a running Cell, including bounded concurrent batch fan-out, and projects current/listed model metadata. |
| [`mcp.py`](mcp.py) | Resolves persisted MCP connectors and forwards list/tools/call/resource/prompt operations to the MCP manager; permission and untrusted-output screening remain in the dispatcher. |
| [`progress.py`](progress.py) | Maintains transient per-dispatcher todos and updates/reads persisted approved-plan steps and reviewer progress. |
| [`remote_capabilities.py`](remote_capabilities.py) | Normalizes narrowly structured SSH verification probes, checks remote capability availability, and registers verified service metadata in the remote-compute registry. |
| [`remote_science.py`](remote_science.py) | Invokes registered SSH folding and mutation-scoring wrappers, parses explicit result markers, and records remote provenance. Missing or failed services return errors rather than fabricated science. |
| [`science.py`](science.py) | Builds allowlisted public scientific-database requests through the shared fetch path and normalizes UniProt, PDB, Ensembl, ChEMBL, PubChem, arXiv, and OpenAlex responses. |
| [`session.py`](session.py) | Constrains control operations to the current root session, reads durable branch/checkpoint/permission status, and delegates filesystem-aware checkpoint/fork/revert/recovery operations to an attached Web session-domain service. |
| [`skills.py`](skills.py) | Searches, reads, edits, publishes, versions, rolls back, and deletes scoped Code-as-Action Skills while preserving bundled-skill precedence and filesystem confinement. |

## Subdirectories

There are no tracked child directories in this package.

## Control, security, and failure boundaries

- [`HostDispatcher`](../host_dispatch.py), not an individual service, is the authorization and audit boundary. Calling a service directly is trusted in-process composition and bypasses that envelope.
- Shell execution stays inside the scientific worker through [`sdk/bash.py`](../sdk/bash.py). This package only mints and consumes one-shot capabilities; reported stdout/stderr is bounded and redacted before persistence.
- Credential values are memory-only in [`credentials.py`](credentials.py), but any consumer that receives a redeemed value has the power associated with it. Name-based redaction is not a proof that arbitrary output contains no secret.
- [`files.py`](files.py) confines paths, while the actual Tool classes own read/write behavior. Artifact snapshots and provenance registration are separate, best-effort persistence steps and are not a global filesystem/SQLite transaction.
- Endpoint start/stop scripts are metadata only. A successful readiness probe does not establish tenant isolation, authentication, or safe public exposure.
- General `host.compute`, remote capability provisioning, folding, and mutation scoring are evolving integration surfaces. A registered route or service class does not prove that provider credentials, remote software, GPU capacity, or end-to-end UI recovery are configured.
- Public-database, MCP, LLM, and remote SSH calls can fail independently or return hostile content; dispatcher screening is an additional layer, not validation of scientific correctness.

## Related documentation

- [System architecture](../../docs/architecture.md)
- [Security model](../../docs/security.md)
- [Remote compute](../../docs/compute.md)
- [Skills](../../docs/skills.md)
