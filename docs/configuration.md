# Configuration

Config is via env vars (all have working defaults), read from the environment or a git-ignored `.env` at the repo root. **You rarely need to touch files** — set your model from the UI (**Customize → Models**). To configure by env instead, copy `.env.example` to `.env`. `OPENAI4S_SKIP_DOTENV=1` skips the `.env` loader entirely — the offline test suite sets it so a developer's real `.env` can never configure the tests.

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

The `openai` and `anthropic` wires stream token by token whenever the caller supplies a delta callback, and fall back to one blocking request if the stream fails before its first event. `responses` is always SSE; `gemini` is always blocking. `OPENAI4S_LLM_STREAM=0` (also `false`/`no`/`off`) forces the blocking path on the two that choose — worth reaching for behind a proxy that mishandles SSE, at the cost of the reply arriving as one blob per turn.

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

The core engine stays stdlib-only, and the control `.venv` carries just the optional `science` extra (numpy / pandas / matplotlib). A baseline scientific stack (scipy / seaborn / scikit-learn / biopython / httpx / …, see `CORE_PACKAGES` in [`openai4s/kernel/preinstall.py`](../openai4s/kernel/preinstall.py)) is available on top of that, but **`serve` does not install it**.

Starting the daemon never modifies your Python environment. `serve` only *reports* what is missing — on stderr and via `GET /api/kernel/packages` (`preinstall.phase == "needs_provision"`). Installing is an explicit act:

```bash
openai4s setup            # provision the kernel environments
```

or Customize → Compute → "Install package" in the UI, or `host.pip_install` from a cell.

This used to be implicit: `serve` resolved ~23 *unpinned* package names against PyPI on a background thread and installed them with `--break-system-packages`. That made booting the daemon mutate the user's interpreter, made two cold starts a week apart produce different environments, and made an offline cold start fail where nobody was looking. Diagnosis and mutation are now separate.

Heavier toolchains live in ready-to-use Conda specs instead, so native dependencies are solved for the user's own macOS or Linux platform rather than copying one machine's build strings. Create the everyday Python + R pair with `./setup.sh --with-kernel-envs` or `openai4s setup --profile standard`; use `--update` (or `./setup.sh --update-kernel-envs`) to synchronize an existing environment without pruning user-installed packages. `openai4s setup` with no profile preserves the historical behavior of creating all four environments. Use `--dry-run` to preview and `--only <name>` for one. Specs live in [`envs/`](../envs):

- **`python`** *(default)* — NumPy / pandas / SciPy / matplotlib / seaborn / Pillow / PDFium / SOCKS, plus scanpy / anndata / Leiden / UMAP / scikit-learn / RDKit / fair-esm.
- **`struct`** — torch + fair-esm + biotite.
- **`phylo`** — MAFFT / IQ-TREE / FastTree / trimAl / BioPython / ete3.
- **`r`** — R 4.5 / tidyverse / data.table / ggplot2 / R Markdown / knitr / jsonlite / Pandoc.

When `OPENAI4S_STAGE1_TRUSTED_DELIVERY=1`, startup also reports the
`standard` profile's exact local readiness. The check compares the 32 direct
Python and 8 direct R dependencies in the shipped manifests with package
metadata from the discovered `python` and `r` environments. It starts no
interpreter, imports no science package, contacts no network, and changes
nothing. A missing/incomplete environment blocks the first routed Code Cell
before a pending environment switch, safety classification, identity/attempt,
or worker exists; an unreadable inventory fails closed as unavailable. Native
control tools and sole structured finalization remain available because they
need no kernel. Approved/resumed scientific plans are checked before their
status transition, while plan drafting remains available. `openai4s run`
applies the same typed check at its first Code Cell; `serve` and `doctor` report
it without installing anything.

The persistent workbench banner and Customize → Compute card show the complete
missing-environment/package list. Remediation remains an explicit, managed
transaction:

