# Storage repositories

[中文说明](README_zh.md)

This directory contains the domain repositories behind [`Store`](../store.py). `Store` owns the single SQLite connection, schema/migrations, query guard, re-entrant lock, cached facade generation, and compatibility API; repositories share that connection and lock rather than opening databases of their own.

## Place in the architecture

The outer loop writes its canonical action ledger and execution attempts here. Web/CLI projections also persist frames, messages, Cells, Artifacts, permissions, plans, delegation, kernel-generation identities, checkpoints, and recovery events through the same `Store` facade. Host services consume narrow repository projections for data, policy, session control, skills, connectors, and progress.

SQLite transactions can make a defined set of rows atomic. They do **not** create one transaction across SQLite, workspace files, content-addressed blobs, running Python/R namespaces, remote compute, or WebSocket events. Repositories therefore distinguish append-only history, mutable materialized projections, and best-effort external-file binding.

## Files

| File | Responsibility |
|---|---|
| [`__init__.py`](__init__.py) | Re-exports repository classes used by the `Store` composition facade. |
| [`actions.py`](actions.py) | Stores immutable ordered action groups/events for provider history and tool/Cell observations; allocates execution attempts before work and fills each lifecycle milestone only once. |
| [`activation.py`](activation.py) | Atomically activates one checkpoint branch's conversation-scoped SQLite projection: selected branch, session capabilities, conversation permission rules, visible Artifact heads, checkpoint state, and selected Python environment. |
| [`agents.py`](agents.py) | Persists named specialist Agent profiles and their JSON skill/connector overrides. |
| [`annotations.py`](annotations.py) | Stores normalized image-review pins, bodies, ordinals, status transitions, and deletion for one session/Artifact context. |
| [`artifacts.py`](artifacts.py) | Manages Artifact identities and versions, file-path resolution, environment snapshots, restore records, priorities/latest heads, producing Cells, and version-level lineage edges. |
| [`branch_projection.py`](branch_projection.py) | Reconstructs logical branch-aware history from immutable checkpoint cursors plus post-head local rows without deleting physical append-only history. |
| [`capabilities.py`](capabilities.py) | Persists capability enablement with session → project → global precedence, append-only state events, and bootstrap manifests. |
| [`checkpoint_state.py`](checkpoint_state.py) | Captures integrity-digested checkpoint state for plans, reviewer activity/settings/annotations, and project memories; validates/quarantines imported state, remaps identities, and restores only verified scope. |
| [`connectors.py`](connectors.py) | Persists and decodes MCP connector command, arguments, environment, enabled state, and display metadata. |
| [`delegation.py`](delegation.py) | Stores the bounded sub-Agent tree, session budget/leases, child lifecycle/results, and steering messages for restart-safe projection. |
| [`deletion.py`](deletion.py) | Deletes all explicitly owned SQLite aggregates for one session or project in a single transaction and returns filesystem cleanup candidates without unlinking them. |
| [`frames.py`](frames.py) | Persists projects, frame hierarchy/scope, visible messages, activity steps, token counters, searchable frame details, and Cell execution logs with replay/visibility metadata. |
| [`kernels.py`](kernels.py) | Stores durable UUID identities, manifests, owner/process metadata, ordinals, activity, and terminal state for Python/R kernel generations; never claims to serialize their namespaces. |
| [`memories.py`](memories.py) | Provides project-scoped long-term memory CRUD and category/block projections. |
| [`metadata.py`](metadata.py) | Groups small repositories for project notes, folders, managed endpoint metadata, compaction archives, and Host-call audit records with special handling for derivable/secret-bearing RPC arguments. |
| [`permissions.py`](permissions.py) | Resolves scoped allow/ask/deny rules, seeds local defaults, persists approval requests/events, expires decisions, and atomically consumes narrowly bound restart-continuation grants. |
| [`plans.py`](plans.py) | Persists structured plans and per-step statuses/notes for a frame. |
| [`recovery.py`](recovery.py) | Appends ordered recovery-attempt and repair journal entries so failed or partial recovery remains inspectable. |
| [`settings.py`](settings.py) | Stores key/value settings and structured projections for model profiles and message feedback. |
| [`skills.py`](skills.py) | Stores immutable content-addressed Skill files/manifests, active installation pointers, optimistic activation/deactivation, and append-only version history. |
| [`snapshots.py`](snapshots.py) | Implements a stdlib workspace content-addressed store and session branch/checkpoint envelopes, restore previews/conflicts, forks, operation journals, and retained-tree discovery without modifying the user's Git repository. |

## Subdirectories

There are no tracked child directories in this package.

## Durable model

- **Canonical history:** action groups/events, capability events, recovery journal entries, Skill versions, and checkpoint operation records are append-oriented. Execution attempts and kernel generations have controlled lifecycle fields that advance but must not rewrite completed history.
- **UI/session projections:** frames/messages/steps, active branch, Artifact heads, settings, plans, annotations, and profiles are mutable views. They must not be mistaken for the terminal signal or complete audit record.
- **Workspace state:** `WorkspaceCAS` stores immutable blobs/tree manifests below the OpenAI4S data directory, excludes recognized secret paths, limits file sizes, and never uses or changes the researcher's Git index/branch.
- **Kernel state:** checkpoint envelopes retain environment/generation references and replay recipes, not pickled Python/R memory. [`kernel/recovery.py`](../kernel/recovery.py) decides what can safely be rebuilt.

## Consistency and security boundaries

- An Artifact version is fully immutable only when its snapshot binding succeeds. A row may retain a live/path-backed reference when snapshot capture fails; callers must inspect metadata rather than assume every version has frozen bytes.
- Workspace restore is conflict-aware and uses atomic replacement per file, but it is not an all-filesystem transaction. A mid-restore failure can leave a partially restored tree, with operation/recovery records needed for diagnosis.
- Checkpoint activation makes its listed conversation-scoped **database projections** atomic. Project/global policy remains live, and filesystem/kernel recovery is coordinated separately.
- Read-only agent SQL is enforced by the `Store` query guard, not by granting direct database access. Repository methods themselves are trusted in-process code.
- Permission decisions persist authority metadata, not resumable Python stacks or stored execution arguments. After restart, a matching fresh action must consume a narrow continuation grant.
- Connector configuration and other JSON metadata can contain sensitive operator input; audit redaction rules cover specific Host calls, not every arbitrary stored field. Deployment backups must be protected accordingly.
- Deletion first commits SQLite ownership changes and only returns candidate paths. Server-side cleanup must revalidate those paths before unlinking; database success does not prove byte cleanup succeeded.

## Related documentation

- [System architecture](../../docs/architecture.md)
- [Web runtime](../../docs/webapp.md)
- [Security model](../../docs/security.md)
- [Store facade](../store.py)
