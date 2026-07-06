<div align="center">

<img src="readme-gifs-hd/openai4s_penta.gif" alt="OpenAI4S · Open AI for Scientist" width="480"/>

### Open AI for Scientist

## 💸 Replicating Claude Science in two cuts or less

**An open-source, _Code-as-Action_ scientific research agent.**<br/>
<sub>The model's action space is a Turing-complete kernel — **not** a fixed tool schema.</sub>

<p>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-d97706.svg"></a>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-3fb950.svg">
  <img alt="Core deps" src="https://img.shields.io/badge/core-pure%20stdlib-58a6ff.svg">
  <img alt="Paradigm" src="https://img.shields.io/badge/paradigm-Code--as--Action-bc8cff.svg">
  <img alt="Tests" src="https://img.shields.io/badge/tests-206%20passing-3fb950.svg">
</p>
<p>
  <a href="https://github.com/PKU-YuanGroup/OpenAI4S/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/PKU-YuanGroup/OpenAI4S?style=social"></a>
  <a href="https://github.com/PKU-YuanGroup/OpenAI4S/network/members"><img alt="Forks" src="https://img.shields.io/github/forks/PKU-YuanGroup/OpenAI4S?style=social"></a>
  <a href="https://github.com/PKU-YuanGroup/OpenAI4S/issues"><img alt="Issues" src="https://img.shields.io/github/issues/PKU-YuanGroup/OpenAI4S"></a>
  <a href="https://github.com/PKU-YuanGroup/OpenAI4S/pulls"><img alt="PRs Welcome" src="https://img.shields.io/badge/PRs-welcome-3fb950.svg"></a>
</p>

**English** · [简体中文](README_zh.md)

</div>

---

## 🧬 Code-as-Action, not ReAct

Most "AI agents" are **ReAct + `tool_use`**: each step the model emits one `tool_use` JSON, the host runs that single tool, and the loop repeats — the action space is a fixed menu. **OpenAI4S** instead emits **a cell of real Python/R code** that runs in a **persistent kernel**; every "tool" is a plain function on an in-kernel `host` object. The action space is a **Turing-complete language** — one turn can loop, branch, and call libraries while big objects stay resident in kernel memory.

<table>
<tr><th></th><th>🧰 ReAct + <code>tool_use</code></th><th>🧬 Code-as-Action (OpenAI4S)</th></tr>
<tr><td align="right"><b>Action unit</b></td><td>One tool call (JSON)</td><td><b>An arbitrary program</b> (a code cell)</td></tr>
<tr><td align="right"><b>Action space</b></td><td>A fixed menu of tools</td><td><b>A Turing-complete language</b> (Python / R)</td></tr>
<tr><td align="right"><b>Loops & composition</b></td><td>Orchestrated over many round-trips</td><td>Done <b>in one cell</b> — <code>for</code>, <code>if</code>, comprehensions</td></tr>
<tr><td align="right"><b>Intermediate state</b></td><td>Lives in the model's context (text)</td><td>Lives in <b>kernel memory</b> (real objects)</td></tr>
<tr><td align="right"><b>Big objects</b></td><td>Serialized back into context</td><td>Stay resident; <b>only a summary returns</b></td></tr>
<tr><td align="right"><b>N operations</b></td><td>≈ N model round-trips</td><td><b>One round-trip</b> can do many steps</td></tr>
<tr><td align="right"><b>Extending tools</b></td><td>Change the schema + the host</td><td><code>import</code> a library, or read a Skill</td></tr>
<tr><td colspan="3">

```python
# ReAct: ~14 round-trips (read → … → filter → sort → plot).   OpenAI4S: one code cell.
hits   = [f for f in files if pattern in host.read_file(f)]
top3   = sorted(hits, key=os.path.getsize, reverse=True)[:3]
frames = [pd.read_csv(f) for f in top3]      # a 100k-row DataFrame stays in the kernel...
host.save_artifact(plot(frames))             # ...only "<DataFrame 100000×20>" hits context
```

</td></tr>
</table>

---

## 📣 News

- **`2026-07-06`** 🎉 **Open-sourced** — the pure-stdlib Code-as-Action engine, the scientific web app, 24 science Skills, and BYOC remote compute.

---

## 😮 Highlights

- **🧬 Code-as-Action engine** — a Jupyter-style **persistent kernel** *is* the action space. Namespace persists across cells; big objects stay resident, only summaries hit context.
- **🐍 Pure-stdlib core** — the engine **and** the web server are stdlib-only (`http.server` + hand-rolled WebSocket, no framework, no deps). The LLM client speaks OpenAI / Anthropic / Gemini over `urllib` alone.
- **🔌 One-line multi-provider** — `ark` (doubao · glm · kimi · deepseek · minimax) plus official `chatgpt · claude · gemini`, behind a single `host.llm`; switch from the UI.
- **🖥️ Full scientific web app** — live streaming turns, **versioned artifacts** (with a built-in 3Dmol viewer for `.pdb`/`.cif`), a live Notebook sharing the agent's kernel, background & resume.
- **🔬 24 bundled Skills** — 14 GPU/model science Skills (AlphaFold2 · ESMFold2 · Boltz · Chai-1 · OpenFold3 · ProteinMPNN · ESM-2 · Evo2 · Borzoi · scGPT · scVI · DiffDock …) + research-workflow Skills. Skills are **recipes of code**, not JSON schemas.
- **☁️ BYOC remote compute** — dispatch GPU jobs to your own machines via `ssh:<alias>` or the bundled **NVIDIA NIM** provider; real `host.fold` (single-sequence Protenix / AF3-class) under a strict no-fabrication policy.

---

