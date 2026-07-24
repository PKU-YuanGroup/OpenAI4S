# `openai4s/benchmark/`

The runner for the versioned science-workflow benchmark whose manifests live in
[`workflows/`](../../workflows/README.md): ten workflows and twenty cases that
actually execute.

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
| `__init__.py` | The public surface: `Case`, `Workflow`, `load_workflows`, `CaseResult`, `run_case`, `run_all`. Nothing else in the tree should be imported by a caller. |
| `model.py` | What a workflow and a case *are*, and where they are read from. A manifest is JSON rather than YAML for the same reason the core is — no third-party import may be required to read the thing that decides whether a release is good — and it carries a version, because a benchmark whose cases can change silently measures nothing across time. |
| `runner.py` | Runs a case and decides whether what happened is what it declared. The decision is the interesting part, not the execution: the declared outcome is compared against the observed one, and a mismatch in either direction is a failure. |
| `steps.py` | The step implementations, one function per step name, keyed in `STEPS`. Each takes the shared `Context` and the case's inputs and returns a dict merged into the result; raising is how a step reports that the workflow could not proceed, and the runner decides whether that matches the declaration. `SkipCase` is for a host that genuinely cannot run a step (no `Rscript`, no shell), which is a skip rather than a silent pass. |

## Why the manifests are not in here

They live in [`workflows/`](../../workflows/README.md), at the repository root,
so that changing what the benchmark expects is a reviewable diff sitting beside
the code it judges — rather than a fixture edit buried under a package.
