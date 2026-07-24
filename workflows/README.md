# `workflows/`

The versioned science-workflow benchmark's manifests. Ten workflows, 20
cases, each one a JSON file that declares what a run is supposed to do and what
counts as having done it.

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
