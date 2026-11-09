# RL runtime contracts

[中文说明](README_zh.md)

This package is the dependency-free data boundary between OpenAI4S execution
and an external Agentic RL trainer. `models.py` defines the versioned Science
Trajectory records. `recorder.py` collects one episode with append-only,
idempotent writes and deterministic JSON/JSONL export. Reward annotations and
costs are separate records, so a verifier or training snapshot never rewrites
execution facts. Training algorithms and provider-specific policy clients stay
outside the harness.

`__init__.py` exports the public records and adapters. `agent_sink.py` bridges
the existing `AgentEngine` event stream into these records without replacing
the Action Ledger. `rollout.py` exposes `RolloutSession`, a trainer-facing
reset/observe/step/finish/export lifecycle.
