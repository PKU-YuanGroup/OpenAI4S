# Baseline scenarios

[中文说明](README_zh.md)

These scenarios are the required, deterministic, offline `tier:pr` Harness gate. Each file is validated against [`../../schema.py`](../../schema.py). Generic scenarios run through [`../../runner.py`](../../runner.py); `surface: auto_mode_contract` scenarios run through [`../../auto_mode_contract.py`](../../auto_mode_contract.py), while `surface: auto_mode_terminal_contract` scenarios run through [`../../auto_mode_terminal_contract.py`](../../auto_mode_terminal_contract.py). Both Auto Mode adapters are production-independent proposed contracts. Every run has to end where the file says it ends and emit its declared events in order. Where a generic scenario declares `script_consumed`, its scripted model sequence must be used up as well.

## Files

| File | Responsibility |
| --- | --- |
| [`auto_mode_budget_exhausted.json`](auto_mode_budget_exhausted.json) | Stops before admission when the exact run budget is exhausted; the same run cannot silently refill or resume. |
| [`auto_mode_completed_with_issues.json`](auto_mode_completed_with_issues.json) | Keeps `candidate` non-terminal and pins a structured open material finding after the Auto Fix budget is exhausted. |
| [`auto_mode_guardian_action_hash_mismatch.json`](auto_mode_guardian_action_hash_mismatch.json) | The adapter canonicalizes and rehashes an actually mutated runtime action, then fails at the independent `safety_boundary`; both computed hashes remain in the trace. |
| [`auto_mode_guardian_allow_once_replay.json`](auto_mode_guardian_allow_once_replay.json) | Atomically consumes a capability bound to one exact action, then rejects replay of the already-consumed capability at the safety boundary. |
| [`auto_mode_guardian_allow_once_variant.json`](auto_mode_guardian_allow_once_variant.json) | Rejects and burns a one-shot capability when its first use changes the exact action digest, so the variant cannot execute or reuse the token. |
| [`auto_mode_guardian_audit_failure.json`](auto_mode_guardian_audit_failure.json) | An audit append failure cannot authorize an action or masquerade as a Guardian deny; it fails at `safety_boundary`. |
| [`auto_mode_guardian_denial_circuit_consecutive.json`](auto_mode_guardian_denial_circuit_consecutive.json) | Opens only the denial circuit when the current durable denial is the third consecutive denial. |
| [`auto_mode_guardian_denial_circuit_window.json`](auto_mode_guardian_denial_circuit_window.json) | Opens only the denial circuit when the current durable denial becomes the tenth denial in the ordered latest-50 window. |
| [`auto_mode_guardian_denied.json`](auto_mode_guardian_denied.json) | Pins an explicit denial to one exact SHA-256 action digest, without a one-shot capability, standing allow, or premature denial circuit; `no_safe_continuation` terminates the current run. |
| [`auto_mode_guardian_parse_failure.json`](auto_mode_guardian_parse_failure.json) | An unparseable Guardian decision fails closed and never emits `action_authorized`. |
| [`auto_mode_guardian_timeout.json`](auto_mode_guardian_timeout.json) | Two Guardian timeouts consume the bounded two-attempt budget, then record `decision=unavailable` and open only the infrastructure breaker without executing the action. |
| [`auto_mode_guardian_timeout_then_deny.json`](auto_mode_guardian_timeout_then_deny.json) | One timeout schedules exactly one retry with both breakers closed; the second attempt records a non-fallback durable denial, keeps both breakers closed, and terminates on `no_safe_continuation`. |
| [`auto_mode_loop_detected.json`](auto_mode_loop_detected.json) | Opens the general no-progress circuit at its exact threshold and forbids automatic resubmission; Reviewer- or Guardian-owned loops route to their owning terminal instead. |
| [`auto_mode_outcome_unknown.json`](auto_mode_outcome_unknown.json) | Preserves uncertainty after bounded readback cannot reconcile a dispatched external side effect; only reconciliation or operator review may continue, never a blind action retry. |
| [`auto_mode_policy_requires_explicit_setup.json`](auto_mode_policy_requires_explicit_setup.json) | Refuses a policy prerequisite durably before execution and without invoking Guardian as a workaround. |
| [`auto_mode_review_audit_failure.json`](auto_mode_review_audit_failure.json) | A review audit that cannot persist is an independent safety-boundary failure, not a pass or ordinary review result. |
| [`auto_mode_review_evidence_hash_mismatch.json`](auto_mode_review_evidence_hash_mismatch.json) | The adapter canonicalizes and rehashes an actually mutated complete evidence snapshot, then fails at the safety boundary with both computed hashes recorded. |
| [`auto_mode_review_only_completed_with_issues.json`](auto_mode_review_only_completed_with_issues.json) | Preserves review-only material findings as `completed_with_issues` without starting Repair or claiming verification. |
| [`auto_mode_review_parse_failure.json`](auto_mode_review_parse_failure.json) | Two unparseable Scientific Reviewer verdicts exhaust the two-attempt budget and end as recoverable `review_unavailable`, never a false pass. |
| [`auto_mode_review_timeout.json`](auto_mode_review_timeout.json) | Two Scientific Reviewer timeouts leave the candidate non-terminal and end as recoverable `review_unavailable`. |
| [`auto_mode_review_timeout_then_pass.json`](auto_mode_review_timeout_then_pass.json) | One Reviewer timeout schedules exactly one retry; a valid independent second review can then produce `verified`. |
| [`auto_mode_safe_rollback_unavailable.json`](auto_mode_safe_rollback_unavailable.json) | Blocks Auto Repair before any formal-workspace mutation when rollback admission cannot be proven. |
| [`auto_mode_verified.json`](auto_mode_verified.json) | Pins the proposed successful Scientific Reviewer path and unique `verified` terminal. |
| [`scheduled_timeout.json`](scheduled_timeout.json) | Fires a retryable timeout the first time the run reaches the `before_model` fault point. One model attempt, a `model_error` terminal reason, and an explicit `fault_injected` event in the trace; the scripted response sitting behind the fault is deliberately left unconsumed. |
| [`single_response_submitted.json`](single_response_submitted.json) | The straight path: one successful scripted response, one model attempt, a `submitted` terminal reason, the lifecycle events in order, and nothing left in the script. |
| [`two_response_sequence.json`](two_response_sequence.json) | Two scripted responses, so the loop runs twice. Checks that both attempts stay ordered and that only the second one carries the run to its final `submitted` terminal event. |

Do not loosen an expectation just to make a drifting run pass. Change a scenario when the intended contract changes, and review the trace that comes out.

The Auto Mode scenarios do **not** prove that production emits these states;
their first event records `production_state_machine: false`. They freeze the
Stage 0 contract that later implementation stages must satisfy. Evidence
snapshots must be complete, frozen, and resolvable; a pass cannot carry an open
material finding, and `completed_with_issues` cannot precede Auto Fix budget
exhaustion. Guardian scenarios begin only from a deterministic `ask`; a hard
policy boundary must not be relabelled as a Guardian decision. Their canonical
fixtures are hashed inside the adapter, request and completion assessments have
distinct digests, and all digests are checked against an independent reviewed
golden; no
caller-supplied observed digest is trusted.

The five non-Guardian terminal scenarios have their own closed condition
schemas and independent golden. They deliberately set `auto_user_state: null`
and carry no Reviewer or Guardian audit identity. Only `outcome_unknown` is
recoverable, and that recovery is reconciliation or operator review rather
than automatic replay of the uncertain side effect.

One ordinary denial is not a circuit: opening the denial circuit requires three
consecutive denials or 10 denials in the latest 50 decisions.
