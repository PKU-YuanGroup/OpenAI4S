# OpenAI4S science trajectory contract v1

Status: **implemented for the records listed here; external verifier and
trainer interfaces remain separate work**.

This document is normative for the JSON emitted by
`TrajectoryRecorder` when `Trajectory.schema_version` is
`openai4s.science-trajectory.v1`. It records observed rollout facts. It does
not infer a reward from an action, turn an execution event into a policy
decision, or replace the Action Ledger.

## Envelope

The canonical JSON object has these keys:

```json
{
  "schema_version": "openai4s.science-trajectory.v1",
  "episode": {},
  "observations": [],
  "decisions": [],
  "segments": [],
  "execution_events": [],
  "rewards": [],
  "costs": []
}
```

Canonical JSON uses UTF-8, `ensure_ascii=false`, sorted object keys, and no
insignificant whitespace. `Trajectory.sha256()` hashes those exact bytes.
JSONL starts with one `type: "trajectory"` envelope line and then emits the
record arrays in this order: observations, decisions, segments,
execution_events, rewards, costs. Each subsequent line has a singular `type`
and the record fields.

## Identity and append-only rules

Every record carries an `episode_id` except the envelope itself, and the
recorder rejects a record for another episode. Public IDs are idempotency keys:
repeating an identical record is accepted, while reusing an ID with different
canonical JSON raises a conflict. Durable Store writes apply the same rule.
Execution events additionally receive a monotonically increasing per-episode
`sequence`; the SQLite repository allocates it inside the write transaction.
An episode lifecycle may move from `running` to one terminal status once.

The runtime does not delete or rewrite execution events when a verifier changes.
Reward annotations and costs are separate append-only records. A consumer must
retain the original execution facts when it computes a new reward or exports a
new projection.

## Records

`Episode` identifies one trainer-visible rollout: `episode_id`, `task`,
`session_id`, `created_at`, optional `policy_name`, `policy_version`, optional
`parent_episode_id`, `status`, and JSON `metadata`. A delegated worker is a
child episode with its own ID namespace; its parent relationship is explicit.

`Observation` is the exact detached message context presented before one
decision. It contains `observation_id`, `episode_id`, `decision_id`, `messages`,
`created_at`, `source`, and `truncated`.

`PolicyDecision` is one policy sampling boundary. It contains
`decision_id`, `episode_id`, `turn`, `observation_id`, `prompt_hash`, policy
identity, the normalized `action`, `created_at`, and optional joins:
`parent_decision_id`, `frame_id`, and `action_group_id`. If the external
sampler supplied them, `token_ids`, `token_logprobs`, `loss_mask`, and
`action_logprob` are preserved with length and finite-value checks. The raw
provider response and assistant message are optional JSON evidence. Token
telemetry is evidence from the sampler; the runtime does not recalculate it.

`Segment` is an explicit scientific phase. It contains `segment_id`,
`episode_id`, `name`, `created_at`, optional `decision_id`, optional
`parent_segment_id`, optional `objective`, `status`, and JSON `metadata`.
`host.segment()` emits segment lifecycle events and observations/output facts;
it does not calculate reward.

`ExecutionEvent` is a bounded fact about execution, such as an action dispatch,
cell outcome, segment lifecycle transition, or Host RPC lifecycle. It contains
`event_id`, `episode_id`, `kind`, `created_at`, optional `decision_id`,
`segment_id`, `source_id`, `sequence`, and JSON `payload`. Host RPC payloads
contain argument/result hashes and an error category, not private arguments or
results.

`RewardAnnotation` is an independently produced score. It contains
`annotation_id`, `episode_id`, finite `value`, `verifier`,
`verifier_version`, `created_at`, optional decision/segment joins, numeric
`components`, JSON `metadata`, `evidence_refs`, an optional `confidence` in
`[0, 1]`, and a `source` label. Multiple verifier versions may annotate the
same episode. `apply_verifier()` checks the verifier identity and episode
scope, then appends these annotations without changing execution events.

`CostRecord` stores measured resource evidence: `cost_id`, `episode_id`,
`created_at`, optional prompt/completion tokens, wall/GPU milliseconds, an
optional known `cost_usd`, optional `decision_id`, `source`, and JSON metadata.
Unknown pricing remains unknown; a missing counter is not serialized as a
fabricated zero.

## Action Ledger join

The Action Ledger remains the execution source of truth. When the local Agent
runtime has a `RuntimeActionLedger`, it creates the action group while handling
`ActionRouted`; the RL event sink then stores that exact `group_id` in
`PolicyDecision.action_group_id`. The RL layer never creates a second group or
derives one from a turn number. A direct provider-neutral `AgentEngine` run
without a ledger may leave this field null.

The decision and execution context also carries `rl_episode_id` and
`rl_decision_id` into Host dispatch. Host calls and segments are execution
events under that decision; they are not additional policy decisions.

## Delegation and projection

Delegated workers created under an RL context receive a new episode and
trajectory namespace. Their episode stores `parent_episode_id`; their first
decision stores the parent decision context. IDs such as `episode-id:decision-0`
are local namespace IDs and must not be merged with the parent by string
heuristics. ATIF-v1.8 is a projection for interchange; the complete Science
Trajectory remains under its `extra.openai4s` extension and is the authoritative
training input.

`RLRepository.load_trajectory(episode_id)` reconstructs the records in stable
identity order from the SQLite projections. The reconstructed canonical hash
must equal a `DatasetSnapshot.trajectory_sha256` before a snapshot is admitted
as a trainer dataset.

## Current lifecycle boundary

`RolloutSession.reset()` admits a durable run receipt when configured,
`observe()` prepares the exact next context, `step()` accepts an external
`ModelReply` or mapping, and `finish()`/`run()` export the resulting trajectory.
The existing `AgentEngine` still owns routing, permissions, kernel execution,
completion, cancellation, and ledger callbacks. This runtime does not include
PPO, GRPO, advantage estimation, model serving, or a verifier implementation.

`DatasetSnapshot` is the current fixed dataset boundary. It accepts only a
terminal Episode with no open Segments and fully sequenced execution events;
it stores the canonical trajectory hash, the maximum event sequence, and
record counts. `matches()` rejects a candidate whose trajectory has changed.
When a Store repository is configured, this metadata is appended to
`rl_dataset_snapshots` under migration 38 and can be fetched by snapshot ID.
