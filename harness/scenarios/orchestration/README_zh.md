# 编排场景

[English](README.md)

这些场景跑的是**真的** `Reconciler`，对面是一个被脚本化的 backend，而不是 reconciler 的一个模型。它们值得存在的全部理由就在这里：一个把 reconciler 的规则重新实现一遍再去检查它的 harness，会在模型与代码共有的每一个缺陷上保持绿色——而那正是所有"把问题理解错了"（而非"把方案敲错了"）所产生的缺陷。

backend 之所以是脚本化的，是因为 `SubmitResult` 与 `Observation` **就是**那条边界的契约：四种情形加一个 phase。脚本里不做任何决定；做决定是 reconciler 的事，而看着它做决定正是重点。

**声明拒绝的用例，在运行成功时判负。** 这个子系统一半的工作就是拒绝——过期的 epoch、重复提交、不得把已取消的东西复活——而一个以"没抛异常"计分的场景，对这些一样都没测。这条性质是被检查过的而不是假定的：把 `recovery.is_bounded_rather_than_endless` 改成每次尝试都成功，它就会变红。

## 文件

| 文件 | 场景 |
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
