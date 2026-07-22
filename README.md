<div align="center">

<img src="readme-gifs-hd/openai4s_penta.gif" alt="OpenAI4S · Open AI for Scientist" width="480"/>

### Open AI for Scientist

## 💸 Replicating Claude Science in two cuts or less

**An open-source hybrid scientific research agent.**<br/>
<sub>JSON tools orchestrate; persistent Python/R kernels do the science.</sub>

<p>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-d97706.svg"></a>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-3fb950.svg">
  <img alt="Core deps" src="https://img.shields.io/badge/core-pure%20stdlib-58a6ff.svg">
  <img alt="Paradigm" src="https://img.shields.io/badge/paradigm-Code--as--Action-bc8cff.svg">
  <img alt="Tests" src="https://img.shields.io/badge/tests-offline%20suite-3fb950.svg">
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

> [!TIP]
> **Why "two cuts"?** No pricey frontier-model key needed — OpenAI4S runs on **Doubao (豆包)** via the cheapest **"Small" plan on Volcengine Ark (火山方舟)**: **¥9.9 / month (≈ US$1.4)**. Pick the `ark` provider in the UI and you get a Claude-Science-class agent for less than a cup of coffee.

<div align="center">
<img src="docs/ark-agent-plan-9.9.png" alt="Volcengine Ark · Agent Plan — Small tier at ¥9.9/month" width="760"/>
<br/>
<sub>Volcengine Ark · Agent Plan (Personal) — the entry <b>Small</b> tier is <b>¥9.9 / month</b>.</sub>
</div>

---

## 🧬 JSON orchestration, Code-as-Action science

OpenAI4S deliberately has two action planes. Provider-native **JSON tool
calls** handle deterministic orchestration, permissions, metadata, external
services, and human approval. **Python/R Code-as-Action** handles computation,
exploration, analysis, simulation, and long-running scientific work in
persistent kernels. Python cells can synchronously call the in-kernel `host`
API while they run; R is an independent persistent analysis channel.

This is not a choice between tools and code: each does the job it is good at.
Tool-only and conversational work can finish through the Engine-owned,
strictly structured `finalize_response` action. Scientific cells keep the
important `host.submit_output(...)` completion contract, including structured
artifacts and metrics. `host.submit_output` is the only completion signal that
can fire *inside* a Cell; a later sole `finalize_response` may still close the
Engine after earlier Cells have run.

<table>
<tr><th></th><th>JSON control plane</th><th>Python/R science plane</th></tr>
<tr><td align="right"><b>Best for</b></td><td>workflow, permissions, metadata, services</td><td>computation, analysis, simulation</td></tr>
<tr><td align="right"><b>Action unit</b></td><td>One ordered native-tool batch</td><td><b>One complete code cell</b></td></tr>
<tr><td align="right"><b>Composition</b></td><td>auditable schemas and resource policy</td><td><code>for</code>, <code>if</code>, libraries; Python also has mid-cell Host RPC</td></tr>
<tr><td align="right"><b>State</b></td><td>append-only Action Ledger</td><td>kernel memory + versioned artifacts</td></tr>
<tr><td align="right"><b>Completion</b></td><td>Engine-owned <code>finalize_response</code></td><td>Python: <code>host.submit_output(...)</code>; R: no in-cell completion</td></tr>
<tr><td align="right"><b>Extending</b></td><td>named <code>Tool</code> subclass</td><td>import a library or load a Skill</td></tr>
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

- **`2026-07-15`** 🍎 **`v0.1.0` — macOS app** — a one-click, no-toolchain Apple Silicon `.dmg` with an embedded Python and the full default kernel science stack (rdkit · scanpy · the single-cell stack), plus PyPI packaging (`pip install openai4s`) and release automation. **New here? → [Startup guide](docs/startup-guide.md).**
- **`2026-07-06`** 🎉 **Open-sourced** — the pure-stdlib Code-as-Action engine, the scientific web app, 24 science Skills, and BYOC remote compute.

---

## 😮 Highlights

