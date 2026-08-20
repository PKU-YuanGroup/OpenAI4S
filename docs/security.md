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
| **Remote-compute confinement** | `OPENAI4S_COMPUTE_CONFINEMENT` (`auto`) | the provider helper runs inside a real OS boundary — Seatbelt on macOS, bubblewrap on Linux — that puts the user's home out of reach — a `tmpfs` over it on Linux; on macOS a denial of `file-read-data` *and* `file-read-xattr`, since an xattr on macOS routinely holds the file's own bytes and `getxattr` was serving what `open` refused — confines writes to the job's stage directory, and (macOS) denies the keychain services, because the credential is read *by securityd* and no file rule covers that. `available()` proves it by establishing a boundary and probing it, not by `which`; the helper re-checks from inside before reading a credential and exits 71 without acting if it does not hold. **The network is deliberately not isolated** (`network_isolated: false`) — calling a provider's REST API is the helper's whole job, so outbound egress is a separate capability and is not enabled. `enforce` refuses `byoc:*` ops only where no boundary can be established: no `bwrap`/`sandbox-exec` on `PATH`, a host that fails the self-test (e.g. unprivileged user namespaces disabled), or a platform with no backend — and it refuses on *every* op, not just submit. `auto` degrades visibly in those same cases; `off` skips the wrapping entirely (see [`docs/compute.md`](compute.md)) |
| **Secret store** | `OPENAI4S_SECRET_STORE` (`auto`) | credentials behind an opaque reference in the system keychain (after a real round-trip self-test) or the process environment; `auto` **fails closed** when neither is available. Plaintext is reachable only by asking for it by name, and no obfuscated-file fallback exists |
| **Data-dir permissions** | always on | the data dir is `0700` and the database (plus any `-wal`/`-shm`) is `0600`; POSIX only — Windows needs an ACL, and the posture reports `supported: false` there rather than claiming a boundary |
| **Browser response headers** | always on | a hash-based CSP with no `'unsafe-inline'` in `script-src` and a same-origin `connect-src`, plus `nosniff` / `X-Frame-Options` / `Referrer-Policy` on every response including streamed artifact bytes |

`web_fetch` rejects loopback and private-network targets by default to reduce
SSRF risk. `OPENAI4S_ALLOW_PRIVATE_FETCH=1` is an explicit trusted-local
override (useful for testing a service on `127.0.0.1`); it does not weaken the
kernel OS sandbox or authorize arbitrary worker networking.

Additional enforcement: an opencode-style **permission broker** gates
risk-bearing tools, a **secret-file guard** blocks `.env` / `*.key` / `id_rsa`
from file tools — and, because a credential is in the directory as often as it
is in the name, every path under `.ssh` / `.aws` / `.gnupg` / `.docker` /
`.kube` / `.azure` / `.config/gcloud` / `.config/gh` whatever the file is
called. The guard is applied to the *resolved* path as well as to the string
the caller wrote, so a symlink inside the workspace cannot walk a secret past
it. File-tool paths are workspace-confined. `host.bash` binds
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

**Retention is bounded by construction.** `diagnostics.rotate_log` rolls a log
at 8 MiB and keeps 3 generations, deleting the oldest — a size rather than a
duration, because a daemon can be quiet for a week or chatty for an hour and
bytes are what actually run out. Unbounded logs are not a neutral default; they
are a slow disk-full that arrives at the least convenient moment.

**`openai4s diagnostics`** writes a redacted bundle for a bug report: postures
and versions, plus log tails. The database is never included — it holds research
work and, until every credential is brokered, secrets — and the manifest names
what was left out, so nobody is tempted into a second, manual, unredacted
collection. Log lines pass through `observability.redact_text`, which scans
*word by word*: `redact` asks whether a whole value is a credential, which is
right for a field and wrong for a log line where a token sits mid-sentence. An
earlier version of the bundle passed the structured lines and leaked the plain
one.

The log tail it collects is `logs/app.out` — the file every packaged launcher
redirects the daemon's stdout and stderr into, and therefore where structured
events land, since they are written to stderr. An earlier version globbed
`*.log*`, which matches no file the product writes, so a bundle from a real
install carried postures and versions and *no logs at all*, with a manifest
that listed what it did include and so read as complete.

A credential inside a **URL** needs its own pass. `redact_text` scans word by
word and a URL has no spaces, so the whole thing arrives as one word — and
`_looks_opaque` deliberately answers "not a credential" for anything starting
`http://`, because fingerprinting every URL would gut the log. The secret is
*inside*, in a query value or a path segment, so URL-shaped words go through
`observability.redact_url`, which keeps the parameter name as provenance and
fingerprints the value. The daemon's own startup banner is exactly this shape —
`listening at http://127.0.0.1:8760/?token=…`, printed to stdout, which the
launchers redirect into `app.out`, which the bundle collects.

What leaves in the bundle is decided **deny-by-default**, and that is a
different layer from the redaction above. `redact`/`redact_text`/
`redact_identities`/`redact_url` make the *local* operator log safer to read,
and the log keeps its richness on disk. The archive is narrower, because it is
the thing standing between a user's disk and a public issue tracker:

