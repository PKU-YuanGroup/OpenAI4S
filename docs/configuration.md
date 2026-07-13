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

### Extending the provider catalog

Provider identity, model presets, capabilities, and wire transport are separate.
A deployment or plugin can register another provider over one of the four
shipped wires without editing the chat router:

```python
from openai4s.llm import register_model_preset, register_provider

register_provider(
    "lab_openai",
    wire="openai",
    base_url="http://127.0.0.1:11434/v1",
    model="science-model",
    tool_calling=False,
    context_window_tokens=16_384,
)
register_model_preset(
    "lab_openai",
    "science-model",
    "Local science model",
)
```

Registration is validated, process-local, and limited to the shipped
`openai`, `responses`, `anthropic`, and `gemini` adapters; it cannot load
arbitrary transport code. Use a startup plugin or deployment composition layer
to repeat registrations after restart. `provider_specs()`, `model_presets()`,
and `get_model_capabilities()` expose detached or immutable catalog views.

## Kernel environments (conda)

The agent kernel uses a scientific stack (numpy / pandas / scipy / matplotlib / scikit-learn / biopython / …) that installs automatically in the background on first `serve`. For heavier toolchains, four ready-to-use conda specs let the agent pick per task — create them with `openai4s setup` (`--dry-run` to preview, `--only <name>` for one). Specs live in [`envs/`](../envs):

- **`python`** *(default)* — scanpy / anndata / leiden / UMAP / scikit-learn / RDKit / fair-esm / pandas / matplotlib.
- **`struct`** — torch + fair-esm + biotite.
- **`phylo`** — MAFFT / IQ-TREE / FastTree / trimAl / BioPython / ete3.
- **`r`** — tidyverse.

## Ports & data

`OPENAI4S_HOST` (`127.0.0.1`) · `OPENAI4S_PORT` (`8760`) · `OPENAI4S_DATA_DIR` (`~/.openai4s`, holds the SQLite db, artifacts, logs, pidfile). See [Security](security.md) for remote / SSH-tunnel access.

`OPENAI4S_SEED_DEMO` (`1`) — set to `0` to skip the first-boot live
UniProt/RCSB demo. This is useful for CI, air-gapped deployments, or an
intentionally empty workbench; it does not affect existing sessions.

`OPENAI4S_NOTEBOOK_REPL` (`off`) — set to `1` to re-enable the web UI's in-Notebook developer REPL (arbitrary kernel code from the right panel); off by default, so the Notebook is a read-only execution trace (see [Security](security.md)).

## Optional Jupyter adapter

The daemon and KernelSpec tooling remain zero-dependency. Install the optional
wire stack only when an external Jupyter client should launch a standalone
OpenAI4S Python/R worker:

```bash
python -m pip install 'ipykernel>=7,<8'
openai4s jupyter describe
openai4s jupyter install
```

`openai4s jupyter export <directory>` writes specs without installing them;
`install --prefix <prefix>` targets `<prefix>/share/jupyter/kernels`. See
[Optional Jupyter compatibility](jupyter.md) for the independent-namespace and
Host-RPC limitations.

## CLI

```bash
openai4s init      # guided first-run model configuration (headless-friendly)
openai4s serve     # daemon + web UI (foreground)
openai4s status    # is it up?
openai4s stop      # stop the daemon
openai4s run "…"   # one Code-as-Action task in-process, no daemon
openai4s setup     # build the four conda kernel environments
openai4s jupyter describe               # inspect optional bridge availability
openai4s jupyter export ./kernel-specs  # pure-stdlib KernelSpec export
openai4s jupyter install                # install user KernelSpecs
```

`openai4s init` stores the selected provider/model/base URL in the normal
OpenAI4S settings database. Interactive API-key input is hidden; automation may
pipe one line to `openai4s init --api-key-stdin --non-interactive`. An API key
is never accepted as a command-line value or returned by `--json`, keeping it
out of shell history and structured command output.

## Platform support

The native runtime is supported on Linux and macOS. Windows users should run
the Linux package under WSL2. The current persistent-kernel transport, resource
accounting, process interruption, and OS sandbox adapters depend on Unix
primitives; installing the wheel with native Windows Python does not imply that
scientific Cell execution is supported there.
