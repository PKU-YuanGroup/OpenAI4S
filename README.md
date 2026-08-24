<div align="center">

<img src="readme-gifs-hd/openai4s_penta.gif" alt="OpenAI4S · Open AI for Scientist" width="480"/>

### Open AI for Scientist

## 💸 Replicating Claude Science in two cuts or less

**An open-source hybrid scientific research agent.**<br/>
<sub>JSON tools orchestrate; persistent Python/R kernels do the science.</sub>

**Launched by the Peking University–YuanKong Intelligence AI Joint Research Laboratory.**<br/>
<sub>由北京大学—元空AI联合实验室推出。</sub>

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

- **`2026-08-04`** 🔭 **`main` — on the way to `v0.2.0`** — **read-only session sharing** over an outbound relay tunnel (`openai4s share` / `openai4s relay`), **seven normalized public-database connectors** that carry where a record came from and when, a versioned **`/api/v1`** surface (keyset pagination, one error envelope, a resumable WebSocket cursor), **environments as a transaction** (`openai4s env plan|apply|rollback`), a redacted `doctor` / `diagnostics` support bundle, consent-gated revocable telemetry, a retrosynthesis-planning Skill, and a **10-workflow / 20-case benchmark** that runs against the real Store, kernels, and dispatcher. Linux and Windows desktop packages are built and tested — **they ship with the next release**.
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
- **🔬 603 bundled Skills** — 42 curated OpenAI4S recipes for GPU/model science, research workflows, and platform operations, plus all 561 recipes from the pinned MIT-licensed GPTomics/bioSkills collection. Skills are **recipes of code**, not JSON schemas; the large third-party collection is searched on demand and occupies only one always-on prompt line. User-authored Skills stay under the data directory and cannot shadow bundled trust.
- **☁️ BYOC remote compute** — with a configured, reachable provider, dispatch GPU jobs via `ssh:<alias>` or the bundled **NVIDIA NIM** integration. General remote compute remains a Prototype surface; `host.fold` uses a strict no-fabrication policy.
- **🔗 Read-only session sharing** — publish a session as a snapshot anyone with the link can view and import, through a relay **you** run. The daemon never binds a public port; it dials out. Memories, permission state, and keys never leave, and residual secrets fail the publish closed. → [Web sharing](docs/webshare.md)
- **🔎 Source-attributed retrieval** — seven normalized public-database connectors (UniProt · RCSB PDB · Ensembl · ChEMBL · PubChem · arXiv · OpenAlex). Retrieved records carry where they came from and when, without the API key that fetched them.
- **🧰 Operable, not just runnable** — a versioned `/api/v1` (keyset pagination, one error envelope, correlation IDs, a resumable WebSocket cursor), a local credential required at startup, a redacted `doctor` / `diagnostics` support bundle, and consent-gated telemetry that is off by default and destroys its identity when revoked.

---

## 📦 What ships today

A capability map of the current tree — what is implemented and reachable, plane by plane.

| plane | what's implemented |
|---|---|
| **Control & orchestration** | class-based native `Tool`s · append-only Action Ledger · plan/review with a durable state machine · context compaction that archives the raw slices it summarizes · concurrent sub-agent delegation (fanout 48, depth 4) a user can stop mid-flight · enforced Specialist allowlists a child cannot widen · MCP connectors · cross-session memory |
| **Scientific execution** | persistent Python **and** R kernels · synchronous mid-cell `host` RPC · object-level data lineage · versioned artifacts · environment provenance recorded per kernel *generation*, never borrowed from the daemon · background execution · 603 Skills (42 curated + 561 pinned bioSkills) · a FIFO execution coordinator with ABA-safe watchdog recovery |
| **Data & retrieval** | seven normalized public-database connectors (UniProt · RCSB PDB · Ensembl · ChEMBL · PubChem · arXiv · OpenAlex) whose records carry source and time · a nightly canary over three of them · Agent-Plan-keyed **Doubao Search Custom** as the primary web search · Tavily and keyless search as backups · managed DataPro professional-dataset search |
| **Workbench** | live streaming · Action Timeline · read-only-by-default Notebook · branch fork/activate/revert · verified recovery with an explicit Partial/Failed state · `@file` references pinned to the version they name · 2D chemistry/genome/sequence/MSA/LaTeX renderers · Markdown and `.ipynb` export |
| **Sharing & portability** | read-only session shares over an outbound relay you operate · quarantined portable Session packages · an optional Jupyter KernelSpec bridge onto the same kernels |
| **Ops, safety & release** | `/api/v1` and a startup credential · Seatbelt/bubblewrap sandbox adapters with visible degraded and fail-closed modes · durable approvals that deny by default when unattended · redacted diagnostics · revocable telemetry · environments as a transaction · a 10-workflow/20-case benchmark against the real Store, kernels, and dispatcher · a staged release pipeline that verifies artifacts before anything becomes public |

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
2. **Search API** *(optional, recommended)* — open **Settings ⚙ → Network**, keep **Allow network access** on, and paste your Ark **Agent Plan Key** into the primary **Doubao Search Custom** card → **Save credential**. If the active Ark model already uses that key, OpenAI4S reuses it automatically. Tavily and keyless engines remain backup options; the dedicated Doubao health check never reports a fallback result as Doubao.

