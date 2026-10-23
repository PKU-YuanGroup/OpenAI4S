# Delegation continue — admission contract and acceptance preparation

[中文](delegation-continue-design_zh.md)

Status: **D1 preparation only — not implemented in this version**. This
document fixes the proposed read-only continuability projection, the
identity-bound admission contract, the execution-coordinator/capture
boundaries, and the future acceptance cases for continuing a finished or
stopped delegated child. It adds no route, response field, UI control,
configuration switch or production code, changes no default, and **this version
does not implement any part of it**. The cases below are requirements for a
later implementation, not test results, and the existing synchronous
`POST …/continue` keeps its present meaning.

## Current boundary

[`DelegationRunner.continue_child`](../openai4s/agent/delegation.py) already
exists and is durable rather than an automatic restart. Restore never calls it:
a daemon restart marks every `pending`/`running` child `stopped` with
`stop_reason` `daemon_restart`, discards queued steering, and requires an
explicit continue before any new attempt runs. The method creates the next
attempt from the stored request payload, runs it on the caller's thread when
`wait=True` (the default), and returns the attempt's run envelope — not a child.

Request identity `(root_frame_id, parent_action_group_id, native_call_id)` plus
`request_sha256`, and attempt identity `(request_id, attempt_no)`, are already
persisted by the numbered Store migration in `delegation_requests` /
`delegation_attempts`.
[`DelegationProjectionRepository.continue_request`](../openai4s/storage/delegation.py)
refuses while the latest attempt is `pending`/`running`
(`DelegationRequestConflict`), enforces the session spawn cap, and records the
new child with `previous_attempt_id`. A continuation therefore always has a
request and attempt identity to bind to; what does not exist is an identity for
*the continue operation itself*.

| Surface | Current behavior to preserve or distinguish |
| --- | --- |
| Continue endpoint | `POST /api/v1/frames/{fid}/delegations/{child_id}/continue` → `SessionRunner.continue_delegation_child`. 404 for an unknown child, 409 `delegation_record_stale` when no live runner owns the root frame, 409 `delegation_conflict` for `DelegationConflictError`, 409 `delegation_error` otherwise. |
| Response | The stored child through `project_browser_child` — the same shape `GET …/delegations` returns — not the run envelope. A child with no stored row is cut down to the browser allowlist. |
| Blocking | `wait=True` holds the HTTP handler for the whole child run. There is no asynchronous accept and no 202 on this route. |
| Durable request/attempt | `delegation_requests` and `delegation_attempts`; attempt states are `pending/running/done/failed/stopped`. Duplicate *delegate* calls are already deduplicated by request identity. |
| Attempt creation | Only `continue_request` mints an attempt, one per accepted call; restore never does. A live latest attempt refuses a further continue. |
| `GET …/delegations` | Read-only durable projection through `workbench_state.delegation`. Children carry `request_id` and `attempt_id`; they carry no `attempt_no`, no continuability flag and no refusal reason. |
| Timeline | `renderDelegationPanel` in [`frontend/src/features/timeline/island.ts`](../frontend/src/features/timeline/island.ts) offers stop and steer for `running`/`pending` children only. There is no continue control. |
| Trusted capture | With `cell_hooks_factory` set, `wait=False` and fanout > 1 are refused, the child's `permissions.background` is forced to `deny`, and an exclusive capture gate admits one delegated capture chain at a time. |

The CLI path has no durable store:
[`DelegationBudget.continue_request`](../openai4s/agent/delegation.py) raises
`DelegationError("continue requires durable delegation storage")` when the
budget is not bound to a root frame, store and runner instance. Any future
UI-only admission therefore applies to the Web/session path, not to every
delegation surface.

Adjacent coverage already exists and states what is true today:
[`tests/test_delegation_idempotency.py`](../tests/test_delegation_idempotency.py)
proves duplicate delegate identities create one child, a digest mismatch is 409,
and a restart does not auto-continue until `continue_child` is called explicitly;
[`tests/test_delegation_control.py`](../tests/test_delegation_control.py) proves
the route's 404/409 division and that a stale record answers 409
`delegation_record_stale` rather than 404 or a fabricated success;
[`tests/test_delegation_persistence.py`](../tests/test_delegation_persistence.py)
proves restore marks children stopped and does not create a new attempt. None of
them exercises duplicate *continue* requests, because that identity does not
exist yet.

## Continuability projection (future)

The projection answers one question from durable rows alone: could an explicit
continue be admitted right now? It must be computed from the stored request,
attempt and child rows plus the current runner lease, and it must not write,
reserve budget, spawn a child or revive a run. It is a read at a stated time,
not a liveness claim about the remote or the process.

