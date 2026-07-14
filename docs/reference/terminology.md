---
title: Terminology
description: Canonical terms and identity boundaries used throughout OpenAI4S.
status: current
audience:
  - contributors
  - operators
  - users
verified_commit: a92e736
last_verified: 2026-07-14
owner: OpenAI4S maintainers
---

# Terminology

| Term | Meaning |
|---|---|
| **Action** | The one route selected from a model reply: a native tool batch, Engine-owned finalization, one complete Python/R Cell, or no action. |
| **Native tool batch** | Ordered provider-native JSON calls used for orchestration, metadata, external services, permissions, and workflow control. |
| **FinalizeAction** | A sole `finalize_response` call owned and validated by the Engine; it is not a registry Tool. |
| **Cell** | One complete fenced Python or R program executed by a persistent language worker. |
| **Science runtime** | The Python and R Cell channels used for computation and analysis. |
| **Host RPC** | A synchronous Python-worker request to the Host while a Cell is still running. The normal wire exchange is `host_call → host_response`. |
| **Tool** | A registered deterministic control-plane class with schema, side-effect policy, and execution behavior. |
| **HostDispatcher** | The trusted in-process routing envelope that applies capability-specific permission, approval, audit, and screening where configured. Coverage differs by operation; it is not a plugin sandbox. |
| **Host service** | A focused implementation under `openai4s/host/`, such as files, LLM, skills, delegation, MCP, or remote compute. |
| **Kernel** | The host-side manager plus one Python or R worker process speaking the JSON-line protocol. |
| **Kernel slot** | The Python or R lifecycle position owned by a Web session supervisor. |
| **Generation** | One durable identity for a particular worker incarnation. A restart creates a new generation. |
| **Kernel lease** | A frozen reference to one exact generation used for scoped interrupt, shutdown, and recovery. |
| **Execution ticket** | FIFO admission record binding a session, execution ID, and owner to the single active scientific writer. |
| **Frame** | Durable actor or conversation record. A delegated child has its own `frame_id`. |
| **Root frame** | The Web session and Artifact-collection boundary inherited by descendants. |
| **Project** | Shared grouping scope for sessions and selected project-level state. |
| **Branch** | A logical history projection selected from immutable checkpoints and append-only records. |
| **Workspace** | Session-scoped filesystem area used by Cells and captured deliverables. Files persist independently of in-memory namespaces. |
| **Artifact** | A durable logical deliverable with append-oriented version records and one current head. A version has immutable bytes only when its verified snapshot binding succeeded. |
| **Provenance** | Best-effort evidence relating files, Cells, environments, and supported object transformations. It is not universal Python dataflow tracking. |
| **Action Ledger** | Append-oriented record of proposed actions, canonical results, and terminal events. Attempts and lifecycle rows have separate mutable state. |
| **Projection** | A view derived from durable events/state for one consumer, such as provider history, UI conversation, Notebook, Timeline, or Artifacts. |
| **Completion** | Explicit successful termination from Engine-owned `finalize_response` or Python `host.submit_output(...)`. Ordinary prose, tool success, R Cells, cancellation, and max-turn exhaustion are not completion. |
| **Checkpoint** | Immutable durable record of selected workspace content and structured session state at a known history boundary. |
| **Recovery** | A staged attempt to rebuild workspace/runtime state. Workspace materialization and worker publication have different atomicity guarantees. |
| **Skill** | A versioned code recipe (`SKILL.md`, optionally `kernel.py`) loaded progressively into the scientific workflow. |
| **Compute provider** | A platform integration for staging and executing remote work. Current general remote-compute support is not a full scheduler. |
