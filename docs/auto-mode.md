# Auto Mode product and terminal-state contract

Status: **Stage 0 contract plus Stages 1–12 implemented as independent,
default-off rollout opt-ins**.

This document freezes the product truth implemented by Stages 1–12. Each stage
is an independent, default-off rollout opt-in: enabling a later stage never
implicitly enables an earlier one. Stage 1 consumes only
`stage1_trusted_delivery`. Stage 2
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
pass remains explicitly unverified. Stages 6–12 likewise consume only their
own flags and are described in their own sections; the configuration guide
lists the behavior behind every flag.

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
| Auto Mode preset | `off`, `on`/`autonomous` | `off` | The `autonomous` preset always normalizes to `auto_fix` + `auto_review` and the bounded budget ceiling below. It is not a third independent switch, is not full access, and never weakens another safety layer. |

`Config.auto_mode` parses and normalizes these values. Runtime consumption
remains behind the corresponding independent rollout flags: Stage 2 resolves
and persists the selection, Stages 3–5 apply result-review behavior, Stage 6
records shadow advice on deterministic `ask` decisions, and Stage 7 consumes
the approval reviewer for unattended enforcement. A selection alone does not
enable one of those stages. Unknown boolean spellings and unknown enum values reject
configuration; an explicitly empty value also rejects rather than meaning off.
When the preset is on, contradictory explicit sub-mode values are normalized
to `auto_fix` and `auto_review`. When it is off, the two sub-modes remain
independently selectable.

## Scope and precedence

The Stage 2 durable resolver applies one selection source, in this order:

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
cannot ambiguously weaken the rate threshold. A component consumes its budget
only when that component's rollout stage is enabled.

| Limit | Default ceiling | Environment variable |
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

Stage 7's credential-shaped file fence is one such deterministic control. It
may promote a permissive file rule to `ask`, but a headless refusal and its
audit remain attributed to policy rather than being relabelled as a Guardian
verdict. With an attached channel, the same `ask` can be reviewed by a human.

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

Stages 2–7 make the following records authoritative when their corresponding
rollout flag is enabled. A disabled stage projects no claim from its rows.

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

## Canonical API and event names

Stage 0 originally reserved exactly these versioned routes. Subsequent opt-in
stages implement them, and these remain their only canonical route names:

- `GET/PATCH /api/v1/frames/{fid}/auto-mode`
- `GET /api/v1/frames/{fid}/auto-audits?subject_kind=&before=&limit=`

There is no unversioned or alternate route alias. The new event types are
`auto_run_started`, `candidate_ready`, `auto_audit_started`,
`auto_audit_completed`, `repair_started`, `repair_completed`, and
`auto_run_terminal`. Result and permission audits use the same two canonical
`auto_audit_*` types with `subject_kind=result_review` or
`subject_kind=permission_review`; review/Guardian-specific phrases are not
additional wire or storage event names. The existing `permission_resolved`
event remains the sole permission-resolution type and carries
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

## Workbench status surface (read-only part shipped; editor not built)