## 🎬 Demo

<table>
<tr>
  <td width="50%"><b>Scientific API / MCP workflow</b><br/><img src="readme-gifs-hd/demo-01-hd.gif" alt="Scientific API / MCP workflow"></td>
  <td width="50%"><b>Visual artifact editing</b><br/><img src="readme-gifs-hd/demo-02-hd.gif" alt="Visual artifact editing"></td>
</tr>
<tr>
  <td width="50%"><b>Plan-mode scientific analysis</b><br/><img src="readme-gifs-hd/demo-03-hd.gif" alt="Plan mode scientific analysis"></td>
  <td width="50%"><b>Protein engineering</b><br/><img src="readme-gifs-hd/demo-04-hd.gif" alt="Protein engineering"></td>
</tr>
</table>

---

## ⚡ Quickstart

```bash
git clone https://github.com/PKU-YuanGroup/OpenAI4S && cd OpenAI4S
./setup.sh     # one-time: build the environment with uv
./start.sh     # launch the web UI at http://127.0.0.1:8760/
```

`setup.sh` creates the `.venv` with **uv**; `start.sh` launches the daemon + web UI from it. No API key is needed to boot — **set your model in the UI** (Customize → Models). One-shot without the UI: `uv run openai4s run "Compute the mean of [4,8,15,16,23,42] and submit it." -v`.

---

## 📚 Documentation

| doc | what's inside |
|---|---|
| [**Architecture**](docs/architecture.md) | the Code-as-Action dual loop, the `host` API, kernel design |
| [**Skills**](docs/skills.md) | the 24 bundled Skills + how to write your own |
| [**Remote compute**](docs/compute.md) | BYOC GPU jobs, `host.fold`, auto-provisioning |
| [**Web app**](docs/webapp.md) | UI features, live Notebook, artifacts, the demo session |
| [**Configuration**](docs/configuration.md) | model providers, env vars, conda envs, CLI |
| [**Security**](docs/security.md) | defense-in-depth safety layers & remote-access notes |

---

## 🗺️ Roadmap

- [ ] OS-level sandbox parity (Seatbelt / bubblewrap + seccomp) for the local kernel.
- [ ] Keyless `web_search` beyond DuckDuckGo (rate-limit resilience).
- [ ] More BYOC providers (Modal / SLURM) beyond SSH + NVIDIA NIM.
- [ ] A public benchmark of end-to-end scientific workflows.
- [ ] Local GPU model serving so structure/design Skills run without remote compute.

---

## 💡 Contributing

OpenAI4S is a community effort to keep the **Code-as-Action** paradigm open.

### Development setup

Requires **Python ≥ 3.10** and [**uv**](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/PKU-YuanGroup/OpenAI4S && cd OpenAI4S
./setup.sh                          # uv sync --extra science + pre-commit hook
uv run pytest                       # offline test suite (LLM mocked)
uv run pre-commit run --all-files   # format + lint everything
```

Style is enforced by **pre-commit** — `black`, `isort` (`--profile black`), and `ruff`, pinned in [`.pre-commit-config.yaml`](.pre-commit-config.yaml). Runtime deps: the core is **zero-dependency** (pure stdlib); the optional `science` extra pins `numpy>=1.24 · pandas>=2.0 · matplotlib>=3.7`.

### What we welcome

- **New Skills** — a `SKILL.md` (+ optional `kernel.py`) under `skills/` — recipes of code, not schemas.
- **New providers** — a wire adapter in [`openai4s/llm.py`](openai4s/llm.py), or a BYOC compute provider.
- **Engine & UI** — the core is pure stdlib and readable; the web app is framework-free.

Keep the core dependency-free, guard optional science imports behind `try/except ImportError`, and make sure `uv run pytest` and `uv run pre-commit run --all-files` pass before opening a PR.

---

## 👍 Acknowledgement & related work

- **Claude Science** (Anthropic) — the closed reference architecture whose Code-as-Action design, persistent kernel, host-RPC protocol, and safety layers OpenAI4S independently reproduces in open source.
- **CodeAct** — *"Executable Code Actions Elicit Better LLM Agents"* — code as a unified action interface.
- **ReAct** — *"Synergizing Reasoning and Acting in Language Models"* — the `tool_use` baseline this project departs from.
- The science Skills stand on **ColabFold / AlphaFold, ESM, OpenFold, Boltz, Chai, ProteinMPNN, DiffDock, Evo2, Borzoi, scGPT, scVI-tools** and open data services (NCBI, UniProt, RCSB PDB, EBI, OpenAlex, Crossref).

---

## 🔒 License

Released under the **MIT License** — see [`LICENSE`](LICENSE).

---

## ✨ Star history

<a href="https://star-history.com/#PKU-YuanGroup/OpenAI4S&Date">
  <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=PKU-YuanGroup/OpenAI4S&type=Date" width="600">
</a>

---

## ✏️ Citing

```bibtex
@software{openai4s2026,
  title  = {OpenAI4S: An Open-Source Code-as-Action Scientific Research Agent},
  author = {OpenAI4S contributors},
  year   = {2026},
  url    = {https://github.com/PKU-YuanGroup/OpenAI4S},
  note   = {Open AI for Scientist — a pure-stdlib reproduction of the Code-as-Action paradigm}
}
```

## 🤝 Community contributors

<a href="https://github.com/PKU-YuanGroup/OpenAI4S/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=PKU-YuanGroup/OpenAI4S" alt="OpenAI4S contributors" />
</a>

---

<div align="center">
<sub><b>OpenAI4S</b> · code is the action, the kernel is the environment. · <a href="README_zh.md">简体中文</a></sub>
</div>
