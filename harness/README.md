# Harness

[中文说明](README_zh.md)

The Harness replays scenarios. A scenario scripts what the model would have
said, injects faults at named points, records the run as a normalized event
trace, and checks that trace against the outcome the scenario declared. That
covers what a unit test is awkward for: the order things happened in, how many
model attempts a run took, and whether a failure on the third visit to a point
lands differently from a failure on the first.

Everything here is versioned, stdlib-only, and outside the production import
graph. The generic runner validates the Harness's own schema/event/fault loop
and deliberately does not import the production runtime; `characterize.py` and
the action-routing eval are the current exceptions, and they reach selected
production entry points only from behind fakes.

The deterministic `tier:pr` scenarios are a required Harness self-contract gate. The
pytest suite also exercises the CLI gate in-process
(`tests/test_harness_contract.py`); the separate CI step keeps the contract
gate independent of pytest collection (`pyproject.toml` intentionally collects
only `tests/`). Live-model quality evals and external-resource smoke tests
remain explicit opt-ins.

## Why `harness/` exists separately from `tests/`

`tests/` is the correctness gate: the offline pytest suite that must pass on
every PR. It asserts current behavior of the runtime (kernel protocol, host
API, gateway serializers, security gates) with fakes and tmp data dirs. It
never needs network, secrets, GPUs, SSH, lab hardware, or a live LLM.

`harness/` is the prototype evaluation and scenario layer: infrastructure for
scripted-loop scenarios, normalized traces, quality evals, and fake
platform-provider data. Today the generic runner is not an end-to-end
Agent/Gateway adapter: `surface`, permissions, and fixtures are validated
scenario fields rather than executed production integrations. Scripted
self-contract runs are pass/fail and required. Scored quality runs may be
slower, and they may use external resources only when explicitly opted in.

Rule of thumb:

- A regression assertion about a specific contract belongs in `tests/`.
- A reusable fake provider, a replayable scenario, a golden trajectory, or a
  scored eval belongs in `harness/`.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | The public face of the Harness: scenario schema, loader, result, runner. Production packages never import it. |
| [`characterize.py`](characterize.py) | Imports selected production entry points, drives them behind stdlib `unittest.mock` fakes, and normalizes what they actually did into the reviewed r5 pre-change characterization. Where a snapshot records a known bug it says so; fixing that bug is supposed to change the snapshot. |
| [`cli.py`](cli.py) | Two subcommands. `run` picks scenarios by tier, validates them, executes them, and prints a line per scenario plus a summary; `characterize` compares the r5 characterization with its golden, or rewrites it. Exit codes are deterministic. |
| [`faults.py`](faults.py) | What a run needs in order to repeat: a monotonic clock whose sleeps merely advance it, UUID-shaped ids handed out in call order, and a fault schedule. Each declared fault fires exactly once, on the Nth visit to a named point, and the failure it raises is structured rather than a bare exception. |
| [`normalize.py`](normalize.py) | Swaps volatile UUID, time, path, and port values out of a trace and emits the canonical bytes used for comparison. An identifier gets its placeholder on first appearance, so parent links keep their meaning and reversing two events changes the output. Event lists are never sorted. |
| [`runner.py`](runner.py) | Runs one scenario's scripted loop and records the canonical event trace, firing scheduled faults along the way and checking the declared invariants before it returns a trace digest. This is the production-independent half of the Harness: it neither imports nor drives Agent/Gateway runtime code. |
| [`schema.py`](schema.py) | The versioned JSON contract for a scenario: provider steps, faults, permissions, expectations, and the event envelope a run emits. Validation is strict. An unknown field, or a schema version other than the current one, fails the load rather than being quietly ignored. |

## Subdirectories

| Directory | Intended contents |
| --- | --- |
| [`scenarios/`](scenarios/) | One JSON file per scenario: the prompt, the scripted provider steps to reply with, the faults to inject, the tags that place it in a tier, and the outcome to expect. Fixture and permission metadata is validated but not yet executed, so these are still not end-to-end Agent/Gateway runs. |
| [`providers/`](providers/) | Offline stand-ins for the platform boundaries a run would otherwise cross: model, compute, endpoint, lab. |
| [`golden_traces/`](golden_traces/) | Reviewed reference trajectories, kept for exact comparison and for reviewing drift that turns out to be intentional. They are data to read, not replay to run. |
| [`evals/`](evals/) | Offline eval fixtures and the code that scores them, including the deterministic action-routing quality and contract evaluation. |
| [`smoke/`](smoke/) | Runtime smoke programs that check a platform or an external resource. Nothing here runs unless you opt in. |

## Ground rules

Everything here runs offline, and it runs without secrets — default PR CI
provides none. Nothing in `harness/` may need live network, an API key, a GPU,
SSH, Docker, a browser, or lab hardware. An entry point that genuinely needs
one of those is opt-in only and carries the matching pytest marker (`external`,
`network`, `live_llm`, `gpu`, `ssh`, `docker`, `browser`, `lab`), the same
markers registered in `pyproject.toml`.

No production code lives here either. The runtime implementation stays in
`openai4s/` and `openai4s_compute_provider/`, and the generic runner stays
self-contained. Only the named characterization and eval adapters may import
selected public production entry points, and only from behind deterministic
fakes. Nor may a Harness helper push a hard third-party import into the core
packages.

Two rules protect the record itself. Normalization can replace a volatile
value, but it must not sort an event list: a concurrent scenario
compares explicit causal and per-stream relationships instead of manufacturing
a total order. And a golden trace is comparison data, never executable history
— scenario playback may call declared fakes and nothing else.

Finally, leave `tests/` where it is. Existing test files stay put, and any
future relocation needs its own PR with collect-only proof that no test was
dropped.

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
its golden explicitly and review the diff:

```bash
uv run python -m harness.cli characterize          # compare against the golden
uv run python -m harness.cli characterize --write  # regenerate after review
```

## Trace assets are not interchangeable

Three kinds of recording live near each other here, and they answer different
questions. A canonical run trace is the target record for scripted model,
action, permission, and lifecycle events, and the thing deterministic contract
comparison reads. A host-call tape stores successful host-call results so a
notebook can be replayed offline; it is neither a full trajectory nor a
crash-resume record. A live-model eval snapshot measures prose and task
quality, and it is not a source of truth CI can rely on.

## Governance

Harness changes follow the project-owned
[harness invariants](../CONTRIBUTING.md#harness-invariants) and offline-test
policy. New behavior should be backed by deterministic scenario contracts, and
intentional golden changes must be reviewed explicitly.
