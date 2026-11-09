# RL runtime contracts

[中文说明](README_zh.md)

This package is the dependency-free data boundary between OpenAI4S execution
and an external Agentic RL trainer. `models.py` defines the versioned Science
Trajectory records. `recorder.py` collects one episode with append-only,
idempotent writes and deterministic JSON/JSONL export. Reward annotations and
costs are separate records, so a verifier or training snapshot never rewrites
execution facts. Training algorithms and provider-specific policy clients stay
outside the harness.

Host segment APIs record observations and artifact evidence only. Reward
annotations are written by a trusted verifier or trainer; the Agent has no
self-scoring Host API.

`verifier.py` defines the provider-neutral `RewardVerifier` protocol and
`apply_verifier()`. It validates verifier identity, episode scope and
append-only execution facts before adding versioned annotations.

`snapshot.py` defines `DatasetSnapshot`, a terminal, content-addressed view
that freezes the full trajectory hash and event sequence boundary for a
training export. It does not copy or rewrite trajectory records.

`policy.py` defines `PolicyProvider` and `PolicyAdapter`. The adapter records
the behavior policy identity and sampling configuration in `ModelReply.extra`;
it preserves missing logprob evidence instead of estimating it after sampling.

`environment.py` defines `RolloutEnvironment`, which constructs a fresh
Engine/Recorder pair and workspace for each Episode reset. Reset metadata
records initial artifact references, configuration, and budget without
exposing the absolute workspace path.

`replay.py` defines `TrajectoryReplayer`. Event replay is cursorable, decision
replay pairs each sampled decision with the exact observation, and artifact
replay checks immutable version references through a caller-supplied checker.
Execution replay never runs implicitly; it requires an explicit controlled
runner supplied by the embedding application.

`__init__.py` exports the public records and adapters. `agent_sink.py` bridges
the existing `AgentEngine` event stream into these records without replacing
the Action Ledger. `rollout.py` exposes `RolloutSession`, a trainer-facing
reset/observe/step/finish/export lifecycle.

`worker.py` provides the synchronous `RolloutWorker` boundary for one
Episode: it resets, runs, and releases an isolated environment, reports a
durable interrupted receipt as `recovery_required` without retrying side
effects, and returns schema-versioned JSON/JSONL exports with a SHA-256 of the
exact payload bytes.

`atif.py` provides `ATIFExporter`, a deterministic ATIF-v1.8 interchange
projection that keeps segment, reward, cost, and execution identifiers in
ATIF extension metadata.

`host_events.py` defines the bounded Host RPC lifecycle facts (`started`,
`completed`, and `failed`) used to join nested execution to a parent decision
and segment without copying private arguments or results.

`projector.py` adds a read-only durable-fact projection at export time. It
follows exact Artifact version and Compute job IDs already referenced by an
execution event, including checksums, producing cells, receipts, input
versions, manifests, and append-only job events. It never resolves an Artifact
head, copies source rows, or estimates GPU/provider cost.

`cost.py` accepts only explicitly authenticated provider cost evidence bound to
the exact compute job and receipt. Missing provider measurements remain
unknown; durable job elapsed time is metadata and is never relabeled as GPU
time or a billing amount.

`admission.py` supplies a provider-neutral resource request and idempotent
allocation boundary. It binds a provider receipt before start and reports
admission wait separately from actual compute time; it does not replace the
durable Compute job repository.

`budget.py` keeps estimated reservations separate from actual usage and makes
hard versus soft limits explicit. Receipt-bound allocations cannot be released
after provider work may have started; an unknown terminal state stays reserved
until reconciliation.

`DurableBudgetLedger` uses the Store's RL repository to persist the budget
snapshot and receipt-bound allocations with revision checks, so a daemon
restart does not recreate or double charge a reservation.
