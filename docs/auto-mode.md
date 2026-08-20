# Auto Mode product and terminal-state contract

Status: **Stage 0 Auto Mode contract; Stage 1 trusted delivery, Stage 2
durable Auto Run storage, and Stage 3 Scientific Reviewer shadow implemented
as default-off opt-in**.

This document freezes the product truth that Stages 1–12 must implement. It is
normative for the new Auto Mode states, but it is not evidence that later
gates exist today. Stage 1 consumes only `stage1_trusted_delivery`. Stage 2
consumes only `stage2_auto_run_storage`. Stage 3 consumes only
`stage3_scientific_review_shadow` and records a shadow judgment without
gating completion. Stage 4 consumes only `stage4_review_completion_gate` and
does gate completion: with it on and `result_review_mode` not `off`, the
candidate is streamed provisional and its exact canonical assistant row is
committed with `review_status=candidate` before the verdict. It cannot look
Verified until exact CAS promotion succeeds. The gate calls Stage 5 when
`result_review_mode=auto_fix` and `stage5_auto_repair` is on: a repaired
candidate is returned as the answer to deliver, not merely as a reason to
withhold Verified. A repaired answer without a newly persisted independent
pass remains explicitly unverified. Later stages consume only their own flags
and are described in their own sections.

The existing `review:auto:<frame>` setting remains the old single-call,
post-completion Reviewer. It records an ordinary review step after the final
answer and does not gate completion. It must not be described as the Auto Mode
defined here.

## Stage 1 delivery boundary

`OPENAI4S_STAGE1_TRUSTED_DELIVERY=1` enables three prerequisites without
enabling Auto Mode:

- completion Artifact links name exact immutable versions through the canonical
  `/api/v1/artifacts/versions/{version_id}` helper; frozen bytes and scope/checksum
  metadata are verified before the assistant message and delivery manifest
  commit, and the link-bearing event is emitted afterwards with a stable
  `delivery_id`;
- an identical current-head checksum reuses the version but appends a durable,
  scoped capture observation for the new producing Cell and lineage; Stage 1
  observations remain local-only and are not yet serialized by Session
  package, share, or export;
- a delegated Web child is captured at its own Cell boundary. Because siblings
  share the session workspace, asynchronous and fanout delegation are refused
  before budget reservation while this flag is enabled; a directory snapshot
  cannot truthfully distinguish concurrent writers. Single synchronous and
  nested synchronous delegation remain available, and flag-off behavior is
  unchanged;
- the local `standard` Python/R manifest check is shown at startup and in the
  workbench. A missing or unavailable profile blocks the first routed Code
  Cell before pending Cell state or execution; control-only/finalize turns may
  still complete without starting a kernel. Approved or resumed scientific
  plans are the deliberate pre-CAS exception. Remediation is explicit through
  `openai4s env plan python r --repair` followed by
  `openai4s env apply python r --repair`; a failed environment apply never
  moves that environment's active generation pointer.

The flag remains off by default. It does not select `review_only`, `auto_fix`,
or `auto_review`; create a candidate, finding, or permission decision; or alter
the non-negotiable Reviewer/Repair/Guardian safety invariants below.

## Product modes

Result review and permission review are separate control planes:

| Setting | Closed vocabulary | Stage 0 default | Contract |
| --- | --- | --- | --- |
| `result_review_mode` | `off`, `review_only`, `auto_fix` | `off` | Whether a candidate is scientifically reviewed and whether findings may start a repair loop. |
| `approvals_reviewer` | `user`, `auto_review` | `user` | Who resolves actions that the deterministic permission policy classified as `ask`. It cannot override a hard deny. |
| Auto Mode preset | `off`, `on`/`autonomous` | `off` | The future `autonomous` preset always normalizes to `auto_fix` + `auto_review` and the bounded budget ceiling below. It is not a third independent switch, is not full access, and never weakens another safety layer. |

At Stage 0, `Config.auto_mode` only parses these values. No runtime component
consumes them. Unknown boolean spellings and unknown enum values reject
configuration; an explicitly empty value also rejects rather than meaning off.
When the preset is on, contradictory explicit sub-mode values are normalized to
`auto_fix` and `auto_review`. When it is off, the two sub-modes remain
independently selectable.

## Scope and precedence

The future durable resolver must apply one selection source, in this order:

