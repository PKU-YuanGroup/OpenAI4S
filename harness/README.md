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
and deliberately does not import the production runtime. Four files are the
current exceptions, and they are exceptions in two different ways:
`characterize.py` drives selected production entry points from behind stdlib
`unittest.mock` fakes, while the action-routing and retrosynthesis-backend
evals and the orchestration runner call a production function on recorded
input — the router in `openai4s/agent/actions.py`, the response normalizer in
the bundled retrosynthesis Skill, the `Reconciler` decision loop in
`orchestration.py` — which needs no fake because there is no live boundary to
stand in for.

`auto_mode_contract.py` is also production-independent. It is the frozen Stage
0 contract adapter for the Auto Mode user states, not evidence about the
production implementation. Its traces say this explicitly and pin the order
and fail-closed meaning that the integrated runtime must satisfy. Canonical
identity, candidate, complete frozen evidence, Artifact-set, action,
request-only review policy, audit-request, and completion-assessment digests
are computed from strict canonical JSON and compared with the separately
reviewed `golden_traces/v1/auto_mode_contract_expected.json`; a scenario cannot
prove itself merely by changing its expected outcome. Material findings and
the termination basis are response-side assessment facts: they are never
leaked into the provisional candidate or precommitted by the audit request.
Hash-mismatch cases rehash an actual mutated runtime fixture instead of
accepting a caller-supplied observed digest. A complete evidence snapshot must
reference every declared Artifact and provenance version exactly once. The
Stage 0 `allow_once` trace accepts only the sealed, internal `results/`
file-write class; an action's self-reported risk label cannot widen it.

`auto_mode_terminal_contract.py` separately freezes the five non-Guardian
control-plane stops: explicit policy setup, exhausted budget, unavailable safe
rollback, unknown external outcome, and loop detection. These stops are not
Auto Mode user states and do not borrow Reviewer or Guardian audit fields. The
adapter validates reason-specific fail-closed conditions, cross-routing
precedence, recovery limits, and independently reviewed canonical digests in
`golden_traces/v1/auto_mode_terminal_contract_expected.json`. It also declares
`production_state_machine: false`; that field describes this adapter, while the
integrated production implementation is verified separately under `tests/`.

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
| [`auto_mode_contract.py`](auto_mode_contract.py) | Replays the frozen Stage 0 Auto Mode contract without importing production state-machine code: `candidate` stays non-terminal; Reviewer/producer identities, complete evidence references, review policy, structured material findings, and exact actions are bound; canonical and specific event names share one event id; and timeout/parse/audit/hash failures close safely. Each Reviewer or Guardian decision gets at most two attempts. Audit or integrity failures use the separate `safety_boundary` terminal instead of masquerading as a Guardian decision. Guardian infrastructure exhaustion records `decision=unavailable` with only the infrastructure breaker open. An ordinary durable deny with no prior denial history keeps both breakers closed and terminates only because its assessment records `terminal_basis=no_safe_continuation`; the denial circuit requires three consecutive denials or 10 of the latest 50. It is intentionally a contract adapter; production conformance is covered by the focused runtime tests. |
| [`auto_mode_terminal_contract.py`](auto_mode_terminal_contract.py) | Replays the five frozen non-Guardian Auto Mode stops without importing production state-machine code. Each reason has a closed condition schema, exact recovery projection, and independent canonical digests; unsafe cross-routing, same-run continuation, ambiguous side-effect retry, Reviewer/Guardian masquerading, and fixture drift fail closed. |
| [`characterize.py`](characterize.py) | Imports selected production entry points, drives them behind stdlib `unittest.mock` fakes, and normalizes what they actually did into the reviewed r5 pre-change characterization. Where a snapshot records a known bug it says so; fixing that bug is supposed to change the snapshot. |
| [`cli.py`](cli.py) | Two subcommands. `run` picks scenarios by tier, validates them, executes them, and labels each result `CONTRACT_* production=false` or `PRODUCTION_* production=true`; its summary reports `contract_only` and `production_backed` separately. `characterize` compares the r5 characterization with its golden, or rewrites it. Exit codes are deterministic. |
| [`faults.py`](faults.py) | What a run needs in order to repeat: a monotonic clock whose sleeps merely advance it, UUID-shaped ids handed out in call order, and a fault schedule. Each declared fault fires exactly once, on the Nth visit to a named point, and the failure it raises is structured rather than a bare exception. |
| [`normalize.py`](normalize.py) | Swaps volatile UUID, time, path, and port values out of a trace and emits the canonical bytes used for comparison. An identifier gets its placeholder on first appearance, so parent links keep their meaning and reversing two events changes the output. Event lists are never sorted. |
| [`orchestration.py`](orchestration.py) | The other kind of runner: it drives the **real** `Reconciler` against a scripted backend. That is a deliberate exception to the rule beside it, on the same grounds as the action-routing eval — the reconciler's decision function has no live boundary a fake would stand in for, since its inputs are a workload row and an observation and both are data. Re-implementing its rules here and then checking them would assert a model against a model, staying green through every defect the two share. |
| [`runner.py`](runner.py) | Runs one scenario's scripted loop and records the canonical event trace, firing scheduled faults along the way and checking the declared invariants before it returns a trace digest. This is the production-independent half of the Harness: it neither imports nor drives Agent/Gateway runtime code. |
| [`schema.py`](schema.py) | The versioned JSON contract for a scenario: provider steps, faults, permissions, expectations, and the event envelope a run emits. Validation is strict. An unknown field, or a schema version other than the current one, fails the load rather than being quietly ignored. |