- a **structured** line survives only as an allowlist of known keys, and every
  key is checked against a **closed set written down in source** — not against
  a pattern. That distinction took three attempts to get right, and each wrong
  answer was the same mistake at a smaller scale. First one shared "short
  enough" regex, which admitted spaces, `/` and `.`: prose in `detail`, a path
  in `surface`, a command in `status`. Then per-field *patterns*, which
  admitted `PRIVATE_COHORT_ALPHA_SEVEN` — a legal identifier with no digits, so
  it satisfies every identifier rule and never reads as opaque. **Syntax is not
  provenance.** Now `event` and `surface` are the vocabularies this repository
  emits, `exception` is a category from a named set of exception types, `level`
  and `status` are enums, `detail` is one fixed sentence, and a variable id
  (`request_id`, `correlation_id`) is *always* fingerprinted — even though the
  daemon generates those, because the archive reads them out of `app.out` and a
  line in a file can carry any 16- or 32-hex string. Support loses nothing: the
  fingerprint of the id a user quotes matches the one in the archive.
- a **file name** is not metadata either. Log members are numbered by the
  archive (`logs/log-0001.json`) and the MANIFEST lists only those generated
  names, because a log named after a token puts it in two places no content
  scrubber looks: the ZIP member name and the listing.
- an **unstructured** line is never shared verbatim at all. `app.out` is the
  daemon's whole stdout and stderr — every `print`, every `traceback.print_exc`,
  every dependency's chatter — and no pattern set makes arbitrary text safe. The
  archive carries a count, a classification and a fingerprint instead.
- `report.json` is built to a **declared schema** whose leaves are closed sets,
  numbers, or reductions — never patterns. `machine` is the real architecture
  set, `platform` and `backend` are enums, a version keeps only its parsed
  numeric components (`6.5.0-15-generic` → `6.5`, `3.privatecohortalpha` → `3`)
  and a migration name is fingerprinted rather than enumerated, because an
  enumerated set of names would go stale *silently* the day someone adds one.
  `json.dumps(..., default=str)` is gone, unknown keys are counted rather than
  rendered, and nothing calls `str()` or `repr()` on a value **or a key**: a
  mapping key can be an object whose `__str__` raises or returns 50 MB.

`record_diagnostic` is the source, and it no longer renders the exception.
There is no redacted rendering of `str(exc)` on the record, because a rendering
is the one operation an unknown exception influences and it can be arbitrary,
enormous, or itself raise. The record carries the surface, the exception's
class **category** — the nearest ancestor in a set of exception types this
repository names, so `type("PRIVATE_COHORT_ALPHA_SEVEN", (RuntimeError,), {})`
reports `RuntimeError` and the caller's own string never appears — and an
`error_class` fingerprint derived from the *type*, so two
occurrences of the same failure remain recognisably the same failure and a
support ticket quoting a `request_id` still leads somewhere. The same rule
applies to the agent's observation when an environment switch fails, and to the
two posture probes in `security_posture`, which report an `error_type` rather
than an exception message.

An earlier version of this section said a shell command quoted inside a failure
was "deliberately not removed" because the bundle is operator-facing. That was
wrong on its own evidence: the same change made `app.out` the file the bundle
collects, and once an artifact leaves the machine "operator-facing" is not a
property it still has.

### Credentials at rest

Model and search credentials are held by a **SecretBroker**
([`security/secret_broker.py`](../openai4s/security/secret_broker.py)): the row
stores an opaque reference and the value lives in the system keychain. New
references use
`secret://v2/<store-namespace>/<scope>/<name>`; the namespace is a
domain-separated digest of the canonical database path, not the path or any
credential. Thus two data directories cannot overwrite the same physical
Keychain or Secret Service slot, and copying a database does not copy authority
to its credentials. The reference is not derived from the value, so it is safe
to log and safe to sit in a row. Covered today: `llm_api_key`,
`tavily_api_key`, the shared `agent_plan_key` used by DataPro and Doubao Search,
the per-profile `api_key` of every saved model profile, and every connector
`env` value.

Legacy v1 system-keychain references contain no Store ownership evidence. They
are never read, claimed, or deleted automatically; the UI reports the
credential absent and the user saves it again into the v2 Store namespace.
This deliberately leaves the ambiguous legacy slot untouched because another
data directory may still use it. DB-local plaintext v1 slots remain readable,
as do explicitly process-global environment variables.

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

**Servers supply credentials through the environment.** The preferred variable
is `OPENAI4S_SECRET_V2_<STORE_NAMESPACE>_<SCOPE>_<NAME>`. Existing
`OPENAI4S_SECRET_<SCOPE>_<NAME>` variables (for example,
`OPENAI4S_SECRET_LLM_LLM_API_KEY`) remain an explicit process-global fallback,
so an upgrade does not silently change an operator's deployment contract. Set
either from systemd's `EnvironmentFile`, a Kubernetes Secret, or whatever the
config management already owns; set `OPENAI4S_SECRET_ENV=1` to opt in before any
are configured. **Nothing is written to disk** — stronger than the keychain
case, not a fallback from it. It is read-only on purpose: if the environment
owns the secret, the app must not overwrite it behind the operator's back, so a
write attempt fails with the exact preferred variable name to set.