1. An imported/quarantined session forces the preset off,
   `result_review_mode=off`, and `approvals_reviewer=user` until an explicit
   fresh continuation is created. Historical labels remain read-only facts.
2. An explicit frame selection overrides its project's selection.
3. An explicit project selection overrides an explicitly configured deployment
   default.
4. An explicitly configured deployment value overrides legacy compatibility.
5. Only when no new selection exists, the old `review:auto:{root_frame_id}`
   boolean maps to `result_review_mode=review_only`. It can never select
   `auto_fix`, `approvals_reviewer=auto_review`, or the Auto Mode preset.
6. With no source, the built-in values are preset off, result review off, and
   user approval.

An unset deployment value is not an explicit `off`; this distinction prevents
the built-in default from erasing a legacy frame preference during migration.
Sandbox, egress, biosecurity, secret/credential, cost, and deterministic
permission policy are outside this selection order and always take precedence.

## Frozen bounded budgets

These are hard ceilings for the autonomous preset. A deployment default and,
later, project/frame policy may tighten the monotonic limits but cannot loosen
them. The rolling circuit window stays fixed at 50 so changing its denominator
cannot ambiguously weaken the rate threshold. All are reserved and inert in
Stage 0.

| Limit | Default ceiling | Reserved environment variable |
| --- | ---: | --- |
| Reviewer attempts per candidate, including at most one transient retry | 2 | `OPENAI4S_AUTO_MAX_REVIEW_ROUNDS` |
| Repair rounds | 2 | `OPENAI4S_AUTO_MAX_REPAIR_ROUNDS` |
| Repair Agent turns per round | 12 | `OPENAI4S_AUTO_REPAIR_TURNS_PER_ROUND` |
| Additional Cells | 30 | `OPENAI4S_AUTO_MAX_EXTRA_CELLS` |
| Auto Run wall time | 900 seconds | `OPENAI4S_AUTO_WALL_TIME_S` |
| Additional token budget relative to the initial turn | 1.5× | `OPENAI4S_AUTO_EXTRA_TOKEN_MULTIPLIER` |
| Repeated unchanged finding | 2 | `OPENAI4S_AUTO_REPEATED_FINDING_LIMIT` |
| Same action digest without a durable delta | 3 | `OPENAI4S_AUTO_SAME_ACTION_NO_DELTA_LIMIT` |
| Turns without Artifact/Plan/Evidence progress | 5 | `OPENAI4S_AUTO_NO_PROGRESS_TURN_LIMIT` |
| Guardian decision timeout | 90 seconds | `OPENAI4S_AUTO_GUARDIAN_TIMEOUT_S` |
| Consecutive Guardian denials before circuit-open | 3 | `OPENAI4S_AUTO_GUARDIAN_CONSECUTIVE_DENIAL_LIMIT` |
| Guardian rolling circuit window | 50 decisions | `OPENAI4S_AUTO_GUARDIAN_WINDOW_SIZE` |
| Denials in that window before circuit-open | 10 | `OPENAI4S_AUTO_GUARDIAN_WINDOW_DENIAL_LIMIT` |

A limit is checked before admitting the next action and is recorded in the Auto
Run. Reaching a token, cost, time, turn, cell, review, or repair hard limit
forbids new autonomous actions. With a durable `issues` verdict and unresolved
findings, a review/repair limit stops as
`completed_with_issues(stop_reason=budget_exhausted)`; exhausted Reviewer
availability attempts stop as `review_unavailable`; a Guardian timeout stops as
`blocked_by_guardian`. A limit outside those state-specific terminals commits
`terminal_reason=budget_exhausted` with **Paused · Budget exhausted** user
truth. No limit silently replenishes itself on reopen. Repeated-finding,
same-action/no-delta, and no-progress limits open a durable no-progress circuit.
With unresolved review findings this stops as
`completed_with_issues(stop_reason=loop_detected)`; a Guardian-denial loop
stops as `blocked_by_guardian(stop_reason=loop_detected)`; a loop before either
domain has a terminal fact uses the existing
`terminal_reason=loop_detected`. Every projection names **Loop detected**, and
none silently resubmits the same finding or action. Guardian timeout fails the
proposed action closed. Either Guardian denial threshold opens its durable
denial circuit. Cancellation remains separate and Auto Mode never resumes it.

## Finding identity

