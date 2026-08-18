# Auto Mode Stage 0–12 evidence

This table is the Stage 12 field-acceptance record for branch
`feat/auto-mode-roadmap`. Every earlier flag remains default-off. Stage 12
does not silently enable Stages 1–11.

| Stage | Requirement | Implementation | Tests | Commit |
| --- | --- | --- | --- | --- |
| 0 | Frozen Auto Mode contract and acceptance pack | `docs/auto-mode.md`, `workflows/next-round-acceptance.json` | `tests/test_benchmark_workflows.py` | `7d95377` |
| 1 | Trusted delivery, exact version links, environment preflight | `openai4s/server/delivery.py`, `openai4s/server/urls.py` | `tests/test_environment_readiness.py`, delivery tests | `881d1c3` |
| 2 | Durable Auto Run / finding / decision storage | `openai4s/server/auto_mode.py`, `openai4s/storage/auto_mode.py` | `tests/test_auto_mode_storage.py` | `7a79611` |
| 3 | Scientific Reviewer shadow, immutable Evidence Snapshot | `openai4s/server/scientific_review.py`, `evidence_snapshot.py` | `tests/test_evidence_snapshot.py` | `5e436bc` |
| 4 | Completion gate: candidate → review → verified / issues | `openai4s/server/completion_gate.py` | completion-gate tests; `frame_update` `done` is a documented gated-completion status | `fced0fe` |
| 5 | Bounded Repair + independent re-review; no self-certify | `openai4s/server/auto_repair.py` | `tests/test_auto_repair.py` | `05c3104` |
| 6 | Guardian shadow exact-action adjudication | `openai4s/server/guardian_shadow.py` | `tests/test_guardian_shadow.py` | `c377b9a` |
| 7 | Guardian allow-once enforcement; no standing allow | `openai4s/server/guardian_enforce.py` | `tests/test_guardian_enforce.py` | `8e1489c` |
| 8 | Official live Notebook + host-side Python/R version lineage | `openai4s/server/notebook_lineage.py`, `kernel_routes.py` | `tests/test_notebook_lineage.py`; browser smoke on a flag-on daemon | `f4668cb` |
| 9 | Artifact workbench, CSV/PDF/HTML locators, real Ketcher 3.7.0 | `openai4s/server/artifact_workbench.py`, `webui/vendor/ketcher/` | `tests/test_artifact_workbench.py`; live `/ketcher` 3.7.0 + CSV/PDF/HTML HTTP path | `669972d` |
| 10 | ClinVar / PubMed / ClinicalTrials with live public canaries | `openai4s/host/stage10_science.py` | `tests/test_stage10_connectors.py`, `tests/test_stage10_live_canaries.py` | `98e906e` |
| 11 | Durable remote-compute submit/reconcile/cancel + harvest provenance | `openai4s/compute/stage11.py`, `ComputeManager` | `tests/test_stage11_remote_compute.py`, `tests/test_compute_durability.py` | `a936803` |
| 12 | GA kill switch, full-gate evidence, default-off preserved | `openai4s/server/stage12_ga.py` | `tests/test_stage12_ga.py` | this commit |

## Rollback conditions (still live)

- Any critical false allow, secret leak, or cross-project exposure
- Repair loop overwrites a correct Artifact and cannot restore it
- Daemon restart repeats an external side effect or remote charge
- UI shows Verified while the durable review is not `pass`

## Unattended field-acceptance

The frozen pack is `workflows/next-round-acceptance.json` via
`openai4s.benchmark.run_acceptance_pack()`. A clean file-based run passed with
`status: pass`. Default-off observations remain: Ketcher placeholder, ClinVar
absent from the catalog, Notebook REPL off. Stage 8–11 capabilities are proven
by their own flag-on tests, not by flipping the baseline pack.

## Stage 12 gates (this environment)

| Gate | Result |
| --- | --- |
| `uv run pytest -q --tb=line` | exit 0 (full offline suite) |
| Stage 8–11 critical tests | pass (`test_notebook_lineage`, `test_artifact_workbench`, `test_stage10_connectors`, `test_stage11_remote_compute`, contract coverage, flag-consumer lock) |
| `uv run python -m harness.cli run --tier pr --offline` | 38/38 pass |
| `uv run python scripts/check_directory_readmes.py` | 107 directories / 979 files |
| `uv run python scripts/capture_response_contract.py --check` | 191/191 |
| `uv run python scripts/capture_response_schemas.py --check` | 191/191 routes, no breaking change. Additive workbench verb coverage was reported and left unfrozen so leftover WIP observations are not published as the contract. |
| `uv run python scripts/source_secret_scan.py` | 1234 files pass |
| `uv run mypy` | 8 source files, no issues |
| `uv run pre-commit run --files` (Stage 12 set) | pass |
| Stage 10 live canaries (`-m "network or external"`) | 2 passed (ClinVar, PubMed, ClinicalTrials.gov) |
| Isolated Stage 8/9 daemon browser smoke | `tests/browser_smoke.mjs` passed on `:8761` |
| Live Ketcher + workbench HTTP | real 3.7.0 HTML, 22 MB vendored `main.js`, CSV table / PDF text / HTML outline |
| `scripts/container_smoke.sh` | not run: Docker is not available in this environment |

Earlier leftover WIP remains unstaged and uncommitted:
`openai4s/kernel/manager.py`, `openai4s/orchestration/bootstrap.py`,
`openai4s/orchestration/worker_gateway.py`, `openai4s/server/gateway.py`
(upload-ownership hunk), `openai4s/server/team_policy.py`,
`tests/test_worker_tcp_transport.py`.