This section specifies how the default workbench shows Auto Mode. The
read-only part is shipped (issue #217): the status block, the budget block
and the Audit view of sections 2, 3, 5, 6 and 7, implemented in
`frontend/src/features/automode/`. The editor of section 4 is not built:
the workbench sends no `PATCH /frames/{id}/auto-mode`, and nothing in the
block starts a review, repair, resume, approval or cancellation. The legacy
`app.js` hatch still talks only to `/frames/{id}/review-settings`.

The surface is three stacked lines at the foot of the session options menu
(`sessionOptionsMenu` in `frontend/src/features/sessions/actions.ts`), under
an "Auto Mode status" / "自动模式状态" caption. A successful
`GET /frames/{id}/auto-mode` fills all three. The lines stay separate. Their
headings do not change to match the preset.

| Line | English heading | Chinese heading | What it answers |
| --- | --- | --- | --- |
| Availability | Availability | 可用性 | Can this conversation use Auto Mode storage, and may a later editor write? |
| Saved selection | Saved selection | 已保存的选择 | Which preset and sub-modes are in force, and which source won? |
| Run | Run | 运行 | Is there an Auto Run on the active logical branch, and what state is it in? |

`schema_version` on this route is `1`. A later client that sees any other
version shows the same copy as HTTP 503 and does not guess a layout.
`last_event_id` and `last_event_ordinal` are the refresh cursor. They are not
a fourth status. `deployment` explains the deployment source. It is not a
second on/off switch.

### 1. Non-goals

| Non-goal | What this version keeps |
| --- | --- |
| No new control | No preset picker, no sub-mode picker, no clear button, and no budget editor. The block's only controls are the Audit entry (a read-only view, not a mode switch) and, after a failed read, an explicit retry that is another GET. |
| No automatic run | Reading or, later, saving a selection does not start a Reviewer, a Repair Agent, a Permission Guardian, or a model call. There is no transition route. `PATCH /frames/{id}/auto-mode` writes configuration only. |
| `review-settings` keeps its meaning | `PATCH /frames/{id}/review-settings` with `{auto_review}` still writes the old post-completion Reviewer switch. This surface does not reinterpret that switch, remove it, or rename it to Auto Mode. |

A saved `autonomous` preset is a bounded configuration (`auto_fix` plus
`auto_review` plus the hard ceilings below). The availability line and the
run line do not inherit that word as “started” or “已开启”.

### 2. Status model

Keys below are the JSON keys `AutoModeService.get` returns. Enumerations are
the closed sets in `openai4s/server/auto_mode.py` and
`openai4s/config.py`.

| Group | API key | Closed values | How the line uses it |
| --- | --- | --- | --- |
| Envelope | `schema_version` | `1` | Gate the layout. |
| Availability | `feature_enabled` | `true`, `false` | True only when `OPENAI4S_STAGE2_AUTO_RUN_STORAGE` is on. It is not the preset. |
| Availability | `writable` | `true`, `false` | True only when `feature_enabled` is true and the session is not quarantined. A later editor is offered only then. |
| Availability | `disabled_reason` | `import_quarantine`, `stage2_feature_disabled`, or JSON `null` | Null when `writable` is true. Quarantine wins over a disabled flag. There is no third reason. |
| Scope | `root_frame_id`, `branch_id` | identifiers | The GET for any frame in the conversation resolves to the root and the active logical branch. The lines show that scope. |
| Selection | `selection.preset` | `off`, `autonomous` | Saved-selection value. `autonomous` is the product preset name. |
| Selection | `selection.result_review_mode` | `off`, `review_only`, `auto_fix` | Saved-selection value. |
| Selection | `selection.approvals_reviewer` | `user`, `auto_review` | Saved-selection value. `auto_review` here is who resolves an `ask`. It is not the menu switch. |
| Selection | `selection.source` | `import_quarantine`, `frame`, `project`, `deployment_explicit`, `legacy_result_review`, `built_in_defaults` | Which row won. Precedence is the list in *Scope and precedence*. |
| Selection | `selection.explicit` | `true`, `false` | False only for `built_in_defaults`. True means some source won, including quarantine and the legacy switch. It does not by itself mean the user saved a frame override. |
| Selection | `selection.revision` | integer `≥ 0` | Frame-row revision. A later PATCH sends this, including when the winning source is not `frame`. |
| Selection | `selection.source_revision` | integer `≥ 0` | Revision of the winning row. The editor does not send it as `revision`. |
| Deployment | `deployment.explicit` | `true`, `false` | True when at least one of `OPENAI4S_AUTO_MODE`, `OPENAI4S_RESULT_REVIEW_MODE`, `OPENAI4S_APPROVALS_REVIEWER` is set in the process environment. An explicit `off` is different from an unset variable. |
| Deployment | `deployment.explicit_fields` | subset of `preset`, `result_review_mode`, `approvals_reviewer` | The fields whose variables were actually set. Forced normalization is visible on `selection`, not by inventing an entry here. |
| Ceilings | `budgets` | the thirteen ceiling fields in *Frozen bounded budgets* | Read-only deployment ceilings. The budget block labels them as ceilings. |
| Run | `run` | object or JSON `null` | Null means no public run on the active logical branch. An object is the sanitized run, plus `legacy`, and when `legacy` is false also `budget_usage` and `circuit`. |
| Cursor | `last_event_id`, `last_event_ordinal` | identifier or null; integer or null | Compared after a hint or a reconnect. Not shown as the run state. |

`run` public fields used by the run line are `status`, `user_truth`,
`terminal_reason`, `result_review_mode`, `approvals_reviewer`,
`review_round`, `repair_round`, and `unresolved_finding_count`. `run_id`,
`turn_id`, and `execution_id` stay on a secondary detail row. Digests stay
in that detail row.

The last five fields are facts about this run. The store derives them when
the run is read. They never come from `selection`:

- `result_review_mode` and `approvals_reviewer` are the values frozen when
  the run started. `result_review_mode` is the run's mode. It is sent only
  when the frozen selection does not name a different mode. The permission
  gate resolves the conversation's current selection for each `ask`, so a
  reviewer saved after the run started appears on the saved-selection line,
  not here.
- `review_round` is the durable `round_index` of the latest result review in
  the visible event history. It counts from 0, like the `review_round` of a
  live `auto_audit_started` event. `repair_round` is the 0-based position of
  the latest visible repair among the repair rows that this history names.
  Each field is absent until the first review or repair starts. A
  checkpoint prefix sees only its own rounds.
- `unresolved_finding_count` is the N of **Completed · unverified · N
  unresolved issues**. It is sent only for `completed_with_issues`, and only
  when N is at least 1. N counts the findings recorded by the latest
  completed result review of the run's current candidate. No later
  independent review has cleared them. N counts them the way the completion
  gate does for the message's user truth: the material ones (`material`,
  `major`, `high`, `critical`) when there are any, otherwise all of them.
- None of the five is sent for an imported (`quarantined_import`) run, for
  any run in a quarantined session, or for a run whose proof no longer
  validates. That last run is projected as `failed` / `safety_boundary`.
  Export and share do not carry the five fields either, because they belong
  to this read projection and not to the run record.

`run.status` is one of `running`, `candidate`, `reviewing`, `repairing`,
`verified`, `completed_with_issues`, `review_unavailable`,
`blocked_by_guardian`, `cancelled`, `failed`, `paused`, `unverified_import`.
The first four are in progress. The rest are finished. Terminal wording for
the finished states and for `terminal_reason` stays the user-truth sentences
already fixed in *State vocabulary and sole entry conditions*. When
`run.user_truth` is present, the run line shows that string unchanged.