| Proposed field | Proposed meaning |
| --- | --- |
| `root_frame_id` | The frame tree that scopes delegation, budget and visibility. |
| `child_id` | The child the user is looking at; the same id the stop/steer/continue routes take. |
| `depth`, `parent_child_id` | Tree placement. A child at `MAX_DEPTH` is a leaf; continuing it re-runs that leaf and grants it no new delegation. |
| `request_id` | The durable logical request, stable across attempts. |
| `attempt_id`, `attempt_no`, `previous_attempt_id` | The exact attempt and its predecessor — the pair a client must echo back for admission. |
| `status`, `task_status`, `stop_reason` | Durable child state, including `daemon_restart` after a restart. |
| `continuable` | `true` only when the durable record and live ownership both support a new attempt; absent evidence is `false`, never an optimistic `true`. |
| `reason` | One stable code from the table below when `continuable` is `false`; absent or `ok` when it is `true`. |
| `evaluated_at` | UTC time of the read, so a client can tell an old evaluation from a new one. Not a remote observation time. |

| Proposed reason | Evidence it would name |
| --- | --- |
| `ok` | A live runner owns the root, the latest attempt is terminal, and the session cap has room. |
| `not_found` | No child with this id in this root's durable record. |
| `no_durable_request` | The child row has no request/attempt identity (legacy or non-durable work); an attempt cannot be minted from an unrecorded request. |
| `attempt_in_flight` | The latest attempt is `pending`/`running` — the storage conflict `continue_request` already raises. |
| `root_inactive` | No live delegation runner owns the root frame; the restart case behind `delegation_record_stale`. |
| `session_cap_reached` | `spawned` has reached the session `budget_limit`; cumulative spawns are never refunded. |
| `trusted_capture_unavailable` | Capture admission/lease/gate would refuse, or the request asks for parallel work the capture ceiling forbids. |
| `evidence_unavailable` | The durable rows or the runner lease could not be read; unknown must not be rendered as continuable. |

The codes are proposed vocabulary, not an existing response. Adding them to the
GET payload requires the ordinary contract and response-schema gates, and an old
client must keep working when they are absent.

## Identity-bound idempotent admission (future)

Delegate-time identity is already idempotent; continue-time identity is not. A
future admission request must carry the client's expected
`(root_frame_id, child_id, request_id, attempt_no)` together with an
idempotency key for the operation itself. The server must then:

1. Accept at most one attempt per idempotency key. A double click, a retried
   HTTP request or two tabs submitting the same operation return the same
   accepted attempt identity rather than minting a second child row.
2. Refuse an identity that does not match the durable record — a different
   root, child or request, or an `attempt_no` older than the latest — as a 409
   carrying the current attempt identity. A stale view must not silently create
   a second attempt.
3. Keep the existing digest discipline: same key with a different digest is a
   conflict, mirroring `DelegationRequestConflict`.
4. Charge the operation one attempt and one spawn, never two; rejected
   admissions charge nothing.
5. Not raise any cap, not bypass trusted-capture admission, and not replay the
   previous attempt's in-memory state (see below).

No table or column for continue-operation identity exists today, and this
version adds none. A later implementation must decide whether the record is a
new durable row — numbering a migration — or can be derived from existing
attempt rows; that decision is out of scope here.

## Execution coordination, capture and observation timeout (future)

- **The execution coordinator.** Today the route runs
  `continue_delegation_child` synchronously on the HTTP handler thread; it is
  not submitted as a `WebExecutionCoordinator` ticket, so the per-session FIFO
  execution queue, its owner/lease binding and its exact-owner interrupt are not
  what admits it. A future asynchronous acceptance must change that shape rather
  than preserve it: the new attempt must run as a first-class execution
  submitted through
  [`WebExecutionCoordinator`](../openai4s/server/execution_coordinator.py) and
  the core
  [`SessionExecutionCoordinator`](../openai4s/execution/coordinator.py), bound
  to its own execution id, owner and lease. A stale ticket or the previous
  attempt's lease must never be reusable, and a cancelled or expired execution
  must not be revived by a continue request.
- **Trusted capture.** The capture ceiling
  (`_apply_trusted_capture_ceiling`, the `cell_hooks_factory` conditions, the
  admission/lease hooks and the exclusive gate) is a mandatory provenance
  constraint, not a caller preference. A continuation may be refused because
  capture is busy; it must never start an attempt outside capture, lower the
  child's `background: deny`, or serialize around the gate by spawning a
  parallel child.
- The synchronous route is the compatibility surface, not the future UI shape.
  A child can run for many turns, so a future entry may prefer an accepted
  (asynchronous) response that names the attempt identity. If it is added, it
  must be an additive route or an explicitly versioned shape; it must not change
  what today's `wait=True` response means.
- Storage already guarantees at most one live attempt per durable request.
  Continuation must keep using that guard rather than a UI-side check, and must
  keep the parent Cell blocked while a synchronous child runs: the capture gate
  exists because two children executing Cells cannot be attributed truthfully.
