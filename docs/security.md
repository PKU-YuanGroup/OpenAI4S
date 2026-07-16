# Security

> ⚠️ Read this before exposing the daemon beyond `localhost`.

Python/R scientific workers now have an OS-sandbox adapter at their spawn
boundary. On macOS it uses Seatbelt (`sandbox-exec`); on Linux it uses
bubblewrap. The default `OPENAI4S_KERNEL_SANDBOX=auto` performs a real startup
self-test, enforces the boundary when available, and otherwise continues with a
high-visibility **degraded** status. Use `enforce` to fail closed before a worker
starts, or `off` for an explicit trusted-host opt-out. Unsupported/degraded is
not equivalent to sandboxed; keep the default loopback bind even when the
self-test passes.

The sandbox makes the host filesystem read-only to a worker except for the
session workspace and its private temporary directory. Raw worker network is
blocked unless the trusted host-global
`OPENAI4S_KERNEL_ALLOW_RAW_NETWORK=1` escape hatch is set. Host-side Web/MCP
services remain available through audited Host RPC. This boundary covers
Python/R kernels and their subprocesses; the separate local `compute/jobs`
surface remains a privileged local operation and must not be treated as an
untrusted multi-tenant sandbox.

[`openai4s.security`](../openai4s/security) adds independent policy layers:

| layer | env (default) | what it does |
|---|---|---|
| **OS kernel sandbox** | `OPENAI4S_KERNEL_SANDBOX` (`auto`) | Seatbelt/bubblewrap detection + write/network self-test; `enforce` fails closed, `auto` reports degradation |
| **Child environment allowlist** | always on | rebuilds the Python/R environment from explicit runtime names; daemon LLM/API/cloud/OAuth secrets and loader-injection variables are not inherited |
| **Pre-exec classifier** | `OPENAI4S_SAFETY` (`heuristic`) | screens every *agent-authored* Python/R cell (`heuristic` / `llm` / `off`); an opted-in user's REPL Cell skips this classifier but still enters the worker sandbox and audit path |
| **`dlopen` audit hook** | `OPENAI4S_SAFETY_AUDIT_HOOK` (on) | `sys.addaudithook` refuses `ctypes.dlopen` of a `.so` from an agent-writable path |
| **Biosecurity screener** | `OPENAI4S_BIOSECURITY` (on) | trajectory screener (ALLOW / ESCALATE / BLOCK) on biosecurity-relevant content |
| **Injection detector** | `OPENAI4S_INJECTION_SCAN` (on) | annotates tool-returned content (web / PDF / MCP) so the model treats it as **data, not instructions** |
| **Egress allowlist** | `OPENAI4S_EGRESS` (`off`) | application policy for `web_fetch` / `web_search` and authorized `host.bash`; the OS sandbox is the separate raw-network boundary |
| **Remote-compute confinement** | `OPENAI4S_COMPUTE_CONFINEMENT` (`auto`) | `enforce` refuses `byoc:*` ops because no host-side boundary exists for the provider helper yet (see [`docs/compute.md`](compute.md)); `auto` runs unconfined and reports the posture rather than implying one |
| **Data-dir permissions** | always on | the data dir is `0700` and the database (plus any `-wal`/`-shm`) is `0600`; POSIX only — Windows needs an ACL, and the posture reports `supported: false` there rather than claiming a boundary |
| **Browser response headers** | always on | a hash-based CSP with no `'unsafe-inline'` in `script-src` and a same-origin `connect-src`, plus `nosniff` / `X-Frame-Options` / `Referrer-Policy` on every response including streamed artifact bytes |

`web_fetch` rejects loopback and private-network targets by default to reduce
SSRF risk. `OPENAI4S_ALLOW_PRIVATE_FETCH=1` is an explicit trusted-local
override (useful for testing a service on `127.0.0.1`); it does not weaken the
kernel OS sandbox or authorize arbitrary worker networking.

