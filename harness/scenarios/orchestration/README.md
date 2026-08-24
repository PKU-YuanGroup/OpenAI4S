# Orchestration scenarios

[中文说明](README_zh.md)

These run the **real** `Reconciler` against a scripted backend, not a model
of one. That is the whole reason they are worth having: a harness that
re-implemented the reconciler's rules and then checked them would stay
green through every defect the model shares with the code — which is every
defect that comes from misunderstanding the problem rather than mistyping
the solution.

The backend is scripted because a `SubmitResult` and an `Observation` *are*
the boundary's contract: four cases and a phase. Nothing in the script
decides anything; deciding is the reconciler's job and watching it decide
is the point.

**A scenario that declares a refusal fails when the run succeeds.** Half of
what this subsystem does is refuse — a stale epoch, a double submission, a
cancel that must not resurrect — and a scenario scoring "no exception"
would measure none of it. That property is checked, not assumed: mutating
`recovery.is_bounded_rather_than_endless` so every attempt succeeds turns
it red.

## Files

| File | Scenario |
| --- | --- |
| [`alloc_backend_unavailable_is_not_a_state_change.json`](alloc_backend_unavailable_is_not_a_state_change.json) | `alloc.backend_unavailable_is_not_a_state_change` — A scheduler that cannot be reached must not become a dead workload. |
| [`alloc_happy_path.json`](alloc_happy_path.json) | `alloc.happy_path` — A batch workload is submitted, runs, and completes. |
| [`alloc_rejected_submission_fails_cleanly.json`](alloc_rejected_submission_fails_cleanly.json) | `alloc.rejected_submission_fails_cleanly` — A backend that refuses gives the workload a terminal state and a reason. |
| [`alloc_unknown_submission_is_adopted_not_resubmitted.json`](alloc_unknown_submission_is_adopted_not_resubmitted.json) | `alloc.unknown_submission_is_adopted_not_resubmitted` — A submission whose outcome was lost is found by token, not sent twice. |
| [`alloc_unknown_submission_resubmits_only_after_asking.json`](alloc_unknown_submission_resubmits_only_after_asking.json) | `alloc.unknown_submission_resubmits_only_after_asking` — Nothing carries the token, so submitting again cannot double-allocate. |
| [`cancel_a_reclaimed_session_is_not_reported_as_user_cancelled.json`](cancel_a_reclaimed_session_is_not_reported_as_user_cancelled.json) | `cancel.a_reclaimed_session_is_not_reported_as_user_cancelled` — The plane can only say 'cancelled'; why we cancelled is ours to record. |
| [`cancel_an_unplaced_allocation_still_finishes.json`](cancel_an_unplaced_allocation_still_finishes.json) | `cancel.an_unplaced_allocation_still_finishes` — Nothing was placed, so the barrier concludes instead of re-entering forever. |
| [`cancel_barrier_runs_in_the_declared_order.json`](cancel_barrier_runs_in_the_declared_order.json) | `cancel.barrier_runs_in_the_declared_order` — A user cancel fences, drains, releases and only then marks terminal — and re-enters cleanly on the tick where the plane has not caught up yet, which is why every step in it tolerates being repeated. |
| [`recovery_a_batch_workload_is_not_recovered.json`](recovery_a_batch_workload_is_not_recovered.json) | `recovery.a_batch_workload_is_not_recovered` — Recovery is for interactive sessions; a batch job that died has ended. |
| [`recovery_a_cancelled_session_is_not_resurrected.json`](recovery_a_cancelled_session_is_not_resurrected.json) | `recovery.a_cancelled_session_is_not_resurrected` — Losing a node while being torn down must not resubmit the work. |
| [`recovery_a_lost_session_moves_to_a_new_epoch.json`](recovery_a_lost_session_moves_to_a_new_epoch.json) | `recovery.a_lost_session_moves_to_a_new_epoch` — A node failure loses the kernel's memory and says so. |
| [`recovery_is_bounded_rather_than_endless.json`](recovery_is_bounded_rather_than_endless.json) | `recovery.is_bounded_rather_than_endless` — A node that kills every worker must not be resubmitted to forever. |
