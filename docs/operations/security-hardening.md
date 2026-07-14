---
title: Security hardening
description: Operator checklist for account isolation, network exposure, kernel sandboxing, permissions, secrets, and auditing.
canonical: true
last_verified: 2026-07-14
verification: code-and-tests
status: current
audience: [operators, contributors]
verified_commit: a92e736
owner: OpenAI4S maintainers
---

# Security hardening

OpenAI4S executes model-authored Python/R, approval-gated shell operations, and remote operations whose controls vary by capability. Harden it as a **single-user code execution workbench**, not as a web application for mutually untrusted users. The controls below reduce risk from model mistakes and untrusted content; they do not create a multi-tenant security boundary.

Read [Security architecture](../security.md) for the exact enforcement and failure semantics behind this checklist.

## Baseline profile

Use this profile for a trusted-host deployment:

```dotenv
OPENAI4S_HOST=127.0.0.1
OPENAI4S_DATA_DIR=/var/lib/openai4s
OPENAI4S_KERNEL_SANDBOX=enforce
OPENAI4S_EGRESS=allowlist
OPENAI4S_SAFETY=heuristic
OPENAI4S_SAFETY_AUDIT_HOOK=1
OPENAI4S_INJECTION_SCAN=1
OPENAI4S_BIOSECURITY=1
OPENAI4S_NOTEBOOK_REPL=0
OPENAI4S_UNATTENDED_APPROVAL=deny
```

`OPENAI4S_UNATTENDED_APPROVAL=deny` is explicit documentation of the default; any value other than `allow` stays deny/pending. These settings are not equivalent in strength: the OS sandbox and permission decisions are enforcement controls, while several model/content screeners are heuristic, advisory, or fail open.

## Isolate the OS account

- Create one dedicated account for the daemon. Do not run it as root.
- Do not give that account unrelated repositories, browser profiles, personal home directories, cloud CLIs, or broad `sudo` access.
- Give it only the SSH identities and remote hosts required by selected compute workflows.
- Start it with `umask 077`; make the data directory mode `0700`; remove all group/other access recursively without changing owner execute bits: `chmod -R go-rwx "$OPENAI4S_DATA_DIR"`.
- Keep source/releases readable but not writable by the daemon where practical. The release-specific virtual environment is an exception because first `serve` may install missing scientific packages; preferably populate it before start and then make the release immutable.
- Put backups under a different private path and encrypt them. The database and logs can contain credentials and research content.

OS-account separation matters even with the kernel sandbox: workers inherit the daemon user's identity, the sandbox permits broad host reads needed by interpreters, and `auto` can continue without isolation.

## Keep the Workbench off the public Internet

The default loopback listener has no login because the local OS boundary is the intended access control. Preserve it:

```bash
ssh -N -L 8760:127.0.0.1:8760 user@trusted-host
```

Use a trusted VPN or a local authenticated/TLS reverse proxy only when the operator understands the added boundary. Keep the backend on loopback. The built-in non-loopback token:

- is one process-wide token, not a user identity;
- is initially delivered in a URL and then an `HttpOnly` cookie;
- does not provide TLS, roles, revocation lists, rate limiting, or tenant isolation;
- excludes `/health` from authentication;
- sits beside an Origin check that accepts non-browser requests without an `Origin` header.

It is defense in depth for a trusted network, not a public deployment mechanism. In particular, the Gateway exposes a host-side local compute-job route; do not assume every code-execution surface runs inside the Python/R sandbox.

Serve `openai4s.org/docs/` as generated static files under a separate web-server configuration and, preferably, a separate unprivileged account. The static server must have no read access to `OPENAI4S_DATA_DIR`, environment files, SSH keys, or Workbench logs.

## Require the kernel sandbox when it matters

`OPENAI4S_KERNEL_SANDBOX` has three modes:

| Mode | Behavior |
|---|---|
| `enforce` | Detect and self-test Seatbelt/bubblewrap before each worker is accepted; refuse worker startup if the boundary is unavailable |
| `auto` | Enforce after a successful self-test, otherwise continue unsandboxed with a warning and `unavailable` sandbox status |
| `off` | Deliberately run without the OS boundary and report that state |

Use `enforce` for an unattended or remote trusted-host service. Install `bubblewrap` on Linux and verify user-namespace/container restrictions allow its real self-test. On macOS, verify `sandbox-exec` in the exact service context. A passing developer-shell test does not prove a supervisor-constrained service can start the sandbox.

Sandbox status is per started Python/R generation. Before either language runs, the Workbench correctly reports `not_started`. Exercise every enabled language after deployment and after changes to the OS, service unit, interpreter, or mount layout.

The current boundary is intentionally limited:

- the host filesystem is broadly readable so the interpreter and packages work, with targeted secret paths denied/masked;
- writes are confined to the session workspace and a private temporary directory;
- raw worker network is denied unless the host-global compatibility override is enabled;
- Host RPC, the daemon, local compute jobs, and remote provider operations are separate trust boundaries;
- there is no claim of seccomp, VM isolation, tenant separation, or containment against every kernel/OS vulnerability.

Do not set `OPENAI4S_KERNEL_ALLOW_RAW_NETWORK=1` on a routine deployment. If compatibility forces it, record the exception and assume arbitrary Cell network access.

## Restrict network egress

