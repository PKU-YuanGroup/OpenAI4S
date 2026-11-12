# RL runtime

[中文说明](README_zh.md)

OpenAI4S can serve as the **environment** for agentic reinforcement learning. An
external trainer samples the policy's next action; OpenAI4S executes that action
with its real tools, permissions, kernels, and artifacts; and this package
records what happened as a versioned *Science Trajectory* that the trainer can
turn into training samples.

This module is only the boundary. It ships **no loss function, no optimizer, and
no reward model** — those stay in the trainer — and it is **standard-library
only**, so importing it adds no dependency to the core.

## What this is

A trainer normally has two bad options: import the agent's Python internals, or
scrape its database. This package exists so it needs neither. Everything crosses
the boundary as plain JSON:

- **reset** a task and receive the first observation;
- **step** with a sampled reply and receive the execution result and the next
  observation;
- **finish** with a terminal trajectory, exportable as JSON, JSONL, or ATIF;
- **release** the episode and its workspace.

The split of responsibilities is strict:

- OpenAI4S keeps ownership of routing, permissions, kernels, completion,
  cancellation, and the Action Ledger. Nothing here starts a second execution
  loop.
- The trainer keeps ownership of sampling, reward, attribution, and optimization.
- **Execution facts are append-only.** Reward annotations and cost records are
  separate, versioned records, so re-scoring a rollout — or replacing the
  verifier entirely — never rewrites history.
- The Agent cannot score itself: there is no self-reward Host API. A trusted
  verifier or trainer writes every reward.

The normative record format is the
[trajectory contract](../../docs/rl/trajectory-contract-v1.md).

## How a rollout works

1. **`reset`** builds a fresh `AgentEngine` and `TrajectoryRecorder` through your
   factory, plus an isolated workspace. Reset metadata records initial artifact
   references, configuration, and budget — never the absolute workspace path.
2. **`observe`** returns the exact messages the policy should see, together with
   the current native tool declarations.
3. **`step`** hands your sampled reply to the engine. It executes a code cell, a
   native tool batch, or a finalization, then returns the result and the next
   observation.
4. **`finish`** closes the episode and freezes the trajectory. `export` returns a
   schema-versioned payload with a SHA-256 over the exact bytes.
5. **`release`** disposes of the environment and workspace.

One episode is one engine, one recorder, and one workspace. Segments group
actions into meaningful phases: by default the engine creates one mechanical
segment per routed action, and scientific code can open an explicit phase that
spans several decisions with `host.begin_segment(...)`, `phase.observe(...)`, and
`phase.close()`.

## Quick start

### Run one rollout locally

```python
from openai4s.agent.engine import AgentEngine
from openai4s.rl import Episode, PolicyAdapter, RolloutEnvironment, RolloutWorker

def build_engine(reset):
    """Wire your normal engine: model port, action executor, tools, max turns."""
    return AgentEngine(model=..., executor=..., max_turns=reset.max_turns)

worker = RolloutWorker(
    RolloutEnvironment(build_engine, workspace_root="/data/rollouts"),
)

def sample(observation, *, tools):
    """Any ``(observation, *, tools) -> ModelReply | mapping`` callable works.

    It may be async. Wrap an inference-server client with
    ``PolicyAdapter(provider, "vllm", "step-1200")`` to record its identity.
    """
    return my_inference_server.chat(observation, tools=tools)

trajectory = worker.run(
    Episode("ep-1", "tune the reaction yield", "session-1", created_at=0),
    [{"role": "user", "content": "Tune the reaction yield."}],
    sample,
)
print(worker.export(trajectory, "jsonl").sha256)
```

`worker.run()` returns the terminal `Trajectory`. Exports are deterministic and
carry a schema version and a byte hash, so a training shard can be verified
without re-running anything.

### Serve a trainer over JSONL

For a separate process, machine, or GPU host, start the worker over
stdin/stdout and talk to it with the bundled client. The factory is
administrator configuration and never a field the RPC can set:

```bash
python -m openai4s.rl.serve my_deployment:build_environment
```

```python
from openai4s.rl import RolloutClient

client = RolloutClient(
    ["ssh", "gpu-box", "python", "-m", "openai4s.rl.serve", "my_deployment:build_environment"]
)
client.reset({"episode": {...}, "messages": [{"role": "user", "content": "..."}]})
```

The transport is newline-delimited JSON over one stream. Logs go to stderr and
stdin/stdout stay protocol-only. `step`, `recover`, `result`, and `step_result`
are idempotent: an exact retry returns the recorded response. If the transport
drops mid-request, the client reports the state as unknown and you must query
the durable result — the client never auto-resubmits side effects.

## From rollouts to training data

`DatasetSnapshot` freezes a terminal trajectory at a content-addressed boundary;
`RolloutDataset` then projects one `TrainingSample` per `PolicyDecision`:

```python
from openai4s.rl import DatasetSnapshot, RolloutDataset

snapshot = DatasetSnapshot.from_trajectory(trajectory, "snap-ep1", created_at=1)
dataset = RolloutDataset.from_trajectory(trajectory, snapshot)
open("shard.jsonl", "w").write(dataset.jsonl())
```

