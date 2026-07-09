# Security

> ⚠️ Read this before exposing the daemon beyond `localhost`.

The daemon runs agent-authored code with **no OS-level sandbox** (no Seatbelt / bubblewrap) — `kernel/execute`, `compute/jobs`, and `host.bash` are equivalent to a shell on the host. This is fine for a single-user local tool bound to `127.0.0.1` (the default). On top of that, [`openai4s.security`](../openai4s/security) adds software layers reverse-engineered from Claude Science — all **opt-out via env**, all **fail-open** when no base model is set:

| layer | env (default) | what it does |
|---|---|---|
| **Pre-exec classifier** | `OPENAI4S_SAFETY` (`heuristic`) | screens every *agent-authored* cell before it runs (`heuristic` / `llm` / `off`); your own Notebook cells are never screened |
| **`dlopen` audit hook** | `OPENAI4S_SAFETY_AUDIT_HOOK` (on) | `sys.addaudithook` refuses `ctypes.dlopen` of a `.so` from an agent-writable path |
| **Biosecurity screener** | `OPENAI4S_BIOSECURITY` (on) | trajectory screener (ALLOW / ESCALATE / BLOCK) on biosecurity-relevant content |
| **Injection detector** | `OPENAI4S_INJECTION_SCAN` (on) | annotates tool-returned content (web / PDF / MCP) so the model treats it as **data, not instructions** |
| **Egress allowlist** | `OPENAI4S_EGRESS` (`off`) | fences `web_fetch` / `web_search` / `bash` to science APIs & package indexes; blocked domains recover via `host.request_network_access(domain=…)`, which **you** approve |

Additional enforcement: an opencode-style **permission broker** gates risk-bearing tools, a **secret-file guard** blocks `.env` / `*.key` / `id_rsa` from all file tools, and every file/shell op is **workspace-jailed**.

### The Notebook REPL is off by default

The web UI's right-hand Notebook is a **read-only execution trace** by default. The developer REPL — arbitrary kernel code execution from the right panel — is **disabled** and only comes back when you set `OPENAI4S_NOTEBOOK_REPL=1`. With it off, the mutating `kernel/*` routes (`execute`, `env`, `restart`, `stop`, `start`, `interrupt`) return `403`; the classifier note above about "your own Notebook cells" applies only once you have opted the REPL back in. `kernel/install` remains available because it backs Customize → Compute rather than the Notebook REPL.

ReAct **tool calls** — the deterministic `list` / `read` / `glob` / `grep` / `web` / `env` / `edit` / `write` / `bash` ops the model can invoke as ` ```tool ` JSON — route through the **same** `HostDispatcher` as `host.*` cell calls, so they pass the same permission broker, egress fence, injection screen, and pre-exec (dangerous-command) static gate. The ReAct surface adds no bypass around any of these layers.

### Secret reads and secret logs

The agent can introspect its own SQLite store through the read-only `host.query`, so secret-bearing tables are **denylisted** and never reach it:

- `host.query` refuses any statement that references `settings` (the live LLM API key + saved model profiles), `connectors` (MCP server env / launch command), `memories`, `host_call_log`, or `permission_rules`. `host.query.schema()` also hides these tables. The check runs against a copy with single-quoted string literals and comments stripped, so a denied word appearing only inside a literal (e.g. `SELECT 'settings' AS note`) is not falsely rejected, while an identifier-quoted table reference (`FROM "settings"`) still trips it.
- Because the denylist is a table-name match, a query that reads the unrelated `agents.connectors` *column* is also refused; no bundled skill relies on that read.

Credential values passed to `host.credentials.set(name, value)` are held only in an in-memory vault (never persisted). To keep that true end to end, the **RPC audit log** redacts them: `credentials_get` / `credentials_list` are not logged at all, and `credentials_set` is logged for audit **with its args redacted** — the plaintext value never enters `host_call_log`. The replay tape recorder likewise skips `credentials_set`, so an exported notebook cannot carry a plaintext credential.

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
