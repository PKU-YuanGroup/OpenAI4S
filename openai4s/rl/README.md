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
`apply_verifier()`. It validates verifier identity, episode scope, decision /
segment joins, durable evidence references, and append-only execution facts
before adding versioned annotations.

`snapshot.py` defines `DatasetSnapshot`, a terminal, content-addressed view
that freezes the full trajectory hash and event sequence boundary for a
training export. It does not copy or rewrite trajectory records.

`dataset.py` defines `RolloutDataset` and `TrainingSample`. The adapter joins
one explicit snapshot to one sample per PolicyDecision, keeps decision,
segment, and episode reward annotations separate, and includes the immutable
execution-event snapshots joined to each decision (with their stable event
IDs). Deterministic JSON or streaming JSONL is self-contained at the Trainer
boundary and does not choose an advantage estimator.

`policy.py` defines `PolicyProvider` and `PolicyAdapter`. The adapter records
the behavior policy identity and sampling configuration in `ModelReply.extra`;
it preserves missing logprob evidence instead of estimating it after sampling.
Modern providers can also receive the detached current native tool projection
through `tools`; `RolloutSession.current_tools` exposes the same declarations
after `observe()`. Older observation-only samplers remain supported.
The configuration keyword may be `config` or `sampling_config`.
`PolicyAdapter.sample_async()`, `RolloutSession.run_async()`, and
`RolloutWorker.run_async()` accept synchronous or awaitable samplers, so an
inference-server client can be used without moving execution out of the
existing Engine.
Trajectory observations retain that tool projection, while decisions retain
prompt token IDs and sampling configuration when the sampler supplies them.

`environment.py` defines `RolloutEnvironment`, which constructs a fresh
Engine/Recorder pair and workspace for each Episode reset. Reset metadata
records initial artifact references, configuration, and budget without
exposing the absolute workspace path.

`RolloutEnvironmentAdapter` is the JSON-safe Trainer boundary over that
environment. It exposes reset/observe/step/finish/release/export plus read-only
reward and cost projections, so an external Trainer does not need to import
Engine action classes or inspect SQLite. It never computes reward, hides the
workspace path, or creates a second execution loop.

`replay.py` defines `TrajectoryReplayer`. Event replay is cursorable, decision
replay pairs each sampled decision with the exact observation, and artifact
replay checks immutable version references through a caller-supplied checker.
Execution replay never runs implicitly; it requires an explicit controlled
runner supplied by the embedding application.

`__init__.py` exports the public records and adapters. `agent_sink.py` bridges
the existing `AgentEngine` event stream into these records without replacing
the Action Ledger. `rollout.py` exposes `RolloutSession`, a trainer-facing
reset/observe/step/finish/export lifecycle.

`worker.py` provides the `RolloutWorker` boundary for one Episode: its
`run()` path is synchronous and `run_async()` accepts an awaitable sampler; it
resets, runs, and releases an isolated environment, reports a
durable interrupted receipt as `recovery_required` without retrying side
effects, and returns schema-versioned JSON/JSONL/ATIF exports with a SHA-256 of
the exact payload bytes. `export("atif")` is the Harbor interchange projection;
the complete Science Trajectory remains in its `extra.openai4s` extension.

`rpc.py` provides a pure-standard-library JSON-lines transport for an external
trainer. `RolloutRPC` validates `reset`/`observe`/`step`/`finish`/`export`/
`release`/`rewards`/`costs` requests and delegates them to
`RolloutEnvironmentAdapter`; `serve_rollout_rpc` emits one structured response
per input line, keeps malformed or oversized requests on the stream, and never
starts a second execution loop.

`atif.py` provides `ATIFExporter`, a deterministic ATIF-v1.8 interchange
projection that keeps segment, reward, cost, and execution identifiers in
ATIF extension metadata. `completion_contract()` validates Harbor-style
positive terminal evidence: a non-running Episode needs exactly one final
`episode.finished` event with a matching stop reason. Failed, cancelled,
interrupted, `max_turns`, and ordinary `done` outcomes remain valid terminal
records but are never marked with positive `completion_evidence`; that field
only says the runtime received a terminal completion payload. Scientific
quality remains an external verifier's Reward Annotation.
`ATIFExporter(..., children=[...])` accepts caller-supplied delegated child
trajectories and emits each as an independent ATIF document plus a reference
from the parent decision. It never queries the Store or invents a child
trajectory.

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

`DurableResourceAdmission` persists the capacity reservation and lifecycle
through the Store with revision checks, so an unknown remote allocation still
occupies capacity after a daemon restart.

`budget.py` keeps estimated reservations separate from actual usage and makes
hard versus soft limits explicit. Receipt-bound allocations cannot be released
after provider work may have started; an unknown terminal state stays reserved
until reconciliation.

`DurableBudgetLedger` uses the Store's RL repository to persist the budget
snapshot and receipt-bound allocations with revision checks, so a daemon
restart does not recreate or double charge a reservation.
