# Configuration

Config is via env vars (all have working defaults), read from the environment or a git-ignored `.env` at the repo root. **You rarely need to touch files** — set your model from the UI (**Customize → Models**). To configure by env instead, copy `.env.example` to `.env`.

## Model providers

One `OPENAI4S_LLM_PROVIDER` selects a wire adapter; each ships a default `base_url` and `model`, so usually you only set the key. Four wire formats live behind one normalized `host.llm`: OpenAI-compatible `/chat/completions`, OpenAI `/responses`, Anthropic `/v1/messages`, and Gemini `generateContent`.

| provider | wire | default model | vision |
|---|---|---|:---:|
| `ark` | openai | `doubao-seed-2.0-pro` (+10 more via plan/v3) | ✅ |
| `chatgpt` | openai | `gpt-5` | ✅ |
| `openai_responses` | responses | `gpt-5` | — |
| `claude` | anthropic | `claude-sonnet-4-5` | ✅ |
| `gemini` | gemini | `gemini-2.5-flash` | ✅ |

`ark` is Volcengine's plan/v3 gateway — one endpoint + key serving `doubao-seed-2.0-{pro,code,lite,mini}`, `glm-5.2`, `kimi-k2.7-code`, `kimi-k2.6`, `deepseek-v4-{pro,flash}`, `minimax-{m3,m2.7}` — all pre-registered as switchable model profiles. Without a key the daemon still starts; the UI shows a *"configure your API key"* banner until you set one.

Each of api_key / base_url / model resolves **per-provider var → generic var → provider default** (e.g. `OPENAI4S_CLAUDE_API_KEY` → `OPENAI4S_LLM_API_KEY` → default). The `openai_responses` provider uses the stateless Responses API wire and preserves function-call/reasoning output items across turns; its current adapter is text/tool-only.

## Kernel environments (conda)

The agent kernel uses a scientific stack (numpy / pandas / scipy / matplotlib / scikit-learn / biopython / …) that installs automatically in the background on first `serve`. For heavier toolchains, four ready-to-use conda specs let the agent pick per task — create them with `openai4s setup` (`--dry-run` to preview, `--only <name>` for one). Specs live in [`envs/`](../envs):

- **`python`** *(default)* — scanpy / anndata / leiden / UMAP / scikit-learn / RDKit / fair-esm / pandas / matplotlib.
- **`struct`** — torch + fair-esm + biotite.
- **`phylo`** — MAFFT / IQ-TREE / FastTree / trimAl / BioPython / ete3.
- **`r`** — tidyverse.

## Ports & data

`OPENAI4S_HOST` (`127.0.0.1`) · `OPENAI4S_PORT` (`8760`) · `OPENAI4S_DATA_DIR` (`~/.openai4s`, holds the SQLite db, artifacts, logs, pidfile). See [Security](security.md) for remote / SSH-tunnel access.

`OPENAI4S_NOTEBOOK_REPL` (`off`) — set to `1` to re-enable the web UI's in-Notebook developer REPL (arbitrary kernel code from the right panel); off by default, so the Notebook is a read-only execution trace (see [Security](security.md)).

## CLI

```bash
openai4s serve     # daemon + web UI (foreground)
openai4s status    # is it up?
openai4s stop      # stop the daemon
openai4s run "…"   # one Code-as-Action task in-process, no daemon
openai4s setup     # build the four conda kernel environments
```
