---
title: Security architecture
description: Trust boundaries, enforced controls, heuristic screeners, failure modes, and safe access assumptions for OpenAI4S.
canonical: true
last_verified: 2026-07-14
verification: code-and-tests
status: current
audience: [operators, contributors, users]
verified_commit: a92e736
owner: OpenAI4S maintainers
---

# Security architecture

This page is the stable compatibility entry for OpenAI4S security documentation. Operators should also follow the concrete [security-hardening checklist](operations/security-hardening.md).

> OpenAI4S is a local or trusted-host, single-user scientific workbench. It is
> **not** a hardened public multi-tenant service. It executes model-authored
> code and exposes privileged local/remote operations. Many enter permission
> and audit controls, but remote-compute coverage is not uniform; keep the
> daemon on loopback and isolate it with a dedicated OS account.

## Threat model and trust boundaries

The design attempts to limit accidental or adversarial behavior from model-authored Cells, shell commands, untrusted Web/MCP content, and user-installed extensions. It assumes the host operator, daemon code, and built-in components are trusted. It does not claim protection against a malicious administrator, kernel exploit, compromised Python/R interpreter, or mutually untrusted users sharing one daemon account.

| Boundary | Runs where | Security meaning |
|---|---|---|
| Public documentation | Static files at `/docs/` | May be public; contains no Workbench runtime or data |
| HTTP/WebSocket Gateway | Daemon process | Single-user control surface; loopback is the primary access boundary |
| JSON control tools and Host RPC | Daemon process | Capability-specific permission, audit, egress, injection-screening, and path-policy checks where configured; coverage is not uniform |
| Python/R workers | Child processes | Optional OS sandbox plus sanitized environment; Python supports mid-Cell Host RPC, R does not |
| Kernel subprocesses / `host.bash` | Under the worker identity | Inherit the worker boundary; shell additionally requires a one-shot Host capability |
| Local compute jobs | Daemon-side job manager | Privileged local operation, not the Python/R sandbox |
| `host.compute` provider helpers and SSH services | Local helper/container or remote host | Separate experimental boundary: submission is approval-gated, but result/cancel/close and legacy direct SSH/SCP do not all enter the same approval gate |

No one layer is sufficient. The intended posture combines loopback access, OS-account isolation, kernel sandboxing, least-privilege approvals, file/secret policy, and audit records.

## Control matrix

| Layer | Scope | Default | Failure behavior |
|---|---|---|---|
| Loopback bind | Gateway | `127.0.0.1` | No built-in login on loopback; relies on host access control |
| Non-loopback token | Gateway | Enabled automatically for non-loopback, or by `OPENAI4S_REQUIRE_TOKEN=1` | Rejects requests without one process token, but is not user/role authentication or TLS |
| Origin check | Mutating `/api` requests and WebSocket upgrade | On | Rejects a present cross-origin `Origin`; clients without `Origin` are accepted |
| Child environment allowlist | Python/R workers and descendants | Always on | Constructs a new allowlisted environment; does not copy the daemon environment |
| OS kernel sandbox | Python/R workers and descendants | `OPENAI4S_KERNEL_SANDBOX=auto` | `auto` continues unsandboxed with warning/status on failure; `enforce` refuses startup |
| Raw worker network boundary | OS sandbox | Blocked when sandbox enforced | Not enforced when sandbox is off/degraded; host RPC networking remains separate |
| Permission broker | Risk-bearing Host/control actions | Seeded rules, interactive `ask` | Headless/unattended defaults deny/pending; explicit `allow` override permits |
| `host.bash` capability | Exact shell invocation | Required | Missing, expired, reused, mismatched, or wrong-generation token fails closed |
| Workspace and secret-file checks | File tools | On | Reject path escapes and selected secret-shaped paths |
| Code classifier | Agent-authored Cells | `heuristic` | Static high-confidence matches block; classifier exceptions fail open |
| Python `dlopen` audit hook | Python worker | On | Blocks targeted loading from writable roots; not a syscall sandbox and absent from R |
| Injection scanner | Selected untrusted tool output | On | Annotates detected content; never removes/blocks it and errors fail open |
| Biosecurity prompt/screener | Agent policy; CLI Cell path for screener | On | CLI `BLOCK` refuses a Cell; `ESCALATE` is advisory; missing/erroring model allows; Web currently has prompt guidance only |
| Host egress allowlist | Selected Web/search/bash paths | `OPENAI4S_EGRESS=off` | Off or unrecognized mode is fail-open |
| Web fetch SSRF guard | `web_fetch` redirect chain | On | Blocks resolved private/loopback/link-local/reserved targets unless explicitly overridden |

## Gateway access is local-first

The default Gateway listens on `127.0.0.1:8760`. Reach a remote trusted host through a tunnel:

