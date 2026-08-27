# `workflows/codebase-mode/`

**A source deliverable, and whether the Host believes the claim about it** — When the deliverable is a reusable pipeline or a change to a codebase, the run has to save the implementation to source files, keep a thin entry point, actually run the tests, and then declare `source_files`, `entry_points`, `architecture_summary` and `test_evidence` at completion. This workflow drives that end to end against real subsystems: a real persistent Python kernel writes each file, the real Store registers each one as an artifact version, the tests really run as a subprocess launched from a real cell whose row lands in the real `execution_log`, and the real `HostDispatcher` decides whether to accept the submission. The nine cases are three acceptances and six refusals, each aimed at one check.

The refusals matter more than the acceptance. A benchmark that only watches a good run succeed measures nothing about the half of the contract whose job is to say no — and every one of these mutations produces a payload that *looks* complete.

Steps: `open_session`, `produce_codebase`, `verify_codebase` (mutation cases insert `tamper_codebase`)
Permissions: `workspace:read`, `workspace:write`, `kernel:execute`
Declared artifacts: `seqpipe/domain.py`, `seqpipe/io.py`, `seqpipe/pipeline.py`, `run_pipeline.py`, `tests/test_domain.py`

| File | Purpose |
| --- | --- |
| `workflow.json` | The versioned manifest: steps, permissions, declared artifacts, failure conditions, the source tree each case writes, and the cases below. Version `1.0.0`. |

## Cases

| Case | Declared outcome | What it pins |
| --- | --- | --- |
| `codebase-mode/structured-pipeline-accepted` | `success` | A package, a thin entry point and a passing test run are verified and accepted, and the verified declarations are committed with the completion |
| `codebase-mode/single-file-task-stays-single-file` | `success` | There is no file-count or line-count floor: an honestly single-module task passes unchanged |
| `codebase-mode/analysis-run-is-unaffected` | `success` | The other half of backward compatibility — `analysis_run` neither requires the fields nor validates them |
| `codebase-mode/deleted-source-file` | `failure` | A declared source file that is gone is refused |
| `codebase-mode/corrupted-source-file` | `failure` | Content swapped after the claim is caught by the declared sha256 |
| `codebase-mode/broken-entry-point` | `failure` | An entry point that no longer compiles is refused *even though the forger also refreshed its digest*, so the claim is internally consistent |
| `codebase-mode/forged-test-cell` | `failure` | `test_evidence` naming a cell this run never executed is refused |
| `codebase-mode/tests-actually-failed` | `failure` | A real cell really ran and really failed; pass/fail is read off the stored stdout, so the model calling it a pass changes nothing |
| `codebase-mode/interrupted-multi-file-write` | `failure` | Three of seven files written and all seven claimed — the half-set claimed complete is refused |
