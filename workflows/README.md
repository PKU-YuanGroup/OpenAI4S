# `workflows/`

The versioned science-workflow benchmark's manifests: eleven workflows and 34
cases, each workflow JSON declaring what a run is supposed to do and what
counts as having done it. The root-level `next-round-acceptance.json` is a
separate Stage 0 field/safety pack, not a twelfth science workflow.

They live in the repository rather than in a fixture directory for one reason:
a case change has to be a reviewable diff. The runner that executes them is
[`openai4s/benchmark/`](../openai4s/benchmark/README.md), and every step it
takes drives production code — the real Store, the real kernel manager, the
real host dispatcher, the real compute manager. What gets injected is only what
cannot run offline: the model, the network, and a package manager.

A declared outcome is part of the contract, not a status column. `failure`,
`permission_denied`, `recovered` and `provenance` cases fail when the run
*succeeds*, because a benchmark that scores "no exception" measures nothing
about the half of the system whose job is to refuse.

| Workflow | What it covers |
| --- | --- |
| [`next-round-acceptance.json`](next-round-acceptance.json) | Strict Stage 0 pack for six field paths, seven safety actions, and denominator-explicit baseline metrics |
| [`artifact-lineage/`](artifact-lineage/README.md) | A derived artifact carries its lineage |
| [`environment-provenance/`](environment-provenance/README.md) | An artifact's environment provenance |
| [`environment-transaction/`](environment-transaction/README.md) | plan -> apply -> rollback as a transaction |
| [`evidence-package/`](evidence-package/README.md) | Exporting and verifying an evidence package |
| [`permission-boundary/`](permission-boundary/README.md) | The workspace boundary refuses a write outside it |
| [`python-analysis/`](python-analysis/README.md) | Python analysis producing a traceable artifact |
| [`r-analysis/`](r-analysis/README.md) | R is its own channel, not a wrapper |
| [`remote-compute/`](remote-compute/README.md) | submit -> poll -> harvest against a real shell |
| [`science-retrieval/`](science-retrieval/README.md) | Scientific retrieval with source evidence |
| [`telemetry-identity/`](telemetry-identity/README.md) | Revoking telemetry destroys the identity with it |
| [`tool-bringup/`](tool-bringup/README.md) | Tool bring-up: build, weights, canary, admission, frozen record |

Replay the strict Stage 0 pack, with a JSON report suitable for a gate, using
`openai4s benchmark --acceptance --json`. A matching baseline gap is printed as
`BASELINE`, never as a current capability. The loader freezes the manifest's
path IDs, claims, execution modes, assertion keys, expected values, and
canonical content digest under one `pack_version`; a semantic edit without a
reviewed version/digest is rejected rather than silently changing the baseline.
The current `2026-08-16.2` manifest requires the Reviewer observation to prove
that the formal workspace fingerprint remained unchanged.
