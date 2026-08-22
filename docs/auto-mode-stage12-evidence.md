# Auto Mode Stage 0–12 evidence

This table maps the integrated Stage 0–12 implementation to its durable
repository evidence. Every rollout flag remains default-off, and Stage 12 does
not silently enable Stages 1–11. The table intentionally names source and tests
rather than branch-local commit hashes: the stages are delivered together and
their security and recovery fixes must not be reconstructed from an obsolete
stack.

| Stage | Requirement | Implementation | Verification |
| --- | --- | --- | --- |
| 0 | Frozen Auto Mode contract and acceptance pack | `docs/auto-mode.md`, `workflows/next-round-acceptance.json` | `tests/test_benchmark_workflows.py` |
| 1 | Trusted delivery, exact version links, environment preflight | `openai4s/server/delivery.py`, `openai4s/server/urls.py` | `tests/test_completion_delivery.py`, `tests/test_environment_readiness.py` |
| 2 | Durable Auto Run / finding / decision storage | `openai4s/server/auto_mode.py`, `openai4s/storage/auto_mode.py` | `tests/test_auto_mode_service.py`, `tests/test_auto_mode_storage.py` |
| 3 | Scientific Reviewer shadow, immutable Evidence Snapshot | `openai4s/server/scientific_review.py`, `openai4s/server/evidence_snapshot.py` | `tests/test_scientific_review_service.py`, `tests/test_evidence_snapshot.py` |
| 4 | Completion gate: candidate → review → verified / issues | `openai4s/server/completion_gate.py` | `tests/test_completion_gate.py`, `tests/test_stage4_atomic_promotion.py`, `tests/test_stage4_candidate_promotion.py` |
| 5 | Bounded Repair + independent re-review; no self-certify | `openai4s/server/auto_repair.py` | `tests/test_auto_repair.py` |
| 6 | Guardian shadow exact-action adjudication | `openai4s/server/guardian_shadow.py` | `tests/test_guardian_shadow.py` |
| 7 | Guardian allow-once enforcement; no standing allow | `openai4s/server/guardian_enforce.py` | `tests/test_guardian_enforce.py`, `tests/test_permissions.py` |
| 8 | Official live Notebook + host-side Python/R version lineage | `openai4s/server/notebook_lineage.py`, `openai4s/server/kernel_routes.py` | `tests/test_notebook_lineage.py`, kernel and browser smoke tests |
| 9 | Artifact workbench, CSV/PDF/HTML locators, real Ketcher 3.7.0 | `openai4s/server/artifact_workbench.py`, `openai4s/server/webui/vendor/ketcher/` | `tests/test_artifact_workbench.py`, `tests/test_webui_static_contract.py` |
| 10 | ClinVar / PubMed / ClinicalTrials with bounded cache and provenance | `openai4s/host/stage10_science.py` | `tests/test_stage10_connectors.py`, `tests/test_stage10_live_canaries.py` |
| 11 | Durable remote-compute submit/reconcile/cancel + harvest provenance | `openai4s/compute/stage11.py`, `openai4s/compute/manager.py` | `tests/test_stage11_remote_compute.py`, `tests/test_compute_durability.py` |
| 12 | GA kill switch, full-gate evidence, default-off preserved | `openai4s/server/stage12_ga.py` | `tests/test_stage12_ga.py` |

## Rollback conditions (still live)

- Any critical false allow, secret leak, or cross-project exposure
- Repair loop overwrites a correct Artifact and cannot restore it
- Daemon restart repeats an external side effect or remote charge
- UI shows Verified while the durable review is not `pass`

## Unattended field acceptance

The frozen pack is `workflows/next-round-acceptance.json`, executed through
`openai4s.benchmark.run_acceptance_pack()`. It asserts the default-off baseline,
including the Ketcher placeholder, an absent ClinVar catalog entry, and a
disabled Notebook REPL. Stage 8–11 flag-on behavior is verified by the focused
tests above; the baseline pack does not enable those capabilities as a side
effect.

## Full integration gates

The following commands are the reproducible Stage 12 gate. Exact counts and
one-machine availability are deliberately not frozen here: they change as the
suite and route inventory evolve, and the current PR/CI run is the authority
for a particular revision.

| Gate | Command / scope |
| --- | --- |
| Full offline suite | `uv run pytest -n auto --maxprocesses=4 --dist loadfile` |
| Stage 8–11 focused coverage | Notebook lineage, Artifact workbench, connector, remote-compute, response-contract, and feature-flag consumer tests listed above |
| Deterministic scenario contracts | `uv run python -m harness.cli run --tier pr --offline` |
| Directory documentation | `uv run python scripts/check_directory_readmes.py` |
| Response route inventory | `uv run python scripts/capture_response_contract.py --check` |
| Captured response shapes | `uv run python scripts/capture_response_schemas.py --check` |
| Release-source credential literals | `python scripts/source_secret_scan.py` |
| Strict typed boundaries | `uv run mypy` |
| Formatting, lint, and typed hooks | `uv run pre-commit run --all-files` |
| Container behavior | `bash scripts/container_smoke.sh` where Docker is available |
| Browser behavior | Workbench, admission-fault, Stage 0/1 acceptance, and cross-engine browser probes described in `tests/README.md` |
| Live connector canaries | `uv run pytest -m "network or external" tests/test_stage10_live_canaries.py` when explicitly opted in |