`run.budget_usage.<field>` is a meter `{limit, used, reserved, remaining,
exhausted, authority}` for each ceiling field. `authority` is `auto_budget`
or `guardian`. `run.circuit` is `{state, reason, last_delta_cursor}` with
`state` of `closed` or `tripped`. `last_delta_cursor` is not shown.
`run.legacy: true` means the store returned no budget projection: `budget_usage`
is empty and the circuit is the closed placeholder. The run line can still
show `status`.

| Situation | Availability line | Saved-selection line | Run line |
| --- | --- | --- | --- |
| `feature_enabled` false, and `disabled_reason` is `stage2_feature_disabled` | Storage is off. `writable` is false. The preset is not described as started. | Still shows `selection`, including a deployment `autonomous` preset when that is the winner. The value is a saved or inherited configuration. | Independent. Null `run` reads as no Auto Run. A stored run, if the projection has one, still shows its own `status`. |
| `writable` false, `disabled_reason` `stage2_feature_disabled` | Same as the row above. This is the only non-quarantine reason. | Same as the row above. | Same as the row above. |
| `writable` false, `disabled_reason` `import_quarantine` | Imported session, read only. Quarantine wins even when the stage flag is also off, so `feature_enabled` may be false while the reason remains `import_quarantine`. | `preset` `off`, `result_review_mode` `off`, `approvals_reviewer` `user`, `source` `import_quarantine`. `explicit` is true. The line says the safe triple is held because the session was imported. | Historical run facts stay visible as read-only provenance. The line does not offer resume. `unverified_import` stays unverified. |
| `writable` true, `disabled_reason` null | Storage is available. A later editor may be offered. Availability still does not say a run is in progress. | Follows `source` and the triple. | Follows `run` only. |
| `selection.explicit` false | Unchanged. | `source` is `built_in_defaults`: preset off, result review off, approvals `user`. The value says this is the built-in default and that no override is saved. | Unchanged. |
| `selection.explicit` true | Unchanged. | A source other than `built_in_defaults` won. The source label says which. `explicit` alone is not copied as “you saved this”. | Unchanged. |
| `source` `frame` | Unchanged. | Saved on this conversation. | Unchanged. |
| `source` `project` | Unchanged. | Saved on this project. | Unchanged. |
| `source` `deployment_explicit` | Unchanged. | Set by deployment configuration. When `deployment.explicit_fields` is non-empty, the detail names those fields. | Unchanged. |
| `source` `legacy_result_review` | Unchanged. | Inherited from the old Auto review switch. See section 5. The preset is `off` and approvals stay `user`. Result review is `review_only` only when the scoped setting is a legacy true spelling (`1`, `true`, `yes`, `on`); any other stored spelling, including `0`, is result review `off`. | Unchanged. |
| `source` `built_in_defaults` | Unchanged. | Built-in default. `explicit` is false. | Unchanged. |
| `source` `import_quarantine` | Read only, as above. | Safe triple, as above. | As the quarantine row above. |
| `run` null | Unchanged. | Unchanged. | No Auto Run. Ceilings may still be listed. No meter is near its ceiling. |
| `run` object, `status` in `running`, `candidate`, `reviewing`, `repairing` | Unchanged. | Unchanged. The selection triple is not rewritten to match the run. | In progress, then the status sentence. The line also shows this run's own `result_review_mode` and `approvals_reviewer` when those fields are present, plus `review_round` and `repair_round` when present, each counted from 1. |
| `run` object, any other `status` | Unchanged. | Unchanged. | Finished, then `user_truth` or the terminal sentence for `status` / `terminal_reason`. A paused budget stop uses **Paused · Budget exhausted**. It does not read as in progress. |
| A meter is near its ceiling | Unchanged. | Unchanged. | The budget block, not the selection line, carries the warning. Rule below. |
| HTTP 503 `auto_mode_storage_unavailable` | Status unavailable. | Status unavailable. The client does not invent an off preset. | Status unavailable. |
| HTTP 404 `frame_not_found` | Session not found. | Session not found. | Session not found. |

Budget block, under the run line:

| Condition | What is shown |
| --- | --- |
| Always, after a successful GET | The `budgets` object as deployment ceilings, labelled ceilings. The block is read-only. Stage 2 accepts no budget PATCH. |
| `run` null, or `run.legacy` true | Ceilings, plus “no usage recorded”. The near-ceiling rule does not apply. Empty `budget_usage` is not zero usage. |
| `run.legacy` false | Each `budget_usage` meter as “`used` of `limit`, `remaining` remaining”. When `reserved` is greater than zero, the same meter includes the reserved count. `authority` `auto_budget` is labelled Auto Run. `authority` `guardian` is labelled Guardian. |
| Near ceiling | Display rule only. The server sends no near-limit field. A meter is near its ceiling when `exhausted` is false, `limit` is a positive finite number, and `remaining * 5 <= limit`. The meter then adds “near ceiling”. |
| At ceiling | `exhausted` true. The meter reads “at ceiling”. When `terminal_reason` or `circuit.reason` is `budget_exhausted`, the run line uses **Paused · Budget exhausted** and the block names the exhausted meters. |
| Token ceiling not frozen | `extra_token_multiplier` with `limit` `0` and `exhausted` false. The meter reads “token ceiling not frozen”. It is not described as near or at its ceiling. |
| Circuit | `circuit.state` `closed` shows no circuit warning. `tripped` shows `circuit.reason` with the user-truth string when `user_truth` carries one. `budget_measurement_unavailable` is shown as the server string `无法验证 token 预算`, in either UI language. `quota_exceeded` is **Paused · Team quota exhausted**. `loop_detected` is **Paused/Blocked · Loop detected**. |