A finding has two keys and they answer different questions. Its **fingerprint**
is content only -- severity, category, claim and evidence refs -- because Stage
5 compares fingerprints across repair rounds to notice that a finding did not
go away. Its **identity** (`finding_id`) is that content *within one review
run*, so two sessions that reach the same conclusion record two findings rather
than colliding on one row. `review_findings.finding_id` is globally unique, and
must stay so: session import resolves a finding's owner by id alone.

Deriving the identity from content alone made the two keys the same key. The
second session to reach a given conclusion failed its `complete_review` insert
on the primary key, its Auto Run stayed in `reviewing`, and the branch refused
every later turn -- and a recurring wrong claim is precisely the finding most
likely to recur.

## State vocabulary and sole entry conditions

These names belong to one Auto Run. A run is identified by
`root_frame_id + branch_id + turn_id + execution_id`; a candidate additionally
has an immutable candidate/snapshot identity. Terminal state is a committed
fact, not text inferred from an assistant message or a transient WebSocket
event.

`candidate`

- Kind: non-terminal, provisional phase.
- Sole entry condition: a valid Engine completion and its immutable Evidence
  Snapshot, candidate payload, Artifact-version set, and `candidate_ready`
  event have committed in one durable transition for the same run identity.
- It is not entered by ordinary model prose, a normal tool result, an R Cell,
  cancellation, max-turn exhaustion, a partially written snapshot, or a UI
  receiving the last text chunk.
- User truth: **Candidate · provisional / not verified**. Its files remain
  downloadable, but neither the answer nor the files may carry a Verified
  claim.

`verified`

- Kind: successful terminal state for a review-enabled run.
- Sole entry condition: the latest immutable candidate has a durable,
  schema-valid `pass` review bound to the exact candidate and Evidence Snapshot
  hashes; the snapshot is complete; all evidence references resolve; and no
  material finding for that candidate is `open`, `claimed`, or `unaddressed`.
  The `auto_run_terminal(verified)` record commits after those facts exist.
- The answer delivered to the user must be byte-identical to the candidate
  that `pass` was bound to. A repair the delivering caller can no longer apply,
  any other drift between delivered and reviewed text, and a delivery that
  fails after the review are each disqualifying: the run takes a non-verified
  terminal rather than certifying bytes nobody reviewed.
- A durable `pass` coexisting with any such material finding is a
  review/findings integrity inconsistency. It enters the separate
  `safety_boundary`; the projector may neither silently rewrite the verdict to
  `issues` nor present the candidate as Verified or Unavailable.
- In `auto_fix`, a pass is eligible for Verified only when the frozen Reviewer
  fingerprint satisfies the configured independence policy (by default it is
  different from the main Agent fingerprint) **and** the Reviewer actor/session
  is not the producer of that candidate, its Repair Run, or any agent allowed to
  promote it. Actor/session independence is absolute and cannot be relaxed by
  configuring the same model. A model saying “verified”, a Repair Agent saying
  “fixed”, or a user accepting risk is never sufficient.
- If no Reviewer/session satisfying the frozen independence policy can be
  provisioned, no inference call is admitted and the run enters
  `review_unavailable` with reason `reviewer_independence_unavailable`. If a
  review is instead received from or persisted under a candidate producer,
  Repair actor, promotion actor, or a drifted identity/fingerprint binding, that
  is an identity-integrity `safety_boundary` failure; it is never an Unavailable
  result and can never make the candidate Verified.
- User truth: **Verified** and the exact reviewed candidate/version set. A later
  mutation creates a new candidate and removes eligibility until that candidate
  independently passes.

`completed_with_issues`

- Kind: finite, unverified terminal state.
- Sole entry condition: the latest candidate has a durable, schema-valid
  `issues` verdict with at least one unresolved finding, and either
  `review_only` forbids repair or `auto_fix` reached a hard review, repair, or
  no-progress budget. The terminal record names the reason and the preserved
  best candidate. Any checkpoint, branch, or authorized-mutation-set admission
  failure instead enters `safe_rollback_unavailable` before Repair starts; a
  sandbox/path escape remains the higher-priority `safety_boundary`.
- A Reviewer provider, timeout, or parse/schema failure does not enter this
  state; when its failure and terminal transition can commit durably, that is
  `review_unavailable`. A review-audit persistence failure or immutable
  evidence-integrity mismatch instead uses the separate safety boundary. A
  Guardian hard stop does not enter it; that is `blocked_by_guardian`.
- A persisted `pass` that conflicts with an unresolved material finding does not
  enter this state by automatic verdict coercion; it is the separate
  review/findings integrity `safety_boundary` failure.
