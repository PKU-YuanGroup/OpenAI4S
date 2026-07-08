# Architecture — the Code-as-Action dual loop

OpenAI4S drives the model with a **dual loop**: an outer REPL *turn* loop, and an inner synchronous *host-RPC* loop that runs **inside** a single code cell.

```mermaid
flowchart TB
    UI["CLI  ·  Web UI (HTTP + WebSocket daemon)"] --> M
    subgraph outer["① OUTER LOOP · REPL turn loop"]
        direction TB
        M["Model emits prose + ONE code cell"] --> SAFE{"Pre-exec<br/>safety classifier"}
        SAFE -->|SAFE| K["Persistent kernel · subprocess<br/>namespace persists · stdout captured"]
        K --> COLLECT["Collect stdout · artifacts · rusage"]
        COLLECT -->|"continue?"| M
    end
    subgraph inner["② INNER LOOP · host RPC · synchronous, mid-cell"]
        direction TB
        H["host.web_search · web_fetch · bash · read_file<br/>host.llm · delegate · compute · fold · save_artifact"]
    end
    K <-->|"host_call → host_ack → host_response"| H
    M -.->|prompt| LLM["Multi-provider base model<br/>ark · chatgpt · claude · gemini"]
    LLM -.->|completion| M
```

- **① Outer loop** — the REPL *turn* loop: the model produces a turn (prose + one code cell), the cell is screened and executed in the persistent kernel, results/costs are collected, and the loop decides whether to continue. A task ends only when the agent calls `host.submit_output(...)`.
- **② Inner loop** — *within a single cell*, agent code can call `host.llm(...)` / `host.delegate(...)` / `host.compute(...)` any number of times. Each is a synchronous `host_call → host_ack → host_response` RPC on a channel **separate from stdout capture**, so the cell blocks, the host services the call mid-execution, and the cell resumes. **This inner RPC loop does not exist in a `tool_use` architecture** — there, actions are atomic and never call back into the host mid-execution.

## The `host` singleton

Everything the agent can do is a call on the in-kernel `host` singleton ([`openai4s/sdk/host.py`](../openai4s/sdk/host.py)):

```python
host.web_search(...)   host.web_fetch(...)   host.bash(...)          # networked tools
host.read_file / write_file / edit_file / grep / glob / list_dir     # filesystem (workspace-jailed)
host.llm(...)          host.delegate(...)    host.collect(...)       # models & sub-agents
host.compute.create(...).submit_job(...)   host.fold(...)            # remote GPU (BYOC) + folding
host.save_artifact(...) host.artifacts(...) host.view_image(...)     # versioned artifacts
host.skills.*  host.env.use(...)  host.mcp.call(...)  host.query(...) # skills, envs, MCP, read-only SQL
host.submit_output(...)                                              # the only way to end a task
```

## Key design points

- **Persistent namespace** across cells (real kernel semantics); big objects stay in kernel memory.
- **stdout/stderr captured** so `print` never corrupts the protocol wire; **per-cell linecache tags** give accurate `error_lineno`.
- **Synchronous host RPC mid-execution** — `host.llm(...)` blocks the cell, the host services it, the cell resumes.
- **`getrusage`-based accounting** (wall / cpu / peak_rss) per cell.
- **Bounded-depth delegation** — `host.delegate(...)` spawns concurrent sub-agents running the same loop (fanout cap 48, session cap 1000); children at `MAX_DEPTH` (4) become leaves that cannot re-delegate.
- **Context compaction** — older turns are summarized past a token threshold; raw slices archived to disk.

The engine is **pure Python stdlib**: the kernel is a subprocess speaking a hardened JSON-per-line protocol, the LLM client speaks OpenAI / Anthropic / Gemini wires over `urllib`, and the daemon is `http.server` + a hand-rolled WebSocket — no framework, no third-party dependency in the core.

## The hybrid ReAct tool surface

Alongside Code-as-Action, a small **ReAct tool surface** ([`openai4s/tools/`](../openai4s/tools)) exposes the deterministic operations — `list` / `read` / `glob` / `grep` / `web` / `env` / `edit` / `write` / `bash` — as structured tool calls. The model invokes one by emitting a ` ```tool ` cell carrying a JSON call instead of Python; the call routes through the **same `HostDispatcher`** as `host.*`, so it inherits the permission broker, egress fence, injection screen, and step-card machinery. These are for cheap, side-effect-light steps (look at a file, grep the tree, fetch a page). **Real computation still flows through ` ```python ` cells** — the Turing-complete kernel, its persistent namespace, and mid-cell host RPC remain the path for anything that actually computes.

## The Notebook as a read-only execution trace

The web UI's right-hand Notebook is, by default, a **read-only execution trace** of the kernel: it renders each cell the agent ran with its stdout/stderr/artifacts, but there is **no user REPL** — arbitrary in-Notebook code entry is gated behind `OPENAI4S_NOTEBOOK_REPL` (see [Security](security.md)). Runtime segments in the trace are labeled by `kernel_id`: `python` for the default env, `python — struct` / `python — phylo` etc. when the agent switches conda env, so a single session's trace shows which environment each cell ran under.

The selected conda env is **persisted per-session** in `frames.runtime_env` and **re-seeded on resume** — reopening a session restarts the kernel in the same env. Mind the persistence boundary: **workspace files persist** across a restart, but **in-memory Python variables do not** — a resumed (or restarted) kernel starts with a fresh namespace.