Worked example, stage flag off, deployment preset `autonomous`, no run. The
three lines read as storage off, saved selection autonomous with the forced
`auto_fix` and `auto_review` pair and source deployment, and no Auto Run.
None of the three reads “已开启” or “On”.

### 3. Copy

Headings are the table in the introduction. Values:

| Slot | English | Chinese | When |
| --- | --- | --- | --- |
| Availability | Storage off | 存储未开启 | `disabled_reason` is `stage2_feature_disabled` |
| Availability | Imported session, read only | 导入会话，只读 | `disabled_reason` is `import_quarantine` |
| Availability | Storage available | 存储可用 | `feature_enabled` true and `disabled_reason` null |
| Availability | Status unavailable | 状态不可用 | HTTP 503, or `schema_version` other than `1` |
| Availability | Session not found | 找不到会话 | HTTP 404 `frame_not_found` |
| Preset `off` | Off | 关闭 | `selection.preset` |
| Preset `autonomous` | Autonomous | 自主 | `selection.preset`. The heading above it remains “Saved selection”. |
| Result `off` | Off | 关闭 | `selection.result_review_mode` |
| Result `review_only` | Review only | 仅审核 | `selection.result_review_mode` |
| Result `auto_fix` | Auto-fix | 自动修复 | `selection.result_review_mode` |
| Approvals `user` | You | 由你 | `selection.approvals_reviewer` |
| Approvals `auto_review` | Auto review of asks | 自动复核询问 | `selection.approvals_reviewer`. This string is not the menu item “Auto review” / “自动审核”. |
| Source `frame` | Saved on this conversation | 已保存在此会话 | |
| Source `project` | Saved on this project | 已保存在此项目 | |
| Source `deployment_explicit` | Set by deployment | 由部署配置指定 | |
| Source `legacy_result_review` | Inherited from Auto review | 继承自自动审核 | |
| Source `built_in_defaults` | Built-in default, no saved override | 内置默认，没有已保存的覆盖 | `explicit` false |
| Source `import_quarantine` | Held at the safe default after import | 导入后固定为安全默认 | |
| Run null | No Auto Run | 没有自动运行 | |
| Run in progress | In progress | 进行中 | The four non-terminal statuses |
| Run finished | Finished | 已结束 | Every other `run.status` |
| Budget ceilings | Deployment ceilings | 部署上限 | The `budgets` object |
| No usage | No usage recorded | 尚未记录用量 | `run` null or `run.legacy` true |
| Near ceiling | Near ceiling | 接近上限 | The display rule in section 2 |
| At ceiling | At ceiling | 已到上限 | `exhausted` true |
| Token meter | Token ceiling not frozen | 令牌上限尚未冻结 | `extra_token_multiplier` limit `0` and not exhausted |
| Meter authority | Auto Run / Guardian | 自动运行 / 权限守护 | `authority` `auto_budget` / `guardian` |
| Audit entry | Audit | 审计 | Later entry control. Not a mode switch. |
| Audit empty | No audits | 没有审计记录 | Successful audit GET with an empty `audits` array |
| Audit failure | Audits unavailable | 审计不可用 | Audit GET 503 |

Selection value shape, under the saved-selection heading:

`{preset}; result review {result_review_mode}; approvals {approvals_reviewer}. {source label}.`

Chinese:

`{预设}；结果审核 {result_review_mode}；审批 {approvals_reviewer}。{来源}。`

Run value shape:

`{In progress | Finished | No Auto Run}. {user_truth or status sentence}. This run: result review {run.result_review_mode}; approvals {run.approvals_reviewer}. Review round {run.review_round + 1} · repair round {run.repair_round + 1}.`

The “this run” clause is omitted when the run is null or those fields are
absent. The rounds clause appears only while the run is in progress, and it
names only the rounds the run carries. The status sentence is the frozen
user truth from this document, or `run.user_truth` when the payload has it.
The client does not translate `run.user_truth`.

The words “On”, “Enabled”, and “已开启” are not values on these three lines.
The existing composer on/off hint remains the legacy switch only.

The shipped block needs some copy the table above does not fix. It lives in
`frontend/src/features/automode/copy.ts`, beside the strings above:

| Slot | English | Chinese | When |
| --- | --- | --- | --- |
| Block caption | Auto Mode status | 自动模式状态 | Above the three lines; also the block's accessible name |
| Loading | Loading… | 正在读取… | All three lines until this menu opening's own GET answers. A previous answer cached in the tab is never shown first. |
| Retry | Retry reading | 重试读取 | After a failed or unreadable status or audit read. Another GET. |
| Detail row | Details | 详情 | Run identity and digests; audit and finding identity |
| `running` | Running · not verified | 运行中 · 未验证 | No frozen user truth exists for this status |
| `reviewing` | Reviewing the candidate · not verified | 正在审核候选 · 未验证 | Same |
| `repairing` | Repairing · not verified | 正在修复 · 未验证 | Same |
| `completed_with_issues` | Completed · unverified · {N} unresolved issues (“1 unresolved issue” when N is 1) | 已完成 · 未验证 · {N} 个未解决的问题 | `run.unresolved_finding_count` is N |
| `completed_with_issues`, no count | Completed · unverified · unresolved issues | 已完成 · 未验证 · 有未解决的问题 | The run carries no `unresolved_finding_count` |
| Rounds | Review round {r} · repair round {p}. | 审核第 {r} 轮 · 修复第 {p} 轮。 | In progress only. Each round is the field plus 1. With one field only: “Review round {r}.” / “Repair round {p}.” |
| `unverified_import` | Unverified · imported history | 未验证 · 导入的历史 | |
| Budget meter | `{used} of {limit}, {remaining} remaining` | `已用 {used}/{limit}，剩余 {remaining}` | Plus “{n} reserved” / “预留 {n}” and the near/at mark |
| Exhausted list | Exhausted: {meters}. | 已耗尽：{meters}。 | `terminal_reason` or `circuit.reason` is `budget_exhausted` |
| Circuit | Circuit tripped · {reason} | 熔断已触发 · {reason} | The reason is shown as the server's own user-truth string in either language |
| Audit filter | All kinds / Result review / Permission review | 全部类型 / 结果审核 / 权限审核 | `subject_kind` omitted / `result_review` / `permission_review` |
| Audit paging | Load more | 加载更多 | `has_more` with a `next_before` |

