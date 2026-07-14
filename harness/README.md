# Harness

Chinese version: [`README_zh.md`](README_zh.md)

This directory is the versioned, stdlib-only prototype scenario layer for
scripted providers, deterministic fault injection, normalized traces, offline
contract evals, and opt-in smoke tests. The generic runner validates the
Harness schema/event/fault loop itself and deliberately does not import the
production runtime; `characterize.py` and the action-routing eval are the
current paths that exercise selected production entry points behind fakes.

The deterministic `tier:pr` scenarios are a required Harness self-contract gate. The
pytest suite also exercises the CLI gate in-process
(`tests/test_harness_contract.py`); the separate CI step keeps the contract
gate independent of pytest collection (`pyproject.toml` intentionally collects
only `tests/`). Live-model quality evals and external-resource smoke tests
remain explicit opt-ins.

## Why `harness/` exists separately from `tests/`

`tests/` is the **correctness gate**: the offline pytest suite that must pass
on every PR. It asserts current behavior of the runtime (kernel protocol,
host API, gateway serializers, security gates) with fakes and tmp data dirs.
It never needs network, secrets, GPUs, SSH, lab hardware, or a live LLM.

`harness/` is the **prototype evaluation and scenario layer**: infrastructure
for scripted-loop scenarios, normalized traces, quality evals, and fake
platform-provider data. Today the generic runner is not an end-to-end
Agent/Gateway adapter: `surface`, permissions, and fixtures are validated
scenario fields rather than executed production integrations. Scripted
self-contract runs are pass/fail and required. Scored quality runs may be
slower and may use external resources only when explicitly opted in.

Rule of thumb:

- A regression assertion about a specific contract belongs in `tests/`.
- A reusable fake provider, a replayable scenario, a golden trajectory, or a
  scored eval belongs in `harness/`.

## Direct files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Public harness facade exporting the scenario schema, loader, result, and runner. Production packages never import it. |
| [`characterize.py`](characterize.py) | Drives selected production entry points behind stdlib fakes, normalizes their observed behavior, and emits the reviewed r5 pre-change characterization—including explicitly labelled known bugs. |
| [`cli.py`](cli.py) | Implements `run` scenario selection/validation and `characterize` compare/write commands with deterministic exit codes and summaries. |
| [`faults.py`](faults.py) | Supplies fake monotonic time, stable UUIDs, exact-occurrence fault schedules, and structured injected failures. |
| [`normalize.py`](normalize.py) | Replaces volatile UUID/time/path/port values in traces while preserving event and causal order, then emits canonical bytes. |
| [`runner.py`](runner.py) | Executes the Harness's production-independent scripted loop, records canonical events, applies scheduled faults, checks Harness invariants, and returns a trace digest; it does not import or drive Agent/Gateway runtime code. |
| [`schema.py`](schema.py) | Defines and strictly validates versioned JSON contracts for scenarios, provider steps, faults, expectations, and event envelopes. |

## Direct subdirectories

| Directory | Intended contents |
| --- | --- |
| [`scenarios/`](scenarios/) | Declarative Harness scenarios: prompt, validated fixture/permission metadata, scripted provider steps, faults, tags, and expected outcomes. They are not yet end-to-end Agent/Gateway runs. |
| [`providers/`](providers/) | Fake/offline platform providers implementing test-facing equivalents of model, compute, endpoint, or lab boundaries. |
| [`golden_traces/`](golden_traces/) | Reviewed reference trajectories used for exact comparison and intentional drift review; they are data, not executable replay. |
| [`evals/`](evals/) | Offline eval fixtures and scoring code, including deterministic action-routing quality/contract evaluation. |
| [`smoke/`](smoke/) | Explicitly opt-in runtime smoke programs for platform or external-resource checks. |

## Ground rules

- **Offline by default.** Nothing in `harness/` may require live network,
  API keys, GPUs, SSH, Docker, a browser, or lab hardware unless the entry
  point is explicitly opt-in and marked with the corresponding pytest marker
  (`external`, `network`, `live_llm`, `gpu`, `ssh`, `docker`, `browser`,
  `lab`) — the same opt-in markers registered in `pyproject.toml`.
- **No secrets.** Harness content must run without secrets by default, and
  default PR CI never provides any.
- **No production code.** Runtime implementation stays in `openai4s/` (and
  `openai4s_compute_provider/`). The generic runner remains self-contained;
  explicitly named characterization/eval adapters may import selected public
  production entry points behind deterministic fakes.
- **No order laundering.** Normalization may replace volatile values, but it
  never sorts event lists. Concurrent scenarios compare explicit causal and
  per-stream relationships rather than manufacturing a total order.
- **No side-effect replay.** A golden trace is comparison data, not executable
  history. Scenario playback may call only declared fakes.
- **Core stays stdlib-only.** Harness helpers must not introduce hard
  third-party imports into the core packages.
- **Don't move tests here.** Existing `tests/` files stay where they are;
  any future relocation needs its own PR with collect-only proof that no
  test was dropped.

## Required local gate

Run both commands from the repository root before opening a PR (`harness` is
not installed into the venv; `python -m` resolves it via the working
directory):

```bash
uv run pytest
uv run python -m harness.cli run --tier pr --offline
```

The CLI exits non-zero for an invalid schema, a missing selected scenario, a
duplicate scenario id, an invariant failure, a declared fault that never
fired, or an empty tier. Golden updates are never implicit: when an
intentional runtime fix changes the r5 pre-change characterization, regenerate
its golden explicitly and review the diff —

```bash
uv run python -m harness.cli characterize          # compare against the golden
uv run python -m harness.cli characterize --write  # regenerate after review
```

## Trace assets are not interchangeable

- A **canonical run trace** is the target record for scripted
  model/action/permission/lifecycle events and deterministic contract
  comparison.
- A **host-call tape** stores successful host-call results for offline notebook
  playback. It is not a full trajectory or crash-resume record.
- A **live-model eval snapshot** measures prose/task quality and is not a
  deterministic CI truth source.

## Governance

Harness changes follow the project-owned
[harness invariants](../CONTRIBUTING.md#harness-invariants) and offline-test
policy. New behavior should be backed by deterministic scenario contracts, and
intentional golden changes must be reviewed explicitly.