Additional enforcement: an opencode-style **permission broker** gates
risk-bearing tools, a **secret-file guard** blocks `.env` / `*.key` / `id_rsa`
from file tools, and file-tool paths are workspace-confined. `host.bash` binds
its canonical working directory to the workspace or an explicitly trusted
extra root, but it does not parse every command argument as a path jail:
outside reads can remain possible, and outside writes are not an OS guarantee
when the sandbox is off or degraded. Approval requests are durable SQLite
records. They survive broker/daemon recreation and are resolvable by ID; the
absence of a browser subscriber never silently allows a request. Headless
execution defaults to deny unless the operator explicitly sets
`OPENAI4S_UNATTENDED_APPROVAL=allow`.

A durable card is not a replay token. While the daemon is still running, a
decision wakes the exact blocked call. After a daemon restart that thread is
gone: approving the surviving card records that the old operation **did not
execute**, appends an argument-free `permission_resolution` marker to the
Action Ledger, and returns `requires_continue=true`. The browser then requires
an explicit **Continue and replan** action. Conversation/project/global choices
persist the selected standing rule. A `once` choice instead creates one exact
`root_frame_id` + tool + permission-target grant, expires after 15 minutes, and
is consumed atomically only when a fresh matching action reaches an `ask`
decision. Stored/redacted approval payloads are never executed as arguments.

### The Notebook REPL is off by default

The web UI's right-hand Notebook is a **read-only execution trace** by default.
The developer REPL is disabled and only appears when
`OPENAI4S_NOTEBOOK_REPL=1`. With it off, the mutating `kernel/*` routes
(`execute`, `env`, `restart`, `stop`, `start`, `interrupt`) return `403`;
`kernel/install` remains available because it backs Customize → Compute. When
enabled, the input is multiline, selects Python/R, and appends a new immutable
Cell through the same FIFO execution coordinator as Agent work. Interrupts
must carry the exact `execution_id`, `owner.kind`, and `owner.id`; broad
session-level SIGINT is rejected.

Provider-native JSON control tools — deterministic list/read/glob/grep/web/env/
edit/write and orchestration capabilities — route through the same policy
envelope as `host.*` Cell calls. Their public schema, approval metadata, and
real behavior live together in named `Tool` subclasses. The legacy fenced
`tool`-block syntax is compatibility-only and is not the advertised action
surface.

There is no registered shell tool. `host.bash` asks the Host to authorize the
exact command hash, canonical cwd, active worker generation, challenge, and
short expiry; detected domains are checked during authorization. The session
frame ID is retained for audit, not as an additional consume-time token
binding. The worker validates and consumes that random token once before it
starts `subprocess`; the Host never executes the shell. Static command/egress
checks remain defense in depth, and the redacted result plus a bounded
workspace diff enter the audit/step records. A missing, expired, reused,
wrong-generation, or mismatched token fails closed.

User-authored Skills are likewise separated from bundled trust. Host/Web writes
are confined to `<data_dir>/user-skills`, reject symlink/path escapes, and
cannot shadow a bundled directory. User-space frontmatter cannot promote a
document to the trusted `openai4s` origin; the normal Host authoring workflow
uses an explicit publish transition from `draft` to `personal`.

### Secret reads and secret logs

The agent can introspect its own SQLite store through the read-only `host.query`,
so secret-bearing and internal-control tables are **denylisted** and never reach
it:

- The denylist covers `settings` (live/saved model credentials), `connectors`,
  `memories`, `host_call_log`, permission rules/requests, raw Action Ledger and
  execution-attempt tables, kernel generations, capability state/manifests,
  branches/checkpoints/snapshot operations, and the Recovery Journal.
  `host.query.schema()` hides the same set. The check runs against a copy with
  single-quoted string literals and comments stripped, so a denied word only
  inside a literal (for example `SELECT 'settings' AS note`) is not falsely
  rejected, while an identifier-quoted table reference (`FROM "settings"`)
  still trips it.