- User truth: **Completed · unverified · N unresolved issues**, with findings
  and the preserved candidate/version set visible. “User accepted” may be
  recorded, but may not rewrite this state to Verified.

`review_unavailable`

- Kind: finite, unverified terminal state.
- Sole entry condition: result review is required for the latest candidate,
  but either no eligible Reviewer/session can be provisioned under the frozen
  independence policy (`reason=reviewer_independence_unavailable`, with no
  inference retry required); the immutable snapshot is complete and hash-valid
  but its read-only evidence adapter/coverage remains insufficient after the
  bounded attempts (`reason=evidence_incomplete`); or no admissible verdict
  exists after the bounded attempts because the Reviewer timed out, the
  provider/session failed, or its response could not be parsed and
  schema-validated as a verdict. There is at most one transient retry, and a
  tightened review-attempt budget may permit none. Each selection/attempt, the
  exact missing adapter coverage on an evidence-incomplete attempt, and the
  terminal transition must commit durably.
- This state is only for eligible-session/inference availability and
  response/adapter-coverage failures over a complete, hash-valid snapshot.
  Failure to persist a required review/audit record; failure to construct or
  freeze a complete immutable Evidence Snapshot; a structural or
  candidate/evidence/snapshot hash mismatch; a missing, wrong, or unresolvable
  immutable evidence reference; a review attributed to a
  candidate/Repair/promotion actor; or drift from the frozen Reviewer
  identity/fingerprint binding; or a durable `pass` coexisting with an
  unresolved material finding cannot enter `review_unavailable`. Those are
  integrity failures and use the existing failure terminal with
  `terminal_reason=safety_boundary`, outside the five Auto Mode states.
- Missing adapter coverage can never be interpreted as a pass or Verified. A
  retry may add the required read-only representation or adapter output, but it
  cannot invent evidence, mutate the snapshot, or silently omit the unsupported
  material.
- User truth: **Unavailable · not verified**. Candidate artifacts remain
  accessible and the precise unavailable reason is shown without implying the
  scientific result was rejected.

`blocked_by_guardian`

- Kind: safety terminal state for an autonomous run.
- Sole entry condition: deterministic policy classified an exact action as
  `ask`, the action was not executed, and either a durable Guardian assessment
  or a durable Guardian-failure record committed with the terminal transition.
  The stopping result is a non-recoverable denial/Critical assessment, timeout,
  invalid output, lost session, or denial/infrastructure circuit breaker. A
  single recoverable denial may instead return a rationale to the main Agent
  for a materially safer replan; it is terminal only when the Guardian/breaker
  says the run must stop or no safe continuation remains. If the assessment or
  failure record cannot be persisted, this state is ineligible and the action
  fails closed at the separate safety boundary.
- User truth: **Blocked · Guardian**, naming the safe reason category and the
  unexecuted action class without exposing secrets. It never claims that the
  refused side effect occurred.

Deterministic controls always run before Guardian, but the subsystem that
stopped an action does not by itself choose the terminal label. The committed
reason does. These non-Guardian terminal truths remain outside the five Auto
Mode states:

| Durable terminal reason | Sole trigger and user truth |
| --- | --- |
| `policy_requires_explicit_setup` | A dangerous/unknown tool, non-canonicalizable target, or deterministic policy conflict refused the unexecuted action and its refusal audit committed. UI: **Blocked · Policy requires explicit setup**. A plain egress allowlist refusal may use this reason; it is not a Guardian verdict. |
| `budget_exhausted` | A token, cost, time, turn, cell, or other hard budget denied admission of the next action outside a state-specific result-review or Guardian terminal. UI: **Paused · Budget exhausted**, naming the exhausted counter without silently replenishing it. An open-finding review/repair stop instead uses `completed_with_issues(stop_reason=budget_exhausted)`. |
| `safe_rollback_unavailable` | Auto Repair could not commit its pre-repair checkpoint, encountered a branch conflict, or could not prove that the proposed repair remains inside its authorized mutation set. Repair never starts. UI: **Blocked · Safe rollback unavailable**. A real sandbox/path escape is the higher-priority safety boundary below. |
| `outcome_unknown` | An external write or remote side effect may have committed, bounded readback/reconciliation still cannot determine `output_committed`, and the action therefore cannot be safely retried. UI: **Needs review · Outcome unknown**. |
| `loop_detected` | Repeated finding/action/no-delta behavior reached its durable circuit outside a result-review or Guardian terminal. UI: **Paused/Blocked · Loop detected**. When a review finding or Guardian denial owns the loop, the corresponding five-state terminal carries `stop_reason=loop_detected` instead. |
| `safety_boundary` | A secret/credential probe or sensitive exfiltration, sandbox/path escape, failure to persist a required audit, immutable snapshot/reference integrity failure, or exact-action/review digest mismatch occurred. UI: **Failed · Safety boundary**. |