An injected credential resolves **with no settings row at all**, which is the
only state a fresh server can be in: nothing can put the reference row there,
because `put` refuses by design and migration has no plaintext to move. A
resolver that stopped at an empty row made the variable dead on any data
directory that had never had a key saved through a writable backend — and
nothing raised, so the symptom was only that the UI reported the model as
unconfigured. `resolve_setting` therefore asks a **read-only** backend for
`<scope>/<key>` when the row is absent, the scope coming from the same
`SETTINGS_SECRETS` table migration uses. Read-only specifically: behind a
writable backend an empty row is the app's own answer, and since clearing a key
swallows a failed delete, going to the backend anyway would let a revoked
credential come back to life.

The reference it builds carries this Store's **namespace**, i.e. the same v2
reference `put` would have written. That is not a detail: a v1 reference
reaches only the plain `OPENAI4S_SECRET_<SCOPE>_<NAME>`, while the refusal an
operator sees when the UI declines to save a key names the namespaced
`OPENAI4S_SECRET_V2_<NS>_<SCOPE>_<NAME>`. Built the v1 way, following that
instruction exactly still resolved to nothing — the same dead end one spelling
over. The v2 path tries the namespaced variable and falls back to the plain
one, so both work; the plain form is the portable one, since a namespace is
derived from the data directory's real path and a Secret written against one
data directory would not resolve after the volume moved.

The corollary is that clearing an injected key from the UI does not unset it —
the environment owns that value, and the settings route reports the
`has_api_key` it re-reads afterwards rather than claiming the clear took.

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

- **Windows has no system-keychain backend**, so `auto` fails closed unless
  environment injection is configured. Plaintext remains available only when
  the operator explicitly selects `OPENAI4S_SECRET_STORE=plaintext`.
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

One documented exception: when a WSL2 user has explicitly set
`localhostForwarding=false` in `.wslconfig`, the Windows launcher binds the
daemon to the WSL NAT (`eth0`) address instead, because loopback is then
unreachable from the Windows browser. That address is routable only from the
Windows host across Microsoft's virtual switch — not from the LAN — and the
token gate below still applies to it. Details:
[Windows / WSL2 guide](windows-wsl.md).

The server requires an access token by default, on loopback too. It is minted
once under the data dir (`access-token`, mode 0600), survives restarts, and is
printed at startup as a URL you open once to set the cookie. Scripts send it as
`Authorization: Bearer <token>` or `X-OpenAI4S-Token`.

The `?token=` form in that startup URL works for one thing only: opening the
app at `/`. Every other path refuses it — including `/preview/<id>`, which
answers with artifact bytes and used to be bootstrappable because the rule was
written as "not `/api/v1/*` and not `/static/*`" rather than as an allowlist. A
mutation carrying `?token=` is refused outright, cookie or no cookie: a URL
with a credential in it is a credential you can paste into chat, and one that
still works is one nobody notices they leaked.

`OPENAI4S_REQUIRE_TOKEN=0` turns the gate off on loopback, until the version
named by `gateway.LEGACY_TOKEN_OPT_OUT_REMOVED_IN`. Weigh it against what the daemon exposes: `kernel/execute`,
`compute/jobs` and `host.bash` all execute code, and "local" includes every
other process on the machine. The Host and Origin guards stop a malicious web
page; they do nothing about a local process.

## Web sharing

Web sharing (off by default; see [webshare.md](webshare.md)) never changes the
daemon's bind — it dials *out* over WSS to a relay you run. The public surface is
a **materialized read-only snapshot**, not a proxy to the gateway. Invariants:

- The daemon stays on `127.0.0.1`; the tunnel is always daemon-initiated and only
  created when sharing is both enabled and configured (otherwise zero share
  network threads exist).
- A visitor can only reach bytes that were captured at share time by the
  `SessionPackageService` export pipeline (fail-closed secret scan), served
  GET/HEAD-only from an immutable snapshot directory or the fixed in-memory viewer
  asset set. The share request path never imports the dispatcher, kernel, or a
  subprocess and never proxies a gateway route.
- The share package is a **flattened** single-branch snapshot with no checkpoints
  and no project memories, permission, or capability state.
- The relay treats daemon responses as constrained input (status/header
  allowlist, `Set-Cookie`/`Location`/hop-by-hop refused); the daemon treats the
  relay as untrusted (bounded, schema-checked frames). The publisher token grants
  no local-daemon access — only the ability to publish under your share subdomains.
- Imported shares remain quarantined and view-only until an explicit fresh
  restart, exactly like any imported Session package.

The tunnel is not a way to remotely access the daemon: the read-only snapshot
surface has no route overlap with the gateway.