- **🧬 Hybrid action engine** — class-based native JSON tools orchestrate while persistent Python/R kernels execute science. CLI and Web adapters start foreground language slots lazily, so tool/finalize routing itself does not spawn one; individual tools may still manage dedicated workers.
- **📒 Ledger-first runtime** — action groups/events and terminal facts are append-only; execution attempts, generation lifecycle, usage, and completion records remain durable and reconstructable.
- **🐍 Pure-stdlib core** — the engine **and** the web server are stdlib-only (`http.server` + hand-rolled WebSocket, no framework, no deps). The LLM client speaks OpenAI / Anthropic / Gemini over `urllib` alone.
- **🔌 One-line multi-provider** — `ark` (doubao · glm · kimi · deepseek · minimax) plus official `chatgpt · claude · gemini`, behind a single `host.llm`; switch from the UI.
- **🖥️ Scientific workbench** — live streaming, versioned artifacts, provenance, an Action Timeline surface, and a **read-only-by-default Notebook**. An explicit developer flag enables multiline Python/R input against the shared kernels.
- **🔐 Hardened local execution** — strict child-environment allowlists, durable approvals, one-shot generation-bound `host.bash` capabilities, and OS sandbox adapters (Seatbelt on macOS, bubblewrap on Linux) with visible degraded/fail-closed modes.
- **🔬 34 bundled Skills** — GPU/model science Skills (AlphaFold2 · ESMFold2 · Boltz · Chai-1 · OpenFold3 · ProteinMPNN · ESM-2 · Evo2 · Borzoi · scGPT · scVI · DiffDock …) + research-workflow Skills. Skills are **recipes of code**, not JSON schemas; user-authored Skills stay under the data directory and cannot shadow bundled trust.
- **☁️ BYOC remote compute** — with a configured, reachable provider, dispatch GPU jobs via `ssh:<alias>` or the bundled **NVIDIA NIM** integration. General remote compute remains a Prototype surface; `host.fold` uses a strict no-fabrication policy.

---

## 🎬 Demo

<table>
<tr>
  <td width="50%"><b>Live API workflow</b> — from UniProt / RCSB to a 3D structure &amp; report<br/><img src="readme-gifs-hd/demo-01-hd.gif" alt="Live API workflow: from UniProt / RCSB to a 3D structure and report"></td>
  <td width="50%"><b>Real-data analysis</b> — human insulin INS (P01308): from UniProt / RCSB to a reproducible report<br/><img src="readme-gifs-hd/demo-05-hd.gif" alt="Real-data analysis: human insulin INS / UniProt P01308 from UniProt / RCSB to a reproducible report"></td>
</tr>
<tr>
  <td width="50%"><b>Visual artifact editing</b> — “raise the confidence cutoff to 75” in one line<br/><img src="readme-gifs-hd/demo-02-hd.gif" alt="Visual artifact editing: raise the confidence cutoff to 75 in one line"></td>
  <td width="50%"><b>Annotation-driven chart editing</b> — lasso a region &amp; recolor the legend<br/><img src="readme-gifs-hd/demo-06-hd.gif" alt="Annotation-driven chart editing: lasso a region and recolor the legend"></td>
</tr>
<tr>
  <td width="50%"><b>Plan-mode research</b> — artemisinin &amp; paclitaxel solubility prediction<br/><img src="readme-gifs-hd/demo-03-hd.gif" alt="Plan-mode research: artemisinin and paclitaxel solubility prediction"></td>
  <td width="50%"><b>Protein engineering</b> — from sequence to ranked mutants &amp; structural rationale<br/><img src="readme-gifs-hd/demo-04-hd.gif" alt="Protein engineering: from sequence to ranked mutants and structural rationale"></td>
</tr>
</table>

---

## ⚡ Quickstart

```bash
git clone https://github.com/PKU-YuanGroup/OpenAI4S && cd OpenAI4S
./setup.sh     # one-time: build the environment with uv
./start.sh     # launch the web UI at http://127.0.0.1:8760/
```

`setup.sh` creates the lightweight control `.venv` with **uv**. For the comprehensive Python + R scientific kernels, install a Conda-family manager (`micromamba`, `mamba`, or `conda`) and run `./setup.sh --with-kernel-envs` instead. Existing kernel environments can be synchronized with `./setup.sh --update-kernel-envs`; updates do not prune user-installed packages. `start.sh` launches the daemon + web UI. No API key is needed to boot — **set your model in the UI** (Customize → Models). One-shot without the UI: `uv run openai4s run "Compute the mean of [4,8,15,16,23,42] and submit it." -v`.

### macOS app (no toolchain required)

