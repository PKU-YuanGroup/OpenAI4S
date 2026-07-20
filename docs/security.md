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
| **Secret store** | `OPENAI4S_SECRET_STORE` (`auto`) | credentials behind an opaque reference in the system keychain (after a real round-trip self-test) or the process environment; `auto` **fails closed** when neither is available. Plaintext is reachable only by asking for it by name, and no obfuscated-file fallback exists |
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

### Correlation IDs and structured logs

Every HTTP request carries an id
([`observability.py`](../openai4s/observability.py)). A client-supplied
`X-Request-Id` is honoured — bounded to 64 chars and stripped to
`[A-Za-z0-9-_]`, so it cannot forge a log line or inject a header — otherwise
one is generated. It is echoed back in `X-Request-Id` and held in a `ContextVar`
so anything reached from the request, including a thread it spawns, can stamp
the same id without threading a parameter through every call.

Structured logs are **off unless `OPENAI4S_STRUCTURED_LOGS=1`**: turning them on
by default would change what every existing deployment writes to disk. When on,
each event is one JSON object per line on stderr.

Redaction is by **value shape, not field name**. A denylist of key names is not
evidence that a log has no secrets in it — a credential stored under an
unremarkable key is precisely the one such a rule misses. So any long, opaque,
mixed-class string is replaced by `<redacted:<fingerprint>>` wherever it occurs,
including nested, alongside the obvious key-name matches. The fingerprint is
stable and non-reversible, so two lines about the same secret remain
correlatable without either revealing it. Paths, URLs, and short identifiers are
deliberately preserved — redaction that eats the useful fields makes the log
worthless, and a worthless log stops being read.

**Prompts and research data are never logged by this path.** There is no
`log_prompt` helper, and the request log records the path only, never the query
string. The model's messages and the kernel's data are the likeliest carriers of
a user's unpublished work, so the default is that they have no route out through
here at all.

Retention is currently the operator's: the daemon writes to stderr and does not
rotate, expire, or ship logs anywhere. A deployment that enables structured logs
owns their lifetime.

### Credentials at rest

Model and search credentials are held by a **SecretBroker**
([`security/secret_broker.py`](../openai4s/security/secret_broker.py)): the row
stores an opaque reference such as `secret://v1/llm/llm_api_key` and the value
lives in the system keychain. The reference is not derived from the value, so it
is safe to log and safe to sit in a row. Covered today: `llm_api_key`,
`tavily_api_key`, the per-profile `api_key` of every saved model profile
(`secret://v1/model_profile/<id>`), and every connector `env` value
(`secret://v1/connector_env/<id>.<VAR>`).

Connector env brokers **every** value, not only the credential-shaped ones.
Choosing by variable name would mean a regex over names — the same name-based
heuristic the confined compute runtime's README warns about, where "a secret
stored under an unrecognized name is not removed". A connector's env is small,
the UI only ever shows the names, and a benign `MODE=prod` in the keychain costs
nothing next to one missed `TOKEN_FOR_X`.

A reference is a truthy string that is not a key, which sets one trap worth
knowing about: `if profile["api_key"]:` reports a revoked credential as present,
and handing that field to a provider fails auth in a way that looks like a bad
key. Every read goes through `resolve_profile_key` /
`Store.get_secret_setting`, which resolve the value and report absence honestly.
Deleting a profile deletes its credential, so a removed endpoint does not leave
its key in the keychain with nothing left that refers to it.

| mode (`OPENAI4S_SECRET_STORE`) | behaviour |
|---|---|
| `auto` (default) | System keychain (verified by a **real round-trip self-test**), else environment injection. If neither is available, **fail closed** — refuse to handle credentials at all. |
| `keychain` | Keychain only. Fail closed. |
| `env` | Environment injection only. Fail closed. |
| `plaintext` | Store in the database in the clear. Never implicit; asked for by name. |

**`auto` fails closed rather than degrading.** It used to fall through to
plaintext with a warning, which inverted the risk: the deployment least able to
protect a secret — a Linux server, with neither a keychain nor a session bus —
was exactly the one that silently got none, while a laptop that needed it least
got the keychain. A warning printed at boot is not a control; it scrolls away
and the credential stays in the clear.

**Servers supply credentials through the environment.** Set
`OPENAI4S_SECRET_<SCOPE>_<NAME>` (e.g. `OPENAI4S_SECRET_LLM_LLM_API_KEY`) from
systemd's `EnvironmentFile`, a Kubernetes Secret, or whatever the config
management already owns; set `OPENAI4S_SECRET_ENV=1` to opt in before any are
configured. **Nothing is written to disk** — stronger than the keychain case,
not a fallback from it. It is read-only on purpose: if the environment owns the
secret, the app must not overwrite it behind the operator's back, so a write
attempt fails with the exact variable name to set.

Backends are driven through the system CLIs, because the core is stdlib-only and
cannot depend on `keyring`: `security` on macOS, `secret-tool` (Secret Service)
on Linux desktops. The value is fed on **stdin, never argv** — `security`'s own
help says "Use of the -p or -w options is insecure", and a value on the command
line is readable by any local `ps` for the life of the call. Presence of the CLI
is not treated as availability of a keychain: a locked keychain or a missing
session bus fails only at first use, so the broker proves a round-trip before
trusting a backend with a real secret.

There is deliberately **no obfuscated-file backend**. Base64, XOR, or a
hand-rolled cipher over a key stored beside the ciphertext is not a boundary; it
is a plaintext store described in words that suggest otherwise.

Existing plaintext keys migrate on daemon start, ordered **write → verify by
reading back → replace the row with a reference**. Every prefix of that is safe
to be interrupted at: crash after the write and the plaintext is still
authoritative and the next start retries. The verify step reads the value back
and compares it, because a write that did not raise is not evidence the value is
retrievable — and a reference that resolves to nothing is worse than the
plaintext it replaced. A key that cannot be migrated stays plaintext and keeps
working, reported on stderr.

Still outstanding, and stated plainly rather than left implied:

- **Windows has no backend**, so it resolves to plaintext under `auto`.
  `security` and `secret-tool` cover macOS and Linux desktops; DPAPI would need
  a `ctypes` shim.
- **The file mode is the only barrier for what is not yet migrated.** The data
  dir is `0700` and the database `0600` (see the table above), which removes the
  trivial read by another local account — but a mode is not encryption.
- **Rotation and recovery have no owner yet.** Nothing re-keys or expires a
  stored credential, and a keychain entry deleted out from under the app reports
  as "not configured" and must be re-entered.

What *is* enforced throughout is that credentials do not leave over the API:
connector and model-profile responses are allowlist projections (`env_keys` /
`has_api_key`, never the values), covered by canary regressions in
`tests/test_secret_canary.py` that assert on the secret's bytes rather than on
field names.

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
