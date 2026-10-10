# Lab simulation trajectories

[中文说明](README_zh.md)

Seven `offline`, `tier:pr`, `lab-sim` scenarios drive the real Store and LabManager with an explicit fake through the named `harness/lab.py` adapter. The `lab` tag denotes external resources and must not be used here.

The shared schema requires a nonempty model script, so `provider_script` contains only the explicit `lab_simulation` sentinel; no LLM is called. Each `fixtures.lab_simulation.case` declares the complete fault recipe: `lost_response` injects both response loss and an initial query failure, while `provider_lost` uses the post-dispatch death hook. An independent golden fixes actual states, error codes and counts without reading or emitting evaluations, truth, identities or clocks. Both CLI and pytest compare the same golden; neither updates it implicitly.

## Files

| File | Responsibility |
| --- | --- |
| [`lab_lost_response.json`](lab_lost_response.json) | A lost execute response and failed query stay unknown until explicit status reconciliation. |
| [`lab_duplicate_submission.json`](lab_duplicate_submission.json) | Submitting the same key returns its receipt without executing twice. |
| [`lab_stale_revision.json`](lab_stale_revision.json) | A stale revision is rejected before provider execution. |
| [`lab_provider_lost.json`](lab_provider_lost.json) | A provider dying after execute leaves the command unknown and ends the run as provider_lost. |
| [`lab_budget_exhausted.json`](lab_budget_exhausted.json) | Exhausted step budget rejects the next command and releases the provider. |
| [`lab_approval_denied.json`](lab_approval_denied.json) | A real HostDispatcher broker denial leaves the command ledger and device untouched. |
| [`lab_recovery_forbidden.json`](lab_recovery_forbidden.json) | Recovery cannot replay a side-effecting Lab command. |