Each sample carries the observation, the decision (including prompt token IDs
and loss mask when the sampler supplies them), and only the execution events and
rewards explicitly joined to that decision. Segment-level and episode-level
rewards stay outside the per-decision samples: turning them into a per-token
target is an attribution choice the trainer makes, and this package will not
guess it. It also does not pick an advantage estimator.

## Rewards, costs, and budgets

- **Rewards.** `apply_verifier(recorder, verifier)` runs a provider-neutral
  `RewardVerifier` against an immutable snapshot, checks identity, scope, joins,
  and evidence references, and only then appends versioned annotations.
- **Costs.** `cost.py` accepts provider cost evidence only when it is
  explicitly authenticated and bound to the exact compute job and receipt. A
  missing measurement stays unknown; job wall time is never relabeled as GPU
  time or a bill.
- **Admission and budget.** `admission.py` binds a provider receipt before work
  starts and reports queue wait separately from compute time. `budget.py` keeps
  estimated reservations apart from actual usage and makes hard versus soft
  limits explicit. Both have durable wrappers that survive a daemon restart
  without recreating or double-charging a reservation.

## Safety and failure behavior

- Durable steps are claim-once: an accepted request with a recorded response
  returns that response, while claimed-but-unanswered or legacy-unknown work is
  never replayed automatically.
- Recovery is fail-closed. An interrupted episode is reported as
  `recovery_required` with its durable receipt; execution is not re-run on the
  trainer's behalf.
- Cancellation is exact-owner. Requesting an interrupt drains real execution
  before the lane and workspace are released; a stuck execution stays occupied
  rather than being silently detached.
- Exports and the RPC stream are strict: malformed or oversized lines produce a
  structured error without desynchronizing the stream, and a running episode
  cannot be exported.

## Module map

| file | role |
|---|---|
| `__init__.py` | Public records and adapters; heavier modules load lazily. |
| `models.py` | Versioned JSON records: Episode, Observation, PolicyDecision, Segment, ExecutionEvent, RewardAnnotation, CostRecord, RunReceipt, Trajectory. |
| `recorder.py` | Thread-safe append-only recorder with idempotent writes and deterministic JSON/JSONL export. |
| `segments.py` | Segment lifecycle: one mechanical segment per routed action, plus explicit cross-decision scientific phases. |
| `verifier.py` | Provider-neutral `RewardVerifier` protocol and `apply_verifier()`; validates identity, scope, joins, and evidence. |
| `snapshot.py` | `DatasetSnapshot`: a content-addressed, terminal view that freezes the trajectory hash and event boundary. |
| `dataset.py` | `RolloutDataset` and `TrainingSample`: one sample per decision with token IDs, loss mask, and explicit reward joins. |
| `requests.py` | Strict request normalization, idempotent step semantics, and durable terminal receipts. |
| `environment.py` | `RolloutEnvironment` (fresh engine, recorder, and workspace per episode) and the JSON-safe `RolloutEnvironmentAdapter`. |
| `rollout.py` | `RolloutSession`: the trainer-facing reset/observe/step/finish lifecycle over the unmodified `AgentEngine`. |
| `agent_sink.py` | Bridges the `AgentEngine` event stream into these records without replacing the Action Ledger. |
| `policy.py` | `PolicyProvider` and `PolicyAdapter`: normalizes sampled replies and records behavior-policy identity; missing logprobs stay missing. |
| `worker.py` | `RolloutWorker`: one episode from reset to release, deterministic JSON/JSONL/ATIF exports, and explicit recovery results. |
| `replay.py` | `TrajectoryReplayer`: cursorable event, decision, and artifact replay; execution replay needs an explicit runner. |
| `atif.py` | `ATIFExporter`: deterministic ATIF-v1.8 projection for Harbor, plus the positive-terminal-evidence check. |
| `host_events.py` | Bounded Host-RPC lifecycle facts (`started`, `completed`, `failed`) that join nested work to a decision. |
| `projector.py` | Read-only projection of durable facts already referenced by execution events. |
| `cost.py` | Accepts authenticated, receipt-bound provider cost evidence; never estimates GPU time or billing. |
| `admission.py` | Provider-neutral, idempotent, receipt-bound resource admission with separate queue and compute time. |
| `budget.py` | Hard/soft budget ledger with estimated versus actual usage; unknown allocations stay reserved until reconciled. |
| `collector.py` | `RolloutCollector`: fixed concurrency, bounded pending/result queues, and completion-order results. |
| `async_lane.py` | Runs each episode's synchronous lifecycle on one owned thread while sampling stays on the event loop. |
| `client.py` | `RolloutClient`, `AsyncRolloutClient`, and `RemoteRolloutWorker`: bounded JSONL clients. |
| `serve.py` | The trusted entry point: `python -m openai4s.rl.serve module:factory`. |
| `rpc.py` | `RolloutRPC` and the standard-library JSON-lines server; one structured response per input line. |