Apple Silicon users can skip the checkout entirely: download `OpenAI4S-<version>-macos-arm64.dmg` from the [latest release](https://github.com/PKU-YuanGroup/OpenAI4S/releases/latest), drag it to Applications, and launch. The image embeds its own Python plus the default kernel science stack — numpy · pandas · scipy · matplotlib · scikit-learn · **rdkit** (cheminformatics) · **scanpy** and the single-cell stack · umap · numba · biopython — so the first launch needs no network and no `pip`. Data lives in `~/.openai4s`.

The build is ad-hoc signed but **not notarized**, so Gatekeeper refuses it the first time. On **macOS 15+**, open it once, then allow it under System Settings → Privacy & Security → **Open Anyway**; on **macOS 12–14**, right-click the app → **Open** → **Open**. Either way, `xattr -dr com.apple.quarantine /Applications/OpenAI4S.app` also clears it.

**First run — point it at a model, then at search.** Launching the app opens the workbench at `http://127.0.0.1:8760/`. No key ships, so:

1. **Model API** — open **Settings ⚙ → Models**, pick a protocol (**Ark-compatible** for Doubao/GLM/Kimi/DeepSeek/MiniMax, or **OpenAI-** / **Anthropic-compatible**), paste your **API Key**, click **Add**, then **Set active**. Cheapest path: the `ark` protocol on Volcengine Ark's ¥9.9/mo plan.
2. **Search API** *(optional, recommended)* — open **Settings ⚙ → Network**, keep **Allow network access** on, register at **[tavily.com](https://tavily.com)**, and paste the key into **Search API key (Tavily)** → **Save**. Without a key, web search still falls back to keyless scrapers.

Full walkthrough (install → Gatekeeper → model → search → R kernel): **[Startup guide](docs/startup-guide.md)**.

The CLI ships inside the app — symlink it if you want it on your PATH:

```bash
sudo ln -sf /Applications/OpenAI4S.app/Contents/Resources/runtime/bin/openai4s /usr/local/bin/openai4s
openai4s setup        # only if you want the R kernel: needs micromamba/mamba/conda
```

The R kernel is not bundled (it needs a conda environment). On Intel Macs and Linux, install from PyPI (`pip install openai4s`) instead.

---

## 📚 Documentation

The canonical bilingual documentation is published at **[openai4s.org/docs](https://openai4s.org/docs/)**. Its public source and issue tracker live in [Nobody-Zhang/openai4s-docs](https://github.com/Nobody-Zhang/openai4s-docs); the links below point to the code-adjacent copies kept with this repository.

| doc | what's inside |
|---|---|
| [**Startup guide**](docs/startup-guide.md) | macOS `.dmg` walkthrough: install, Gatekeeper, and configuring the model + Tavily search keys |
| [**Architecture**](docs/architecture.md) | the hybrid action router, Action Ledger, `host` RPC, and lazy kernels |
| [**Backend extension guide**](docs/backend-extension-guide.md) | where new Tool classes, host services, repositories, and session behaviour belong |
| [**Skills**](docs/skills.md) | the 34 bundled Skills + how to write your own |
| [**Remote compute**](docs/compute.md) | BYOC GPU jobs, `host.fold`, auto-provisioning |
| [**Web app**](docs/webapp.md) | UI features, Action Timeline, read-only Notebook, artifacts, and implementation status |
| [**Jupyter adapter**](docs/jupyter.md) | optional standalone Python/R KernelSpecs, install commands, and compatibility limits |
| [**Configuration**](docs/configuration.md) | model providers, env vars, conda envs, CLI |
| [**Security**](docs/security.md) | defense-in-depth safety layers & remote-access notes |

---

## 🗺️ Roadmap

- [x] Ship the next-generation workbench foundation: branch activation and
  append-only Revert/Undo projections, verified recovery with explicit
  Partial/Failed state, dependency-level stale propagation, durable delegation,
  quarantined portable Session packages, checkpointed plan/review/memory state,
  and dedicated 2D chemistry/genome/sequence/MSA/LaTeX renderers. Arbitrary
  in-memory namespace objects are deliberately not serialized; recovery remains
  Partial unless a safe recipe can rebuild and verify them, and Fork is offered
  only on records that carry a proven checkpoint mapping, so older history
  returns 409.
- [ ] Add stronger Linux isolation beyond bubblewrap where available (for example seccomp) and expand packaged sandbox smoke coverage.
- [ ] Keyless `web_search` beyond DuckDuckGo (rate-limit resilience).
- [ ] More BYOC providers (Modal / SLURM) beyond SSH + NVIDIA NIM.
- [ ] A public benchmark of end-to-end scientific workflows.
- [ ] Local GPU model serving so structure/design Skills run without remote compute.

---

## 💡 Contributing

OpenAI4S is a community effort to keep the **Code-as-Action** paradigm open.

Before opening a PR, please read [`CONTRIBUTING.md`](CONTRIBUTING.md) — it defines branch naming, the PR checklist ([`.github/pull_request_template.md`](.github/pull_request_template.md)), code ownership ([`.github/CODEOWNERS`](.github/CODEOWNERS)), review & release policy, and the offline-test policy.

### Development setup

Requires **Python ≥ 3.10** and [**uv**](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/PKU-YuanGroup/OpenAI4S && cd OpenAI4S
./setup.sh                          # uv sync --locked --extra science + pre-commit hook
./setup.sh --with-kernel-envs       # optional: full Python + R kernel stacks
uv run pytest                       # offline test suite (LLM mocked)
uv run pre-commit run --all-files   # format + lint everything
```

Style is enforced by **pre-commit** — `black`, `isort` (`--profile black`), and `ruff`, pinned in [`.pre-commit-config.yaml`](.pre-commit-config.yaml). Runtime deps: the core is **zero-dependency** (pure stdlib); the optional `science` extra pins `numpy>=1.24 · pandas>=2.0 · matplotlib>=3.7`.

### What we welcome

- **New Skills** — a `SKILL.md` (+ optional `kernel.py`) under `skills/` — recipes of code, not schemas.
- **New providers** — a wire adapter under [`openai4s/llm/providers/`](openai4s/llm/providers/) plus its provider definition and registry entry, or a BYOC compute provider.
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

<!-- CONTRIBUTORS:START -->
<a href="https://github.com/Nobody-Zhang" title="Nobody-Zhang"><img src=".github/contributors/Nobody-Zhang.png" width="64" height="64" alt="Nobody-Zhang" /></a>
<a href="https://github.com/HowardLi1984" title="HowardLi1984"><img src=".github/contributors/HowardLi1984.png" width="64" height="64" alt="HowardLi1984" /></a>
<a href="https://github.com/YuyangSunshine" title="YuyangSunshine"><img src=".github/contributors/YuyangSunshine.png" width="64" height="64" alt="YuyangSunshine" /></a>
<a href="https://github.com/wangyu-sd" title="wangyu-sd"><img src=".github/contributors/wangyu-sd.png" width="64" height="64" alt="wangyu-sd" /></a>
<a href="https://github.com/Lyu6PosHao" title="Lyu6PosHao"><img src=".github/contributors/Lyu6PosHao.png" width="64" height="64" alt="Lyu6PosHao" /></a>
<a href="https://github.com/Grace-xyx" title="Grace-xyx"><img src=".github/contributors/Grace-xyx.png" width="64" height="64" alt="Grace-xyx" /></a>
<a href="https://github.com/Linmj-Judy" title="Linmj-Judy"><img src=".github/contributors/Linmj-Judy.png" width="64" height="64" alt="Linmj-Judy" /></a>
<a href="https://github.com/yusowa0716" title="yusowa0716"><img src=".github/contributors/yusowa0716.png" width="64" height="64" alt="yusowa0716" /></a>
<a href="https://github.com/riiiiiiin" title="riiiiiiin"><img src=".github/contributors/riiiiiiin.png" width="64" height="64" alt="riiiiiiin" /></a>
<a href="https://github.com/jiangzx25" title="jiangzx25"><img src=".github/contributors/jiangzx25.png" width="64" height="64" alt="jiangzx25" /></a>
<!-- CONTRIBUTORS:END -->

<sub>Auto-generated daily from the GitHub <a href="https://github.com/PKU-YuanGroup/OpenAI4S/graphs/contributors">contributors graph</a> by <code>scripts/update_contributors.py</code>.</sub>

---

<div align="center">
<sub><b>OpenAI4S</b> · code is the action, the kernel is the environment. · <a href="README_zh.md">简体中文 </a> · Friend Link https://linux.do </sub>
</div>
