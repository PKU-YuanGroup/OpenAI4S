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

`__init__.py` exports the public records and adapters. `agent_sink.py` bridges
the existing `AgentEngine` event stream into these records without replacing
the Action Ledger. `rollout.py` exposes `RolloutSession`, a trainer-facing
reset/observe/step/finish/export lifecycle.

`atif.py` provides `ATIFExporter`, a deterministic ATIF-v1.8 interchange
projection that keeps segment, reward, cost, and execution identifiers in
ATIF extension metadata.

`host_events.py` defines the bounded Host RPC lifecycle facts (`started`,
`completed`, and `failed`) used to join nested execution to a parent decision
and segment without copying private arguments or results.
