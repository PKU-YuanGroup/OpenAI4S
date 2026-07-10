# Harness

This directory is the versioned, stdlib-only scenario layer for scripted
providers, deterministic fault injection, normalized traces, offline contract
evals, and opt-in smoke tests. Runtime implementation remains in `openai4s/`;
the harness only drives it or supplies fakes.

The deterministic `tier:pr` scenarios are a required CI contract gate. The
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

`harness/` is the **evaluation and scenario layer**: infrastructure for
exercising the agent as a whole — end-to-end scenarios, normalized traces,
quality evals, and fake platform providers that those scenarios plug in.
Scripted contract runs are pass/fail and required. Scored quality runs may be
slower and may use external resources only when explicitly opted in.

Rule of thumb:

- A regression assertion about a specific contract belongs in `tests/`.
- A reusable fake provider, a replayable scenario, a golden trajectory, or a
  scored eval belongs in `harness/`.

## Layout

| Directory | Intended contents |
| --- | --- |
| `schema.py` | Strict JSON scenario/event contracts with an explicit schema version. |
| `runner.py` | Deterministic scripted contract runner and invariant checks. |
| `normalize.py` | UUID/time/path/port normalization that preserves event order. |
| `faults.py` | Fake clock/UUID sources and exact-occurrence fault schedules. |
| `characterize.py` | Offline-faked probes of selected production behavior before runtime migration. |
| `scenarios/` | Declarative end-to-end agent scenarios (task prompt, fixtures, expected outcome shape) runnable against a fake or live backend. |
| `providers/` | Fake/offline platform providers (compute, model endpoints, lab) implementing the same contracts as real ones, for use by scenarios and tests. |
| `golden_traces/` | Captured reference trajectories (turn/frame sequences) used for replay comparison and drift detection. |
| `evals/` | Offline eval definitions and scoring code for agent output quality. |
| `smoke/` | Minimal smoke scripts (e.g. one-shot `openai4s run` drivers) for quick manual or CI-optional verification. |

## Ground rules

- **Offline by default.** Nothing in `harness/` may require live network,
  API keys, GPUs, SSH, Docker, a browser, or lab hardware unless the entry
  point is explicitly opt-in and marked with the corresponding pytest marker
  (`external`, `network`, `live_llm`, `gpu`, `ssh`, `docker`, `browser`,
  `lab`) — the same opt-in markers registered in `pyproject.toml`.
- **No secrets.** Harness content must run without secrets by default, and
  default PR CI never provides any.
- **No production code.** Runtime implementation stays in `openai4s/` (and
  `openai4s_compute_provider/`); harness code only drives or fakes it.
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