The thirteen ceiling labels (for example “Additional Cells” / “额外 Cell”) are
in the same file. Client-rendered status and terminal sentences are
localized; a `run.user_truth` the server sent is shown exactly as sent.

In the menu the Audit entry sits directly under the run line, and the
thirteen ceilings sit behind one closed disclosure whose summary counts the
meters at and near their ceilings. What needs attention -- each near or at
ceiling meter, the exhausted list and a tripped circuit -- is repeated below
that disclosure and is always visible, so the menu stays shorter than the
window without hiding a warning.

Later editor copy, unused until a version that implements section 4:

| Slot | English | Chinese |
| --- | --- | --- |
| Revision conflict | This selection was saved elsewhere. Reloaded. Try again. | 选择已被另存，已重新读取。请再试一次。 |
| Storage refused | Storage off | 存储未开启 |
| Quarantine refused | Imported session, read only | 导入会话，只读 |
| Clear control | Clear saved override | 清除已保存的覆盖 |
| Autonomous confirm | Autonomous saves result review as auto-fix and approvals as auto review of asks. It does not start a run. | 自主会把结果审核存成自动修复，并把审批存成自动复核询问。这不会开始一次运行。 |
| Preset-off confirm | Preset off keeps the result-review and approval choices shown here. | 预设关闭会保留此处显示的结果审核和审批选择。 |

### 4. Interaction specification (later version)

This version builds none of the controls in this section. A later editor
follows these rules.

| Rule | Behavior |
| --- | --- |
| Who may edit | The editor is present only when `writable` is true. `disabled_reason` is then null. Storage off and import quarantine show the availability copy and no editor. |
| What is sent | The body always includes `revision` from `selection.revision`, plus the full triple `preset`, `result_review_mode`, and `approvals_reviewer`. The editor does not send `source_revision`, `budgets`, or `run`. |
| Autonomous pair | The autonomous choice is offered only as the forced triple: `preset` `autonomous`, `result_review_mode` `auto_fix`, `approvals_reviewer` `auto_review`. The confirm copy in section 3 is shown before the request. The server stores that pair even if a client sends other sub-modes with `autonomous`. |
| Preset off | Preset off is sent with the two sub-modes the user picked. Those sub-modes stay independent. Sending `preset` `off` alone would make the server store result review `off` and approvals `user`, so the editor does not send the preset alone. |
| Omitted preset | If a request includes a sub-mode and omits `preset`, the server stores `preset` `off`. The editor avoids that path by sending the full triple. |
| Success | PATCH returns the same object as GET. All three lines and the budget block are replaced from that object. A successful PATCH does not mean a run started. |
| `409` `auto_mode_revision_conflict` | Discard the draft. GET again. Show the revision-conflict copy. Do not send the PATCH again automatically. |
| `409` `auto_mode_storage_disabled` | Refetch. Show “Storage off”. The flag changed under the client. |
| `423` `session_import_quarantined` | Refetch. Show “Imported session, read only”. Hide the editor. |
| `503` `auto_mode_storage_unavailable` | Show “Status unavailable” on all three lines. Do not keep a stale “storage available”. |
| Clear override | The clear control is shown only when `writable` is true and `source` is `frame`. It sends `revision` and all three selection fields as JSON null. A partial null is `400` `invalid_auto_mode_clear` and is not a control the editor offers. After success, the lines show whichever lower source now wins. |
| Empty body | A PATCH with only `revision` is `400` `empty_auto_mode_patch`. The editor does not send it. |
| Unknown fields | Anything outside `revision` and the triple is `400` `invalid_auto_mode_fields`. |
| Bad revision | A missing revision is `400` `auto_mode_revision_required`. A non-integer or negative revision is `400` `invalid_auto_mode_revision`. |
| Bad enum | An unknown preset, result mode, or reviewer is `400` `invalid_auto_mode`. The editor offers only the closed values. |

Clearing a frame override does not delete a project row, a deployment
setting, or the legacy `review:auto:<root>` setting.

### 5. Relationship to the legacy Auto review switch

The menu item labelled “Auto review” / “自动审核”
(`composer.option.autoReview`) stays. Its checkmark is
`GET /frames/{id}/review-settings` field `auto_review`. Clicking it still
sends `PATCH /frames/{id}/review-settings` with `{auto_review}`. That route
writes `review:auto:<frame id>` and means the old single-call Reviewer after
the answer. `inherits_auto_review` true means the scoped key is absent and
the checkmark may be coming from the global `auto_review_enabled` setting.