- A timeout ends the observation, not the attempt. The attempt keeps its own
  frame, execution ownership and artifact identities, and a client that stops
  waiting reconciles by reading the durable child/attempt rows. It must never
  resolve an unknown outcome by posting the continue again.
- A response lost after acceptance is an unknown outcome. Re-admission with the
  same idempotency key is the only safe retry; a fresh key is a new attempt and
  must be an explicit user decision.
- An accepted attempt that does not survive a daemon restart must be reported,
  not replayed. If a later asynchronous accept is persisted, restore must mark
  it stopped like a child row and require explicit admission; if it is not
  persisted, the accept must not claim a durability it does not have.
- Wait budgets, retry hints and any 202 shape are future implementation
  decisions. No numeric observation timeout is fixed by this document.

## Compatibility and non-goals

- **This version does not implement the contract.** No route change, no new
  field, no asynchronous accept, no UI control, no configuration switch and no
  storage migration ships here; this document is a preparation record only.
- Keep the existing route's meaning: `POST …/continue` stays explicit,
  synchronous by default, and answers with the browser child projection.
  Existing error codes `not_found`, `delegation_record_stale`,
  `delegation_conflict`, `delegation_error` and `bad_request` keep their
  statuses and semantics.
- No automatic continuation, no restart replay, no new execution system, no
  background scheduler and no daemon-level poller.
- No widening of parallelism: `FANOUT_CAP` 48, `SESSION_CAP` 1000 and
  `MAX_DEPTH` 4 are unchanged, a depth-4 child remains a leaf, and trusted
  capture keeps its single-child ceiling and its forced `background: deny`.
- No memory restoration. A continuation is a new run of the stored request in a
  new child frame: kernel state, in-flight tool calls, unsaved variables and the
  previous attempt's Artifact versions are not carried over. Older evidence
  stays historical and is never rewritten.
- No new UI entry in this version. A later entry must default off and
  be disableable independently; with the projection or route absent, the
  Timeline keeps today's stop/steer behavior and shows nothing it cannot
  support.
- No database migration, new default switch, or change to restore behavior in
  this version.

## Future local acceptance matrix

All cases below are **planned** and must be proven with a real
`DelegationRunner` and a real store/restart path — not a stub runner or a
source-string assertion. Extend the three delegation test files and the Timeline
tests; synthetic data and temporary directories only, no live model or provider.

| ID | Controlled case | Required observable result |
| --- | --- | --- |
| D01 | Two concurrent, duplicated or retried continue requests for one terminal child | Exactly one new attempt and one new child row; both responses name the same attempt identity; the session charges one spawn. |
| D02 | Continue carrying an `attempt_no` older than the latest attempt | 409 that names the current attempt; no second child row, no silent acceptance. |
| D03 | Continue for a child of another session's frame tree | The existing owner/visibility refusal (404/403 without confirming existence); zero rows and zero attempts for the foreign id. |
| D04 | Daemon restart with a live child, then a continue | Restore marks the child `stopped`/`daemon_restart` and creates no attempt; no automatic replay; the explicit continue starts exactly one new attempt afterward. |
| D05 | Continue with the root frame's runner gone | GET projection reports not continuable with `root_inactive`; POST keeps answering 409 `delegation_record_stale`, never a fabricated 200. |
| D06 | Continue while the latest attempt is still `pending`/`running` | Conflict response; the durable attempt set and child count are unchanged. |
| D07 | Continue when `spawned` equals the session cap | Refusal; budget counters and attempt history are unchanged and the spawn is not refunded twice. |
| D08 | Continue under trusted Artifact capture | `wait=False`/parallel requests are refused, `background` stays `deny`, and a capture-admission refusal reaches the caller as an understandable refusal instead of a child that bypasses capture. |
| D09 | Response lost after acceptance; client retries with the same identity; separately, the observer times out | The retry returns the accepted attempt without a second attempt; the timed-out observer does not cancel the attempt, which continues to its own terminal state. |
| D10 | Child row with no durable request identity | Projection says `no_durable_request`; no request id is invented and no attempt is created. |
| D11 | Repeated GET of the projection | Zero rows, attempts and steering writes; no budget charge; the read works with no live runner and does not revive a child. |
| D12 | A completed continuation | The new attempt has its own child frame and Artifact versions; the previous attempt's evidence is unchanged; `FANOUT_CAP`, `SESSION_CAP` and `MAX_DEPTH` are unchanged. |

Completion of this preparation means the projection, the refusal vocabulary, the
admission contract and the cases above are agreed, and existing delegation tests
still pass with no behavior change. Runtime completion requires executing
D01–D12 against the real runner, store and restart path and recording the result;
this document alone does not satisfy that later gate.
