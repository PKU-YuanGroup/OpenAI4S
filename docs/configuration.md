---
title: Configuration
description: Model, kernel, network, data-directory, and CLI configuration.
outline: deep
status: current
audience: [operators, contributors, users]
verified_commit: a92e736
last_verified: 2026-07-14
owner: OpenAI4S maintainers
---

# Configuration

> Verified against repository revision `a92e736` on 2026-07-14. Provider
> defaults are convenience defaults, not an availability guarantee.

Configuration is read from environment variables or a git-ignored `.env` at
the repository root. Non-secret settings generally have usable defaults; model
API keys intentionally do not. The daemon can start without a key, but a live
provider call fails until the selected provider is configured. **You rarely
need to touch files** — set your model from the UI (**Customize → Models**). To
configure by environment instead, copy `.env.example` to `.env`.

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

`base_url` and `model` resolve **explicit value → per-provider OpenAI4S var →
generic OpenAI4S var → provider built-in default**. `api_key` resolves the
explicit value, per-provider OpenAI4S var, and generic OpenAI4S var, then tries
the provider's conventional native variable (for example `ANTHROPIC_API_KEY`);
it has no credential default. The `openai_responses` provider uses the
stateless Responses API wire and preserves function-call/reasoning output items
across turns; its current adapter is text/tool-only.

## Kernel environments (conda)

The Gateway currently attempts to install a baseline scientific stack in the
background on first `serve`; this is application startup behavior, not a core
runtime dependency. Operators should prebuild and validate the environment
instead of relying on that networked, best-effort startup path. For heavier
toolchains, four conda specs let the agent pick per task — create them with
`openai4s setup` (`--dry-run` to preview, `--only <name>` for one). Specs live
in [`envs/`](https://github.com/PKU-YuanGroup/OpenAI4S/tree/main/envs):

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
openai4s serve     # daemon + web UI (foreground)
openai4s status    # is it up?
openai4s stop      # stop the daemon
openai4s run "…"   # one Code-as-Action task in-process, no daemon
openai4s setup     # build the four conda kernel environments
openai4s jupyter describe               # inspect optional bridge availability
openai4s jupyter export ./kernel-specs  # pure-stdlib KernelSpec export
openai4s jupyter install                # install user KernelSpecs
```