```bash
openai4s env plan python r --repair
openai4s env apply python r --repair
```

`plan` is read-only. `apply` builds fresh generations and runs the actual
Python/R interpreter plus the complete direct-package verification before it
atomically moves either environment's `current` pointer. A build or
verification failure leaves the previous pointer unchanged. The UI only copies
these commands; it never runs them automatically.

## Ports & data

`OPENAI4S_HOST` (`127.0.0.1`) · `OPENAI4S_PORT` (`8760`) · `OPENAI4S_DATA_DIR` (`~/.openai4s`, holds the SQLite db, artifacts, logs, pidfile). See [Security](security.md) for remote / SSH-tunnel access.

`OPENAI4S_SEED_DEMO` (`0`) — set to `1` to run the bundled example analysis at
startup. **The default changed and the variable's sense reversed.** It used to
default to `1`, and on a fresh data dir that meant the daemon bound its port and
then, on a background thread, started a Python kernel, executed six cells,
called the UniProt and RCSB REST APIs, spawned the bundled MCP connector and
wrote four artifacts — before the user had typed anything. A fresh boot now does
none of that.

The example is still there; it moved behind a button. The dashboard offers *Run
the example analysis* when a session list is empty, and the button posts
`{"confirm": true}` to `POST /api/v1/example/session` (see
[Web app API](webapp-api.md)). Set the variable to `1` on a demo machine that
should come up pre-populated. Either way the seed runs at most once — the two
paths share one seeder.

`OPENAI4S_TOKEN` — the daemon access token, for CLI subcommands when the token
file under the data dir is not readable by the calling user (a daemon running
under another account). Normally unset: the CLI reads the file.

`OPENAI4S_REQUIRE_TOKEN` (`1`) — `0` turns the local access-token gate off, and
only on a loopback bind. Kept until the version named by
`gateway.LEGACY_TOKEN_OPT_OUT_REMOVED_IN`, which a test fails on rather than a
sentence nobody re-reads; see [Security](security.md) for what the daemon
exposes to an unauthenticated caller.

`OPENAI4S_NOTEBOOK_REPL` (`off`) — set to `1` to re-enable the web UI's in-Notebook developer REPL (arbitrary kernel code from the right panel); off by default, so the Notebook is a read-only execution trace (see [Security](security.md)).

## Auto Mode roadmap flags (Stage 2)

Every rollout flag defaults off. Stage 1 implements trusted delivery, Stage 2
implements the durable Auto Run/configuration/projection foundation, and
Stage 3 implements Scientific Reviewer V2 shadow recording, and Stage 4
implements the review-only completion gate, and Stage 5 implements bounded
auto-fix / re-review, and Stage 6 records Permission Guardian shadow
assessments. Stage 7–12 flags remain inert reservations and cannot grant
standing authority.

