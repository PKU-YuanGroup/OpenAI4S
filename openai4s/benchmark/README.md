# `openai4s/benchmark/`

The runner for the versioned science-workflow benchmark whose manifests live in
[`workflows/`](../../workflows/README.md): ten workflows and twenty cases that
actually execute, plus a separate strict Stage 0 field/safety acceptance pack.

The proposal that asked for them was specific about what would make them
worthless — a directory of fixtures nobody runs, or cases that pass because the
thing they exercise is a mock. So every step here drives the real subsystem:
the real Store, the real kernel manager, the real host dispatcher, the real
compute manager, the real connector service, the real environment transaction.
What is injected is only what cannot run offline — the LLM (the suite already
mocks it), the network (connector fetches are fed recorded bodies), and the
package manager (an environment build cannot download a solver in a unit
test) — and each of those is injected *into* production code rather than
replacing it. A step that builds its own answer measures the step.

**A declared outcome is part of the contract.** A case that expects `failure`
and gets a clean run has failed just as surely as one that expects success and
raises, because a benchmark scoring "no exception" measures nothing about the
half of the system whose job is to refuse. `provenance`, `recovered` and
`permission_denied` exist for the same reason.

| File | Purpose |
| --- | --- |
| `__init__.py` | The public surface: the workflow APIs plus `AcceptancePack`, `load_acceptance_pack`, and `run_acceptance_pack`. Nothing else in the tree should be imported by a caller. |
| `acceptance.py` | Loads and replays the strict, versioned next-round acceptance pack: exactly six field paths and seven safety actions, with expected/observed/pass/evidence/duration records and denominator-explicit aggregate metrics. |
| `model.py` | What a workflow and a case *are*, and where they are read from. A manifest is JSON rather than YAML for the same reason the core is — no third-party import may be required to read the thing that decides whether a release is good — and it carries a version, because a benchmark whose cases can change silently measures nothing across time. |
| `runner.py` | Runs a case and decides whether what happened is what it declared. The decision is the interesting part, not the execution: the declared outcome is compared against the observed one, and a mismatch in either direction is a failure. |
| `steps.py` | The step implementations, one function per step name, keyed in `STEPS`. Each takes the shared `Context` and the case's inputs and returns a dict merged into the result; raising is how a step reports that the workflow could not proceed, and the runner decides whether that matches the declaration. `SkipCase` is for a host that genuinely cannot run a step (no `Rscript`, no shell), which is a skip rather than a silent pass. |

## Why the manifests are not in here

They live in [`workflows/`](../../workflows/README.md), at the repository root,
so that changing what the benchmark expects is a reviewable diff sitting beside
the code it judges — rather than a fixture edit buried under a package.

## Next-round acceptance entrypoint

The stable one-command machine-readable entrypoint is:

```bash
openai4s benchmark --acceptance --json
```

The corresponding public Python entrypoint is:

```python
from openai4s.benchmark import run_acceptance_pack

report = run_acceptance_pack()  # JSON-serializable; report["pass"] is the gate
```

The CLI adapter does only composition: it calls this public function,
serializes the returned object, and returns zero exactly when `report["pass"]`
is true. The acceptance module deliberately does not add a second command
parser.

The JSON report is also the Stage 0 measurement record, not merely a list of
assertions. `manifest_digest` binds the exact canonical manifest content to
`pack_version`; changing a claim, execution mode, assertion key, or expected
value without a reviewed version/digest fails closed. The current
`2026-08-16.2` contract adds `workspace_unchanged=true` to the mandatory
Reviewer expectation. The runner always reloads and validates that packaged
canonical declaration; a caller-supplied `AcceptancePack` is executable only
when it equals the canonical pack exactly, so deleting probes or weakening a
nested expectation cannot self-issue a passing report. The report binds that
identity and `recorded_at_ms` to the observed p50/p95 field-path latency,
reported Reviewer tokens, cell-failure rate, same-checksum/later-cell
duplicate-version rate, and planted-case review-hit rate. Cell, Reviewer, and
duplicate denominators count attempts before the operation that may fail;
raised, error, and interrupted terminals therefore remain visible. Every
metric carries its denominator definition and explicit zero-sample behavior.
Deterministically injected Reviewer token/hit numbers live under
`offline_contract`; genuinely observed provider samples live separately under
`live_observed`, whose values are `null` when there was no live sample. Offline
numbers must never be presented as current-model performance.

The field probes use the production Python/R kernels, Store/Artifact
repository, Reviewer evidence pipeline, Notebook exporter, Ketcher route body,
and science connector catalog. Ketcher is fetched from the production Gateway
handler through a real, isolated loopback HTTP socket. Its ephemeral access
token banner is captured and verified inside a spawned child process, never
written to the acceptance CLI streams or report. Only the LLM boundary is
deterministically and call-locally injected for the offline Reviewer case. An
injected Reviewer that writes the formal workspace is observed as
`workspace_unchanged=false` and fails the field-path assertion. Stage 0 does not
pretend that observation is the future read-only Reviewer sandbox. An
unresolved or sandbox-blocked R worker is reported as unavailable, not replaced
with Python. Ketcher's current placeholder and ClinVar's absence are
`baseline_observation` results with `capability_pass=false`; a successful replay
of those observations is not a claim that either feature works.

The report's `environment` object records the requested and observed kernel
sandbox posture, safety controls, egress/network posture, unattended-approval
setting, Notebook/team modes, and the fact that reserved roadmap flags remain
unconsumed in Stage 0. The frozen pack measures the repository's default posture;
it does not silently override caller configuration. A deliberate non-default
environment such as `OPENAI4S_NOTEBOOK_REPL=1` therefore makes the frozen
read-only Notebook observation fail. That is visible configuration drift, not
benchmark nondeterminism; inspect `environment` before comparing reports.
The network preflight follows the same rule: the production global network
switch and egress gate take deterministic precedence over an `allow` permission.
With `OPENAI4S_ALLOW_NETWORK=0`, the observation is `deny` and the frozen
default-posture `allow` assertion fails visibly; it is never rewritten to pass.

Safety probes run the real Host permission/path, egress, code-classifier, and
shell-precheck layers. Only a generated workspace read and confined write are
executed. The external-write path reaches confinement resolution but never
opens its target; network, sensitive-payload egress, and delete actions never
reach a transport or shell.