- Because the denylist is a table-name match, a query that reads the unrelated `agents.connectors` *column* is also refused; no bundled skill relies on that read.

Credential values passed to `host.credentials.set(name, value)` are held only in an in-memory vault (never persisted). To keep that true end to end, the **RPC audit log** redacts them: `credentials_get` / `credentials_list` are not logged at all, and `credentials_set` is logged for audit **with its args redacted** — the plaintext value never enters `host_call_log`. The replay tape recorder likewise skips `credentials_set`, so an exported notebook cannot carry a plaintext credential.

### Credentials at rest — a known gap

Configured credentials are stored in SQLite **as plaintext**: model-profile API
keys and `llm_api_key` / `tavily_api_key` in `settings`, and connector `env` in
`connectors`. There is no encryption layer today. Two consequences worth stating
plainly rather than leaving implied:

- **Anything that copies the data dir copies the secrets.** A backup, an rsync,
  a container image layer, or a support bundle carries them in the clear.
- **The file mode is the only barrier.** The data dir is `0700` and the database
  `0600` (see the table above), which removes the trivial read by another local
  account — but a mode is not encryption, and it does nothing on a platform
  where POSIX modes are not enforced.

What *is* enforced is that credentials do not leave over the API: connector and
model-profile responses are allowlist projections (`env_keys` / `has_api_key`,
never the values), covered by canary regressions in
`tests/test_secret_canary.py` that assert on the secret's bytes rather than on
field names.

Encrypting these at rest — a broker holding opaque references with short-lived
leases, backed by the system keychain or Secret Service, failing closed on
headless rather than to an obfuscated file — remains outstanding.

### BYOC provider import-time secret scrubbing

The remote-compute worker (`openai4s_compute_provider`) loads an untrusted-ish provider shim (`skills/remote-compute-<id>/provider.py`) by file path. To keep a provider's **top-level module code** from reading credential-shaped or known-prefix environment variables, scrubbing is two-staged. This is a **name-based heuristic** — a secret stored in a variable whose name matches neither rule below is **not** scrubbed:

- `openai4s_compute_provider/__main__.py` calls `scrub_secret_env()` — the provider-agnostic baseline — **before** `exec_module` imports `provider.py`. It removes every env var whose name matches a credential shape (`*_API_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`, …, via `CRED_KEY_RE`) or starts with a known provider/cloud secret prefix (`NGC_`, `NVIDIA_`, `HF_`, `AWS_`, `OPENAI_`, `ANTHROPIC_`, `OPENAI4S_LLM_`, … — `BASELINE_SECRET_PREFIXES`).
- The resident prologue (`ByocResident._prologue`) re-scrubs with the *loaded* provider's own declared `secret_env_prefixes` before it reads the credential (from stdin for oneshot, fd-3 for repl). The credential itself is passed over that channel and is **never** placed in the process environment.

Because stage 1 cannot know the provider's declared prefixes before importing it, the baseline is what enforces the name-based rule at provider import time; the provider-specific prefixes are folded in at stage 2, before the credential is read. Non-secret operational vars the worker needs (e.g. `OPENAI4S_HOST_NETNS_INO` for the confinement probe, `HTTP_PROXY`/`HTTPS_PROXY`) do not match either rule and survive. This is enforced by synthetic-secret import-time and prologue tests in `tests/test_compute_nvidia.py`.

## Remote access

The daemon binds `127.0.0.1` by default. Reach the UI over an SSH tunnel — **never** expose `0.0.0.0` on an untrusted network:

```bash
ssh -L 8760:127.0.0.1:8760 user@your-host
```

If you must bind a non-loopback address (`OPENAI4S_HOST=0.0.0.0`) or set `OPENAI4S_REQUIRE_TOKEN=1`, the server prints a one-time access token at startup and rejects any request without `?token=…` (`401`).