```bash
ssh -N -L 8760:127.0.0.1:8760 user@trusted-host
```

On loopback, the Gateway intentionally has no application login. A non-loopback bind creates a random process token, prints a tokenized URL, accepts it through the query string, redirects a browser to remove the query, and sets an `HttpOnly` cookie. `/health` remains public. This token does not provide encryption, identity, roles, per-user data separation, rate limiting, or safe Internet exposure.

Mutating API requests are rejected when they carry an `Origin` whose network location differs from `Host`; the WebSocket upgrade has the same same-origin check. Non-browser requests without an `Origin` pass. Treat this as CSRF defense in depth, not authentication.

The Workbench and public static documentation must remain separate deployments. Serving static docs from `openai4s.org/docs/` does not justify reverse-proxying the daemon under the same public origin.

## Worker process isolation

### Sanitized environment

Every Python/R worker environment is rebuilt from explicit runtime and trusted OpenAI4S allowlists. Provider/model/cloud/OAuth credentials, proxy URLs, loader injection variables, shell startup injection settings, and credential-shaped names are not inherited. The selected interpreter path, workspace, generation, and Host protocol values are synthesized by the manager.

This prevents ambient daemon secrets from crossing the normal spawn boundary. It does not protect secrets that the operator puts in an allowed variable, workspace file, command, package, or unrecognized external channel.

### Seatbelt and bubblewrap

The pure-stdlib sandbox adapter wraps workers with:

- macOS Seatbelt through `sandbox-exec`; or
- Linux bubblewrap through `bwrap`.

Before accepting a worker it performs a real probe for workspace write, private-temp write, outside-write denial, and raw-network denial when configured. An enforced sandbox broadly permits host reads needed by interpreters while applying targeted read denials for the OpenAI4S database, checkout `.env`, `~/.ssh`, `.netrc`, and `.pgpass`. Writes are confined to the session workspace and private temp. Linux uses a read-only root bind, selected read masks, and a private network namespace; it deliberately retains the host PID namespace. Seatbelt applies explicit write/network policy and targeted read denials.

`OPENAI4S_KERNEL_SANDBOX` accepts:

- `auto` — enforce after a successful test, otherwise warn and report unavailable while continuing unsandboxed;
- `enforce` — fail closed before worker startup;
- `off` — explicitly disable and report the boundary.

`OPENAI4S_KERNEL_ALLOW_RAW_NETWORK=1` is a host-global compatibility override. It should not be enabled routinely. Sandbox status belongs to an exact worker generation and remains `not_started` until a Python/R worker actually runs the test.

This is a process containment layer, not a VM, seccomp policy, or tenant boundary. Host-side services and local compute jobs are outside it.

## Files, shell, and approvals

File capabilities resolve paths against the active session workspace and reject escapes. Secret-shaped targets such as `.env`, key files, and common SSH private-key names are blocked in the file-tool envelope. The sandbox is still important because static file-tool checks do not mediate arbitrary library calls when a worker is unsandboxed.

There is no registered native shell tool. `host.bash` asks the Host to authorize an exact command hash, canonical working directory, current worker generation, random challenge, and short expiry. The worker validates and consumes the capability once, then starts the subprocess itself; the Host does not execute that shell. The frame ID remains audit context rather than an additional consume-time binding. Static command and URL-domain checks are defense in depth, not a shell parser or complete path jail.

The permission broker resolves risk-bearing actions against SQLite-backed rules:

- `allow` proceeds;
- `deny` returns a recoverable error;
- `ask` persists a decision request and waits for a human or timeout.

An absent browser subscriber never silently approves. Headless execution defaults to deny unless `OPENAI4S_UNATTENDED_APPROVAL=allow` is explicitly set. Conversation/project/global rules persist; broad wildcard rules widen future authority.

A durable request is not an execution replay. A live decision can resume the exact blocked call. After daemon restart, approving a surviving request records that the old operation did not execute and requires an explicit fresh continuation/replan. A restart-only `once` grant is exact, expires after 15 minutes, and is consumed only by a matching new action. Stored approval payloads are never replayed as arguments.

## Classifiers and content screening

### Agent Cell classifier

`OPENAI4S_SAFETY` supports `off`, `heuristic` (default), and `llm`:

1. Code with no recognized risk token takes a static fast path to safe.
2. High-confidence attack signatures are blocked.
3. In `heuristic`, residual code with a risk token but no signature is allowed.
4. In `llm`, residual code is sent to a model; an unparseable answer is unsafe, but a missing key, model/transport exception, or outer gate exception fails open.

Only **agent-origin** Cells are classified. An enabled Notebook REPL's user Cells skip this classifier. The Web and CLI both apply the code classifier to agent Cells.

