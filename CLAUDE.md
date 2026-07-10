# CLAUDE.md / AGENTS.md

This file provides shared guidance to both Claude Code (`CLAUDE.md`) and Codex (`AGENTS.md`, symlinked to this file) when working with code in this repository.

Keep future guidance platform-neutral unless a section explicitly calls out a Claude Code- or Codex-specific workflow.

## What this is

**OpenAI4S** is a pure-stdlib **Code-as-Action** autonomous scientific research agent — an open reproduction of Anthropic's "Claude Science" architecture. The model's action space is a Turing-complete kernel (it writes and runs real Python/R), **not** a fixed `tool_use` schema. Every capability the agent has is a method on an in-kernel `host` singleton.

**Core is zero-dependency by design.** The engine, the LLM client (over `urllib`), and the web server (`http.server` + a hand-rolled WebSocket) use only the Python standard library. This is a hard constraint — see conventions below.

## Commands

```bash
./setup.sh                          # one-time: uv sync --extra science + install pre-commit hook
./start.sh                          # launch daemon + web UI at http://127.0.0.1:8760/
uv run pytest                       # full offline test suite (LLM is mocked — no network, no keys)
uv run pytest tests/test_agent.py::test_max_turns_stop   # a single test
uv run pytest tests/test_kernel.py -k background         # tests matching a pattern
uv run pre-commit run --all-files   # format + lint (black · isort --profile black · ruff)
uv run openai4s run "…" -v          # run one Code-as-Action task in-process, no daemon
openai4s setup                      # build the 4 conda kernel envs from envs/*.yml (--dry-run to preview)
```

CLI subcommands (`openai4s <cmd>`): `serve` · `status` · `stop` · `url` · `run` · `setup`. `start.sh` just runs `openai4s serve`.

Tests are **offline**: `tests/conftest.py` redirects `~/.openai4s` to a tmp dir per test and sets a fake `deepseek` provider + key. Don't add tests that require live LLM/network calls to the default suite.

## The dual loop (the central architecture)

Read `docs/architecture.md` first. The system is two nested loops:

- **Outer loop** — `openai4s/agent/loop.py`. The REPL *turn* loop: model emits prose + exactly one ```python code cell → pre-exec safety classifier → cell runs in a **persistent kernel subprocess** (namespace survives across turns) → stdout/stderr/artifacts/`getrusage` collected and fed back as an observation → repeat. **A task ends only when the agent calls `host.submit_output(...)`** — completion is a structured host-channel signal, never a text convention.

- **Inner loop** — `openai4s/kernel/manager.py`. *Within a single cell*, agent code may call `host.llm(...)` / `host.delegate(...)` / `host.compute(...)` any number of times. The kernel worker emits a `host_call` frame mid-execution over a channel separate from stdout; the manager routes it to the `HostDispatcher`, writes back `host_response`, and the blocked cell resumes. This synchronous mid-cell RPC is what a `tool_use` architecture lacks.

**Kernels are per-session and long-lived.** The gateway runs each user message's loop on a persistent kernel keyed by `root_frame_id`, so the namespace survives across turns and the web UI's live Notebook shares the *same* kernel (lifecycle: `restart_kernel` / `stop_kernel` / `start_kernel` / `kernel_alive` / `kernel_status` in `gateway.py`). Sub-agents from `host.delegate` run on separate kernels via a bounded `ThreadPoolExecutor` (`agent/delegation.py`).

**Kernel I/O is thread- and deadlock-sensitive — preserve its discipline.** `worker.py` holds `_HOST_CALL_LOCK` for the whole `host_call` request/response transaction (only one RPC in flight at a time) and serializes stdout writes; `manager.py` bumps `generation` on every respawn. When touching the kernel/manager protocol, keep the single-frame-reader loop, the id-routed `host_response`, and the transaction lock intact — and re-run `tests/test_kernel.py` after any change.

## Where things live

- **`openai4s/agent/`** — outer loop (`loop.py`), context `compaction.py` (summarize old turns past a token threshold, archive raw slices), `delegation.py` (concurrent sub-agents via `host.delegate`; fanout cap 48, session cap 1000, `MAX_DEPTH` 4 — depth-4 children are leaves that can't re-delegate).
- **`openai4s/kernel/`** — `manager.py` (host side: spawns `worker.py`, drives the JSON-per-line protocol + inner RPC), `worker.py` (the subprocess kernel that executes cells; per-cell `compile(code, "<kernel:N>")` via `linecache` for accurate `error_lineno`, `getrusage` accounting, arms the in-kernel dlopen guard), `environments.py` (per-task conda-env selection), `background.py` (`host.exec_background`), `guards.py`, `provenance.py`. Cells run under `sys.executable` by default (i.e. the active venv) or a selected conda env.
- **`openai4s/kernel/provenance.py`** — object-level data lineage, running *inside* the worker. It tags objects read from an artifact with that artifact's source `version_id`, propagates the tag through indexing/slicing/`json.loads`/scalar ops, and on write reports `lineage_edges` (input version → output version) to the host. Escape hatch: `OPENAI4S_PROVENANCE_OFF=1`. This backs the UI's "produced by cell N / inputs" provenance view.
- **`openai4s/sdk/host.py`** — the `host` facade injected into the kernel. Every method here routes through `host_call(method, args)`.
- **`openai4s/host_dispatch.py`** (large — the real work) — the `HostDispatcher`: server-side implementation of every `host.*` method (`llm`, `query`, `artifacts`, `delegate`, `compute`, `fold`, `mcp`, `skills`, endpoints, credentials…). **Soft-fail contract:** a handler may return a single-key `{"error": msg}` dict; the worker turns that into a `RuntimeError`.
- **`openai4s/llm.py`** — multi-provider wire client over `urllib`. One normalized `chat()` behind three wire formats: OpenAI `/chat/completions`, Anthropic `/v1/messages`, Gemini `generateContent`. Providers: `ark` (Volcengine → doubao/glm/kimi/deepseek/minimax), `chatgpt`, `claude`, `gemini`. Add a provider = add a wire adapter here.
- **`openai4s/store.py`** (large) — the SQLite data model, single source of truth (`openai4s.db` in the data dir). Tables: `frames` (turn tree), `execution_log` (per-cell code + usage), `artifacts`/`artifact_versions`, `compaction_archives`, `agents`, `custom_skills`, `memories`, `managed_endpoints`, `notes`, `lineage_edges`, `host_call_log`. Exposed **read-only** to the agent via `host.query`. (Note: the `config.py` docstring calling the DB "reserved, not used" is stale — the store is fully active.)
- **`openai4s/server/`** — `daemon.py` (minimal single-page UI + `/run` REST) and `gateway.py` (the real one, ~6k lines: full web UI + REST `/api/*` + WebSocket `/api/ws`, all on stdlib `http.server` + hand-rolled WebSocket). `serve` uses the gateway. It also owns **artifact capture**: after each cell it captures unsaved matplotlib figures (headless `MPLBACKEND=Agg`) and written files into versioned artifacts, and folds in remote-GPU job provenance. Data contract for the UI: execution-log entries carry `source` / `stdout` / `stderr` / `status` / `figures` / `files_written` / `files_read` / `cpu_seconds` / `peak_rss_kb`, and artifact lineage exposes cell/input `dependency_mappings`.
- **`openai4s/server/webui/`** — static frontend (`app.js`, `index.html`, `style.css`, vendored 3Dmol under `vendor/`). Served as static files **from the working tree**, so JS/CSS edits are live on reload — no build step.
- **`openai4s/security/`** — defense-in-depth, all opt-out via env but with the cheap static gates ON by default so an out-of-the-box run still refuses obvious attacks: `classifier.py` (pre-exec code-safety gate — `OPENAI4S_SAFETY` = `off` | `heuristic` (default, static scan) | `llm` (static fast-path + an LLM classifier for residual uncertain code)), `biosecurity.py` (`OPENAI4S_BIOSECURITY`), `injection.py` (screens web/pdf/mcp tool output for prompt injection, `OPENAI4S_INJECTION_SCAN`), `audit_hook.py` (the in-kernel dlopen guard, `OPENAI4S_SAFETY_AUDIT_HOOK`). Plus `permissions.py` and `egress.py` at package root — the network egress fence, `OPENAI4S_EGRESS` = `off` (fail-open, default) | `allowlist` (blocked domains return a proxy-403 soft error; the agent must call `request_network_access`), read fresh on each call so a UI toggle takes effect live. **`webtools.py`** is the *agent-facing* online layer (distinct from the egress fence): keyless `web_search` (walks DuckDuckGo → Bing → DuckDuckGo lite → Mojeek, with Crossref/arXiv scholarly fast paths) and `web_fetch` (HTML → readable markdown/text), stdlib `urllib` plus optional requests/BeautifulSoup, gated by `OPENAI4S_ALLOW_NETWORK` (default on).
- **`openai4s/compute/`** + **`openai4s_compute_provider/`** — BYOC remote GPU. `compute/` is the host-side manager/registry; `openai4s_compute_provider/` is the **stdlib-only sandboxed SDK that runs on the remote machine**. Secret scrubbing is two-staged: `__main__` runs the provider-agnostic `scrub_secret_env()` baseline **before** it imports `provider.py`, then the resident prologue re-scrubs with the loaded provider's own declared `secret_env_prefixes` before the credential is read (from stdin/fd-3) — so provider top-level code cannot read credential-shaped or known-prefix env vars (a name-based heuristic — a secret in an unrecognized variable name is NOT scrubbed), and the credential itself is never placed in the env. Provider shims that import third-party SDKs live only in `skills/remote-compute-<id>/provider.py`. `host.fold` (single-sequence Protenix/AF3-class) runs under a strict no-fabrication policy.
- **`skills/`** + **`openai4s/skills_loader/loader.py`** — 24 bundled Skills. Each is `skills/<name>/SKILL.md` (+ optional `kernel.py` sidecar), a **recipe of code, not a JSON schema**. `SKILL.md` frontmatter: `name` / `description` / `origin` (`openai4s|organization|personal|draft|unknown` — `openai4s` origin is read-only). Progressive disclosure: the system prompt lists only name + one-line summary; full docs load on demand via `host.search_skills()`; `kernel.py` sidecars are compile-checked before use.
- Other roots: `mcp_client.py` + `mcp_servers/` (MCP), `prompts.py` (system prompts), `replay.py` (trajectory replay), `pkgscan.py`, `jobs.py`, `config.py` (dataclass `Config` + zero-dep `.env` loader).

## Conventions & gotchas

- **Never add a hard third-party import to the core.** Optional science libs (numpy/pandas/matplotlib, the `science` extra) must be guarded by `try/except ImportError` at every in-tree use site. The kernel inherits whatever is in the active venv, so agent *cells* can use anything installed — but the engine itself cannot.
- **Config resolution is layered:** each of api_key / base_url / model resolves *per-provider var → generic `OPENAI4S_LLM_*` var → provider default* (e.g. `OPENAI4S_CLAUDE_API_KEY` → `OPENAI4S_LLM_API_KEY` → default). The daemon boots with no key set — the model is configured from the UI (Customize → Models) or `.env`.
- Ports/data via env: `OPENAI4S_HOST` (`127.0.0.1`), `OPENAI4S_PORT` (`8760`), `OPENAI4S_DATA_DIR` (`~/.openai4s`).
- **pre-commit excludes** `openai4s/server/webui/vendor/` (minified 3Dmol/fonts) and `tests/fixtures/` (byte-exact captured data) — never reformat those. ruff ignores `E501,F401,E722,E402,E741`.
- The daemon is a **singleton** keyed by pidfile; `openai4s serve` refuses to start if one is already running. Bind stays on `127.0.0.1` — expose via SSH tunnel, never `0.0.0.0` on an untrusted network (see `docs/security.md`).
- **Edit the large files surgically, never wholesale-rewrite them:** `gateway.py` (~6k lines), `host_dispatch.py` (~2.5k), `store.py` (~2.5k), `server/webui/app.js` (~4.6k). They pack many independent contracts; a rewrite silently drops one.
- **Don't autoclose matplotlib figures in `worker.py`** — the gateway is responsible for `savefig`-ing and closing unsaved figures after each cell so it can capture them as artifacts. Autoclosing in the worker would make figures vanish before capture.
- The `webui/` frontend is **served as static files straight from the working tree** — there is no build/bundle step, so JS/CSS edits are live on browser reload.

## Verify after changes

Tests are the floor, not the ceiling — much of what matters here is runtime behavior a unit test won't exercise.

- `uv run pytest` for the offline suite; scope kernel/engine work with `tests/test_kernel.py` / `tests/test_agent.py` and run them explicitly after protocol changes.
- For anything touching the kernel, host RPC, gateway streaming, or the web UI, **drive it end-to-end in a real browser** against a running `./start.sh` (the UI streams turns over WebSocket; behavior like figure capture, provenance, and live-Notebook kernel sharing only surfaces at runtime). A one-shot `uv run openai4s run "…" -v` is the fastest Code-as-Action smoke test without the UI.

## Docs

`docs/architecture.md` (dual loop, host API) · `docs/skills.md` · `docs/compute.md` (BYOC/`host.fold`) · `docs/webapp.md` · `docs/configuration.md` · `docs/security.md`.