## Subdirectories

| Directory | Intended contents |
| --- | --- |
| [`scenarios/`](scenarios/) | One JSON file per scenario: the prompt, the scripted provider steps to reply with, the faults to inject, the tags that place it in a tier, and the outcome to expect. For the generic runner, fixture and permission metadata is validated but not executed; the orchestration family is the exception, whose `fixtures.orchestration` block scripts the backend the real `Reconciler` is driven against. None of these are end-to-end Agent/Gateway runs. |
| [`providers/`](providers/) | Offline stand-ins for the platform boundaries a run would otherwise cross: model, compute, endpoint, lab. |
| [`golden_traces/`](golden_traces/) | Reviewed reference trajectories, kept for exact comparison and for reviewing drift that turns out to be intentional. They are data to read, not replay to run. |
| [`evals/`](evals/) | Offline eval fixtures and the code that scores them: the deterministic action-routing quality and contract evaluation, and the retrosynthesis backend replay, which scores recorded external-model responses through the production normalizer without loading a model weight. |
| [`smoke/`](smoke/) | Runtime smoke programs that check a platform or an external resource. Nothing here runs unless you opt in. |

## Ground rules

Everything here runs offline, and it runs without secrets — default PR CI
provides none. Nothing in `harness/` may need live network, an API key, a GPU,
SSH, Docker, a browser, or lab hardware. An entry point that genuinely needs
one of those is opt-in only and carries the matching pytest marker (`external`,
`network`, `live_llm`, `gpu`, `ssh`, `docker`, `browser`, `lab`), the same
markers registered in `pyproject.toml`.

No production code lives here either. The runtime implementation stays in
`openai4s/` and `openai4s_compute_provider/`, and the generic and Auto Mode
contract runners stay self-contained. Only the named characterization, eval,
and orchestration adapters may import selected public production entry points,
and only against deterministic fakes and scripted data. Nor may a Harness
helper push a hard third-party import into the core packages.

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

Four kinds of recording live near each other here, and they answer different
questions. A canonical run trace is the target record for scripted model,
action, permission, and lifecycle events, and the thing deterministic contract
comparison reads. A host-call tape stores successful host-call results so a
notebook can be replayed offline; it is neither a full trajectory nor a
crash-resume record. A backend response tape — `evals/retrosynthesis_backend_cases.json`
is the one that exists — is a recorded external-model reply replayed through
the production normalizer: it says whether that response is still parsed
correctly, and nothing about whether the model is still right. A live-model
eval snapshot measures prose and task quality, and it is not a source of truth
CI can rely on.

## Governance

Harness changes follow the project-owned
[harness invariants](../CONTRIBUTING.md#harness-invariants) and offline-test
policy. New behavior should be backed by deterministic scenario contracts, and
intentional golden changes must be reviewed explicitly.
