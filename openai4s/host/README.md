# Host services

[中文说明](README_zh.md)

The host-side capability services live here, one class per domain, from shell authorization to Skill editing. [`HostDispatcher`](../host_dispatch.py) composes them and stays wrapped around every call as the shared RPC envelope: argument validation, permission and approval checks, audit records, untrusted-output screening, activity events, and soft-error routing. Nothing in this package is a network endpoint of its own; each service only implements the behavior of its own domain.

## Where this fits

Python's worker-side [`host` facade](../sdk/host.py) emits a synchronous `host_call`. [`kernel/manager.py`](../kernel/manager.py) hands it to `HostDispatcher`, which applies policy and invokes one of the services below. The returned value goes back in the matching `host_response`, so the blocked Cell can resume. Native control tools go through the same dispatcher where their capability overlaps, which keeps control-plane and in-kernel policy consistent.

A service can fail softly by returning the single-key shape `{"error": message}`. The Python worker turns that into a `RuntimeError`; it is never a successful scientific result or a task completion. Permission, replay, audit, and injection policy stay in the dispatcher, and most services deliberately do not reimplement them.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Re-exports most of the service classes used by composition code. `BashAuthorizationService` and `ScienceConnectorService` are not in `__all__`; callers import those from their own modules. |
| [`bash.py`](bash.py) | Authorizes kernel-local `host.bash` without ever running it; the module does not import `subprocess`. The trusted host repeats the safety and egress checks the worker already did, redacts the proposal, then mints a short-lived token bound to the command digest, cwd, worker generation, and challenge. That token can be redeemed exactly once. Whatever the worker reports back is bounded and redacted before it is recorded. |
| [`completion.py`](completion.py) | The only success contract a Cell has. It validates an `output`, one to four completed-action bullets, and an optional output schema, and keeps one valid submission for the active dispatch context. |
| [`credentials.py`](credentials.py) | Session-local credentials, held in memory and handed out as short-lived, action-bound, single-use leases. Rotating a credential invalidates its outstanding leases. No raw value is persisted here. |
| [`data.py`](data.py) | The Store-backed data surface. Read-only SQL, schema access, and frame browsing sit on one side; Artifact metadata, versions, paths, save, restore, and image projection on the other, together with provenance and lineage reads and reports. Enumeration and lookup of Artifacts stay inside the caller's own session and project. The current frame is only the handle that resolves that `root_frame_id`/`project_id` scope, so Artifacts written by earlier Cells of the same session stay reachable. |
| [`delegation.py`](delegation.py) | The front of the session's delegation runtime. It applies a stored agent profile's overrides and injects the built-in specialist context; the delegate, children, collect, stop, message, and stats calls themselves pass through to the runtime that owns the children. |
| [`delegation_policy.py`](delegation_policy.py) | Parses a child Agent's method and capability policy once, then freezes it. Naming any capability makes the policy restricted. Even then, five methods (`submit_output`, `prov_record`, `prov_resolve_path`, `search_capabilities`, `capabilities`) are allowed on top of the listed capabilities and their aliases — under every restricted policy, including one whose capability list is empty. Per-method allow/ask/deny decisions and tool visibility ride along, and the separate unrestricted mode stays explicit in the projection rather than being implied. |
| [`endpoints.py`](endpoints.py) | Loopback port allocation, endpoint metadata with its start and stop scripts, and a readiness probe against the live route. Registration stores those lifecycle scripts; it does not run them, and it introduces no egress policy of its own. |
| [`files.py`](files.py) | The workspace path boundary, and only that. It resolves the late-bound session workspace, keeps relative paths inside it, and rejects secret basenames. The remaining methods are compatibility dispatch into the class-based file tools, which is where concrete I/O behavior lives. |
| [`llm.py`](llm.py) | Calls the configured model synchronously from a running Cell. A batch request fans out concurrently under the fan-out cap. The service also reports the current model, and its model listing is not a catalogue: it returns exactly one entry, the configured model plus its context window. |
| [`mcp.py`](mcp.py) | Resolves a persisted MCP connector by id first, then by exact display name, and hands list/tools/call/resource/prompt operations to the MCP manager. Screening what comes back is not its job. Permission and untrusted-output checks stay in the dispatcher. |
| [`progress.py`](progress.py) | Todos live in memory here; plan steps and reviewer progress live in the Store. Approval is not a precondition for ticking a step: absent an explicit `plan_id`, the plan it updates is whatever the Store returns for the frame, which is the newest plan that has not been discarded. |
| [`remote_capabilities.py`](remote_capabilities.py) | Registration is gated on evidence. A narrowly structured probe spec is normalized into one safe remote command and run to check that the remote capability is actually there; only then does the verified service metadata enter the remote-compute registry. |
| [`remote_science.py`](remote_science.py) | Runs the registered folding and mutation-scoring wrappers over SSH, parses their explicit result markers, and buffers remote provenance for the producing cell. A missing or failed service returns an error. It does not fabricate science. |
| [`connector_manifest.py`](connector_manifest.py) | What each science connector depends on from its upstream API, declared. Two levels: **required** (the array container and the record id, without which the connector returns nothing) and **expected** (fields the parser reads but degrades without). Not a second copy of the parse logic — an offline test proves each required path is present in the connector's own fixture and load-bearing (delete it and the adapter stops returning the record), so the manifest cannot over-claim. Backs the nightly canary. |
| [`science.py`](science.py) | Seven public databases behind one envelope: UniProt, PDB, Ensembl, ChEMBL, PubChem, arXiv, and OpenAlex. Requests are built against the allowlist and go out through the shared fetch path, and each response is normalized into the same record shape. |
| [`session.py`](session.py) | Pins control operations to the dispatcher's current root session, so no call can reach into another conversation. Checkpoints and pending permission requests always come from Store. Branch and recovery status come from the attached Web session-domain service, which is the normal Web runtime; with no domain attached, the status projection falls back to a read-only branch list from Store and reports recovery as unavailable. The filesystem-aware checkpoint, fork, revert, and recovery operations are delegated to that same domain service. |
| [`skills.py`](skills.py) | The Skill lifecycle end to end: search, read, edit, publish, version, roll back, delete. Scope decides which directory on disk owns a Skill; bundled Skills keep precedence over user ones, and writes stay confined to the skill directories. |