Auto Mode reads a different key. `selection.source` becomes
`legacy_result_review` only when `review:auto:<root_frame_id>` exists, and
only when no quarantine, frame, project, or explicit deployment source wins.
That mapping is `preset` `off`, `approvals_reviewer` `user`, and
`result_review_mode` `review_only` or `off` as in section 2. It never selects
`autonomous`, `auto_fix`, or `auto_review` approvals. The global setting is
not an Auto Mode source. The menu's frame id and the root id are the same
when the open session is the root. The status lines follow the GET
`selection`, not the checkmark.

| What the user can see | How it is presented |
| --- | --- |
| Auto review checkmark on, `source` `legacy_result_review` | The menu checkmark keeps the name Auto review. The saved-selection line reads inherited, result review only, approvals with you, preset off. There is one switch, the old one. The status line is not a second switch. |
| Auto review checkmark on, `source` `built_in_defaults` | The checkmark is the old Reviewer, often inherited from the global setting. The saved-selection line stays “built-in default”. The two are labelled differently and are not merged into one on/off. |
| Auto review checkmark off, `source` `frame` or `project` or `deployment_explicit` | The saved-selection line shows the Auto Mode source. The checkmark is not cleared to match it, and the Auto Mode line is not turned off to match the checkmark. |
| `source` `import_quarantine` | The saved-selection line is the safe triple. The old checkmark, if still readable, keeps its own label and is not offered as a way out of quarantine. |

The read-only status block sits at the foot of the session options menu,
after a separator, and does not replace the Auto review row.

### 6. Audit entry

The block carries an Audit / 审计 control under the run line. It is not a
mode switch. It closes the menu and opens a read-only Audit view in the
workbench modal, which calls `GET /frames/{id}/auto-audits` with a kind
filter (All kinds, Result review, Permission review) and pages of 20.

| Query | Rule |
| --- | --- |
| `subject_kind` | Omitted for both kinds. Otherwise exactly `result_review` or `permission_review`. |
| `limit` | Integer 1 through 500. Default 100. |
| `before` | Omitted on the first page. The next page sends the previous response's `next_before`. |
| Order | Newest first, as returned. The client does not re-sort a page into the middle of another page. |
| Stop | `has_more` is true only when `next_before` is present. The client stops when `has_more` is false. |

Response keys are `schema_version`, `root_frame_id`, `branch_id`,
`subject_kind`, `audits`, `next_before`, and `has_more`.

| HTTP | Code | Panel |
| --- | --- | --- |
| 200, empty `audits` | | No audits |
| 400 | `invalid_subject_kind` | Reset the filter to both kinds. Do not retry the rejected value. |
| 400 | `invalid_limit`, `invalid_cursor` | Show Audits unavailable for that request. Do not walk `before` with a cursor the user typed. |
| 404 | `frame_not_found` | Session not found |
| 503 | `auto_mode_storage_unavailable` | Audits unavailable |

Each audit row shows these fields when present: `subject_kind`,
`subject_entity_kind`, `status`, `verdict`, `outcome`, `decision`, `risk`,
`round`, `attempt`, `finding_count`, `public_summary`, `error_kind`,
`created_at`, `started_at`, `completed_at`, and `event_ordinal`.
`subject_entity_kind` is only `candidate_evidence_snapshot` for
`result_review` and `approval_action` for `permission_review`.

The primary sentence is `public_summary`. `rationale_summary` is a bounded
summary already truncated by the server. It may appear in the detail row
under the label Summary / 摘要. It is not labelled as a prompt.

Findings, when the sanitized row includes them, show `severity`, `category`,
`status`, `claim`, `evidence_refs`, `version_ids`, `artifact_ids`, and
`cell_ids`. `finding_id` and `fingerprint` stay on the detail row.

Identity fields `audit_id` and `run_id`, plus `reviewer_profile_id` and
`profile_revision`, stay on the detail row. Digests (`audit_request_digest`,
`assessment_digest`, `action_digest`, `candidate_digest`) may be shown there
as hashes. They are not the decision text.

The panel does not show an assessment prompt, a system prompt, hidden
rationale, a permission-request body, or a reusable authorization. Those
values are not on the sanitized audit. A later payload that adds them is
still omitted. The panel does not reconstruct them from a digest.

`auto_audit_started` and `auto_audit_completed` refresh this list when the
panel is open. The event is not inserted as a row. The list is replaced or
paged from GET.

### 7. WebSocket events and refresh

SQLite is the source of the three lines. The socket is a hint that a
transition already committed. A lost hint is recovered by GET. The client
does not retry a side effect.

| Event | What the client does |
| --- | --- |
| `auto_run_started` | GET `/auto-mode` for the open frame when `root_frame_id` matches. |
| `candidate_ready` | GET `/auto-mode`. |
| `auto_audit_started` | GET `/auto-mode`. If the audit panel is open, GET `/auto-audits` for that event's `subject_kind`. |
| `auto_audit_completed` | Same as `auto_audit_started`. |
| `repair_started` | GET `/auto-mode`. |
| `repair_completed` | GET `/auto-mode`. |
| `auto_run_terminal` | GET `/auto-mode`. If the audit panel is open, GET `/auto-audits` again. |