Egress, biosecurity, credential, cost, and sandbox controls all retain priority
over model decisions, but their durable reason must select the truthful row
above instead of being labelled from the subsystem name. In particular,
permission/action audit failure and exact-action mismatch are never Guardian
decisions; result-review audit or immutable-evidence integrity failure is never
review unavailability. User cancellation and ordinary Agent failure also keep
their existing meanings. None of these terminals may be remapped to one of the
five states merely to make an Auto Run look complete.

## Durable sources and projection truth

Stages 2–7 will make the following records authoritative. Until those stages
land, every row in this table is **planned and absent**.

| Fact | Durable source required before the fact may be shown |
| --- | --- |
| Candidate identity and phase | `auto_mode_runs` candidate pointer plus immutable Evidence Snapshot and committed `candidate_ready` event. |
| Verified / issues / unavailable result | `review_runs`, `review_findings`, snapshot/evidence hashes, and the committed `auto_run_terminal` transition. |
| Repair claim | `repair_runs`, exact before/after Artifact versions, execution ledger, and a later independent review; a repair row alone never proves success. |
| Guardian decision | `permission_review_assessments` bound to the existing `ask` decision, exact action digest, policy/prompt/model versions, and the permission/action audit transaction. If that audit cannot commit, no Guardian fact is projected; the action fails closed as a safety-boundary failure. |
| Final Auto Run status | `auto_mode_runs` plus its idempotent terminal event, tied to the same run and branch identities. |

SQLite is the source of truth. WebSocket events are notification hints. An
assistant message, step title, badge cached in browser memory, or model-produced
JSON is never enough to reconstruct a status.

Provider/session timeout and parse/schema failures may project Unavailable only
when their required failure records and terminal transition committed. If a
required audit row is missing, references a different candidate/version, fails
immutable-hash validation, or cannot be reconciled after restart, projections
fail closed to the separate safety-boundary failure, never Unavailable or
Verified.

Failure to provision a Reviewer/session that satisfies the run's frozen
independence policy follows the same durable Unavailable projection with
`reason=reviewer_independence_unavailable`; it is a pre-inference availability
failure, so retrying a model response cannot cure it. By contrast, a review row
whose actor produced, repaired, or may promote the candidate, or whose frozen
identity/fingerprint binding has drifted, is identity-integrity evidence of an
invalid transition and projects the separate safety-boundary failure.

A complete, hash-valid snapshot whose read-only evidence adapter still cannot
cover required material after its bounded attempts projects Unavailable with
`reason=evidence_incomplete`. Every attempt durably names the missing coverage;
neither the model nor the projector may fill the gap from prose. Failure to
construct/freeze the snapshot itself, or any structural, hash, or immutable-ref
integrity failure, instead projects the separate safety-boundary failure.

Projection also validates the review/findings set as one fact. A durable `pass`
with any `open`, `claimed`, or `unaddressed` material finding is contradictory
integrity evidence and projects the separate safety-boundary failure. It is not
repaired by trusting one row over another, coercing the verdict, or hiding the
finding.

## Canonical planned API and event names

Stage 0 reserves, but does not implement, exactly these versioned routes:

- `GET/PATCH /api/v1/frames/{fid}/auto-mode`
- `GET /api/v1/frames/{fid}/auto-audits?subject_kind=&before=&limit=`

There is no unversioned or alternate route alias. The new event types are
`auto_run_started`, `candidate_ready`, `auto_audit_started`,
`auto_audit_completed`, `repair_started`, `repair_completed`, and
`auto_run_terminal`. Result and permission audits use the same two canonical
`auto_audit_*` types with `subject_kind=result_review` or
`subject_kind=permission_review`; review/Guardian-specific phrases are not
additional wire or storage event names. The existing `permission_resolved`
event remains the sole permission-resolution type and later gains
`resolution_actor` and `audit_id` after the durable transaction commits.

