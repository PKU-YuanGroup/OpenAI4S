# `workflows/tool-bringup/`

**Tool bring-up with a frozen, verified record** — Design and prediction tools are *not* preinstalled when a campaign starts: the run must build the tool environment from a public source, download and verify weights, write a running adapter, prove the tool on a canary against a real campaign target, prove the canary output parses and a downstream sequence-design adapter consumes it, and freeze image digest, weights checksums, runtime and cost into `bringup.json`. Only a record that verifies — and whose admission says so — proceeds. The eleven cases each pin one check of that contract, including the full-forgery case that only the evaluator-held reference digests can catch.

Steps: `tool_bringup`, `verify_bringup`
Permissions: `environment:apply`, `network:weights`, `workspace:read`
Declared artifacts: `bringup/bringup.json`, `weights/model.weights`, `bringup/canary_output.json`, `bringup/downstream_result.json`

| File | Purpose |
| --- | --- |
| `workflow.json` | The versioned manifest: steps, permissions, declared artifacts, failure conditions, and the cases below. Version `1.0.0`. JSON rather than YAML for the same reason the core is, and versioned because a benchmark whose cases can change silently measures nothing across time. |

## Cases

| Case | Declared outcome | What it pins |
| --- | --- | --- |
| `tool-bringup/pass` | `provenance` | A complete bring-up verifies against the reference digests and is admitted |
| `tool-bringup/recovered` | `recovered` | A failed canary is frozen into the record, re-run, and re-admitted with the attempt history intact |
| `tool-bringup/missing-record` | `failure` | No record at all refuses with `BringupError` before any check runs |
| `tool-bringup/canary-no-output` | `failure` | A canary that exits 0 with no output produces nothing verifiable |
| `tool-bringup/unparseable-canary` | `failure` | Output that does not parse as the declared format refuses admission |
| `tool-bringup/downstream-refused` | `failure` | A downstream adapter that will not consume the output refuses admission |
| `tool-bringup/tampered-weights` | `failure` | One flipped weight byte is caught by the recorded digest |
| `tool-bringup/canary-output-deleted` | `failure` | A record claiming an output whose file is gone is caught |
| `tool-bringup/forged-record` | `failure` | Payload, digest and seal all rewritten — only the evaluator-held reference notices |
| `tool-bringup/wrong-weights` | `failure` | Honestly downloaded weights that mismatch the reference digest are caught |
| `tool-bringup/budget-exceeded` | `failure` | Cost beyond the declared budget refuses admission |

## Failure conditions the manifest declares

- the bring-up record is missing or was rewritten and is still believed
- a weights file mismatches its recorded digest or the evaluator's reference digest
- the canary output is missing, unparseable, or missing declared fields
- the downstream adapter did not consume the output or its proof fails verification
- the cost exceeds the declared budget and admission still proceeds

## The `bringup.json` contract

The record the run freezes under `bringup/bringup.json` carries `schema_version`, a self-vouching `record_sha256`, `tool` (name, version, source, revision, adapter, `env_name`/`env_generation` naming the built environment), `weights` (per-file path, sha256, size, source, `verified`), `canary` (target, command, outputs with digests, a parse proof with status/format/fields, and a downstream consumption proof), `admission` (status plus reasons), `runtime` (wall time and the attempt history), and `cost` (`gpu_h` within an optional `budget_hours`). The verifier is `openai4s.benchmark.bringup.verify_bringup`, and the harness step raises on any failing check — missing records refuse with `BringupError`, everything else with the joined problem list.

`record_sha256` establishes internal consistency only: anyone can rewrite a weights file *and* its recorded digest and re-seal the record, and every internal check passes. Ground truth enters through the `expected_weights` seam — digests the evaluator froze from the reference build — which is exactly what the `forged-record` case demonstrates. Real binder/MD campaign queries will require the agent run to produce this record, and the evaluator will call the same `verify_bringup` with the reference digests; that is the "only a PASS admits into production" mechanism.

Two boundaries are deliberate and documented. The offline simulation builds the environment through the real `EnvironmentStore` transaction with an injected fake package manager, and runs the canary via `sys.executable` against the installed tool script — the recorded env interpreter is a stub, and enforcing "not preinstalled" isolation is a later phase. The `env_generation` check couples to the `environments/<env>/generations/<id>/manifest.json` layout of the case root: a real campaign either keeps that relative layout or drops this one check.