Any other event type leaves the three lines alone. The client does not copy
event fields onto the lines in place of the GET body. A hint reads only while
the block (or, for the audit list, the Audit view) is on screen; opening the
menu is itself a read, so a hint missed while it was closed costs nothing.

The event registry takes exactly one handler per type. `candidate_ready` and
`auto_run_terminal` already belong to the send lane (the gated-candidate card
and the workbench refresh), which keeps that work and passes the event on as
a hint; the automode lane registers the other five.

Which GET body the lines show:

| Situation | Rule |
| --- | --- |
| The conversation was switched or reopened (`_openGen` moved: a branch activation and a revert both reopen it) | Everything held is dropped and the lines read “Loading…” until a read issued under the new opening answers. A response to a read issued before is dropped, even if it is the only one to arrive. |
| A read was issued after the shown read arrived | It replaces the shown one whatever its cursor says. The server answered it later, and a revert can legitimately move a branch's cursor back. |
| Two reads were in flight together, for the same branch | The higher `last_event_ordinal` wins. At an equal cursor the higher `selection.revision` wins: a selection save emits no event, so the cursor alone would reject it. At a full tie the later-issued read wins, which is how a project, deployment or legacy change with no revision of its own still lands. |
| Two reads in flight together named different branches | The later-issued read wins. |
| A failed read | It follows the same rules, so a stale success cannot hide that the latest read failed, and a failure older than the shown answer cannot replace it. |

Reopen and reconnect use the same GET. They do not replay the socket buffer
as authority. A reconnect (a socket after the first that opens) reads again
if the block or the Audit view is on screen.

A selection PATCH does not emit these events. The lines change because the
PATCH response is a GET body.

There is no transition endpoint. The client does not call one in order to
turn a hint into a run.

### 8. Stage-flag checklist

Verified from `RoadmapFeatureFlags` and `AutoModeConfig` in
`openai4s/config.py`. Every stage flag uses `_strict_env_flag`, whose default
is false when the variable is unset. Accepted true spellings are `1`,
`true`, `yes`, `on`. Accepted false spellings are `0`, `false`, `no`,
`off`. Any other spelling fails configuration load. Stage 12 does not turn
on stages 1–11. The availability line reads `feature_enabled` from stage 2
only. The other flags do not change that boolean.

| Environment variable | Config field | Default when unset |
| --- | --- | --- |
| `OPENAI4S_STAGE1_TRUSTED_DELIVERY` | `roadmap_features.stage1_trusted_delivery` | false |
| `OPENAI4S_STAGE2_AUTO_RUN_STORAGE` | `roadmap_features.stage2_auto_run_storage` | false |
| `OPENAI4S_STAGE3_SCIENTIFIC_REVIEW_SHADOW` | `roadmap_features.stage3_scientific_review_shadow` | false |
| `OPENAI4S_STAGE4_REVIEW_COMPLETION_GATE` | `roadmap_features.stage4_review_completion_gate` | false |
| `OPENAI4S_STAGE5_AUTO_REPAIR` | `roadmap_features.stage5_auto_repair` | false |
| `OPENAI4S_STAGE6_GUARDIAN_SHADOW` | `roadmap_features.stage6_guardian_shadow` | false |
| `OPENAI4S_STAGE7_GUARDIAN_ENFORCEMENT` | `roadmap_features.stage7_guardian_enforcement` | false |
| `OPENAI4S_STAGE8_LIVE_NOTEBOOK_LINEAGE` | `roadmap_features.stage8_live_notebook_lineage` | false |
| `OPENAI4S_STAGE9_ARTIFACT_WORKBENCH` | `roadmap_features.stage9_artifact_workbench` | false |
| `OPENAI4S_STAGE10_SCIENTIFIC_CONNECTORS` | `roadmap_features.stage10_scientific_connectors` | false |
| `OPENAI4S_STAGE11_DURABLE_REMOTE_COMPUTE` | `roadmap_features.stage11_durable_remote_compute` | false |
| `OPENAI4S_STAGE12_AUTO_MODE_GA` | `roadmap_features.stage12_auto_mode_ga` | false |

Selection variables are not stage flags. They feed `selection` when the
winning source is `deployment_explicit`. Unset is not the same as an explicit
off: only a variable that is present is listed in `deployment.explicit_fields`.

| Environment variable | Default when unset | Accepted values | Effect on the saved-selection line |
| --- | --- | --- | --- |
| `OPENAI4S_AUTO_MODE` | unset, so `enabled` false and preset `off` | `autonomous` or `1` / `true` / `yes` / `on` enable it; `0` / `false` / `no` / `off` disable it | Enabling forces the deployment pair to `auto_fix` and `auto_review`. The preset name on the wire is `autonomous` or `off`. |
| `OPENAI4S_RESULT_REVIEW_MODE` | `off` | `off`, `review_only`, `auto_fix` | Ignored for the stored pair when the preset is enabled. Those two are forced. |
| `OPENAI4S_APPROVALS_REVIEWER` | `user` | `user`, `auto_review` | Same force when the preset is enabled. |

Ceiling variables are the budget block's deployment ceilings. Defaults and
hard maximums match *Frozen bounded budgets* and `AutoModeBudgets`. A value
outside the closed range fails configuration load. The surface never treats
a tightened ceiling as a used meter.