Full walkthrough (install → Gatekeeper → model → search → R kernel): **[Startup guide](docs/startup-guide.md)**.

The CLI ships inside the app — symlink it if you want it on your PATH:

```bash
sudo ln -sf /Applications/OpenAI4S.app/Contents/Resources/runtime/bin/openai4s /usr/local/bin/openai4s
openai4s setup        # only if you want the R kernel: needs micromamba/mamba/conda
```

The R kernel is not bundled (it needs a conda environment). On Intel Macs, install from PyPI (`pip install openai4s`) instead.

### Linux app (no toolchain required)

> [!NOTE]
> **The Linux and Windows packages ship with the next release.** They are built and tested on `main`, but the published `v0.1.0` carries the macOS image only. Until then, use the source checkout above or `pip install openai4s`. The two sections below describe those packages as they will be published.

Download `OpenAI4S-<version>-linux-x86_64.tar.gz` from the [latest release](https://github.com/PKU-YuanGroup/OpenAI4S/releases/latest), unpack it anywhere, and run it. Same embedded Python and same bundled science stack as the macOS image, as a relocatable directory:

```bash
tar -xzf OpenAI4S-*-linux-x86_64.tar.gz && cd OpenAI4S-*-linux-x86_64
./OpenAI4S          # starts the daemon and opens http://127.0.0.1:8760/
./install.sh        # optional: `openai4s` on your PATH + an application-menu entry
```

`install.sh` is per-user and needs no root — it only writes into `$HOME`, and `./uninstall.sh` undoes it while leaving your data in `~/.openai4s` alone. Install `bubblewrap` (`apt install bubblewrap`) so cells run sandboxed; without it the default `OPENAI4S_KERNEL_SANDBOX=auto` reports a visibly degraded, unisolated kernel. Only `x86_64` is published — on arm64 Linux, install from PyPI (`pip install openai4s`).

### Windows (via WSL2)

Download `OpenAI4S-<version>-windows-x86_64.zip`, unzip it, and double-click `OpenAI4S.cmd`. The first run checks WSL2 and a working bubblewrap 0.8.0+ sandbox, verifies and installs the bundled Linux payload, creates `~/.local/bin/openai4s`, starts the daemon there, and opens an authenticated local URL in your Windows browser. No application download, no `pip`, no toolchain. Ubuntu 24.04 is the supported baseline; mainland PyPI/Conda mirrors and an optional WSL-reachable proxy can be configured by the launcher. See the bilingual [Windows/WSL2 guide](docs/windows-wsl.md).

**Native Windows is not supported, and the program refuses to start a kernel there** rather than warning and proceeding — it spawns POSIX subprocesses, the R channel rides file descriptors 3 and 4 through a shell redirection, and the sandbox has no Windows backend. WSL2 reports as Linux, so this package runs the same build every other platform runs. If you do not have WSL2 yet, the launcher stops and tells you the exact command (`wsl --install`, from an Administrator PowerShell). Details: **[Supported platforms](docs/platforms.md)**.

### 🐳 Docker and Kubernetes

```bash
docker compose up -d --build          # http://127.0.0.1:8760/
docker compose exec openai4s openai4s url   # the URL, token included
```

The image is built from this tree — Debian-slim CPython, the wheel, and the `science` extra — and runs as an unprivileged user with one volume at `/data`. Supply the model key as `OPENAI4S_SECRET_LLM_LLM_API_KEY` (a `Secret` in the cluster); the image reads credentials from the environment and writes nothing credential-shaped to the volume. For a cluster, `kubectl apply -f deploy/kubernetes.yaml` gives a single-replica Deployment, a `ReadWriteOnce` claim and a ClusterIP Service, with probes on `/health`.

No image is published yet: build it from the checkout. Two things are worth knowing before you expose it. Binding `0.0.0.0` inside the container makes the access token mandatory and switches the DNS-rebind `Host` allowlist off, so the token becomes the only control in front of endpoints that execute code — which is why the compose file publishes to loopback and the Service is a `ClusterIP`. And an unprivileged container cannot give bubblewrap the namespaces it needs, so the kernel sandbox degrades visibly and the container becomes the boundary; that is a coarser one, and **[the container guide](docs/docker.md)** says exactly what it stops covering.

---

## 📚 Documentation

The canonical bilingual documentation is published at **[openai4s.org/docs](https://openai4s.org/docs/)**. Its public source and issue tracker live in [Nobody-Zhang/openai4s-docs](https://github.com/Nobody-Zhang/openai4s-docs); the links below point to the code-adjacent copies kept with this repository.

| doc | what's inside |
|---|---|
| [**Startup guide**](docs/startup-guide.md) | macOS `.dmg` walkthrough: install, Gatekeeper, model setup, and one-key Doubao Search authorization (with Tavily/keyless backups) |
| [**Architecture**](docs/architecture.md) | the hybrid action router, Action Ledger, `host` RPC, and lazy kernels |
| [**Backend extension guide**](docs/backend-extension-guide.md) | where new Tool classes, host services, repositories, and session behaviour belong |
| [**Model backend bring-up**](docs/model-backend-bringup.md) | local/remote GPU selection, checkpoint staging, real-inference canary admission, and connector portability |
| [**Skills**](docs/skills.md) | 42 curated Skills + 561 pinned bioSkills + how to write your own |
| [**Remote compute**](docs/compute.md) | BYOC GPU jobs, `host.fold`, auto-provisioning |
| [**Science connectors**](docs/science-connectors.md) | the seven public databases, their filters, and retrieval provenance |
| [**Web app**](docs/webapp.md) | UI features, Action Timeline, read-only Notebook, artifacts, and implementation status |
| [**Web sharing**](docs/webshare.md) | read-only session shares, the trust model, and running your own relay |
| [**Jupyter adapter**](docs/jupyter.md) | optional standalone Python/R KernelSpecs, install commands, and compatibility limits |
| [**Configuration**](docs/configuration.md) | model providers, env vars, conda envs, CLI |
| [**Docker / Kubernetes**](docs/docker.md) | the image, `compose.yaml`, the cluster manifests, and what a wildcard bind actually changes |
| [**Supported platforms**](docs/platforms.md) | the per-OS support tiers and why native Windows refuses to start a kernel |
| [**Windows / WSL2**](docs/windows-wsl.md) | Ubuntu 24.04 installation, sandbox checks, lifecycle commands, mainland mirrors, and localhost proxy behavior |
| [**Security**](docs/security.md) | defense-in-depth safety layers & remote-access notes |

---

## 🗺️ Roadmap

### Delivered

- [x] Ship the next-generation workbench foundation: branch activation and
  append-only Revert/Undo projections, verified recovery with explicit
  Partial/Failed state, dependency-level stale propagation, durable delegation,
  quarantined portable Session packages, checkpointed plan/review/memory state,
  and dedicated 2D chemistry/genome/sequence/MSA/LaTeX renderers. Arbitrary
  in-memory namespace objects are deliberately not serialized; recovery remains
  Partial unless a safe recipe can rebuild and verify them, and Fork is offered
  only on records that carry a proven checkpoint mapping, so older history
  returns 409.
- [x] Read-only session sharing over an outbound relay you operate, with the
  daemon never binding a public port and residual secrets failing the publish
  closed.
- [x] An **executable** benchmark of end-to-end scientific workflows — 10
  workflows / 20 cases run against the real Store, kernel managers, host
  dispatcher, and compute manager, where a declared `failure` /
  `permission_denied` / `recovered` / `provenance` outcome fails when the run
  *succeeds*. Publishing comparable public results is still ahead.
- [x] Environments as a transaction (`openai4s env plan|apply|rollback`): a
  generation is built fresh, verified, and only then pointed at atomically, so
  an artifact's provenance can name an immutable one.

### Next

- [ ] **Publish the Windows and Linux desktop packages** next to the macOS image, so every supported platform installs without a toolchain.
- [ ] **NVIDIA scientific computing suites** — bring **BioNeMo** (biomolecular foundation models) and **Parabricks** (GPU-accelerated genomics pipelines) in as first-class Skills and BYOC backends, beyond today's NVIDIA NIM integration.
- [ ] Local GPU model serving so structure/design Skills run without remote compute.
- [ ] More BYOC providers (Modal / SLURM) beyond SSH + NVIDIA NIM.
- [ ] Stronger Linux isolation beyond bubblewrap where available (for example seccomp), and wider packaged sandbox smoke coverage.
- [ ] Keyless `web_search` beyond DuckDuckGo (rate-limit resilience).

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

## ✨ Star History

<a href="https://www.star-history.com/?repos=PKU-YuanGroup%2FOpenAI4S&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=PKU-YuanGroup/OpenAI4S&type=date&theme=dark&legend=top-left&sealed_token=s8MqiKCKQtHsKQ-3BcacqDuESDZqB_cdJtFhGm3vgR1iD_oTvZ4LKSqaYvbdn1M-HgWpPdZC5jjSY3VN09poKGhisKf1sIPJ9q--mhjVpEpOcLNBwIUz_w" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=PKU-YuanGroup/OpenAI4S&type=date&legend=top-left&sealed_token=s8MqiKCKQtHsKQ-3BcacqDuESDZqB_cdJtFhGm3vgR1iD_oTvZ4LKSqaYvbdn1M-HgWpPdZC5jjSY3VN09poKGhisKf1sIPJ9q--mhjVpEpOcLNBwIUz_w" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=PKU-YuanGroup/OpenAI4S&type=date&legend=top-left&sealed_token=s8MqiKCKQtHsKQ-3BcacqDuESDZqB_cdJtFhGm3vgR1iD_oTvZ4LKSqaYvbdn1M-HgWpPdZC5jjSY3VN09poKGhisKf1sIPJ9q--mhjVpEpOcLNBwIUz_w" />
 </picture>
</a>

---

## ✏️ Citing

```bibtex
@software{openai4s2026,
  title        = {OpenAI4S: An Open-Source Code-as-Action Scientific Research Agent},
  author       = {OpenAI4S contributors},
  organization = {Peking University Shenzhen Graduate School--YuanKong Intelligence AI Agent Joint Research Laboratory},
  year         = {2026},
  url          = {https://github.com/PKU-YuanGroup/OpenAI4S},
  note         = {Open AI for Scientist — a pure-stdlib reproduction of the Code-as-Action paradigm}
}
```

## 🤝 Community contributors

<!-- CONTRIBUTORS:START -->
<a href="https://github.com/Nobody-Zhang" title="Nobody-Zhang"><img src=".github/contributors/Nobody-Zhang.png" width="64" height="64" alt="Nobody-Zhang" /></a>
<a href="https://github.com/wangyu-sd" title="wangyu-sd"><img src=".github/contributors/wangyu-sd.png" width="64" height="64" alt="wangyu-sd" /></a>
<a href="https://github.com/HowardLi1984" title="HowardLi1984"><img src=".github/contributors/HowardLi1984.png" width="64" height="64" alt="HowardLi1984" /></a>
<a href="https://github.com/YuyangSunshine" title="YuyangSunshine"><img src=".github/contributors/YuyangSunshine.png" width="64" height="64" alt="YuyangSunshine" /></a>
<a href="https://github.com/Lyu6PosHao" title="Lyu6PosHao"><img src=".github/contributors/Lyu6PosHao.png" width="64" height="64" alt="Lyu6PosHao" /></a>
<a href="https://github.com/Grace-xyx" title="Grace-xyx"><img src=".github/contributors/Grace-xyx.png" width="64" height="64" alt="Grace-xyx" /></a>
<a href="https://github.com/ClarenceYC" title="ClarenceYC"><img src=".github/contributors/ClarenceYC.png" width="64" height="64" alt="ClarenceYC" /></a>
<a href="https://github.com/Linmj-Judy" title="Linmj-Judy"><img src=".github/contributors/Linmj-Judy.png" width="64" height="64" alt="Linmj-Judy" /></a>
<a href="https://github.com/yusowa0716" title="yusowa0716"><img src=".github/contributors/yusowa0716.png" width="64" height="64" alt="yusowa0716" /></a>
<a href="https://github.com/riiiiiiin" title="riiiiiiin"><img src=".github/contributors/riiiiiiin.png" width="64" height="64" alt="riiiiiiin" /></a>
<a href="https://github.com/jiangzx25" title="jiangzx25"><img src=".github/contributors/jiangzx25.png" width="64" height="64" alt="jiangzx25" /></a>
<a href="https://github.com/stau-7001" title="stau-7001"><img src=".github/contributors/stau-7001.png" width="64" height="64" alt="stau-7001" /></a>
<a href="https://github.com/EQSTLab" title="EQSTLab"><img src=".github/contributors/EQSTLab.png" width="64" height="64" alt="EQSTLab" /></a>
<!-- CONTRIBUTORS:END -->

<sub>Auto-generated daily from the GitHub <a href="https://github.com/PKU-YuanGroup/OpenAI4S/graphs/contributors">contributors graph</a> and a maintained public-recognition list by <code>scripts/update_contributors.py</code>.</sub>

---

<div align="center">
<sub><b>OpenAI4S</b> · code is the action, the kernel is the environment. · <a href="README_zh.md">简体中文 </a> · Friend Link https://linux.do </sub>
</div>
