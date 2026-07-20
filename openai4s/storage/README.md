# Storage repositories

[中文说明](README_zh.md)

The domain repositories behind [`Store`](../store.py) live here. `Store` owns the single SQLite connection, the schema and migrations, the query guard, the re-entrant lock, the cached facade generation, and the compatibility API; every repository in this package is handed that connection and that lock, and none of them opens a database of its own.

## Where this fits

The outer loop writes its canonical action ledger and its execution attempts here. Web and CLI projections go through the same `Store` facade to persist frames, messages, Cells, Artifacts, permissions, plans, delegation, kernel-generation identities, checkpoints, and recovery events. Host services read narrow repository projections for data, policy, session control, Skills, connectors, and progress.

A SQLite transaction can make a defined set of rows atomic. It cannot stretch across SQLite, workspace files, content-addressed blobs, a running Python/R namespace, remote compute, and WebSocket events all at once. That is why the repositories keep three things apart: append-only history, mutable materialized projections, and the best-effort binding to files that live outside the database.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Re-exports the repository classes that the `Store` composition facade wires up. |
| [`actions.py`](actions.py) | The canonical action ledger. Groups and events are immutable once written, and a reducer replays them to rebuild provider history, tool batches, and Cell observations. An execution attempt is allocated before the work starts; each lifecycle milestone is then filled exactly once, so a finished attempt can never be rewritten. |
| [`activation.py`](activation.py) | Activates one checkpoint branch in a single transaction: selected branch, session capabilities, conversation permission rules, visible Artifact heads, checkpoint state, and the selected Python environment all move together. A crash cannot publish a branch id whose surrounding policy and data still describe another branch. |
| [`agents.py`](agents.py) | Named specialist Agent profiles, with their skill and connector overrides stored as JSON. |
| [`annotations.py`](annotations.py) | Figure-review pins for one session/Artifact context: normalized coordinates, body, ordinal, status transitions, deletion. Allocating the ordinal and inserting the row stay in one critical section, so two concurrent pins cannot land on the same number. |
| [`artifacts.py`](artifacts.py) | Everything the system knows about a produced file. An Artifact is a stable identity; each write appends a version that records where the bytes are, the environment snapshot they were made under, and the Cell that produced them. Lineage edges connect an output version back to the input versions it was derived from, which is what lets the UI answer "what produced this figure, and what fed it" without reading the workspace. Restore records, priority, and the latest head are kept here as well. |
| [`branch_projection.py`](branch_projection.py) | Rebuilds the logical, branch-aware history from immutable checkpoint cursors plus the rows written after the current head. Physical append-only history is never deleted to make a branch read correctly. |
| [`capabilities.py`](capabilities.py) | Durable capability enablement. Precedence is the same for every kind of capability (session over project over global; an absent row means enabled), a materialized table answers the fast policy check, and each change also appends to an event table. Bootstrap manifests live here too. |
| [`checkpoint_state.py`](checkpoint_state.py) | The session-domain state that has to travel with a branch: plans, reviewer activity/settings/annotations, and project memories, captured as canonical JSON under a SHA-256 integrity digest. Imported state is validated and quarantined rather than trusted, identities are remapped, and only the verified scope is restored. |
| [`connectors.py`](connectors.py) | What an MCP server is configured to be. The command, its arguments, and its environment go in as JSON and are decoded back out on read, next to the enabled flag and the display name. Starting one of these servers is the MCP client's job, not this table's. |
| [`delegation.py`](delegation.py) | The bounded sub-Agent tree, made durable so it still reads correctly after a restart. Child slots are reserved against the session spawn cap inside one immediate transaction and released when the run ends, so a fanout cannot quietly overrun its budget. Child lifecycle, results, and steering messages are stored alongside it. |
| [`deletion.py`](deletion.py) | Deletes every SQLite aggregate owned by one session or one project in a single transaction. The compatibility schema has no foreign keys, so each owned table is named explicitly. It returns the filesystem paths that are now cleanup candidates and unlinks nothing itself. |
| [`frames.py`](frames.py) | The session spine: projects, the frame hierarchy and the scope a frame resolves to, the messages a user sees, activity steps, token counters, and frame search. The Cell execution log lives here too, and each logged Cell carries a visibility and a replay policy — that is how a protocol-only Cell can stay in the audit record while the read-only Notebook keeps it out of view. |
| [`kernels.py`](kernels.py) | Durable UUID identities for Python and R kernel generations, plus manifests, owner and process metadata, ordinals, activity, and terminal state. These rows describe a process lifecycle; they never claim that a live namespace was serialized. |
| [`memories.py`](memories.py) | Project-scoped long-term memory, and a deliberately small table. Add, list, delete, and the category and block projections callers read instead of grouping the rows themselves. |
| [`metadata.py`](metadata.py) | Five small repositories grouped in one module: project notes, folders, managed endpoint metadata, compaction archives, and the Host-call audit log. Credential reads are derivable and are never duplicated into the log; secret-bearing RPCs stay auditable by method name, but their raw arguments do not cross the persistence boundary. |
| [`permissions.py`](permissions.py) | Resolves scoped allow/ask/deny rules, seeds the local defaults, persists approval requests and events, and expires pending decisions that pass their deadline. A restart-continuation grant is narrowly bound and consumed atomically, once. |
| [`plans.py`](plans.py) | Structured plans for a frame, with per-step status and notes. |
| [`recovery.py`](recovery.py) | The recovery journal. Every attempt and every repair appends an ordered entry, so a failed or partial restore stays inspectable after the daemon restarts. |
| [`settings.py`](settings.py) | One key/value table, with two structured views built on top of it. Model profiles are stored as a JSON list, and `mutate_model_profiles` reads, edits, and writes them back under the `Store` lock so a concurrent edit cannot lose a profile. Message feedback is keyed by frame. |
| [`skills.py`](skills.py) | Content-addressed Skill packages: immutable blobs, files, and manifests; an installation pointer that only moves under optimistic concurrency; and an append-only history of activation and deactivation. Package validation and materialization belong to [`skills_loader/versions.py`](../skills_loader/versions.py), not here. |
| [`shares.py`](shares.py) | The `shares` table for web sharing: one durable row per share holding its lifecycle status (`publishing`/`ready`/`failed`/`revoked`), current snapshot id, bundle hash, and optional expiry. A partial unique index enforces at most one active share per session; the filesystem publish and lease GC live in `server/share_service.py`. |
| [`snapshots.py`](snapshots.py) | Two halves. `WorkspaceCAS` is a stdlib content-addressed store for workspace bytes, with restore previews, conflict detection, and release of trees plus the blobs nothing else shares; it is told which trees to keep. `SessionSnapshotRepository` holds the session branch and checkpoint envelopes, forks, and operation journals, and it is the half that discovers the retained trees, by querying the checkpoint rows. Neither half reads or writes the researcher's Git repository. |

