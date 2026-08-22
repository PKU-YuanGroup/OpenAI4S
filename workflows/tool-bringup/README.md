# `workflows/tool-bringup/`

**Tool bring-up with a frozen, verified record** — Design and prediction tools are *not* preinstalled when a campaign starts: the run must build the tool environment from a public source, download and verify weights, write a running adapter, prove the tool on a canary against a real campaign target, prove the canary output parses and a downstream sequence-design adapter consumes it, and freeze the environment-generation identity, adapter and weights checksums, cumulative runtime, and cumulative cost into `bringup.json`. Only a record that verifies — and whose admission says so — proceeds. The fourteen cases (two admitted/recovered paths and twelve refusals) each pin one check of that contract, including the full-forgery case that only evaluator-held reference digests can catch.

Steps: `tool_bringup`, `verify_bringup`
Permissions: `environment:apply`, `network:weights`, `workspace:read`, `workspace:write`
Declared artifacts: `bringup/bringup.json`, `bringup/adapter.py`, `weights/model.weights`, `bringup/canary_output.json`, `bringup/downstream_result.json`

| File | Purpose |
| --- | --- |
| `workflow.json` | The versioned manifest: steps, permissions, declared artifacts, failure conditions, and the cases below. Version `1.0.0`. JSON rather than YAML for the same reason the core is, and versioned because a benchmark whose cases can change silently measures nothing across time. |

## Cases

| Case | Declared outcome | What it pins |
| --- | --- | --- |
| `tool-bringup/pass` | `provenance` | A complete bring-up verifies against the reference digests and is admitted |
| `tool-bringup/recovered` | `recovered` | A failed canary is frozen, re-run, and re-admitted with exact `failed → passed` attempt history and cumulative accounting |
| `tool-bringup/recovery-budget-exceeded` | `failure` | A retry cannot replace the budget frozen on the first attempt; cumulative cost over that budget refuses admission |
| `tool-bringup/missing-record` | `failure` | No record at all refuses with `BringupError` before any check runs |
| `tool-bringup/fail-build` | `failure` | A failed environment apply freezes a refused attempt instead of running a canary against no generation |
| `tool-bringup/spec-mismatch` | `failure` | An installed package set that does not match `design-tool==1.0.0` refuses admission |
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
- the environment failed to build, or its installed package set does not match the declared spec
- a weights file mismatches its recorded digest or the evaluator's reference digest
- the canary output is missing, unparseable, or missing declared fields
- the downstream adapter did not consume the output or its proof fails verification
- one attempt or the cumulative recovery campaign exceeds its first frozen budget and admission still proceeds

## The `bringup.json` contract

The record the run freezes under `bringup/bringup.json` carries `schema_version`; a self-vouching `record_sha256`; `tool` (name, version, source, revision, an adapter object with confined path/sha256/size, and `env_name`/`env_generation` as the built-environment identity); `weights` (per-file path, sha256, size, source, and `verified`); `canary` (target, the schema-v1 portable logical command `python bin/tool --target … --weights …` with no absolute interpreter or temporary-root path, outputs with digests, a parse proof with status/format/fields, and a downstream consumption proof); `admission` (status plus reasons); `runtime` (the sum of attempt wall times and non-empty attempt records carrying status/reason/wall_s/gpu_h); and `cost` (the sum of attempt `gpu_h` values under the `budget_hours` frozen on the first attempt). A retry accumulates both runtime and cost and cannot replace that budget. Admission is `verified` only when the final attempt passed and the cumulative cost is still within it. The verifier is `openai4s.benchmark.bringup.verify_bringup`, and the workflow benchmark step raises on any failing check or non-admitted report — missing records refuse with `BringupError`, everything else with the joined problem list.

`record_sha256` establishes internal consistency only: anyone can rewrite weights, the canary and downstream proof, all three recorded digests, and then re-seal the record. Ground truth enters through the exact `expected_weights` seam — digests the evaluator froze from the reference build — which is exactly what the `forged-record` case demonstrates by keeping every internal relationship consistent while changing the reference-bound bytes. Real binder/MD campaign queries require the agent run to produce this record, and the evaluator calls the same `verify_bringup` with the complete reference digest set; that is the "only a PASS admits into production" mechanism.

Two boundaries are deliberate and documented. The offline workflow benchmark builds through the real `EnvironmentStore` transaction with an injected fake package manager and executes the installed tool fixture with the test interpreter; `bringup.json` records only the portable logical command (`python bin/tool …`), never `sys.executable` or an absolute temporary path. The recorded environment interpreter is a stub, so enforcing "not preinstalled" isolation remains a later phase. The `env_generation` proof is bound to the case root's `environments/<env>/generations/<id>/manifest.json` identity and confined prefix; a real campaign preserves that relative layout in its submission.