### Prompt-injection scanner

Selected Web, search, MCP, and tool-declared untrusted results are scanned. A hit prepends a warning or adds a warning field so the model treats the payload as data. Original content is retained. The scanner is therefore an annotation mechanism, not a content-security boundary. Its optional model pass and all scanner exceptions fail open.

### Biosecurity policy

The calibrated biosecurity prompt is included in CLI and Web system prompts when enabled. The separate trajectory screener is currently invoked by the CLI Agent's pre-exec path, where `BLOCK` prevents a Cell and `ESCALATE` is logged as advisory. No configured model or a screener exception returns `ALLOW`.

The Web Gateway's current pre-exec callback runs the code classifier but does **not** invoke `screen_trajectory`; Web biosecurity behavior is prompt-level guidance today. Operators must not report the CLI screener as a uniform Gateway enforcement control.

### Python audit hook

The Python worker installs a CPython audit hook that targets `ctypes.dlopen` of shared objects from agent-writable roots. It is best viewed as one escape-pattern guard. It is Python-specific and does not mediate arbitrary syscalls, preloaded native dependencies, R, or host-side execution.

## Network policy

`web_fetch` applies its private-address guard on every manually handled redirect. `OPENAI4S_ALLOW_PRIVATE_FETCH=1` is an explicit trusted-local override.

`OPENAI4S_EGRESS=allowlist` applies a host-owned domain allowlist to selected Host Web/search paths and URL domains statically visible in authorized shell commands. Runtime expansion requires a permissioned request. The default `off` mode means no application allowlist, unparseable targets are allowed, and the layer does not intercept arbitrary sockets. It complements rather than replaces worker network namespaces and host firewalls.

`OPENAI4S_ALLOW_NETWORK=0` disables the Web/search helpers, but model/provider traffic and other host-side integrations have their own paths. Deployment-wide egress control belongs at the OS/network boundary.

## Data and secret exposure

The agent can issue read-only queries through `host.query`, but the Store rejects writes and denylisted internal/secret-bearing tables. The denylist includes model settings, connectors, memories, Host-call logs, permission records, raw Action Ledger and execution attempts, kernel generations, capability/Skill state, delegation state, branches/checkpoints, and recovery records. This is an application guard, not a general SQL information-flow proof.

`host.credentials.set(name, value)` stores plaintext only in an in-memory vault. Credential get/list calls are not written to the Host-call log; set arguments are redacted; replay recording skips set. Other user content can still contain secrets, so protect the database, logs, workspaces, Artifacts, compaction history, exported Notebooks, and portable Session packages according to their contents.

The default Notebook is a read-only execution trace. `OPENAI4S_NOTEBOOK_REPL=1` enables arbitrary user-authored Python/R input and the corresponding lifecycle routes. Agent and user execution still share exact FIFO ownership and cancellation, but enabling the REPL expands who can submit code.

## Remote-compute boundaries

`host.compute` and the purpose-built SSH science services are [Partial/Prototype](compute.md), not a production scheduler or hardened tenant boundary. Remote credentials, provider code, containers, SSH accounts, outputs, and remote retention need separate review.

The BYOC worker runtime scrubs provider secrets in two stages:

1. before loading a provider module, a baseline removes credential-shaped names and known cloud/provider prefixes;
2. before reading the credential from stdin/fd 3, the resident runtime re-scrubs using the loaded provider's declared prefixes.

The credential itself is not placed in the helper environment. The guarantee is **name-based**: a secret stored under an unrecognized variable name can remain visible to provider import-time code. Provider modules are trusted extension code and should be reviewed before use.

The remote capability registry stores SSH aliases and service metadata, not private keys. `host.fold` and `host.score_mutations` return errors when a service is absent or produces no parseable result; they do not synthesize scientific output. A capability registration probe currently proves a path/executable check, not scientific correctness or ongoing service health.

## Known security limits

- There is no multi-user identity or authorization model.
- Loopback has no application authentication; non-loopback token mode is intentionally small.
- `auto` sandbox mode may run workers unsandboxed.
- The sandbox broadly permits host reads and retains the host PID namespace on Linux.
- Classifiers, injection scanning, and biosecurity screening have documented fail-open/advisory paths.
- The egress allowlist is off by default and covers selected Host paths only.
- User Skills, dynamic tools, scientific packages, MCP servers, provider modules, and remote wrappers are executable extension surfaces.
- Local compute jobs run outside the Python/R sandbox.
- Remote compute job state and provider lifecycle are not a durable, scheduler-grade security boundary.

Use `OPENAI4S_KERNEL_SANDBOX=enforce`, a dedicated account, loopback-only access, narrow approvals, private data permissions, and tested backups for the strongest currently supported posture.