## Durable model

- **Canonical history:** action groups and events, capability events, recovery-journal entries, Skill versions, and checkpoint operation records are append-oriented. Execution attempts and kernel generations do have lifecycle fields that advance, but advancing one must not rewrite history that is already complete.
- **UI/session projections:** frames, messages and steps, the active branch, Artifact heads, settings, plans, annotations, and profiles are mutable views. They are not the terminal signal, and they are not the audit record.
- **Workspace state:** `WorkspaceCAS` keeps immutable blobs and tree manifests below the OpenAI4S data directory. It skips the paths it recognizes as secrets, refuses files past its size limit, and never reads or changes the researcher's Git index or branch.
- **Kernel state:** a checkpoint envelope carries environment and generation references and a replay recipe, not pickled Python/R memory. What can actually be rebuilt is decided by [`kernel/recovery.py`](../kernel/recovery.py).

## Consistency and security boundaries

- An Artifact version is fully immutable only once its snapshot binding succeeds. If snapshot capture fails, the row can keep a live, path-backed reference instead. Callers have to inspect the metadata rather than assume every version has frozen bytes behind it.
- Workspace restore is conflict-aware and replaces each file atomically, but it is not a transaction over the whole filesystem. A failure partway through can leave a partially restored tree, and diagnosing that needs the operation and recovery records.
- Checkpoint activation makes the conversation-scoped database projections it lists atomic, and nothing more. Project and global policy stays live, and filesystem and kernel recovery are coordinated separately.
- Read-only agent SQL is enforced by the `Store` query guard, not by handing out restricted database access. The repository methods themselves are trusted in-process code.
- A permission decision persists authority metadata. It does not persist a resumable Python stack or the execution arguments. After a restart, a matching fresh action has to consume a narrow continuation grant.
- Connector configuration, like other JSON metadata, can carry sensitive operator input. Audit redaction rules cover specific Host calls, not every field anyone might store, so deployment backups need to be protected accordingly.
- Deletion commits the SQLite ownership changes first and then returns candidate paths. Server-side cleanup must revalidate those paths before unlinking, and a successful database transaction is no proof that the bytes were cleaned up.

## Related documentation

- [System architecture](../../docs/architecture.md)
- [Web runtime](../../docs/webapp.md)
- [Security model](../../docs/security.md)
- [Store facade](../store.py)