The audit subject has two orthogonal, closed-vocabulary fields. `subject_kind`
names the control plane (`result_review` or `permission_review`), while
`subject_entity_kind` names the exact entity under assessment. Their only
valid pairings are `result_review` + `candidate_evidence_snapshot` and
`permission_review` + `approval_action`. Entity names never appear in
`subject_kind`, and neither field creates another event alias.

Every `auto_audit_started` and matching `auto_audit_completed` event carries
the same `audit_id`, subject fields, and `audit_request_digest`. That digest is
the canonical digest of the immutable audit request and its subject bindings
only; it never incorporates or stands in for the result. Completion separately
carries the canonical assessment plus `assessment_digest`. The assessment
digest binds the request digest, subject fields, attempt, verdict/decision,
findings, risk, authorization, outcome, rationale, failure, and completion
bookkeeping such as durability and retry state. The two digests have different
meanings and must never be reused, substituted, or mixed; a completion whose
assessment does not rehash to its `assessment_digest` fails closed.

Every durable transition has one event id. WebSocket delivery is a projection
of that committed fact, never a second event and never the authority for
recovery.

## Reopen, share, export, and import

- Reopen/REST and live WebSocket views use one projection from the durable
  sources above. Refreshing cannot promote a provisional candidate or erase an
  unavailable/blocked reason.
- A read-only share projects the same terminal label, candidate/version set,
  findings, and sanitized evidence references. It omits secrets, hidden model
  context, and reusable permission material. Missing durable proof downgrades
  the share to Unverified; message prose is not used as a substitute.
- Session export carries the run identity, terminal record, candidate and
  snapshot digests, findings, repair/version references, and sanitized Guardian
  audit references needed to reproduce the projection. Export does not convert
  internal consistency into authorship or scientific truth.
- Import preserves a verifiable historical label as read-only provenance, but
  remains quarantined. It forces permission automation off, imports no reusable
  capability or standing grant, starts no Kernel/Reviewer/Repair process, and
  requires an explicit fresh recovery before new execution. An unverifiable
  imported status is shown as Unverified, never repaired by trusting its text.

## Recovery contract

| Last durable state | Allowed recovery | Forbidden recovery |
| --- | --- | --- |
| `candidate` | Resume/retry review from the same immutable Evidence Snapshot, or explicitly start a new candidate from a safe checkpoint. | Re-run already committed side effects merely because a response was lost; infer pass from old prose. |
| `verified` | Rebuild the projection from durable review evidence. Any requested change starts a new candidate/review identity. | Mutate the reviewed versions in place; let recovery or the Repair Agent self-certify the change. |
| `completed_with_issues` | Preserve findings and best versions; an explicit continuation may create a new Repair Run from a verified checkpoint and must be independently re-reviewed. | Hide findings, silently spend a fresh budget on reopen, or relabel user acceptance as Verified. |
| `review_unavailable` | Explicitly retry review against the unchanged snapshot under the bounded retry/restart policy; configure an eligible independent Reviewer before a fresh continuation for `reviewer_independence_unavailable`; provide the missing read-only adapter coverage without mutating the snapshot for `evidence_incomplete`; or create a fresh run. | Treat provider/timeout/parse/independence/coverage failure as pass; publish a Verified badge without a later admissible verdict; invent or silently omit missing coverage; use this state to conceal an audit-persistence, snapshot-construction/hash/ref, self-review, or frozen-identity integrity failure. |
| `blocked_by_guardian` | Show the denial and require a fresh continuation. A user/administrator may establish an exact, narrow policy before that continuation. | Replay the refused action; reuse an old one-shot grant; let Guardian create a standing allow; weaken sandbox/egress/secret/biosecurity/cost policy. |
| `policy_requires_explicit_setup` | A user/administrator may establish an exact, narrow policy or supported canonical target, then create a fresh continuation whose action is newly reviewed and hashed. | Rename, split, or otherwise work around the refusal; attribute it to Guardian; execute the refused action under an old decision. |
| `budget_exhausted` | Preserve the run and its exact counters. Only an explicit fresh continuation under an authorized budget may admit more work. | Refill a budget on restart/reopen, hide the exhausted counter, or continue in the same run. |
| `safe_rollback_unavailable` | Establish a valid checkpoint, resolve the branch conflict, and start a new Repair Run from that proven state. | Let Repair touch the formal workspace without rollback, widen its mutation set, or treat a path escape as an ordinary conflict. |
| `outcome_unknown` | Continue bounded readback/reconciliation or hand the durable evidence to a user/operator; a later confirmed outcome is appended, never guessed. | Blindly retry, claim success/failure without readback, or reuse the prior one-shot authorization for a second effect. |
| `loop_detected` | Require an explicit materially different continuation; preserve the repeated finding/action fingerprints and counters. | Resubmit the same finding/action, reset the circuit on reopen, or fragment a denied action to evade the fingerprint. |
| Existing failure with `terminal_reason=safety_boundary` | Repair audit persistence, immutable-evidence construction, Reviewer identity binding, or the violated hard boundary, then explicitly create a fresh continuation from a verified checkpoint. Any proposed action is newly authorized and hashed. | Attribute the integrity/hard-boundary failure to Guardian/Reviewer unavailability; replay a refused/mismatched action; infer an audit record that never committed; reuse an evidence snapshot whose immutable hash failed, a self-review whose actor was ineligible, or a `pass` that still has an unresolved material finding. |