## Control, security, and failure boundaries

- [`HostDispatcher`](../host_dispatch.py), not an individual service, is the authorization and audit boundary. Calling a service directly is trusted in-process composition and bypasses that envelope.
- `host.bash` never runs here. Its shell execution stays inside the scientific worker through [`sdk/bash.py`](../sdk/bash.py), and [`bash.py`](bash.py) only mints and redeems the one-shot capability; reported stdout/stderr is bounded and redacted before persistence. Do not read that as a package-wide property. [`remote_science.py`](remote_science.py) and [`remote_capabilities.py`](remote_capabilities.py) each default their runner to `subprocess.run` and shell out from the trusted host process, running `ssh -o ConnectTimeout=15 -o BatchMode=yes <host> <command>` to reach a registered remote GPU host.
- Credential values are memory-only in [`credentials.py`](credentials.py), but any consumer that receives a redeemed value holds the power that goes with it. Name-based redaction is not a proof that arbitrary output contains no secret.
- [`files.py`](files.py) confines paths, while the actual Tool classes own read/write behavior. Artifact snapshots and provenance registration are separate persistence steps that try but do not guarantee; they are not one global filesystem/SQLite transaction.
- Endpoint start/stop scripts are metadata only. A successful readiness probe does not establish tenant isolation, authentication, or safe public exposure.
- General `host.compute`, remote capability provisioning, folding, and mutation scoring are evolving integration surfaces. A registered route or service class does not prove that provider credentials, remote software, GPU capacity, or end-to-end UI recovery are configured.
- Public-database, MCP, LLM, and remote SSH calls can fail independently or return hostile content. Dispatcher screening is one more layer, not a check on scientific correctness.

## Related documentation

- [System architecture](../../docs/architecture.md)
- [Security model](../../docs/security.md)
- [Remote compute](../../docs/compute.md)
- [Skills](../../docs/skills.md)