| Config field under `Config.roadmap_features` | Environment variable | Reserved behavior |
| --- | --- | --- |
| `stage1_trusted_delivery` | `OPENAI4S_STAGE1_TRUSTED_DELIVERY` | Implemented opt-in: exact immutable Artifact delivery, same-head capture observations, and standard-profile readiness admission. |
| `stage2_auto_run_storage` | `OPENAI4S_STAGE2_AUTO_RUN_STORAGE` | Implemented opt-in: durable Auto Run/review/finding/repair/permission-assessment records, canonical post-commit events, CAS selection PATCH, and REST/reopen projection. No Reviewer/Repair/Guardian execution. |
| `stage3_scientific_review_shadow` | `OPENAI4S_STAGE3_SCIENTIFIC_REVIEW_SHADOW` | Implemented opt-in: immutable Evidence Snapshot, independent V2 Reviewer, read-only scratch/adapters, and shadow recording that does not gate completion. |
| `stage4_review_completion_gate` | `OPENAI4S_STAGE4_REVIEW_COMPLETION_GATE` | Implemented opt-in: candidate stays provisional until review; pass promotes Verified, issues become completed_with_issues, failures become review_unavailable. Does not start Repair. |
| `stage5_auto_repair` | `OPENAI4S_STAGE5_AUTO_REPAIR` | Implemented opt-in: bounded Repair Agent plus independent re-review. Reviewer stays read-only; Repair cannot self-certify; identical bytes reuse the prior version. |
| `stage6_guardian_shadow` | `OPENAI4S_STAGE6_GUARDIAN_SHADOW` | Implemented opt-in: exact-action Guardian shadow assessment. It does not execute, cannot create standing allow, and fails closed on hash mismatch. |
| `stage7_guardian_enforcement` | `OPENAI4S_STAGE7_GUARDIAN_ENFORCEMENT` | Implemented opt-in: unattended ``ask`` may ``allow_once`` only for non-dangerous exact actions. Standing allow remains forbidden. |
| `stage8_live_notebook_lineage` | `OPENAI4S_STAGE8_LIVE_NOTEBOOK_LINEAGE` | Implemented opt-in: official live Notebook on the shared kernel generation, host-side Python/R read→version mapping, and automatic write lineage. |
| `stage9_artifact_workbench` | `OPENAI4S_STAGE9_ARTIFACT_WORKBENCH` | Interactive Artifact workbench and Ketcher. |
| `stage10_scientific_connectors` | `OPENAI4S_STAGE10_SCIENTIFIC_CONNECTORS` | ClinVar, literature, and trials connector product path. |
| `stage11_durable_remote_compute` | `OPENAI4S_STAGE11_DURABLE_REMOTE_COMPUTE` | Durable remote-compute submit/reconcile/cancel. |
| `stage12_auto_mode_ga` | `OPENAI4S_STAGE12_AUTO_MODE_GA` | Hardening/rollout/GA controls. |

With `OPENAI4S_STAGE1_TRUSTED_DELIVERY=1`, a final completion links only to
`/api/v1/artifacts/versions/{version_id}` generated by the server URL helper. Artifact
bytes are frozen and verified before their version is claimed; the final
assistant message and delivery manifest commit together before the
link-bearing event is emitted. Repeating the current head's SHA-256 creates a
new scoped capture observation for the producing Cell and lineage, not a fake
version. If snapshot, checksum, relation, or delivery persistence validation
fails, the turn publishes no success link. The delivery event carries a stable
`delivery_id`; REST reopen recovers its already committed message if the socket
event is lost. The delivery ledger does not drive automatic replay; the
ordinary bounded WS sequence buffer may replay it while the turn remains live,
and terminal/restart recovery uses REST. The flag does not enable result
review, repair, Guardian, live Notebook, or any later-stage surface.

With the flag unset or false, the pre-Stage-1 completion and Cell-execution
behavior is preserved. `GET /api/v1/environments/status` still includes a
`standard_profile_readiness` object, but it is the explicit
`enabled:false`/`reason:"feature_disabled"` projection and performs no
discovery.

The reserved product selections live under `Config.auto_mode`:

| Environment variable | Allowed values | Default |
| --- | --- | --- |
| `OPENAI4S_AUTO_MODE` | `0/1`, `false/true`, `no/yes`, `off/on`, `autonomous` | off |
| `OPENAI4S_RESULT_REVIEW_MODE` | `off`, `review_only`, `auto_fix` | `off` |
| `OPENAI4S_APPROVALS_REVIEWER` | `user`, `auto_review` | `user` |

These are closed vocabularies. An unknown or explicitly blank value (including
a misspelled boolean such as `flase`) rejects configuration rather than
becoming truthy. `on`/`autonomous` is one non-contradictory preset: it always
normalizes the effective sub-modes to `auto_fix` + `auto_review`. Explicit
sub-modes apply only while the preset is off.

The preset's frozen ceilings live under `Config.auto_mode.budgets`; an
environment override may tighten but never exceed them. The rolling circuit
window is fixed at 50 so changing its denominator cannot weaken the rate rule:

| Config field | Environment variable | Ceiling |
| --- | --- | ---: |
| `max_review_rounds` | `OPENAI4S_AUTO_MAX_REVIEW_ROUNDS` | 2 attempts per candidate |
| `max_repair_rounds` | `OPENAI4S_AUTO_MAX_REPAIR_ROUNDS` | 2 |
| `repair_turns_per_round` | `OPENAI4S_AUTO_REPAIR_TURNS_PER_ROUND` | 12 |
| `max_extra_cells` | `OPENAI4S_AUTO_MAX_EXTRA_CELLS` | 30 |
| `wall_time_s` | `OPENAI4S_AUTO_WALL_TIME_S` | 900 |
| `extra_token_multiplier` | `OPENAI4S_AUTO_EXTRA_TOKEN_MULTIPLIER` | 1.5 |
| `repeated_finding_limit` | `OPENAI4S_AUTO_REPEATED_FINDING_LIMIT` | 2 |
| `same_action_no_delta_limit` | `OPENAI4S_AUTO_SAME_ACTION_NO_DELTA_LIMIT` | 3 |
| `no_progress_turn_limit` | `OPENAI4S_AUTO_NO_PROGRESS_TURN_LIMIT` | 5 |
| `guardian_timeout_s` | `OPENAI4S_AUTO_GUARDIAN_TIMEOUT_S` | 90 |
| `guardian_consecutive_denial_limit` | `OPENAI4S_AUTO_GUARDIAN_CONSECUTIVE_DENIAL_LIMIT` | 3 |
| `guardian_window_size` | `OPENAI4S_AUTO_GUARDIAN_WINDOW_SIZE` | fixed 50 |
| `guardian_window_denial_limit` | `OPENAI4S_AUTO_GUARDIAN_WINDOW_DENIAL_LIMIT` | 10 |

Stage 2 durable selection precedence is import quarantine
(forces off/user) → explicit frame → explicit project → explicitly configured
deployment → legacy `review:auto:{root}` → built-in defaults. Legacy true maps
only to `review_only`; it can never enable repair or permission automation. An
unset deployment value is distinct from an explicit off for migration. Hard
sandbox, egress, biosecurity, secret/credential, cost, and deterministic
permission policy remains outside and above this precedence order. None is
attributed to Guardian. Its durable reason separately projects policy setup,
budget exhaustion, safe rollback unavailable, unknown external outcome, loop
detection, or a hard/integrity safety boundary; the subsystem name alone never
chooses the terminal label.

Auto Mode is a bounded preset, not full access. In Stage 2 it is configuration
and durable history only; selecting `autonomous` does not yet start execution.
The authoritative
budget, state, recovery, and projection meanings are in the
[Auto Mode product contract](auto-mode.md).

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
openai4s serve     # daemon + web UI (foreground; --detached to background,
                   # plus --host/--port/--no-browser; the detached parent waits
                   # up to 60s for /health, OPENAI4S_DETACHED_READY_TIMEOUT overrides)
openai4s status    # is it up?
openai4s stop      # stop the daemon
openai4s run "…"   # one Code-as-Action task in-process, no daemon
openai4s setup --profile standard          # build Python + R
openai4s setup --profile standard --update # sync Python + R, no pruning
openai4s setup                             # build all four environments
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

The native runtime is supported on Linux and macOS. The current
persistent-kernel transport, resource accounting, process interruption, and OS
sandbox adapters depend on Unix primitives; installing the wheel with native
Windows Python does not imply that scientific Cell execution is supported
there — the kernel spawn path refuses it outright.

Windows users run the Linux build under WSL2. The release ships
`OpenAI4S-<version>-windows-<arch>.zip`, which is that Linux build plus a
launcher that installs it into WSL2 and opens the Windows browser at the
forwarded port; it is not a native Windows build and does not pretend to be
one. The full matrix, and what actually ships per platform, is
[`platforms.md`](platforms.md).