All recovery transitions are idempotent. After daemon restart, committed
external effects are reconciled/read back rather than blindly retried. A
one-shot action capability is atomically consumed and bound to its exact action
digest, run context, generation, expiry, and `max_uses=1`.

## Non-negotiable invariants

- Scientific Reviewer is read-only with respect to the formal workspace and
  formal Artifacts. Verification computation occurs only in isolated scratch.
- Repair Agent and every candidate-producing actor/session cannot review or
  promote that candidate, regardless of whether the configured model matches.
- In `auto_fix`, inability to provision a session satisfying the frozen
  independence policy terminates as
  `review_unavailable(reason=reviewer_independence_unavailable)` before
  inference. A review actually attributed to a producer/Repair/promotion actor,
  or whose frozen identity/fingerprint binding drifted, is an identity-integrity
  `safety_boundary` failure and cannot be relabelled Unavailable.
- Permission Guardian cannot create conversation/project/global standing
  allows. Its automatic allow is once-only and exact-action-bound.
- Guardian cannot receive authority from the main Agent, tool output, Web
  content, a Skill, or its own rationale.
- Reviewer provider/session timeout and parse/schema failure may become the
  durable `review_unavailable` terminal after the bounded attempts; they never
  count as pass.
- On a complete, hash-valid snapshot, insufficient read-only evidence-adapter
  coverage is durably recorded on every bounded attempt and terminates as
  `review_unavailable(reason=evidence_incomplete)`; it can never count as pass or
  Verified.
- A durable `pass` cannot coexist with an `open`, `claimed`, or `unaddressed`
  material finding. That contradiction is a review/findings integrity
  `safety_boundary`; it is never coerced to `issues`, Verified, or Unavailable.
- Failure to persist a required audit; failure to construct or freeze complete
  immutable evidence; any snapshot structure, candidate/evidence/snapshot hash,
  immutable-reference, or exact-action hash mismatch; secret/sensitive-egress
  violation; and sandbox/path escape close to the separate safety boundary.
  They are neither `review_unavailable` nor a Guardian decision and do not
  belong to the five Auto Mode states.
- Existing sandbox, egress, biosecurity, credential/secret, cost, and
  deterministic policy controls take precedence over any model decision. Their
  durable reason truthfully distinguishes policy setup, budget exhaustion,
  safe-rollback failure, unknown external outcome, loop detection, and a hard
  safety boundary; none is relabelled as a Guardian verdict.
- A Guardian refusal may lead only to a materially safer plan, never a renamed
  or fragmented workaround whose purpose is to bypass the refusal.
- Review, repair, permission, cell, token, cost, time, and no-progress budgets
  terminate finitely and durably; no loop is allowed to run silently forever.
- Auto Repair never starts without a committed safe checkpoint and bounded
  mutation set. An unresolved branch/checkpoint problem blocks repair; a
  sandbox/path escape fails at the higher-priority safety boundary.
- An external side effect whose committed outcome remains unknown after bounded
  readback is never blindly retried and never reported as known; it terminates
  as `outcome_unknown` until durable reconciliation or explicit review.
- Stop/Cancel targets the exact execution owner/lease. Auto Mode never resumes
  itself after a user cancellation.

See [Architecture](architecture.md), [Configuration](configuration.md), and
the [Web App API contract](webapp-api.md) for the Stage 1 implementation
boundary.