| Environment variable | Default | Closed range |
| --- | --- | --- |
| `OPENAI4S_AUTO_MAX_REVIEW_ROUNDS` | 2 | 1–2 |
| `OPENAI4S_AUTO_MAX_REPAIR_ROUNDS` | 2 | 0–2 |
| `OPENAI4S_AUTO_REPAIR_TURNS_PER_ROUND` | 12 | 0–12 |
| `OPENAI4S_AUTO_MAX_EXTRA_CELLS` | 30 | 0–30 |
| `OPENAI4S_AUTO_WALL_TIME_S` | 900 | 1–900 |
| `OPENAI4S_AUTO_EXTRA_TOKEN_MULTIPLIER` | 1.5 | 0–1.5 |
| `OPENAI4S_AUTO_REPEATED_FINDING_LIMIT` | 2 | 1–2 |
| `OPENAI4S_AUTO_SAME_ACTION_NO_DELTA_LIMIT` | 3 | 1–3 |
| `OPENAI4S_AUTO_NO_PROGRESS_TURN_LIMIT` | 5 | 1–5 |
| `OPENAI4S_AUTO_GUARDIAN_TIMEOUT_S` | 90 | 1–90 |
| `OPENAI4S_AUTO_GUARDIAN_CONSECUTIVE_DENIAL_LIMIT` | 3 | 1–3 |
| `OPENAI4S_AUTO_GUARDIAN_WINDOW_SIZE` | 50 | 50–50 |
| `OPENAI4S_AUTO_GUARDIAN_WINDOW_DENIAL_LIMIT` | 10 | 1–10 |

### 9. Tests

The read-only surface is covered by the Vitest files in
`frontend/src/features/automode/` (sanitizer, both languages' copy, the
read-ordering rules above, the block inside the real `sessionOptionsMenu`,
the Audit view, hint composition with the send lane), by
`tests/test_auto_mode_browser_fixture.py` (SQLite seeds pinned against the
real `AutoModeService`), and by `tests/browser_auto_mode_status.mjs`, which
drives a real gateway in English and Chinese and fails on any non-GET the
surface makes. The service assertions and menu cases below are the contract
those files check; the rows about PATCH, clear, revision conflict and the
autonomous confirm belong to the section 4 editor and stay open until it is
built.

Service assertions to keep or add, against the real handler or the service
the route calls:

| Case | Expected projection |
| --- | --- |
| Stage 2 flag off | GET `feature_enabled` false, `writable` false, `disabled_reason` `stage2_feature_disabled`. PATCH `409` `auto_mode_storage_disabled`. The selection is still resolved. |
| Flag on, not quarantined | `writable` true, `disabled_reason` null. |
| Quarantine, flag on or off | `disabled_reason` `import_quarantine`, `writable` false, safe triple, `source` `import_quarantine`, `explicit` true. PATCH `423` `session_import_quarantined`. |
| Each `source` | The precedence order already covered, including legacy true and legacy false, and an unset deployment that does not erase legacy. |
| `explicit` | False only for `built_in_defaults`. |
| Autonomous PATCH | Stored triple is `autonomous` / `auto_fix` / `auto_review`. |
| Preset off sent alone | Stored result review `off` and approvals `user`. |
| Clear | All three fields null, with `revision`, removes the frame override. A mixed null is `400` `invalid_auto_mode_clear`. |
| Stale `revision` | `409` `auto_mode_revision_conflict`, and the following GET returns the winner. |
| Audits | `subject_kind` filter, `limit` outside 1–500 rejected, `has_more` only with `next_before`, and the body has no prompt field. |
| Events | The seven event names are the only ones broadcast, and a broadcast failure does not roll back the committed row. |
| Storage missing | The route returns `503` `auto_mode_storage_unavailable` without a selection body. |

Menu cases, once the read-only block exists:

| Case | What the test shows |
| --- | --- |
| Flag off, preset `autonomous`, `run` null | Three strings: storage off, saved autonomous selection, no Auto Run. The block does not contain “On” or “已开启”. |
| Each `disabled_reason` | The availability string in section 3, and no editor control. |
| `explicit` false and true | Built-in copy versus a source label. `explicit` true with source `legacy_result_review` is not labelled as saved on this conversation. |
| Each `source` | The source string in section 3. |
| `run` null, in progress, and finished | “No Auto Run”, “In progress”, and “Finished”, plus the run's own mode fields when present. |
| Selection off while `run.status` is `reviewing` | The saved-selection line stays off. The run line says in progress. |
| Budget | Ceilings with no usage when `run` is null or `legacy` is true. Near-ceiling and at-ceiling copy from the meter rule. The `无法验证 token 预算` string is shown as returned. |
| Legacy versus Auto review | The existing Auto review row still patches `review-settings`. A `legacy_result_review` status line is not a second checkmark. A checked Auto review row with `built_in_defaults` does not flip the saved-selection line to on. |
| Revision conflict | The handler shows the retry copy, issues a GET, and does not PATCH again. |
| Quarantine | Read-only copy, no PATCH from the menu. |
| Clear | The request body is `revision` plus three nulls, and only when `source` is `frame`. |
| Autonomous confirm | The confirm copy names `auto_fix` and `auto_review` before the request. |
| Socket hint | A canonical event triggers GET. The event object is not written into the three lines by itself. |
| Audit panel | Paging uses `before` and `subject_kind`. Rendered text has no prompt field. |

Acceptance for that implementation: a person can point at the menu and name
which line is the saved selection, which line is availability, and which line
is the actual run, including the worked example where the preset is
`autonomous`, storage is off, and there is no run.
