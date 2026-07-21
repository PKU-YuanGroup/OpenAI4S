"""SQLite data model — the shared store the whole system records into.

openai4s exposes these tables read-only through `host.query`; the host writes them
as turns/cells/artifacts/compactions happen. Schema and write paths:

  frames            turn tree (self-referential), per-turn model/effort/token/cost
  action_groups     canonical provider/action groups, ordered per branch
  action_events     append-only proposed/result/lifecycle events within a group
  execution_attempts  attempt-first code execution lifecycle records
  execution_log     per-cell record (code + usage wall/cpu/rss + error)
  artifacts         logical artifact (filename, content_type)
  artifact_versions versioned bytes (version_id, checksum) -> artifacts
  compaction_archives  compacted history slices
  agents            agent profile definitions
  custom_skills     user-authored SKILL.md bodies
  skill_*           immutable Skill blobs/versions and activation history
  capability_*      scoped enablement events/state + bootstrap manifests
  memories          memory blocks (scope/block-listed in host.query)
  managed_endpoints local model endpoints
  notes             project notes
  lineage_edges     object-level data lineage: input_version -> output_version
  host_call_log     RPC audit (DERIVABLE_HOST_CALLS are NOT logged; the args of
                    SECRET_ARG_HOST_CALLS are redacted before write)

Secret-bearing tables (`settings` holds the LLM API key + model profiles,
`connectors` holds MCP server env/command) plus the internal audit/memory tables
are on QUERY_DENYLIST, so `host.query` refuses to read them and `host.query`'s
schema view hides them.

All timestamps are epoch-ms. Booleans are 0/1. One DB per data_dir.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from openai4s.capabilities import CapabilityStateService, SpecialistProfileService
from openai4s.execution.dependencies import (
    analyze_code,
    default_replay_policy,
    default_visibility,
)
from openai4s.security.permissions import harden_db, harden_dir
from openai4s.storage.actions import ActionLedgerRepository
from openai4s.storage.activation import SessionActivationRepository
from openai4s.storage.agents import AgentProfileRepository
from openai4s.storage.annotations import AnnotationRepository
from openai4s.storage.artifacts import ArtifactRepository
from openai4s.storage.artifacts import file_identity as _file_identity
from openai4s.storage.artifacts import same_file_path as _same_file_path
from openai4s.storage.branch_projection import count_cursor, project_branch_records
from openai4s.storage.capabilities import CapabilityStateRepository
from openai4s.storage.checkpoint_state import CheckpointStateRepository
from openai4s.storage.compute_jobs import ComputeJobRepository
from openai4s.storage.connectors import (
    ConnectorRepository,
    broker_connector_env,
    forget_connector_env,
    resolve_connector_env,
)
from openai4s.storage.delegation import DelegationProjectionRepository
from openai4s.storage.frames import FrameRepository
from openai4s.storage.kernels import KernelGenerationRepository
from openai4s.storage.memories import MemoryRepository
from openai4s.storage.metadata import (
    DERIVABLE_HOST_CALLS,
    SECRET_ARG_HOST_CALLS,
    CompactionRepository,
    EndpointRepository,
    FolderRepository,
    HostCallRepository,
    NotesRepository,
)
from openai4s.storage.migrations import (
    SCHEMA_VERSION,
    MigrationError,
    _is_duplicate_column,
    applied_migrations,
    current_version,
    run_migrations,
)
from openai4s.storage.permissions import (
    DEFAULT_PERMISSION_RULES as _DEFAULT_PERMISSION_RULES,
)
from openai4s.storage.permissions import PermissionRuleRepository
from openai4s.storage.permissions import perm_match as _perm_match
from openai4s.storage.plans import PlanRepository
from openai4s.storage.recovery import RecoveryJournalRepository
from openai4s.storage.settings import SettingsRepository
from openai4s.storage.shares import SharesRepository
from openai4s.storage.skills import SkillVersionRepository
from openai4s.storage.snapshots import SessionSnapshotRepository

_SCHEMA = """
CREATE TABLE IF NOT EXISTS frames (
    frame_id      TEXT PRIMARY KEY,
    parent_id     TEXT,
    project_id    TEXT NOT NULL DEFAULT 'default',
    root_frame_id TEXT,
    kind          TEXT,               -- 'turn' | 'delegate' | 'compaction_fork'
    name          TEXT,
    task_summary  TEXT,               -- auto one-line summary shown in the UI
    model         TEXT,
    effort        TEXT,
    status        TEXT,               -- 'processing'|'done'|'failed'|'awaiting_user_response'
    runtime_env   TEXT,
    depth         INTEGER NOT NULL DEFAULT 0,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cost_usd      REAL,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_frames_parent  ON frames(parent_id);
CREATE INDEX IF NOT EXISTS ix_frames_project ON frames(project_id);

CREATE TABLE IF NOT EXISTS projects (
    project_id    TEXT PRIMARY KEY,
    name          TEXT,
    description   TEXT,
    context       TEXT,               -- agent context prepended to prompts
    is_example    INTEGER NOT NULL DEFAULT 0,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    message_id    TEXT PRIMARY KEY,
    root_frame_id TEXT NOT NULL,
    branch_id     TEXT,
    frame_id      TEXT,
    seq           INTEGER NOT NULL,
    role          TEXT NOT NULL,      -- 'user' | 'assistant'
    content       TEXT,               -- plain text (may be markdown)
    metadata      TEXT,               -- JSON blob (optional)
    created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_msg_root ON messages(root_frame_id);

CREATE TABLE IF NOT EXISTS execution_log (
    producing_cell_id TEXT PRIMARY KEY,
    frame_id      TEXT,
    root_frame_id TEXT,
    project_id    TEXT NOT NULL DEFAULT 'default',
    cell_seq      INTEGER,
    cell_index    INTEGER,
    state_revision INTEGER,
    kernel_id     TEXT,
    language      TEXT,
    status        TEXT,
    origin        TEXT,
    code          TEXT NOT NULL,
    code_hash     TEXT NOT NULL,
    visibility    TEXT NOT NULL DEFAULT 'scientific'
                  CHECK (visibility IN ('scientific','scratch','recovery','system')),
    pin           INTEGER NOT NULL DEFAULT 0 CHECK (pin IN (0,1)),
    replay_policy TEXT NOT NULL DEFAULT 'conditional'
                  CHECK (replay_policy IN ('safe','conditional','never')),
    variable_reads TEXT NOT NULL DEFAULT '[]',
    variable_writes TEXT NOT NULL DEFAULT '[]',
    variable_deletes TEXT NOT NULL DEFAULT '[]',
    mutation_uncertain INTEGER NOT NULL DEFAULT 0
                  CHECK (mutation_uncertain IN (0,1)),
    stdout        TEXT,
    stderr        TEXT,
    error         TEXT,
    figures       TEXT,               -- JSON list of artifact filenames
    files_read    TEXT,               -- JSON list of relative paths
    files_written TEXT,               -- JSON list of relative paths
    interrupted   INTEGER NOT NULL DEFAULT 0,
    wall_s        REAL,
    cpu_s         REAL,
    peak_rss_kb   INTEGER,
    created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_exec_frame ON execution_log(frame_id);
CREATE INDEX IF NOT EXISTS ix_exec_root  ON execution_log(root_frame_id);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id   TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL DEFAULT 'default',
    root_frame_id TEXT,
    filename      TEXT NOT NULL,
    content_type  TEXT,
    is_user_upload INTEGER NOT NULL DEFAULT 0,
    priority      INTEGER NOT NULL DEFAULT 0,
    latest_version_id TEXT,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS artifact_versions (
    version_id    TEXT PRIMARY KEY,
    artifact_id   TEXT NOT NULL,
    filename      TEXT,
    content_type  TEXT,
    size_bytes    INTEGER,
    checksum      TEXT,
    path          TEXT NOT NULL,
    snapshot_path TEXT,
    producing_cell_id TEXT,
    frame_id      TEXT,
    created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ver_artifact ON artifact_versions(artifact_id);

-- De-duplicated environment snapshots (one row per distinct kernel env). An
-- artifact_version references one via env_snapshot_id so a figure records the
-- package set that PRODUCED it (see gateway._environment_snapshot).
CREATE TABLE IF NOT EXISTS env_snapshots (
    snapshot_id    TEXT PRIMARY KEY,
    created_at     INTEGER NOT NULL,
    kind           TEXT,
    python_version TEXT,
    implementation TEXT,
    platform       TEXT,
    package_count  INTEGER,
    packages_json  TEXT,
    remote_json    TEXT               -- JSON list of remote-GPU job provenance
);

CREATE TABLE IF NOT EXISTS compaction_archives (
    archive_id    TEXT PRIMARY KEY,
    frame_id      TEXT,
    project_id    TEXT NOT NULL DEFAULT 'default',
    branch_id     TEXT,
    ledger_cursor TEXT,
    recovery_pointer TEXT,
    generation_id TEXT,
    metadata      TEXT,
    summary       TEXT,
    handoff       TEXT,
    compacted     TEXT,               -- JSON of the raw slice
    n_messages    INTEGER,
    context_before TEXT,
    context_after TEXT,
    artifact_refs TEXT,
    created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    name          TEXT PRIMARY KEY,   -- UPPER_SNAKE (2-32)
    description   TEXT,
    skill_names   TEXT,               -- JSON list or NULL (=unrestricted)
    connectors    TEXT,               -- JSON list
    unrestricted  INTEGER NOT NULL DEFAULT 1,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS custom_skills (
    name          TEXT PRIMARY KEY,
    origin        TEXT,
    skill_md      TEXT,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);

-- Shared enablement policy for Skills, Specialists, and future capability
-- kinds.  ``capability_events`` is append-only; ``capability_states`` is its
-- efficient current-state projection.  Session state overrides project state,
-- which overrides global state.
CREATE TABLE IF NOT EXISTS capability_states (
    kind            TEXT NOT NULL,
    name            TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    scope           TEXT NOT NULL,       -- global | project | session
    scope_id        TEXT NOT NULL DEFAULT '',
    enabled         INTEGER NOT NULL,
    metadata        TEXT,                -- JSON, non-secret manifest hints
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    PRIMARY KEY(kind, normalized_name, scope, scope_id)
);
CREATE TABLE IF NOT EXISTS capability_events (
    event_id        TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    name            TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    scope           TEXT NOT NULL,
    scope_id        TEXT NOT NULL DEFAULT '',
    event           TEXT NOT NULL,       -- enabled | disabled | sidecar_loaded
    enabled         INTEGER,
    metadata        TEXT,
    created_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_capability_events_lookup
    ON capability_events(kind, normalized_name, created_at);
CREATE TABLE IF NOT EXISTS capability_manifests (
    manifest_id TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    project_id  TEXT,
    kind        TEXT NOT NULL,
    entries     TEXT NOT NULL,            -- JSON snapshot, loaded=false initially
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_capability_manifest_session
    ON capability_manifests(session_id, kind, created_at);

CREATE TABLE IF NOT EXISTS memories (
    memory_id     TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL DEFAULT 'default',
    block         TEXT,               -- memory block name
    content       TEXT,
    created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS managed_endpoints (
    name          TEXT PRIMARY KEY,
    url           TEXT,
    skill         TEXT,
    port          INTEGER,
    status        TEXT,               -- 'registered'|'starting'|'live'|'stopped'
    credential    TEXT,
    start_script  TEXT,
    stop_script   TEXT,
    live_route    TEXT,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    note_id       TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL DEFAULT 'default',
    title         TEXT,
    body          TEXT,
    created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS lineage_edges (
    edge_id           TEXT PRIMARY KEY,
    input_version_id  TEXT NOT NULL,
    output_version_id TEXT NOT NULL,
    producing_cell_id TEXT,
    frame_id          TEXT,
    created_at        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_edge_out ON lineage_edges(output_version_id);
CREATE INDEX IF NOT EXISTS ix_edge_in  ON lineage_edges(input_version_id);

CREATE TABLE IF NOT EXISTS host_call_log (
    call_id       TEXT PRIMARY KEY,
    frame_id      TEXT,
    action_group_id TEXT,
    action_id     TEXT,
    permission_decision_id TEXT,
    method        TEXT NOT NULL,
    args_preview  TEXT,
    result_preview TEXT,
    result_digest TEXT,
    side_effect_class TEXT,
    resource_keys TEXT,
    ok            INTEGER NOT NULL DEFAULT 1,
    created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key           TEXT PRIMARY KEY,
    value         TEXT,
    updated_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS folders (
    folder_id     TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL DEFAULT 'default',
    name          TEXT NOT NULL,
    created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS connectors (
    connector_id  TEXT PRIMARY KEY,   -- slug
    name          TEXT NOT NULL,
    description   TEXT,
    command       TEXT NOT NULL,      -- JSON list argv OR a shell string
    args          TEXT,               -- JSON list
    env           TEXT,               -- JSON dict
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);
-- Remote compute jobs. These outlive the process that submitted them: an ssh
-- job keeps running under nohup and a byoc sandbox keeps billing whether or not
-- this daemon is alive. Holding them only in memory (which is what
-- ComputeManager did) meant a restart stranded every in-flight job — the remote
-- work continued with nothing left that could find, harvest, or cancel it.
CREATE TABLE IF NOT EXISTS compute_jobs (
    job_id          TEXT PRIMARY KEY,
    -- Stable across a resubmit of the same logical work. Reconciliation looks
    -- a job up by this before submitting, so a crash between "provider
    -- accepted" and "we recorded it" cannot become a double-charge.
    idempotency_key TEXT,
    provider        TEXT NOT NULL,     -- "ssh:<alias>" | "byoc:<id>"
    status          TEXT NOT NULL,     -- see compute/manager.py's state machine
    alias           TEXT,              -- ssh
    workdir         TEXT,              -- ssh
    pid             TEXT,              -- ssh
    sandbox_id      TEXT,              -- byoc
    -- The provider's own acknowledgement of the submit. Evidence the job
    -- exists remotely, independent of anything we chose to believe.
    receipt         TEXT,
    outputs         TEXT,              -- JSON: declared output globs
    exit_code       INTEGER,
    reason          TEXT,              -- why a terminal state was reached
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    submitted_at    INTEGER,
    terminal_at     INTEGER
);
CREATE INDEX IF NOT EXISTS ix_compute_jobs_status ON compute_jobs(status);
CREATE UNIQUE INDEX IF NOT EXISTS ix_compute_jobs_idem
    ON compute_jobs(idempotency_key) WHERE idempotency_key IS NOT NULL;
-- Append-only, monotonically sequenced per job. A status column alone says
-- where a job is; this says how it got there, which is what a restart needs to
-- tell "we never submitted" from "we submitted and lost the response".
CREATE TABLE IF NOT EXISTS compute_job_events (
    job_id   TEXT NOT NULL,
    seq      INTEGER NOT NULL,
    kind     TEXT NOT NULL,
    at       INTEGER NOT NULL,
    payload  TEXT,                     -- JSON
    PRIMARY KEY (job_id, seq)
);
CREATE TABLE IF NOT EXISTS frame_steps (
    step_id       TEXT PRIMARY KEY,
    frame_id      TEXT NOT NULL,
    seq           INTEGER NOT NULL,
    kind          TEXT NOT NULL,      -- search|plan|env|skill|bash|edit|write|read|files|artifact|delegate|mcp|fetch|code
    title         TEXT,
    summary       TEXT,               -- one-line result summary (shown as meta)
    input         TEXT,               -- JSON
    output        TEXT,               -- JSON
    status        TEXT,               -- running|done|error
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_frame_steps_frame ON frame_steps(frame_id, seq);

CREATE TABLE IF NOT EXISTS annotations (
    annotation_id  TEXT PRIMARY KEY,
    root_frame_id  TEXT NOT NULL,
    artifact_id    TEXT NOT NULL,
    artifact_name  TEXT,
    rel_x          REAL NOT NULL,      -- 0..1 fraction of image width
    rel_y          REAL NOT NULL,      -- 0..1 fraction of image height
    number         INTEGER NOT NULL,   -- pin ordinal within (frame,artifact)
    body           TEXT NOT NULL,      -- the comment
    status         TEXT NOT NULL DEFAULT 'open',   -- open|sent|resolved
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_annot_frame    ON annotations(root_frame_id);
CREATE INDEX IF NOT EXISTS ix_annot_artifact ON annotations(artifact_id);

CREATE TABLE IF NOT EXISTS plans (
    plan_id       TEXT PRIMARY KEY,
    frame_id      TEXT NOT NULL,
    project_id    TEXT NOT NULL DEFAULT 'default',
    title         TEXT,
    rationale     TEXT,
    confidence    TEXT,               -- 'high'|'medium'|'low' (or a 0..1 string)
    steps         TEXT NOT NULL,      -- JSON [{id,title,detail,deliverables:[...]}]
    status        TEXT NOT NULL DEFAULT 'draft',   -- draft|executing|completed|failed|discarded
    step_status   TEXT,               -- JSON {step_id: {status, note, updated_at}}
    artifact_id   TEXT,               -- the plan_*.json artifact (so revises re-version it)
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plans_frame ON plans(frame_id, created_at);

-- opencode-style tool-call permission rules. Each rule maps a (tool, pattern)
-- to allow|ask|deny at one of three scopes: 'global' (scope_id=''),
-- 'project' (scope_id=project_id) or 'conversation' (scope_id=root_frame_id).
CREATE TABLE IF NOT EXISTS permission_rules (
    rule_id       TEXT PRIMARY KEY,
    scope         TEXT NOT NULL,               -- global | project | conversation
    scope_id      TEXT NOT NULL DEFAULT '',    -- '' for global; project_id; root_frame_id
    tool          TEXT NOT NULL,               -- host method name, or '*'
    pattern       TEXT NOT NULL DEFAULT '*',   -- glob matched against the tool target
    decision      TEXT NOT NULL,               -- allow | ask | deny
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_perm ON permission_rules(scope, scope_id, tool, pattern);
CREATE INDEX IF NOT EXISTS ix_perm_scope ON permission_rules(scope, scope_id);

-- Durable approval requests. Unlike permission_rules (standing policy), each
-- row is one concrete action decision and remains auditable across reconnects
-- and daemon restarts. Terminal requests are immutable.
CREATE TABLE IF NOT EXISTS permission_requests (
    decision_id    TEXT PRIMARY KEY,
    root_frame_id  TEXT,
    frame_id       TEXT,
    project_id     TEXT,
    action_group_id TEXT,
    action_id      TEXT,
    tool_call_id   TEXT,
    tool           TEXT NOT NULL,
    target         TEXT NOT NULL DEFAULT '',
    side_effect_class TEXT,
    resource_keys  TEXT,
    payload        TEXT,
    state          TEXT NOT NULL DEFAULT 'pending',
    scope          TEXT,
    pattern        TEXT,
    message        TEXT,
    resolution_context TEXT,
    continuation_required INTEGER NOT NULL DEFAULT 0,
    continuation_expires_at INTEGER,
    continuation_consumed_at INTEGER,
    created_at     INTEGER NOT NULL,
    expires_at     INTEGER,
    resolved_at    INTEGER
);
CREATE INDEX IF NOT EXISTS ix_permission_request_root
    ON permission_requests(root_frame_id, state, created_at);
CREATE TABLE IF NOT EXISTS shares (
    share_id       TEXT PRIMARY KEY,
    root_frame_id  TEXT NOT NULL,
    title          TEXT,
    status         TEXT NOT NULL DEFAULT 'publishing'
                   CHECK (status IN ('publishing','ready','failed','revoked')),
    snapshot_id    TEXT,
    pending_snapshot_id TEXT,
    bundle_sha256  TEXT,
    bundle_size    INTEGER,
    projection_id  TEXT,
    counts_json    TEXT,
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL,
    revoked_at     INTEGER,
    expires_at     INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_shares_active_frame
    ON shares(root_frame_id) WHERE status IN ('publishing','ready');
CREATE INDEX IF NOT EXISTS ix_shares_root ON shares(root_frame_id);
"""

# Tables host.query must refuse to read. These hold secrets or
# internal audit/memory state that is not part of the agent-visible data model:
#   settings          -> LLM API key + model profiles (which embed API keys)
#   connectors        -> MCP server env vars / launch command (may embed tokens)
#   memories          -> memory blocks (surfaced through host.remember, not SQL)
#   host_call_log     -> RPC audit trail
#   permission_rules  -> permission broker state
#   action_* / execution_attempts -> provider wire state and raw action audit
QUERY_DENYLIST = frozenset(
    {
        "settings",
        "connectors",
        "memories",
        "host_call_log",
        "permission_rules",
        "permission_requests",
        "action_groups",
        "action_events",
        "execution_attempts",
        "kernel_generations",
        "capability_states",
        "capability_events",
        "capability_manifests",
        "skill_blobs",
        "skill_versions",
        "skill_version_files",
        "skill_installations",
        "skill_installation_events",
        "delegation_sessions",
        "delegation_children",
        "delegation_steering",
        "session_branches",
        "session_branch_selection",
        "session_checkpoints",
        "checkpoint_state_snapshots",
        "snapshot_operations",
        "recovery_journal",
    }
)

# Single-quoted string literals and SQL comments are stripped before the denylist
# substring test so a denied table name that appears only inside a *literal*
# (e.g. SELECT 'see settings' AS note) is not falsely rejected — a real table
# reference can never live inside a string literal. Double-quoted / bracketed /
# backtick spans are left intact because SQL uses them to quote identifiers
# (e.g. FROM "settings"), which must still trip the denylist.
_SQL_LITERAL_RE = re.compile(
    r"'(?:[^']|'')*'"  # single-quoted string (with '' escape)
    r"|--[^\n]*"  # line comment
    r"|/\*.*?\*/",  # block comment
    re.DOTALL,
)


def _strip_sql_literals(sql: str) -> str:
    """Blank out single-quoted string literals and comments for denylist checks."""
    return _SQL_LITERAL_RE.sub(" ", sql or "")


def _now_ms() -> int:
    return int(time.time() * 1000)


# How long a writer waits for a competing lock before raising "database is
# locked". Python's sqlite3 already defaults this to 5s via connect(timeout=);
# naming it makes the value a decision rather than a coincidence, and gives the
# multi-process case (openai4s run / init alongside a live daemon) one place to
# tune.
_BUSY_TIMEOUT_S = 5.0


class Store:
    """Thread-safe SQLite wrapper. One per data_dir; created lazily."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._closed = False
        # mode= on mkdir is masked by the umask and only applies on creation,
        # so harden explicitly and unconditionally afterwards.
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        harden_dir(self.db_path.parent)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=_BUSY_TIMEOUT_S,
        )
        # SQLite creates the file at the process umask — 0644 on most systems.
        # This database holds plaintext credentials, so close it to the owner
        # as soon as it exists and before any schema is written into it.
        harden_db(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._apply_pragmas()
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        # Re-run: the schema write is the first thing that can materialise a
        # -wal/-shm sidecar, which would otherwise be born world-readable
        # carrying the same rows.
        harden_db(self.db_path)
        self._migrate()
        self._actions = ActionLedgerRepository(
            self._conn,
            self._lock,
            clock_ms=lambda: _now_ms(),
        )
        self._kernel_generations = KernelGenerationRepository(
            self._conn,
            self._lock,
            clock_ms=lambda: _now_ms(),
        )
        self._checkpoint_states = CheckpointStateRepository(
            self._conn,
            self._lock,
            clock_ms=lambda: _now_ms(),
        )
        self._session_snapshots = SessionSnapshotRepository(
            self._conn,
            self._lock,
            clock_ms=lambda: _now_ms(),
            checkpoint_state=self._checkpoint_states,
        )
        self._session_activation = SessionActivationRepository(
            self._conn,
            self._lock,
            clock_ms=lambda: _now_ms(),
            checkpoint_state=self._checkpoint_states,
        )
        self._recovery_journal = RecoveryJournalRepository(
            self._conn,
            self._lock,
            clock_ms=lambda: _now_ms(),
        )
        self._delegations = DelegationProjectionRepository(
            self._conn,
            self._lock,
            clock_ms=lambda: _now_ms(),
        )
        self._plans = PlanRepository(
            self._conn,
            self._lock,
            clock_ms=lambda: _now_ms(),
        )
        self._annotations = AnnotationRepository(
            self._conn,
            self._lock,
            clock_ms=lambda: _now_ms(),
        )
        self._memories = MemoryRepository(
            self._conn,
            self._lock,
            clock_ms=lambda: _now_ms(),
        )
        self._settings = SettingsRepository(
            self._conn,
            self._lock,
            clock_ms=lambda: _now_ms(),
        )
        self._shares = SharesRepository(
            self._conn,
            self._lock,
            clock_ms=lambda: _now_ms(),
        )
        self._permissions = PermissionRuleRepository(
            self._conn,
            self._lock,
            clock_ms=lambda: _now_ms(),
            get_setting=self.get_setting,
            set_setting=self.set_setting,
        )
        self._connectors = ConnectorRepository(
            self._conn,
            self._lock,
            clock_ms=lambda: _now_ms(),
        )
        self._compute_jobs = ComputeJobRepository(
            self._conn,
            self._lock,
            clock_ms=lambda: _now_ms(),
        )
        self._agents = AgentProfileRepository(
            self._conn,
            self._lock,
            clock_ms=lambda: _now_ms(),
        )
        self._capability_repository = CapabilityStateRepository(
            self._conn,
            self._lock,
            clock_ms=lambda: _now_ms(),
        )
        self._capabilities = CapabilityStateService(self._capability_repository)
        self._skill_versions = SkillVersionRepository(
            self._conn,
            self._lock,
            clock_ms=lambda: _now_ms(),
        )
        self._specialists = SpecialistProfileService(
            self._agents,
            self._capabilities,
        )
        self._frames = FrameRepository(
            self._conn,
            self._lock,
            clock_ms=lambda: _now_ms(),
            get_frame=lambda frame_id: self.get_frame(frame_id),
            resolve_frame_scope=lambda frame_id, **kwargs: self.resolve_frame_scope(
                frame_id, **kwargs
            ),
            get_project=lambda project_id: self.get_project(project_id),
        )
        self._artifacts = ArtifactRepository(
            self._conn,
            self._lock,
            clock_ms=lambda: _now_ms(),
            get_frame=lambda frame_id: self.get_frame(frame_id),
            resolve_frame_scope=lambda frame_id, **kwargs: self.resolve_frame_scope(
                frame_id, **kwargs
            ),
            resolve_artifact_write_scope=lambda **kwargs: self._artifact_write_scope(
                **kwargs
            ),
            execute=lambda sql, params=(): self._exec(sql, params),
            get_artifact=lambda artifact_id: self.get_artifact(artifact_id),
            get_env_snapshot=lambda snapshot_id: self.get_env_snapshot(snapshot_id),
            identify_file=lambda path: _file_identity(path),
            paths_match=lambda left, right: _same_file_path(left, right),
        )
        self._notes = NotesRepository(
            self._conn,
            self._lock,
            clock_ms=lambda: _now_ms(),
        )
        self._folders = FolderRepository(
            self._conn,
            self._lock,
            clock_ms=lambda: _now_ms(),
        )
        self._endpoints = EndpointRepository(
            self._conn,
            self._lock,
            clock_ms=lambda: _now_ms(),
        )
        self._compactions = CompactionRepository(
            self._conn,
            self._lock,
            clock_ms=lambda: _now_ms(),
        )
        self._host_calls = HostCallRepository(
            self._conn,
            self._lock,
            clock_ms=lambda: _now_ms(),
        )

    # --- migration (add columns missing from a pre-existing DB) -----------
    _MIGRATIONS = {
        "messages": [("branch_id", "TEXT")],
        "shares": [("expires_at", "INTEGER")],
        "frames": [
            ("task_summary", "TEXT"),
            ("folder_id", "TEXT"),
            ("runtime_env", "TEXT"),
        ],
        "agents": [("system_prompt", "TEXT"), ("kind", "TEXT")],
        "artifact_versions": [("env_snapshot_id", "TEXT"), ("snapshot_path", "TEXT")],
        "env_snapshots": [("remote_json", "TEXT")],
        "execution_log": [
            ("root_frame_id", "TEXT"),
            ("cell_index", "INTEGER"),
            ("state_revision", "INTEGER"),
            ("kernel_id", "TEXT"),
            ("language", "TEXT"),
            ("status", "TEXT"),
            ("code_hash", "TEXT"),
            ("visibility", "TEXT NOT NULL DEFAULT 'scientific'"),
            ("pin", "INTEGER NOT NULL DEFAULT 0"),
            ("replay_policy", "TEXT NOT NULL DEFAULT 'conditional'"),
            ("variable_reads", "TEXT NOT NULL DEFAULT '[]'"),
            ("variable_writes", "TEXT NOT NULL DEFAULT '[]'"),
            ("variable_deletes", "TEXT NOT NULL DEFAULT '[]'"),
            ("mutation_uncertain", "INTEGER NOT NULL DEFAULT 0"),
            ("figures", "TEXT"),
            ("files_read", "TEXT"),
            ("files_written", "TEXT"),
        ],
        "permission_requests": [
            ("resolution_context", "TEXT"),
            ("continuation_required", "INTEGER NOT NULL DEFAULT 0"),
            ("continuation_expires_at", "INTEGER"),
            ("continuation_consumed_at", "INTEGER"),
            ("action_group_id", "TEXT"),
            ("action_id", "TEXT"),
            ("tool_call_id", "TEXT"),
            ("side_effect_class", "TEXT"),
            ("resource_keys", "TEXT"),
        ],
        "host_call_log": [
            ("action_group_id", "TEXT"),
            ("action_id", "TEXT"),
            ("permission_decision_id", "TEXT"),
            ("result_preview", "TEXT"),
            ("result_digest", "TEXT"),
            ("side_effect_class", "TEXT"),
            ("resource_keys", "TEXT"),
        ],
        "compaction_archives": [
            ("branch_id", "TEXT"),
            ("ledger_cursor", "TEXT"),
            ("recovery_pointer", "TEXT"),
            ("generation_id", "TEXT"),
            ("metadata", "TEXT"),
            ("handoff", "TEXT"),
            ("context_before", "TEXT"),
            ("context_after", "TEXT"),
            ("artifact_refs", "TEXT"),
        ],
    }

    def _migrate(self) -> None:
        """Bring the database to SCHEMA_VERSION, transactionally and once.

        The fast path is a ``PRAGMA user_version`` read: an already-current
        database does no probing at all, where previously every open re-derived
        the schema shape with a table_info scan per table.
        """
        with self._lock:
            report = run_migrations(
                self._conn,
                self.db_path,
                {1: ("legacy_baseline", self._apply_legacy_baseline)},
            )
            if report["migrated"]:
                harden_db(self.db_path)

    def _apply_legacy_baseline(self, conn: sqlite3.Connection) -> None:
        """Version 1: the historical catch-up pass, run once and then stamped.

        This is the whole of what ``_migrate`` used to do on every open. It is
        idempotent by construction — it adds only absent columns, and every
        backfill below is guarded by a predicate that selects only rows still
        needing it — which is exactly why a version can be retrofitted onto an
        existing database without reconstructing which ALTERs had already run.
        Converge once, stamp version 1, and stop re-deriving it forever after.

        Runs inside the transaction owned by run_migrations; it must not commit.
        """
        for table, cols in self._MIGRATIONS.items():
            have = {
                r["name"]
                for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for name, decl in cols:
                if name in have:
                    continue
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                except sqlite3.OperationalError as e:
                    # Only the one error a concurrent re-run legitimately
                    # produces is benign. The old blanket swallow also hid
                    # "database is locked" and "no such table", letting the
                    # process continue against a schema missing a column it
                    # believed it had — a migration failure surfacing much
                    # later as an unexplained runtime error.
                    if not _is_duplicate_column(e):
                        raise MigrationError(
                            f"ALTER TABLE {table} ADD COLUMN {name} {decl} "
                            f"failed: {e}"
                        ) from e
        # Historical child frames inherited the root id but silently kept
        # project_id='default'. Historical artifacts also used their actor
        # frame as root_frame_id. Repair both idempotently when the frame
        # tree still exists; unframed legacy uploads remain untouched.
        conn.execute(
            "UPDATE frames SET project_id=COALESCE((SELECT root.project_id "
            "FROM frames AS root WHERE root.frame_id=frames.root_frame_id),"
            "project_id) WHERE root_frame_id IS NOT NULL"
        )
        conn.execute(
            "UPDATE artifacts SET project_id=COALESCE((SELECT root.project_id "
            "FROM frames AS actor JOIN frames AS root "
            "ON root.frame_id=actor.root_frame_id "
            "WHERE actor.frame_id=artifacts.root_frame_id),project_id) "
            "WHERE root_frame_id IN (SELECT frame_id FROM frames)"
        )
        conn.execute(
            "UPDATE artifacts SET root_frame_id=COALESCE((SELECT "
            "actor.root_frame_id FROM frames AS actor "
            "WHERE actor.frame_id=artifacts.root_frame_id),root_frame_id) "
            "WHERE root_frame_id IN (SELECT frame_id FROM frames)"
        )
        # Messages written before branch-aware history belonged to the
        # canonical root.  Keep the rows immutable and backfill only their
        # newly-added routing projection.
        conn.execute(
            "UPDATE messages SET branch_id=root_frame_id "
            "WHERE branch_id IS NULL OR branch_id=''"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_msg_branch "
            "ON messages(root_frame_id,branch_id,seq)"
        )
        # ``cell_index`` was already the session-monotonic allocation for
        # historical Web Cells.  Backfill the explicitly named runtime
        # revision without pretending that rows which never had an index
        # carry recoverable state.
        conn.execute(
            "UPDATE execution_log SET state_revision=cell_index "
            "WHERE state_revision IS NULL AND cell_index IS NOT NULL"
        )
        # Capture the same immutable dependency metadata for historical
        # Cells as for newly recorded ones.  Rows are selected by the hash
        # sentinel, making the additive migration idempotent.
        legacy_cells = conn.execute(
            "SELECT producing_cell_id,code,language,origin,visibility,"
            "replay_policy FROM execution_log WHERE code_hash IS NULL"
        ).fetchall()
        for cell in legacy_cells:
            visibility = cell["visibility"] or default_visibility(cell["origin"])
            if visibility == "scientific" and str(cell["origin"] or "").lower() in {
                "system",
                "recovery",
            }:
                visibility = default_visibility(cell["origin"])
            replay_policy = cell["replay_policy"]
            if not replay_policy or visibility in {"system", "recovery"}:
                replay_policy = default_replay_policy(visibility)
            metadata = analyze_code(cell["code"] or "", cell["language"] or "python")
            conn.execute(
                "UPDATE execution_log SET code_hash=?,visibility=?,"
                "replay_policy=?,variable_reads=?,variable_writes=?,"
                "variable_deletes=?,mutation_uncertain=? "
                "WHERE producing_cell_id=?",
                (
                    metadata.code_hash,
                    visibility,
                    replay_policy,
                    json.dumps(metadata.reads, ensure_ascii=False),
                    json.dumps(metadata.writes, ensure_ascii=False),
                    json.dumps(metadata.deletes, ensure_ascii=False),
                    1 if metadata.uncertain else 0,
                    cell["producing_cell_id"],
                ),
            )

    def _apply_pragmas(self) -> None:
        """The connection's explicit PRAGMA policy.

        Stated rather than inherited, because a default that happens to be
        right is indistinguishable from one nobody chose — and the next person
        cannot tell which knobs were considered.

        Deliberately NOT set here:

        ``journal_mode``. It stays at the rollback-journal default. There is
        real multi-process access (``openai4s run`` and ``openai4s init`` open
        this database from their own process with no check that the daemon is
        not live), which is the usual argument for WAL — but measuring it
        showed a reader is not blocked by an in-flight writer under either
        mode, so there is no demonstrated problem for WAL to solve. Switching
        the on-disk format of a live user database on folklore is a bad trade;
        WAL also adds -wal/-shm sidecars and is unsafe on network filesystems.
        Revisit under a real concurrency and crash-recovery test, not before.

        ``synchronous``. Already FULL, which is the safe end. Lowering it
        trades crash durability for write speed on a database holding an audit
        ledger. Not a trade to make silently.
        """
        with self._lock:
            # No-op today: the schema declares zero REFERENCES/FOREIGN KEY
            # clauses, so there is nothing to enforce. Set anyway, and by
            # policy rather than by accident: the pragma is per-connection and
            # OFF by default, so the day someone adds a foreign key it would
            # otherwise be silently unenforced — the constraint would read as
            # documentation. Adding real constraints to these tables needs a
            # rebuild (SQLite has no ALTER TABLE ADD CONSTRAINT) and orphan
            # cleanup first; this only ensures they would bite once they exist.
            self._conn.execute("PRAGMA foreign_keys = ON")

    # --- secrets ---------------------------------------------------------
    @property
    def secrets(self):
        """The SecretBroker for this database, resolved once on first use.

        Lazy because resolution runs a real keychain round-trip self-test, and
        the overwhelming majority of Store construction (every test, every CLI
        subcommand that touches no credential) never needs a secret.
        """
        with self._lock:
            broker = getattr(self, "_secret_broker", None)
            if broker is None:
                from openai4s.security.secret_broker import SecretBroker

                broker = SecretBroker(self)
                self._secret_broker = broker
            return broker

    def get_secret_setting(self, key: str) -> str:
        """Read a credential setting, whether it is a reference or legacy plaintext.

        Both shapes have to work: an install that has not migrated, one that
        has, and one where migration failed for a single key must all keep
        running. Callers do not need to know which they are looking at.
        """
        from openai4s.security.secret_migration import resolve_setting

        return resolve_setting(self, self.secrets, key)

    def set_secret_setting(self, key: str, value: str, *, scope: str) -> str:
        """Store a credential through the broker, recording only its reference.

        Returns the reference. An empty value clears both the reference and the
        stored secret — a cleared key must not linger in the keychain where the
        UI reports it as gone.
        """
        from openai4s.security.secret_broker import is_ref

        previous = self.get_setting(key)
        if not value:
            if is_ref(previous):
                try:
                    self.secrets.delete(previous)
                except Exception:  # noqa: BLE001 - clearing the row still matters
                    pass
            self.set_setting(key, "")
            return ""
        ref = self.secrets.put(scope, key, value)
        # Verify before recording: a write that did not raise is not evidence
        # the value is retrievable, and a reference that resolves to nothing is
        # worse than the plaintext it replaced.
        if self.secrets.get(ref) != value:
            raise RuntimeError(
                f"refusing to record {key!r}: wrote to {ref} but could not read "
                f"it back"
            )
        self.set_setting(key, ref)
        return ref

    # --- schema state ----------------------------------------------------
    def schema_state(self) -> dict:
        """Report the database's schema version and how it got there.

        Exists so "is this database current, and what has been applied to it"
        is a question that can be answered without re-deriving the shape from
        table_info — which is what the code had to do before there was a
        version at all.
        """
        with self._lock:
            return {
                "version": current_version(self._conn),
                "expected": SCHEMA_VERSION,
                "current": current_version(self._conn) >= SCHEMA_VERSION,
                "applied": applied_migrations(self._conn),
            }

    # --- low-level -------------------------------------------------------
    def _exec(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._conn.close()
            self._closed = True
        _discard_store(self)

    # --- frames ----------------------------------------------------------
    def new_frame(
        self,
        *,
        parent_id: str | None = None,
        project_id: str = "default",
        kind: str = "turn",
        name: str | None = None,
        model: str | None = None,
        depth: int = 0,
        status: str = "processing",
    ) -> str:
        return self._frames.new_frame(
            parent_id=parent_id,
            project_id=project_id,
            kind=kind,
            name=name,
            model=model,
            depth=depth,
            status=status,
        )

    def resolve_frame_scope(
        self,
        frame_id: str | None,
        *,
        fallback_project: str = "default",
    ) -> dict:
        return self._frames.resolve_frame_scope(
            frame_id,
            fallback_project=fallback_project,
        )

    def update_frame(self, frame_id: str, **fields: Any) -> None:
        self._frames.update_frame(frame_id, **fields)

    def add_frame_tokens(
        self,
        frame_id: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        self._frames.add_frame_tokens(
            frame_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        )

    # --- projects --------------------------------------------------------
    def create_project(
        self,
        *,
        name: str,
        description: str = "",
        context: str = "",
        project_id: str | None = None,
        is_example: bool = False,
    ) -> dict:
        return self._frames.create_project(
            name=name,
            description=description,
            context=context,
            project_id=project_id,
            is_example=is_example,
        )

    def get_project(self, project_id: str) -> dict | None:
        return self._frames.get_project(project_id)

    def update_project(self, project_id: str, **fields: Any) -> None:
        self._frames.update_project(project_id, **fields)

    def delete_project(self, project_id: str) -> dict:
        return self._frames.delete_project(project_id)

    def project_session_ids(self, project_id: str) -> list[str]:
        return self._frames.project_session_ids(project_id)

    def list_projects(self) -> list[dict]:
        return self._frames.list_projects()

    # --- messages --------------------------------------------------------
    def add_message(
        self,
        *,
        root_frame_id: str,
        branch_id: str | None = None,
        role: str,
        content: str,
        frame_id: str | None = None,
        metadata: dict | None = None,
        created_at: int | None = None,
    ) -> dict:
        return self._frames.add_message(
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            role=role,
            content=content,
            frame_id=frame_id,
            metadata=metadata,
            created_at=created_at,
        )

    def list_messages(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None = None,
        start: int = 0,
        limit: int | None = 300,
    ) -> list[dict]:
        return self._frames.list_messages(
            root_frame_id,
            branch_id=branch_id,
            start=start,
            limit=limit,
        )

    def list_message_boundaries(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None = None,
        start: int = 0,
        limit: int | None = 300,
    ) -> list[dict]:
        return self._frames.list_message_boundaries(
            root_frame_id,
            branch_id=branch_id,
            start=start,
            limit=limit,
        )

    def list_branch_messages(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None = None,
        start: int = 0,
        limit: int | None = 300,
        boundaries: bool = False,
    ) -> list[dict]:
        """Project one branch's visible conversation without deleting rows."""

        reader = (
            self._frames.list_message_boundaries
            if boundaries
            else self._frames.list_messages
        )
        projected = project_branch_records(
            self,
            root_frame_id,
            branch_id or self.active_session_branch(root_frame_id),
            list_local=lambda selected: reader(
                root_frame_id,
                branch_id=selected,
                limit=None,
            ),
            record_position=lambda message: int(message.get("seq") or 0),
            cursor_key="message_cursor",
            normalize_cursor=count_cursor,
        )
        start = max(0, int(start))
        if limit is None:
            return projected[start:]
        return projected[start : start + max(0, int(limit))]

    def list_branch_message_boundaries(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None = None,
        start: int = 0,
        limit: int | None = 300,
    ) -> list[dict]:
        return self.list_branch_messages(
            root_frame_id,
            branch_id=branch_id,
            start=start,
            limit=limit,
            boundaries=True,
        )

    def message_count(self, root_frame_id: str) -> int:
        return self._frames.message_count(root_frame_id)

    def cell_count(self, root_frame_id: str) -> int:
        return self._frames.cell_count(root_frame_id)

    def latest_state_revision(self, root_frame_id: str) -> int:
        return self._frames.latest_state_revision(root_frame_id)

    # --- semantic activity steps (plan / search / env / skill / edit / …) ----
    # Every visible host.* tool call becomes a persisted "step" so a reopened
    # session re-renders the same rich activity (not just the final prose).
    def add_step(
        self,
        *,
        step_id: str,
        frame_id: str,
        kind: str,
        title: str | None = None,
        input: dict | None = None,
        status: str = "running",
    ) -> dict:
        return self._frames.add_step(
            step_id=step_id,
            frame_id=frame_id,
            kind=kind,
            title=title,
            input=input,
            status=status,
        )

    def update_step(
        self,
        step_id: str,
        *,
        status: str | None = None,
        output: dict | None = None,
        title: str | None = None,
        summary: str | None = None,
    ) -> None:
        self._frames.update_step(
            step_id,
            status=status,
            output=output,
            title=title,
            summary=summary,
        )

    def list_steps(
        self, frame_id: str, *, start: int = 0, limit: int = 800
    ) -> list[dict]:
        return self._frames.list_steps(frame_id, start=start, limit=limit)

    def step_count(self, frame_id: str) -> int:
        return self._frames.step_count(frame_id)

    # --- frame browse / detail / search --------------------------
    def browse_frames(
        self,
        *,
        project_id: str | None = "default",
        status: str | None = None,
        roots_only: bool = True,
        limit: int = 50,
        before: tuple[int, str] | None = None,
    ) -> list[dict]:
        return self._frames.browse_frames(
            project_id=project_id,
            status=status,
            roots_only=roots_only,
            limit=limit,
            before=before,
        )

    def frame_detail(
        self, frame_id: str, *, page: int = 0, page_size: int = 50
    ) -> dict | None:
        return self._frames.frame_detail(
            frame_id,
            page=page,
            page_size=page_size,
        )

    def search_frames(
        self, pattern: str, *, project_id: str | None = "default", limit: int = 50
    ) -> list[dict]:
        return self._frames.search_frames(
            pattern,
            project_id=project_id,
            limit=limit,
        )

    # --- execution_log ---------------------------------------------------
    def log_cell(
        self,
        *,
        frame_id: str | None,
        code: str,
        result: dict,
        origin: str = "agent",
        cell_seq: int | None = None,
        project_id: str = "default",
        root_frame_id: str | None = None,
        cell_index: int | None = None,
        state_revision: int | None = None,
        kernel_id: str = "python",
        language: str = "python",
        visibility: str | None = None,
        pin: bool = False,
        replay_policy: str | None = None,
        figures: list | None = None,
        files_read: list | None = None,
        files_written: list | None = None,
    ) -> str:
        return self._frames.log_cell(
            frame_id=frame_id,
            code=code,
            result=result,
            origin=origin,
            cell_seq=cell_seq,
            project_id=project_id,
            root_frame_id=root_frame_id,
            cell_index=cell_index,
            state_revision=state_revision,
            kernel_id=kernel_id,
            language=language,
            visibility=visibility,
            pin=pin,
            replay_policy=replay_policy,
            figures=figures,
            files_read=files_read,
            files_written=files_written,
        )

    def list_cells(
        self, root_frame_id: str, *, branch_id: str | None = None
    ) -> list[dict]:
        return self._frames.list_cells(root_frame_id, branch_id=branch_id)

    def cell_detail(self, producing_cell_id: str) -> dict | None:
        return self._frames.cell_detail(producing_cell_id)

    # --- canonical action ledger ---------------------------------------
    def append_action_group(
        self,
        *,
        root_frame_id: str,
        turn_id: str,
        kind: str,
        branch_id: str | None = None,
        ordinal: int | None = None,
        provider: str | None = None,
        model: str | None = None,
        wire_state: Any = None,
        assistant_content: str | None = None,
        assistant_message: Any = None,
        usage: dict[str, Any] | None = None,
        cost_usd: float | None = None,
        group_id: str | None = None,
        created_at: int | None = None,
    ) -> dict:
        return self._actions.append_group(
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            turn_id=turn_id,
            ordinal=ordinal,
            kind=kind,
            provider=provider,
            model=model,
            wire_state=wire_state,
            assistant_content=assistant_content,
            assistant_message=assistant_message,
            usage=usage,
            cost_usd=cost_usd,
            group_id=group_id,
            created_at=created_at,
        )

    def append_action_event(
        self,
        *,
        group_id: str,
        type: str,
        sequence: int | None = None,
        action_id: str | None = None,
        tool_call_id: str | None = None,
        wire_id: str | None = None,
        canonical_arguments: Any = None,
        raw_arguments: Any = None,
        result: Any = None,
        side_effect_class: str | None = None,
        resource_keys: list[str] | tuple[str, ...] | None = None,
        event_id: str | None = None,
        created_at: int | None = None,
    ) -> dict:
        return self._actions.append_event(
            group_id=group_id,
            type=type,
            sequence=sequence,
            action_id=action_id,
            tool_call_id=tool_call_id,
            wire_id=wire_id,
            canonical_arguments=canonical_arguments,
            raw_arguments=raw_arguments,
            result=result,
            side_effect_class=side_effect_class,
            resource_keys=resource_keys,
            event_id=event_id,
            created_at=created_at,
        )

    def append_tool_action_group(
        self,
        *,
        root_frame_id: str,
        turn_id: str,
        events: list[dict[str, Any]],
        branch_id: str | None = None,
        ordinal: int | None = None,
        provider: str | None = None,
        model: str | None = None,
        wire_state: Any = None,
        assistant_content: str | None = None,
        assistant_message: Any = None,
        usage: dict[str, Any] | None = None,
        cost_usd: float | None = None,
        group_id: str | None = None,
        created_at: int | None = None,
    ) -> dict:
        return self._actions.append_tool_group(
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            turn_id=turn_id,
            events=events,
            ordinal=ordinal,
            provider=provider,
            model=model,
            wire_state=wire_state,
            assistant_content=assistant_content,
            assistant_message=assistant_message,
            usage=usage,
            cost_usd=cost_usd,
            group_id=group_id,
            created_at=created_at,
        )

    def get_action_group(
        self, group_id: str, *, include_events: bool = True
    ) -> dict | None:
        return self._actions.get_group(group_id, include_events=include_events)

    def list_action_groups(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None = None,
        turn_id: str | None = None,
        after_ordinal: int | None = None,
        limit: int | None = None,
        include_events: bool = True,
    ) -> list[dict]:
        return self._actions.list_groups(
            root_frame_id,
            branch_id=branch_id,
            turn_id=turn_id,
            after_ordinal=after_ordinal,
            limit=limit,
            include_events=include_events,
        )

    def list_action_events(self, group_id: str) -> list[dict]:
        return self._actions.list_events(group_id)

    def allocate_execution_attempt(
        self,
        *,
        group_id: str,
        producing_cell_id: str,
        state_revision: int | None = None,
        generation_id: str | None = None,
        owner_instance_id: str | None = None,
        replayed_from_cell_id: str | None = None,
        attempt_ordinal: int | None = None,
        attempt_id: str | None = None,
        allocated_at: int | None = None,
    ) -> dict:
        return self._actions.allocate_attempt(
            group_id=group_id,
            producing_cell_id=producing_cell_id,
            state_revision=state_revision,
            generation_id=generation_id,
            owner_instance_id=owner_instance_id,
            replayed_from_cell_id=replayed_from_cell_id,
            attempt_ordinal=attempt_ordinal,
            attempt_id=attempt_id,
            allocated_at=allocated_at,
        )

    def mark_execution_attempt_started(
        self, attempt_id: str, *, started_at: int | None = None
    ) -> dict:
        return self._actions.mark_attempt_started(attempt_id, started_at=started_at)

    def bind_execution_attempt_generation(
        self, attempt_id: str, generation_id: str
    ) -> dict:
        return self._actions.bind_attempt_generation(attempt_id, generation_id)

    def abandon_incomplete_execution_attempts(
        self,
        *,
        owner_instance_id: str,
        finished_at: int | None = None,
    ) -> int:
        return self._actions.abandon_incomplete_attempts(
            owner_instance_id=owner_instance_id,
            finished_at=finished_at,
        )

    def mark_execution_attempt_response(
        self, attempt_id: str, *, response_at: int | None = None
    ) -> dict:
        return self._actions.mark_attempt_response(attempt_id, response_at=response_at)

    def mark_execution_attempt_capture(
        self, attempt_id: str, *, capture_at: int | None = None
    ) -> dict:
        return self._actions.mark_attempt_capture(attempt_id, capture_at=capture_at)

    def finish_execution_attempt(
        self,
        attempt_id: str,
        *,
        terminal_state: str,
        error: Any = None,
        finished_at: int | None = None,
    ) -> dict:
        return self._actions.finish_attempt(
            attempt_id,
            terminal_state=terminal_state,
            error=error,
            finished_at=finished_at,
        )

    def get_execution_attempt(self, attempt_id: str) -> dict | None:
        return self._actions.get_attempt(attempt_id)

    def list_execution_attempts(
        self,
        *,
        group_id: str | None = None,
        producing_cell_id: str | None = None,
        root_frame_id: str | None = None,
        branch_id: str | None = None,
        turn_id: str | None = None,
    ) -> list[dict]:
        return self._actions.list_attempts(
            group_id=group_id,
            producing_cell_id=producing_cell_id,
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            turn_id=turn_id,
        )

    # --- persistent kernel generations --------------------------------
    def create_kernel_generation(self, **fields: Any) -> dict:
        return self._kernel_generations.create(**fields)

    def touch_kernel_generation(self, generation_id: str, **fields: Any) -> dict:
        return self._kernel_generations.touch(generation_id, **fields)

    def compare_and_swap_kernel_bootstrap(
        self,
        generation_id: str,
        *,
        expected_manifest_id: str | None,
        bootstrap: Any,
        at: int | None = None,
    ) -> dict | None:
        return self._kernel_generations.compare_and_swap_bootstrap(
            generation_id,
            expected_manifest_id=expected_manifest_id,
            bootstrap=bootstrap,
            at=at,
        )

    def finish_kernel_generation(
        self,
        generation_id: str,
        *,
        state: str,
        reason: str,
        ended_at: int | None = None,
    ) -> dict:
        return self._kernel_generations.finish(
            generation_id,
            state=state,
            reason=reason,
            ended_at=ended_at,
        )

    def abandon_live_kernel_generations(
        self,
        *,
        owner_instance_id: str,
        reason: str = "daemon_restart",
        ended_at: int | None = None,
    ) -> int:
        return self._kernel_generations.abandon_live(
            owner_instance_id=owner_instance_id,
            reason=reason,
            ended_at=ended_at,
        )

    def get_kernel_generation(self, generation_id: str) -> dict | None:
        return self._kernel_generations.get(generation_id)

    def latest_kernel_generation(
        self,
        root_frame_id: str,
        language: str,
        *,
        branch_id: str | None = None,
    ) -> dict | None:
        return self._kernel_generations.latest(
            root_frame_id,
            language,
            branch_id=branch_id,
        )

    def list_kernel_generations(
        self,
        root_frame_id: str,
        *,
        language: str | None = None,
        branch_id: str | None = None,
    ) -> list[dict]:
        return self._kernel_generations.list(
            root_frame_id,
            language=language,
            branch_id=branch_id,
        )

    # --- immutable session checkpoints / branches ----------------------
    def ensure_session_branch(self, **fields: Any) -> dict:
        return self._session_snapshots.ensure_branch(**fields)

    def create_session_checkpoint(self, **fields: Any) -> dict:
        return self._session_snapshots.create_checkpoint(**fields)

    def get_checkpoint_state_snapshot(
        self,
        checkpoint_id: str,
        *,
        include_state: bool = False,
    ) -> dict | None:
        return self._checkpoint_states.get(
            checkpoint_id,
            include_state=include_state,
        )

    def list_checkpoint_state_snapshots(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        return self._checkpoint_states.list(
            root_frame_id,
            branch_id=branch_id,
            limit=limit,
        )

    def import_quarantined_checkpoint_state(
        self,
        source: dict,
        *,
        checkpoint_id: str,
        root_frame_id: str,
        branch_id: str,
        project_id: str,
        artifact_id_map: dict[str, str] | None = None,
        source_checkpoint_id: str | None = None,
    ) -> dict:
        return self._checkpoint_states.import_quarantined_snapshot(
            source,
            checkpoint_id=checkpoint_id,
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            project_id=project_id,
            artifact_id_map=artifact_id_map,
            source_checkpoint_id=source_checkpoint_id,
        )

    def validate_checkpoint_state_import(
        self,
        source: dict,
        *,
        include_state: bool = False,
    ) -> dict:
        return self._checkpoint_states.validate_checkpoint_state_import(
            source,
            include_state=include_state,
        )

    def restore_checkpoint_state_snapshot(
        self,
        *,
        checkpoint_id: str,
        root_frame_id: str,
        project_id: str,
    ) -> dict:
        """Restore only the structured plan/review/memory projection.

        Normal branch activation calls the same repository inside its broader
        atomic publication transaction.  This narrow facade exists for repair
        tooling and direct repository contract tests.
        """

        return self._checkpoint_states.restore_checkpoint(
            checkpoint_id=checkpoint_id,
            root_frame_id=root_frame_id,
            project_id=project_id,
        )

    def fork_session_branch(self, **fields: Any) -> dict:
        return self._session_snapshots.fork_branch(**fields)

    def get_session_checkpoint(self, checkpoint_id: str) -> dict | None:
        return self._session_snapshots.get_checkpoint(checkpoint_id)

    def get_session_checkpoint_for_source(
        self,
        root_frame_id: str,
        *,
        source_kind: str,
        source_id: str,
    ) -> dict | None:
        return self._session_snapshots.get_checkpoint_for_source(
            root_frame_id,
            source_kind=source_kind,
            source_id=source_id,
        )

    def session_checkpoint_source_map(
        self, root_frame_id: str, *, source_kind: str
    ) -> dict[str, str]:
        return self._session_snapshots.checkpoint_source_map(
            root_frame_id,
            source_kind=source_kind,
        )

    def list_session_checkpoints(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        return self._session_snapshots.list_checkpoints(
            root_frame_id,
            branch_id=branch_id,
            limit=limit,
        )

    def retained_workspace_tree_ids(self) -> tuple[str, ...]:
        return self._session_snapshots.retained_tree_ids()

    def get_session_branch(self, branch_id: str) -> dict | None:
        return self._session_snapshots.get_branch(branch_id)

    def list_session_branches(self, root_frame_id: str) -> list[dict]:
        return self._session_snapshots.list_branches(root_frame_id)

    def ensure_active_session_branch(self, root_frame_id: str) -> str:
        return self._session_activation.ensure(root_frame_id)

    def active_session_branch(self, root_frame_id: str) -> str:
        return self._session_activation.current(root_frame_id)

    def activate_session_branch_checkpoint(self, **fields: Any) -> dict:
        return self._session_activation.activate_checkpoint(**fields)

    def record_snapshot_operation(self, **fields: Any) -> dict:
        return self._session_snapshots.record_operation(**fields)

    def get_snapshot_operation(self, operation_id: str) -> dict | None:
        return self._session_snapshots.get_operation(operation_id)

    def list_snapshot_operations(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        return self._session_snapshots.list_operations(
            root_frame_id,
            branch_id=branch_id,
            kind=kind,
            status=status,
            limit=limit,
        )

    # --- append-only Kernel recovery journal ---------------------------
    def append_recovery_event(self, **fields: Any) -> dict:
        return self._recovery_journal.append(**fields)

    def list_recovery_events(
        self,
        *,
        recovery_id: str | None = None,
        root_frame_id: str | None = None,
        branch_id: str | None = None,
        limit: int = 1000,
        newest: bool = False,
    ) -> list[dict]:
        return self._recovery_journal.list(
            recovery_id=recovery_id,
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            limit=limit,
            newest=newest,
        )

    # --- durable sub-agent delegation projection ----------------------
    def restore_delegation_tree(self, **fields: Any) -> dict:
        return self._delegations.restore(**fields)

    def reserve_delegation_children(self, **fields: Any) -> dict:
        return self._delegations.reserve(**fields)

    def release_delegation_budget(self, **fields: Any) -> dict:
        return self._delegations.release(**fields)

    def persist_delegation_child(self, **fields: Any) -> dict | None:
        return self._delegations.persist_child(**fields)

    def delegation_tree(self, root_frame_id: str) -> dict:
        return self._delegations.project(root_frame_id)

    def delegation_budget(self, root_frame_id: str) -> dict | None:
        return self._delegations.budget(root_frame_id)

    def delete_frame(self, frame_id: str) -> dict[str, Any]:
        return self._frames.delete_frame(frame_id)

    def get_frame(self, frame_id: str) -> dict | None:
        return self._frames.get_frame(frame_id)

    def get_artifact(self, artifact_id: str) -> dict | None:
        return self._artifacts.get_artifact(artifact_id)

    def delete_artifact(self, artifact_id: str) -> list[str]:
        return self._artifacts.delete_artifact(artifact_id)

    def rename_artifact(self, artifact_id: str, filename: str) -> None:
        self._artifacts.rename_artifact(artifact_id, filename)

    def artifact_by_filename(
        self, filename: str, root_frame_id: str | None = None, *, strict: bool = False
    ) -> dict | None:
        return self._artifacts.artifact_by_filename(
            filename,
            root_frame_id,
            strict=strict,
        )

    # --- artifacts -------------------------------------------------------
    def _artifact_write_scope(
        self,
        *,
        frame_id: str | None,
        root_frame_id: str | None,
        project_id: str | None,
    ) -> tuple[bool, str | None, str]:
        return self._artifacts.artifact_write_scope(
            frame_id=frame_id,
            root_frame_id=root_frame_id,
            project_id=project_id,
        )

    def save_artifact(
        self,
        *,
        path: str,
        filename: str,
        content_type: str | None,
        size_bytes: int,
        checksum: str | None,
        producing_cell_id: str | None = None,
        frame_id: str | None = None,
        root_frame_id: str | None = None,
        project_id: str | None = None,
        artifact_id: str | None = None,
        is_user_upload: bool = False,
        priority: int = 0,
        env_snapshot_id: str | None = None,
        snapshot_path: str | None = None,
    ) -> dict:
        return self._artifacts.save_artifact(
            path=path,
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            checksum=checksum,
            producing_cell_id=producing_cell_id,
            frame_id=frame_id,
            root_frame_id=root_frame_id,
            project_id=project_id,
            artifact_id=artifact_id,
            is_user_upload=is_user_upload,
            priority=priority,
            env_snapshot_id=env_snapshot_id,
            snapshot_path=snapshot_path,
        )

    def record_cell_artifact(
        self,
        *,
        path: str,
        filename: str,
        content_type: str | None,
        size_bytes: int,
        checksum: str | None,
        producing_cell_id: str | None,
        frame_id: str | None,
        root_frame_id: str | None = None,
        project_id: str | None = None,
        env_snapshot_id: str | None = None,
        snapshot_path: str | None = None,
        input_version_ids: list[str] | tuple[str, ...] | None = None,
        preserve_filename: bool = False,
        preserve_content_type: bool = False,
        reuse_policy: str = "any",
    ) -> dict:
        return self._artifacts.record_cell_artifact(
            path=path,
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            checksum=checksum,
            producing_cell_id=producing_cell_id,
            frame_id=frame_id,
            root_frame_id=root_frame_id,
            project_id=project_id,
            env_snapshot_id=env_snapshot_id,
            snapshot_path=snapshot_path,
            input_version_ids=input_version_ids,
            preserve_filename=preserve_filename,
            preserve_content_type=preserve_content_type,
            reuse_policy=reuse_policy,
        )

    def record_artifact_restore(
        self,
        *,
        artifact_id: str,
        source_version_id: str,
        expected_latest_version_id: str,
        version_id: str,
        path: str,
        snapshot_path: str,
        size_bytes: int,
        checksum: str,
        frame_id: str | None,
        root_frame_id: str | None = None,
        project_id: str | None = None,
    ) -> dict:
        return self._artifacts.record_artifact_restore(
            artifact_id=artifact_id,
            source_version_id=source_version_id,
            expected_latest_version_id=expected_latest_version_id,
            version_id=version_id,
            path=path,
            snapshot_path=snapshot_path,
            size_bytes=size_bytes,
            checksum=checksum,
            frame_id=frame_id,
            root_frame_id=root_frame_id,
            project_id=project_id,
        )

    def upsert_env_snapshot(self, snapshot: dict) -> str:
        return self._artifacts.upsert_env_snapshot(snapshot)

    def delete_env_snapshots_if_unreferenced(self, snapshot_ids) -> int:
        return self._artifacts.delete_env_snapshots_if_unreferenced(snapshot_ids)

    def get_env_snapshot(self, snapshot_id: str) -> dict | None:
        return self._artifacts.get_env_snapshot(snapshot_id)

    def env_snapshot_for_artifact(
        self, artifact_id: str, version_id: str | None = None
    ) -> dict | None:
        return self._artifacts.env_snapshot_for_artifact(
            artifact_id,
            version_id,
        )

    def list_artifacts(self, filters: dict | None = None) -> list[dict]:
        return self._artifacts.list_artifacts(filters)

    def resolve_artifact_path(self, ident: str) -> str | None:
        return self._artifacts.resolve_artifact_path(ident)

    def version_for_path(self, path: str) -> str | None:
        return self._artifacts.version_for_path(path)

    def version_meta(self, version_id: str) -> dict | None:
        return self._artifacts.version_meta(version_id)

    def list_versions(self, artifact_id: str) -> list[dict]:
        return self._artifacts.list_versions(artifact_id)

    def update_version_path(
        self,
        version_id: str,
        path: str,
        size_bytes: int | None = None,
        checksum: str | None = None,
    ) -> None:
        self._artifacts.update_version_path(
            version_id,
            path,
            size_bytes=size_bytes,
            checksum=checksum,
        )

    def set_version_snapshot(self, version_id: str, snapshot_path: str) -> None:
        self._artifacts.set_version_snapshot(version_id, snapshot_path)

    def set_priority(self, artifact_id: str, priority: int) -> dict | None:
        return self._artifacts.set_priority(artifact_id, priority)

    def set_latest_version(self, artifact_id: str, version_id: str) -> dict | None:
        return self._artifacts.set_latest_version(artifact_id, version_id)

    def add_lineage_edge(
        self,
        *,
        input_version_id: str,
        output_version_id: str,
        producing_cell_id: str | None = None,
        frame_id: str | None = None,
    ) -> None:
        self._artifacts.add_lineage_edge(
            input_version_id=input_version_id,
            output_version_id=output_version_id,
            producing_cell_id=producing_cell_id,
            frame_id=frame_id,
        )

    def lineage_inputs(self, version_id: str) -> list[dict]:
        return self._artifacts.lineage_inputs(version_id)

    def lineage_edges_for(self, version_id: str, direction: str) -> list[dict]:
        return self._artifacts.lineage_edges_for(version_id, direction)

    def producing_cell_for_version(self, version_id: str) -> dict | None:
        return self._artifacts.producing_cell_for_version(version_id)

    # --- notes -----------------------------------------------------------
    def add_note(
        self, *, project_id: str, content: str, title: str | None = None
    ) -> dict:
        return self._notes.add(
            project_id=project_id,
            content=content,
            title=title,
        )

    def list_notes(self, project_id: str) -> list[dict]:
        return self._notes.list(project_id)

    def delete_note(self, note_id: str) -> None:
        self._notes.delete(note_id)

    # --- settings (KV) ---------------------------------------------------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        return self._settings.get(key, default)

    def set_setting(self, key: str, value: str) -> None:
        self._settings.set(key, value)

    def delete_setting(self, key: str) -> None:
        self._settings.delete(key)

    # --- web shares (public read-only snapshots) -------------------------
    def get_share(self, share_id: str) -> dict | None:
        return self._shares.get(share_id)

    def active_share_for_frame(self, root_frame_id: str) -> dict | None:
        return self._shares.active_for_frame(root_frame_id)

    def list_shares_for_frame(self, root_frame_id: str) -> list[dict]:
        return self._shares.list_for_frame(root_frame_id)

    def list_shares(self, *, include_revoked: bool = False) -> list[dict]:
        return self._shares.list_all(include_revoked=include_revoked)

    def list_active_shares(self) -> list[dict]:
        return self._shares.list_active()

    def list_expired_shares(self, now_ms: int) -> list[dict]:
        return self._shares.list_expired(now_ms)

    def begin_share_publish(
        self,
        *,
        share_id: str,
        root_frame_id: str,
        title: str | None,
        pending_snapshot_id: str,
        expires_at: int | None = None,
    ) -> dict:
        return self._shares.begin_publish(
            share_id=share_id,
            root_frame_id=root_frame_id,
            title=title,
            pending_snapshot_id=pending_snapshot_id,
            expires_at=expires_at,
        )

    def mark_share_ready(
        self,
        share_id: str,
        *,
        snapshot_id: str,
        bundle_sha256: str,
        bundle_size: int,
        projection_id: str,
        counts: dict | None = None,
    ) -> dict | None:
        return self._shares.mark_ready(
            share_id,
            snapshot_id=snapshot_id,
            bundle_sha256=bundle_sha256,
            bundle_size=bundle_size,
            projection_id=projection_id,
            counts=counts,
        )

    def mark_share_failed(self, share_id: str) -> None:
        self._shares.mark_failed(share_id)

    def mark_share_revoked(self, share_id: str) -> None:
        self._shares.mark_revoked(share_id)

    def delete_share(self, share_id: str) -> None:
        self._shares.delete(share_id)

    # --- model profiles (saved LLM/API configs) --------------------------
    # Stored as a JSON list under the `model_profiles` setting so users can keep
    # several full API configs (provider + base_url + model + key) side by side
    # and switch between them. Activating one writes the live `llm_*` settings.
    def list_model_profiles(self) -> list[dict]:
        return self._settings.list_model_profiles()

    def set_model_profiles(self, profiles: list[dict]) -> None:
        self._settings.set_model_profiles(profiles)

    def mutate_model_profiles(self, fn):
        return self._settings.mutate_model_profiles(fn)

    # --- permission rules (opencode-style tool-call gate) ----------------
    def set_permission_rule(
        self,
        *,
        scope: str,
        scope_id: str = "",
        tool: str,
        pattern: str = "*",
        decision: str,
    ) -> str:
        return self._permissions.set_rule(
            scope=scope,
            scope_id=scope_id,
            tool=tool,
            pattern=pattern,
            decision=decision,
        )

    def delete_permission_rule(self, rule_id: str) -> None:
        self._permissions.delete_rule(rule_id)

    def get_permission_rule(self, rule_id: str) -> dict | None:
        return self._permissions.get_rule(rule_id)

    def get_permission_rules(self, *, scope: str, scope_id: str = "") -> list[dict]:
        return self._permissions.get_rules(scope=scope, scope_id=scope_id)

    def list_permission_rules_for_frame(
        self, *, root_frame_id: str | None = None, project_id: str | None = None
    ) -> dict:
        return self._permissions.list_for_frame(
            root_frame_id=root_frame_id,
            project_id=project_id,
        )

    def resolve_permission(
        self,
        *,
        root_frame_id: str | None = None,
        project_id: str | None = None,
        tool: str,
        pattern_input: str = "",
    ) -> str:
        return self._permissions.resolve(
            root_frame_id=root_frame_id,
            project_id=project_id,
            tool=tool,
            pattern_input=pattern_input,
        )

    def seed_default_permission_rules(self, *, force: bool = False) -> None:
        self._permissions.seed_defaults(force=force)

    def create_permission_request(self, **request: Any) -> dict:
        return self._permissions.create_request(**request)

    def resolve_permission_request(
        self,
        decision_id: str,
        *,
        state: str,
        scope: str | None = None,
        pattern: str | None = None,
        message: str | None = None,
        resolution_context: str | None = None,
        continuation_required: bool = False,
        resolved_at: int | None = None,
    ) -> dict:
        return self._permissions.resolve_request(
            decision_id,
            state=state,
            scope=scope,
            pattern=pattern,
            message=message,
            resolution_context=resolution_context,
            continuation_required=continuation_required,
            resolved_at=resolved_at,
        )

    def consume_restart_permission_grant(
        self,
        *,
        root_frame_id: str,
        tool: str,
        target: str = "",
        project_id: str | None = None,
        consumed_at: int | None = None,
    ) -> dict | None:
        return self._permissions.consume_restart_once_grant(
            root_frame_id=root_frame_id,
            tool=tool,
            target=target,
            project_id=project_id,
            consumed_at=consumed_at,
        )

    def activate_restart_permission_continuation(
        self,
        decision_id: str,
        *,
        expires_at: int | None = None,
    ) -> dict:
        return self._permissions.activate_restart_continuation(
            decision_id,
            expires_at=expires_at,
        )

    def get_permission_request(self, decision_id: str) -> dict | None:
        return self._permissions.get_request(decision_id)

    def list_permission_requests(
        self,
        *,
        root_frame_id: str | None = None,
        state: str | None = None,
    ) -> list[dict]:
        return self._permissions.list_requests(
            root_frame_id=root_frame_id,
            state=state,
        )

    # --- plans (structured plan → approve → auto-execute) ----------------
    def _plan_row(self, row) -> dict:
        return self._plans.normalize_row(row)

    def create_plan(
        self,
        *,
        frame_id: str,
        project_id: str = "default",
        title: str | None,
        rationale: str | None,
        confidence: str | None,
        steps: list[dict],
        artifact_id: str | None = None,
        status: str = "draft",
    ) -> dict:
        return self._plans.create(
            frame_id=frame_id,
            project_id=project_id,
            title=title,
            rationale=rationale,
            confidence=confidence,
            steps=steps,
            artifact_id=artifact_id,
            status=status,
        )

    def get_plan(self, plan_id: str) -> dict | None:
        return self._plans.get(plan_id)

    def get_plan_by_frame(self, frame_id: str) -> dict | None:
        """The most recent (non-discarded) plan for a frame, else the newest."""
        return self._plans.get_by_frame(frame_id)

    def list_plans(self, frame_id: str, *, limit: int = 50) -> list[dict]:
        return self._plans.list_for_frame(frame_id, limit=limit)

    def update_plan(
        self,
        plan_id: str,
        *,
        title: str | None = None,
        rationale: str | None = None,
        confidence: str | None = None,
        steps: list[dict] | None = None,
        status: str | None = None,
        step_status: dict | None = None,
        artifact_id: str | None = None,
    ) -> None:
        self._plans.update(
            plan_id,
            title=title,
            rationale=rationale,
            confidence=confidence,
            steps=steps,
            status=status,
            step_status=step_status,
            artifact_id=artifact_id,
        )

    def set_plan_step_status(
        self, plan_id: str, step_id: str, status: str, note: str | None = None
    ) -> dict | None:
        """Merge one step's status into the plan's step_status JSON. Returns the
        updated plan (with steps[] status folded in)."""
        return self._plans.set_step_status(plan_id, step_id, status, note)

    def delete_plans_for_frame(self, frame_id: str) -> None:
        self._plans.delete_for_frame(frame_id)

    # --- folders (session grouping within a project) --------------------
    def create_folder(self, *, project_id: str, name: str) -> dict:
        return self._folders.create(project_id=project_id, name=name)

    def list_folders(self, project_id: str) -> list[dict]:
        return self._folders.list(project_id)

    def rename_folder(self, folder_id: str, name: str) -> None:
        self._folders.rename(folder_id, name)

    def delete_folder(self, folder_id: str) -> None:
        self._folders.delete(folder_id)

    def set_frame_folder(self, frame_id: str, folder_id: str | None) -> None:
        self._folders.set_frame_folder(frame_id, folder_id)

    # --- memories --------------------------------------------------------
    def add_memory(
        self, *, content: str, block: str = "general", project_id: str = "default"
    ) -> dict:
        return self._memories.add(
            content=content,
            block=block,
            project_id=project_id,
        )

    def list_memories(
        self, project_id: str | None = None, block: str | None = None
    ) -> list[dict]:
        return self._memories.list(project_id=project_id, block=block)

    def delete_memory(self, memory_id: str) -> None:
        self._memories.delete(memory_id)

    def memory_blocks(self, project_id: str | None = None) -> list[dict]:
        return self._memories.blocks(project_id)

    # --- feedback (per message) -----------------------------------------
    def set_feedback(self, frame_id: str, key: str, rating: str | None) -> None:
        self._settings.set_feedback(frame_id, key, rating)

    def list_feedback(self, frame_id: str) -> dict:
        return self._settings.list_feedback(frame_id)

    # --- image annotations (figure review) ------------------------------
    def add_annotation(
        self,
        *,
        root_frame_id: str,
        artifact_id: str,
        artifact_name: str | None,
        rel_x: float,
        rel_y: float,
        body: str,
    ) -> dict:
        return self._annotations.add(
            root_frame_id=root_frame_id,
            artifact_id=artifact_id,
            artifact_name=artifact_name,
            rel_x=rel_x,
            rel_y=rel_y,
            body=body,
        )

    def get_annotation(self, annotation_id: str) -> dict | None:
        return self._annotations.get(annotation_id)

    def list_annotations(
        self,
        root_frame_id: str,
        *,
        artifact_id: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        return self._annotations.list_for_frame(
            root_frame_id,
            artifact_id=artifact_id,
            status=status,
        )

    def update_annotation(
        self, annotation_id: str, *, body: str | None = None, status: str | None = None
    ) -> dict | None:
        return self._annotations.update(
            annotation_id,
            body=body,
            status=status,
        )

    def mark_annotations_sent(self, annotation_ids: list[str]) -> None:
        self._annotations.mark_sent(annotation_ids)

    def delete_annotation(self, annotation_id: str) -> None:
        self._annotations.delete(annotation_id)

    # --- global search (command palette) --------------------------------
    def search(self, query: str, limit: int = 20) -> dict:
        """Search sessions (name/task_summary) + artifacts (filename) for the
        ⌘K command palette."""
        q = f"%{query.strip()}%"
        with self._lock:
            frames = self._conn.execute(
                "SELECT frame_id,project_id,name,task_summary,updated_at FROM frames "
                "WHERE parent_id IS NULL AND (name LIKE ? OR task_summary LIKE ?) "
                "ORDER BY updated_at DESC LIMIT ?",
                (q, q, limit),
            ).fetchall()
            arts = self._conn.execute(
                "SELECT artifact_id,filename,content_type,root_frame_id,project_id "
                "FROM artifacts WHERE filename LIKE ? ORDER BY created_at DESC "
                "LIMIT ?",
                (q, limit),
            ).fetchall()
        return {
            "sessions": [
                {
                    "id": r["frame_id"],
                    "project_id": r["project_id"],
                    "name": r["name"],
                    "task_summary": r["task_summary"],
                }
                for r in frames
            ],
            "artifacts": [
                {
                    "id": r["artifact_id"],
                    "filename": r["filename"],
                    "content_type": r["content_type"],
                    "root_frame_id": r["root_frame_id"],
                    "project_id": r["project_id"],
                }
                for r in arts
            ],
        }

    # --- agents / specialists -------------------------------------------
    def capability_state(
        self,
        *,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> CapabilityStateService:
        return self._capabilities.scoped(
            project_id=project_id,
            session_id=session_id,
        )

    def skill_versions(self) -> SkillVersionRepository:
        """Return the Store-owned immutable Skill package repository."""

        return self._skill_versions

    def set_capability_enabled(
        self,
        kind: str,
        name: str,
        enabled: bool,
        *,
        scope: str = "global",
        scope_id: str = "",
        metadata: dict | None = None,
    ) -> dict:
        return self._capabilities.set_enabled(
            kind,
            name,
            enabled,
            scope=scope,
            scope_id=scope_id,
            metadata=metadata,
        )

    def capability_snapshot(
        self,
        kind: str,
        names,
        *,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, dict]:
        return self.capability_state(
            project_id=project_id,
            session_id=session_id,
        ).snapshot(kind, names)

    def list_explicit_capability_states(
        self,
        kind: str | None = None,
        *,
        scope: str | None = None,
        scope_id: str | None = None,
    ) -> list[dict]:
        return self._capability_repository.explicit_states(
            kind,
            scope=scope,
            scope_id=scope_id,
        )

    def list_agents(
        self,
        *,
        project_id: str | None = None,
        session_id: str | None = None,
        include_disabled: bool = False,
    ) -> list[dict]:
        return self.specialist_profiles(
            project_id=project_id,
            session_id=session_id,
        ).list(include_disabled=include_disabled)

    def specialist_profiles(
        self,
        *,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> SpecialistProfileService:
        """Return the shared resolver/filter seam for custom and built-ins."""

        return self._specialists.scoped(
            project_id=project_id,
            session_id=session_id,
        )

    def get_agent(
        self,
        name: str,
        *,
        project_id: str | None = None,
        session_id: str | None = None,
        include_disabled: bool = False,
    ) -> dict | None:
        return self.specialist_profiles(
            project_id=project_id,
            session_id=session_id,
        ).resolve(name, include_disabled=include_disabled)

    def upsert_agent(
        self,
        *,
        name: str,
        description: str = "",
        system_prompt: str = "",
        skill_names: list | None = None,
        connectors: list | None = None,
        unrestricted: bool = True,
    ) -> dict:
        return self._agents.upsert(
            name=name,
            description=description,
            system_prompt=system_prompt,
            skill_names=skill_names,
            connectors=connectors,
            unrestricted=unrestricted,
        )

    def delete_agent(self, name: str) -> None:
        self._agents.delete(name)

    # --- connectors (MCP servers) ---------------------------------------
    def list_connectors(self) -> list[dict]:
        return self._connectors.list()

    def get_connector(self, connector_id: str) -> dict | None:
        return self._connectors.get(connector_id)

    def upsert_connector(
        self,
        *,
        connector_id: str,
        name: str,
        command,
        description: str = "",
        args=None,
        env=None,
        enabled: bool = True,
    ) -> dict:
        # Brokered here rather than in the repository: this facade owns the
        # SecretBroker, and the repository must keep returning the real env to
        # the callers that launch the server.
        env = broker_connector_env(self, connector_id, env)
        return self._connectors.upsert(
            connector_id=connector_id,
            name=name,
            command=command,
            description=description,
            args=args,
            env=env,
            enabled=enabled,
        )

    def set_connector_enabled(self, connector_id: str, enabled: bool) -> None:
        self._connectors.set_enabled(connector_id, enabled)

    # --- compute jobs ----------------------------------------------------
    def create_compute_job(self, **kw) -> dict:
        return self._compute_jobs.create(**kw)

    def update_compute_job(self, job_id: str, **fields) -> dict | None:
        return self._compute_jobs.update(job_id, **fields)

    def get_compute_job(self, job_id: str) -> dict | None:
        return self._compute_jobs.get(job_id)

    def compute_job_by_idempotency_key(self, key: str) -> dict | None:
        return self._compute_jobs.by_idempotency_key(key)

    def live_compute_jobs(self) -> list[dict]:
        return self._compute_jobs.live()

    def list_compute_jobs(self, limit: int = 200) -> list[dict]:
        return self._compute_jobs.list(limit)

    def append_compute_job_event(self, job_id: str, kind: str, payload=None) -> int:
        return self._compute_jobs.append_event(job_id, kind, payload)

    def compute_job_events(self, job_id: str) -> list[dict]:
        return self._compute_jobs.events(job_id)

    def delete_compute_job(self, job_id: str) -> None:
        self._compute_jobs.delete(job_id)

    def delete_connector(self, connector_id: str) -> None:
        # Drop the credentials with the row. Otherwise a connector the user
        # removed leaves its env secrets in the keychain with nothing left in
        # the app that refers to them.
        forget_connector_env(self, self._connectors.get(connector_id))
        self._connectors.delete(connector_id)

    def connector_env(self, connector: dict) -> dict:
        """The env a connector's process is launched with, references resolved."""
        return resolve_connector_env(self, connector)

    # --- compaction ------------------------------------------------------
    def archive_compaction(
        self,
        *,
        frame_id: str | None,
        summary: str,
        compacted: list[dict],
        project_id: str = "default",
        **metadata: Any,
    ) -> str:
        return self._compactions.archive(
            frame_id=frame_id,
            summary=summary,
            compacted=compacted,
            project_id=project_id,
            **metadata,
        )

    def list_compaction_archives(self, frame_id: str, *, limit: int = 50) -> list[dict]:
        return self._compactions.list(frame_id, limit=limit)

    # --- endpoints ----------------------------------------------
    def upsert_endpoint(self, name: str, **fields: Any) -> None:
        self._endpoints.upsert(name, **fields)

    def list_endpoints(self) -> list[dict]:
        return self._endpoints.list()

    # --- host_call audit ----------------------------------------
    def log_host_call(
        self,
        *,
        method: str,
        args: list,
        ok: bool,
        frame_id: str | None = None,
        result: Any = None,
        action_group_id: str | None = None,
        action_id: str | None = None,
        permission_decision_id: str | None = None,
        side_effect_class: str | None = None,
        resource_keys: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        self._host_calls.log(
            method=method,
            args=args,
            ok=ok,
            frame_id=frame_id,
            result=result,
            action_group_id=action_group_id,
            action_id=action_id,
            permission_decision_id=permission_decision_id,
            side_effect_class=side_effect_class,
            resource_keys=resource_keys,
        )

    # --- generic read-only query (host.query backing) -------------------
    def query(
        self,
        sql: str,
        params: list | None = None,
        limit: int | None = None,
        timeout_s: float = 5.0,
    ) -> list[dict]:
        """Run a read-only SELECT/CTE. Enforces denylist + timeout."""
        lowered = sql.lower()
        # Denylist check runs against a literal-stripped copy so a denied name
        # inside a string literal/comment is not a false positive, while a real
        # (possibly identifier-quoted) table reference still trips it.
        deny_scan = _strip_sql_literals(lowered)
        for bad in QUERY_DENYLIST:
            if bad in deny_scan:
                raise PermissionError(f"host.query: table '{bad}' is not readable")
        stripped = lowered.lstrip()
        if not (stripped.startswith("select") or stripped.startswith("with")):
            raise ValueError("host.query only allows read-only SELECT/CTE")
        for kw in (
            " insert ",
            " update ",
            " delete ",
            " drop ",
            " alter ",
            " create ",
            " attach ",
            " pragma ",
        ):
            if kw in f" {lowered} ":
                raise ValueError(f"host.query: forbidden keyword {kw.strip()!r}")
        # per-statement timeout via a busy interrupt handler
        with self._lock:
            self._conn.set_progress_handler(_TimeoutGuard(timeout_s), 10000)
            try:
                cur = self._conn.execute(sql, tuple(params or ()))
                rows = cur.fetchmany(limit) if limit else cur.fetchall()
            finally:
                self._conn.set_progress_handler(None, 10000)
        return [dict(r) for r in rows]

    def schema(self) -> dict[str, list[str]]:
        with self._lock:
            tables = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            out: dict[str, list[str]] = {}
            for t in tables:
                name = t["name"]
                if name in QUERY_DENYLIST or name.startswith("sqlite_"):
                    continue
                cols = self._conn.execute(f"PRAGMA table_info({name})").fetchall()
                out[name] = [c["name"] for c in cols]
        return out


class _TimeoutGuard:
    """Progress-handler callback that aborts a query after timeout_s (5s)."""

    def __init__(self, timeout_s: float):
        self._deadline = time.time() + timeout_s

    def __call__(self) -> int:
        return 1 if time.time() > self._deadline else 0


_STORES: dict[str, Store] = {}
_STORES_LOCK = threading.Lock()


def _discard_store(store: Store) -> None:
    """Remove a closed singleton without evicting a newer replacement."""

    key = str(store.db_path.resolve())
    with _STORES_LOCK:
        if _STORES.get(key) is store:
            _STORES.pop(key, None)


def get_store(db_path: Path) -> Store:
    """Process-wide singleton Store per db path."""
    key = str(Path(db_path).resolve())
    with _STORES_LOCK:
        st = _STORES.get(key)
        if st is None or st._closed:
            st = Store(Path(db_path))
            _STORES[key] = st
    return st