`OPENAI4S_EGRESS=off` is the default and means **no application allowlist enforcement**. Set `allowlist` to constrain `web_fetch`, `web_search`, and statically detected URLs in authorized `host.bash` calls. Review runtime domain grants and standing permission rules.

The allowlist is not a transparent network firewall:

- it operates at selected Host-tool boundaries;
- malformed targets and modes fail open by design;
- static shell URL detection can be bypassed by indirect or obfuscated commands;
- it does not govern every daemon-side provider connection;
- it does not stop raw Python/R sockets when the OS sandbox is off, degraded, or explicitly permits network.

Use the OS sandbox for worker raw-network denial and host/network firewalling for deployment-wide egress policy. `OPENAI4S_ALLOW_PRIVATE_FETCH=1` disables the Web fetcher's private/loopback/metadata-address guard and should be limited to a documented local integration.

## Review permissions and interactive execution

- Leave `OPENAI4S_NOTEBOOK_REPL=0` unless the operator explicitly needs arbitrary manual Python/R input. When enabled, user REPL Cells bypass the agent code classifier, although they still use the execution queue, worker sandbox, and audit path.
- Keep unattended approval deny/pending. Setting `OPENAI4S_UNATTENDED_APPROVAL=allow` changes unanswered risk gates into automatic approval and should be treated as a high-risk exception.
- Prefer `once` or project-scoped approvals. Review conversation/project/global rules regularly and remove broad wildcard rules.
- A post-restart approval does not execute stored arguments. It records that the interrupted action did not run and requires an explicit continue/replan. Preserve this behavior operationally; do not build automation that treats approval as replay.
- Remote compute submission, connector calls, file mutations, dynamic tools,
  and package/environment changes deserve narrower targets than read-only
  discovery. Current remote-compute coverage is incomplete: `compute_submit`
  is approval-gated, while result/cancel/close do not request a second
  approval, and legacy direct SSH/SCP helpers do not enter the Tool permission
  gate. Treat the daemon account and its SSH identity as the effective boundary
  for those paths.

The one-shot `host.bash` capability binds command hash, canonical working directory, worker generation, challenge, and expiry. It is still permissioned shell execution by the daemon user, not a safe substitute for account isolation.

## Manage secrets

- Prefer a private supervisor environment file or an external secret manager. Never commit `.env` or put secrets in shell history, command arguments, documentation, Session packages, or static-site build inputs.
- Remember that saved model profiles are stored in SQLite. Protect database backups as credentials.
- `host.credentials.set` values are memory-only and redacted/skipped in the relevant RPC audit/replay paths; they disappear on restart and are not a durable secret store.
- Kernel children receive an allowlisted environment rather than the daemon's complete environment. Do not weaken that allowlist casually.
- File-tool secret-name guards and `host.query` table denylisting protect specific application paths. They are not a general data-loss-prevention system.
- Remote-compute provider environment scrubbing is name-based. A secret placed in an unrecognized variable name is not guaranteed to be removed. Forward only provider-declared keys and inspect provider code before enabling it.
- SSH authentication remains external to the registry. Use dedicated keys, restrictive host entries, and remote accounts without unnecessary privilege.

Rotate credentials after suspected prompt injection, unexpected remote activity, log exposure, or backup loss. Stopping the daemon does not revoke provider-side tokens.

## Understand heuristic and fail-open layers

Do not turn green UI labels into stronger claims than the code makes:

| Layer | Current failure behavior |
|---|---|
| Agent Cell classifier | High-confidence static matches block; heuristic residuals allow; classifier exceptions and unconfigured LLM classification allow |
| Injection scanner | Adds a warning to detected tool content but does not remove or block it; exceptions and unavailable LLM scanning allow unannotated content |
| Biosecurity trajectory screen | In the CLI path, `BLOCK` prevents a Cell, `ESCALATE` is advisory, and missing/erroring model returns `ALLOW`; the Web path currently includes prompt guidance but does not call the trajectory screener |
| `dlopen` audit hook | CPython-only, targeted at shared libraries from writable roots; it is not a general syscall policy |
| Egress allowlist | Off and fail-open by default; selected Host-tool boundary only |
| Sandbox `auto` | Continues unsandboxed when unavailable; only `enforce` fails closed |

These layers remain valuable defense in depth. Operate them with their actual semantics.

## Audit and monitoring

At startup and after a first Python/R Cell, record:

- source revision and environment versions;
- `/health` result and service-account identity;
- Python and R sandbox backend, self-test result, and network policy;
- scientific package installation status;
- pending approvals and broad standing rules;
- enabled user Skills, dynamic tools, connectors, and remote capabilities;
- unexpected files or permission changes under the data directory;
- outstanding `host.compute` jobs, which are held in process memory rather than a durable scheduler.

The Action Timeline is a bounded/redacted user projection, not the complete raw audit database. Preserve the whole data directory and service logs under incident-retention policy when forensic completeness matters.

## Hardening changes require end-to-end tests

After changes to service supervision, sandbox packages, mounts, filesystem permissions, proxying, or network policy, exercise:

1. same-origin HTTP and WebSocket streaming through the selected access path;
2. an existing session and Artifact read;
3. lazy Python and R startup and per-language sandbox status;
4. file write and Artifact capture inside a workspace;
5. a denied and an approved permission flow;
6. Host Web access to an allowed and a blocked target;
7. graceful stop with active and queued work;
8. backup and isolated restore.

Unit tests are the floor. Browser streaming, worker isolation, package availability, SSH, and external providers require deployment-specific validation.
