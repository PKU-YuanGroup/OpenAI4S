# Agentic RL trajectory contracts

[中文说明](README_zh.md)

This directory documents the versioned data boundary between the OpenAI4S
execution runtime and an external Agentic RL trainer. The runtime records
policy, execution, cost, and lifecycle facts. A verifier or trainer may append
reward annotations, while training algorithms remain outside this repository.

## Files

| File | Purpose and status |
| --- | --- |
| `trajectory-contract-v1.md` | Normative English contract for the `openai4s.science-trajectory.v1` JSON/JSONL envelope and its joins. |
| `trajectory-contract-v1_zh.md` | Chinese counterpart of the trajectory contract. |

The contract describes the records emitted by `openai4s/rl/`, the durable
tables owned by `openai4s/storage/rl.py`, the Action Ledger join, and the
limits of the current rollout adapter. It is a data contract, not a claim
that a trainer, verifier, or reward algorithm is bundled here.
