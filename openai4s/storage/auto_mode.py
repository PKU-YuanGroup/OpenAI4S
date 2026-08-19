"""Durable Stage-2 Auto Mode state and append-only audit events.

The repository is deliberately orchestration-free.  It stores exact session,
branch, turn, and execution identities; commits one domain transition and one
canonical event in the same SQLite transaction; and projects branch history
from immutable checkpoint cursors.  It never calls a model, executes a repair,
or resolves a permission request.

The constructor is passive.  ``create_auto_mode_schema`` is invoked only by
the numbered Store migration so a failed upgrade cannot leave half a schema
advertised as current.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from openai4s.storage.branch_projection import project_branch_records
from openai4s.storage.permissions import canonical_permission_action_digest
from openai4s.storage.snapshots import revert_recovery_setting_key


class AutoModeConflictError(ValueError):
    """A durable identity, revision, or idempotency contract was violated."""


_EVENT_TYPES = frozenset(
    {
        "auto_run_started",
        "candidate_ready",
        "auto_audit_started",
        "auto_audit_completed",
        "repair_started",
        "repair_completed",
        "auto_run_terminal",
    }
)
_RUN_STATUSES = frozenset(
    {
        "running",
        "candidate",
        "reviewing",
        "repairing",
        "verified",
        "completed_with_issues",
        "review_unavailable",
        "blocked_by_guardian",
        "cancelled",
        "failed",
        "paused",
        "unverified_import",
    }
)
_TERMINAL_STATUSES = _RUN_STATUSES - {
    "running",
    "candidate",
    "reviewing",
    "repairing",
}
_RUN_MODES = frozenset({"off", "review_only", "auto_fix"})
_REVIEW_VERDICTS = frozenset(
    {
        "pass",
        "completed_with_issues",
        "issues",
        "fail",
        "failed",
        "incomplete",
        "needs_repair",
        "review_unavailable",
        "unavailable",
    }
)
_PERMISSION_OUTCOMES = frozenset(
    {
        "allow",
        "allow_once",
        "allowed",
        "deny",
        "denied",
        "ask",
        "unavailable",
        "failed",
        "shadow_allow",
        "shadow_deny",
    }
)
_RISK_LEVELS = frozenset({"low", "medium", "high", "critical", "unknown"})
_SUBJECT_ENTITY = {
    "result_review": "candidate_evidence_snapshot",
    "permission_review": "approval_action",
}
_HEX64 = re.compile(r"[0-9a-f]{64}")
_MAX_JSON_BYTES = 32 * 1024 * 1024
_MAX_TEXT = 20_000
_PRIVATE_IMPORT_FIELDS = frozenset(
    {
        "authorization",
        "authorization_token",
        "permission_payload",
        "prompt",
        "hidden_prompt",
        "hidden_rationale",
        "rationale",
        "api_key",
        "access_token",
        "credential",
        "credentials",
        "secret",
        "password",
        "cookie",
    }
)


AUTO_MODE_SCHEMA = """
CREATE TABLE IF NOT EXISTS auto_mode_selections (
    scope_kind TEXT NOT NULL CHECK(scope_kind IN ('frame','project')),
    scope_id TEXT NOT NULL,
    is_set INTEGER NOT NULL CHECK(is_set IN (0,1)),
    preset TEXT CHECK(preset IS NULL OR preset IN ('off','autonomous')),
    result_review_mode TEXT CHECK(result_review_mode IS NULL OR result_review_mode IN ('off','review_only','auto_fix')),
    approvals_reviewer TEXT CHECK(approvals_reviewer IS NULL OR approvals_reviewer IN ('user','auto_review')),
    budgets_json TEXT,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(scope_kind,scope_id)
);

CREATE TABLE IF NOT EXISTS auto_mode_runs (
    run_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL,
    root_frame_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('off','review_only','auto_fix')),
    selection_json TEXT NOT NULL,
    budgets_json TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    owner_instance_id TEXT NOT NULL,
    trust_state TEXT NOT NULL DEFAULT 'local' CHECK(trust_state IN ('local','quarantined_import')),
    status TEXT NOT NULL CHECK(status IN ('running','candidate','reviewing','repairing','verified','completed_with_issues','review_unavailable','blocked_by_guardian','cancelled','failed','paused','unverified_import')),
    state_revision INTEGER NOT NULL DEFAULT 1 CHECK(state_revision >= 1),
    candidate_id TEXT,
    candidate_snapshot_sha256 TEXT,
    evidence_snapshot_sha256 TEXT,
    artifact_set_sha256 TEXT,
    candidate_artifact_ids_json TEXT NOT NULL DEFAULT '[]',
    candidate_version_ids_json TEXT NOT NULL DEFAULT '[]',
    terminal_reason TEXT,
    stop_reason TEXT,
    terminal_idempotency_key TEXT,
    terminal_request_sha256 TEXT,
    source_claimed_status TEXT,
    source_terminal_reason TEXT,
    abandoned_at INTEGER,
    abandoned_by_checkpoint_id TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    finished_at INTEGER,
    UNIQUE(root_frame_id,idempotency_key),
    UNIQUE(root_frame_id,branch_id,turn_id,execution_id),
    UNIQUE(run_id,root_frame_id,branch_id,turn_id,execution_id)
);
CREATE INDEX IF NOT EXISTS ix_auto_mode_runs_root
    ON auto_mode_runs(root_frame_id,branch_id,created_at,run_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_auto_mode_active_branch
    ON auto_mode_runs(root_frame_id,branch_id)
    WHERE trust_state='local' AND finished_at IS NULL AND abandoned_at IS NULL;

CREATE TABLE IF NOT EXISTS auto_mode_events (
    event_id TEXT PRIMARY KEY,
    root_frame_id TEXT NOT NULL,
    event_cursor INTEGER NOT NULL CHECK(event_cursor >= 1),
    run_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence >= 1),
    idempotency_key TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('auto_run_started','candidate_ready','auto_audit_started','auto_audit_completed','repair_started','repair_completed','auto_run_terminal')),
    request_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(root_frame_id,event_cursor),
    UNIQUE(run_id,sequence),
    UNIQUE(run_id,idempotency_key),
    FOREIGN KEY(run_id,root_frame_id,branch_id,turn_id,execution_id)
      REFERENCES auto_mode_runs(run_id,root_frame_id,branch_id,turn_id,execution_id)
      ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_auto_mode_events_branch
    ON auto_mode_events(root_frame_id,branch_id,event_cursor);
CREATE UNIQUE INDEX IF NOT EXISTS ux_auto_mode_terminal
    ON auto_mode_events(run_id) WHERE type='auto_run_terminal';

CREATE TABLE IF NOT EXISTS review_runs (
    review_run_id TEXT PRIMARY KEY,
    audit_id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL,
    root_frame_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    start_idempotency_key TEXT NOT NULL,
    start_request_sha256 TEXT NOT NULL,
    completion_idempotency_key TEXT,
    completion_request_sha256 TEXT,
    candidate_id TEXT NOT NULL,
    candidate_snapshot_sha256 TEXT NOT NULL,
    evidence_snapshot_json TEXT NOT NULL,
    evidence_snapshot_sha256 TEXT NOT NULL,
    round_index INTEGER NOT NULL CHECK(round_index >= 0),
    attempt INTEGER NOT NULL CHECK(attempt >= 1),
    reviewer_json TEXT NOT NULL,
    audit_request_digest TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('started','completed','unavailable','failed','unverified_import')),
    verdict TEXT,
    assessment_json TEXT,
    assessment_envelope_json TEXT,
    assessment_digest TEXT,
    usage_json TEXT,
    public_summary TEXT,
    started_at INTEGER NOT NULL,
    completed_at INTEGER,
    UNIQUE(run_id,start_idempotency_key),
    UNIQUE(run_id,completion_idempotency_key),
    UNIQUE(review_run_id,run_id,root_frame_id,branch_id,turn_id,execution_id),
    FOREIGN KEY(run_id,root_frame_id,branch_id,turn_id,execution_id)
      REFERENCES auto_mode_runs(run_id,root_frame_id,branch_id,turn_id,execution_id)
      ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_review_runs_run ON review_runs(run_id,started_at);

CREATE TABLE IF NOT EXISTS review_findings (
    finding_id TEXT PRIMARY KEY,
    review_run_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    root_frame_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    finding_ordinal INTEGER NOT NULL CHECK(finding_ordinal >= 0),
    fingerprint TEXT NOT NULL,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    claim TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    artifact_ids_json TEXT NOT NULL,
    version_ids_json TEXT NOT NULL,
    cell_ids_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(review_run_id,fingerprint),
    UNIQUE(review_run_id,finding_ordinal),
    FOREIGN KEY(review_run_id,run_id,root_frame_id,branch_id,turn_id,execution_id)
      REFERENCES review_runs(review_run_id,run_id,root_frame_id,branch_id,turn_id,execution_id)
      ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_review_findings_run ON review_findings(run_id,status);

CREATE TABLE IF NOT EXISTS repair_runs (
    repair_run_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    root_frame_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    start_idempotency_key TEXT NOT NULL,
    start_request_sha256 TEXT NOT NULL,
    completion_idempotency_key TEXT,
    completion_request_sha256 TEXT,
    finding_ids_json TEXT NOT NULL,
    before_version_ids_json TEXT NOT NULL,
    after_version_ids_json TEXT,
    execution_group_ids_json TEXT,
    verification_review_run_id TEXT,
    checkpoint_id TEXT,
    status TEXT NOT NULL CHECK(status IN ('started','completed','failed','outcome_unknown','unverified_import')),
    started_at INTEGER NOT NULL,
    completed_at INTEGER,
    UNIQUE(run_id,start_idempotency_key),
    UNIQUE(run_id,completion_idempotency_key),
    UNIQUE(repair_run_id,run_id,root_frame_id,branch_id,turn_id,execution_id),
    FOREIGN KEY(run_id,root_frame_id,branch_id,turn_id,execution_id)
      REFERENCES auto_mode_runs(run_id,root_frame_id,branch_id,turn_id,execution_id)
      ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_repair_runs_run ON repair_runs(run_id,started_at);

CREATE TABLE IF NOT EXISTS repair_execution_groups (
    repair_run_id TEXT NOT NULL,
    action_group_id TEXT NOT NULL,
    binding_ordinal INTEGER NOT NULL CHECK(binding_ordinal >= 0),
    action_group_kind TEXT NOT NULL CHECK(length(trim(action_group_kind)) > 0),
    run_id TEXT NOT NULL,
    root_frame_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    bound_at INTEGER NOT NULL,
    ledger_event_count INTEGER,
    ledger_sha256 TEXT,
    sealed_at INTEGER,
    PRIMARY KEY(repair_run_id,action_group_id),
    UNIQUE(action_group_id),
    UNIQUE(repair_run_id,binding_ordinal),
    UNIQUE(repair_run_id,idempotency_key),
    FOREIGN KEY(repair_run_id,run_id,root_frame_id,branch_id,turn_id,execution_id)
      REFERENCES repair_runs(repair_run_id,run_id,root_frame_id,branch_id,turn_id,execution_id)
      ON DELETE CASCADE,
    FOREIGN KEY(action_group_id) REFERENCES action_groups(group_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS permission_review_assessments (
    assessment_id TEXT PRIMARY KEY,
    audit_id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL,
    root_frame_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    action_digest TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    start_idempotency_key TEXT NOT NULL,
    start_request_sha256 TEXT NOT NULL,
    completion_idempotency_key TEXT,
    completion_request_sha256 TEXT,
    audit_request_digest TEXT NOT NULL,
    assessment_json TEXT,
    assessment_envelope_json TEXT,
    assessment_digest TEXT,
    outcome TEXT,
    risk TEXT,
    public_summary TEXT,
    status TEXT NOT NULL CHECK(status IN ('started','completed','unavailable','failed','unverified_import')),
    started_at INTEGER NOT NULL,
    completed_at INTEGER,
    UNIQUE(decision_id),
    UNIQUE(run_id,start_idempotency_key),
    UNIQUE(run_id,completion_idempotency_key),
    UNIQUE(assessment_id,run_id,root_frame_id,branch_id,turn_id,execution_id),
    FOREIGN KEY(run_id,root_frame_id,branch_id,turn_id,execution_id)
      REFERENCES auto_mode_runs(run_id,root_frame_id,branch_id,turn_id,execution_id)
      ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_permission_review_run
    ON permission_review_assessments(run_id,started_at);
"""


def create_auto_mode_schema(connection: sqlite3.Connection) -> None:
    """Install the complete v25 schema without committing the caller."""

    for statement in AUTO_MODE_SCHEMA.split(";"):
        statement = statement.strip()
        if statement:
            connection.execute(statement)
    permission_columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(permission_requests)")
    }
    for name, declaration in (
        ("dangerous", "INTEGER NOT NULL DEFAULT 0"),
        ("canonical_arguments_sha256", "TEXT"),
        ("action_digest", "TEXT"),
    ):
        if name not in permission_columns:
            connection.execute(
                f"ALTER TABLE permission_requests ADD COLUMN {name} {declaration}"
            )
    # The action half of a permission row is append-only.  Resolution fields
    # remain mutable, but a reviewer must never approve one digest and have the
    # request rewritten to a different action before completion or export.
    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_permission_action_immutable "
        "BEFORE UPDATE OF root_frame_id,frame_id,project_id,action_group_id,"
        "action_id,tool_call_id,tool,target,side_effect_class,resource_keys,"
        "payload,dangerous,canonical_arguments_sha256,action_digest,created_at,"
        "expires_at ON permission_requests BEGIN "
        "SELECT RAISE(ABORT,'permission action identity is immutable'); END"
    )
    # ``audit_id`` is one global event-pair identity even though result and
    # permission assessments have focused owner tables.  SQLite cannot express
    # a UNIQUE constraint across two tables, so symmetric triggers make the
    # invariant durable while repository checks provide a typed error.
    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_review_audit_id_global "
        "BEFORE INSERT ON review_runs "
        "WHEN EXISTS(SELECT 1 FROM permission_review_assessments "
        "WHERE audit_id=NEW.audit_id) BEGIN "
        "SELECT RAISE(ABORT,'duplicate global Auto Mode audit_id'); END"
    )
    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_permission_audit_id_global "
        "BEFORE INSERT ON permission_review_assessments "
        "WHEN EXISTS(SELECT 1 FROM review_runs WHERE audit_id=NEW.audit_id) BEGIN "
        "SELECT RAISE(ABORT,'duplicate global Auto Mode audit_id'); END"
    )
    checkpoint_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_checkpoints'"
    ).fetchone()
    checkpoint_columns = (
        {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(session_checkpoints)")
        }
        if checkpoint_exists is not None
        else set()
    )
    # A fresh database creates the snapshot tables when their owning
    # repository is composed immediately after numbered migrations; its DDL
    # already includes this column.  An upgraded database has the table here,
    # so advance the cursor shape in the same v25 transaction as the Auto Mode
    # tables.  Never create a partial snapshot schema from this repository.
    if checkpoint_exists is not None and "auto_event_cursor" not in checkpoint_columns:
        connection.execute(
            "ALTER TABLE session_checkpoints ADD COLUMN "
            "auto_event_cursor INTEGER NOT NULL DEFAULT 0"
        )
    binding_info = {
        str(row[1]): row
        for row in connection.execute("PRAGMA table_info(repair_execution_groups)")
    }
    had_canonical_columns = {
        "action_group_kind",
        "binding_ordinal",
    } <= set(binding_info)
    if "action_group_kind" not in binding_info:
        connection.execute(
            "ALTER TABLE repair_execution_groups ADD COLUMN action_group_kind TEXT"
        )
    if "binding_ordinal" not in binding_info:
        connection.execute(
            "ALTER TABLE repair_execution_groups ADD COLUMN binding_ordinal INTEGER"
        )
    action_groups_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='action_groups'"
    ).fetchone()
    if action_groups_exists is not None:
        connection.execute(
            "UPDATE repair_execution_groups SET action_group_kind=("
            "SELECT kind FROM action_groups WHERE group_id=action_group_id) "
            "WHERE action_group_kind IS NULL OR TRIM(action_group_kind)=''"
        )

    # Older development snapshots did not persist a binding ordinal. Recover
    # the owner's declared order first, then append any remaining rows in their
    # stable historical order. Keep the owner list synchronized with that one
    # canonical result so event/owner/table proofs cannot disagree after boot.
    repair_rows = connection.execute(
        "SELECT repair_run_id,execution_group_ids_json FROM repair_runs"
    ).fetchall()
    known_repairs: set[str] = set()
    for repair_row in repair_rows:
        repair_run_id = str(repair_row[0])
        known_repairs.add(repair_run_id)
        try:
            owner_group_ids = json.loads(repair_row[1] or "[]")
        except (TypeError, ValueError) as error:
            raise sqlite3.IntegrityError(
                "repair execution binding owner order is malformed"
            ) from error
        if (
            not isinstance(owner_group_ids, list)
            or any(
                not isinstance(group_id, str) or not group_id
                for group_id in owner_group_ids
            )
            or len(owner_group_ids) != len(set(owner_group_ids))
        ):
            raise sqlite3.IntegrityError(
                "repair execution binding owner order is malformed"
            )
        historical_rows = connection.execute(
            "SELECT action_group_id FROM repair_execution_groups "
            "WHERE repair_run_id=? ORDER BY bound_at,rowid",
            (repair_run_id,),
        ).fetchall()
        historical_group_ids = [str(row[0]) for row in historical_rows]
        if any(group_id not in historical_group_ids for group_id in owner_group_ids):
            raise sqlite3.IntegrityError(
                "repair execution binding owner references an unknown group"
            )
        ordered_group_ids = owner_group_ids + [
            group_id
            for group_id in historical_group_ids
            if group_id not in owner_group_ids
        ]
        for ordinal, group_id in enumerate(ordered_group_ids):
            connection.execute(
                "UPDATE repair_execution_groups SET binding_ordinal=? "
                "WHERE repair_run_id=? AND action_group_id=?",
                (ordinal, repair_run_id, group_id),
            )
        if ordered_group_ids != owner_group_ids:
            connection.execute(
                "UPDATE repair_runs SET execution_group_ids_json=? "
                "WHERE repair_run_id=?",
                (_canonical(ordered_group_ids), repair_run_id),
            )
    orphan = connection.execute(
        "SELECT repair_run_id FROM repair_execution_groups "
        "WHERE repair_run_id NOT IN (SELECT repair_run_id FROM repair_runs) LIMIT 1"
    ).fetchone()
    invalid_binding = connection.execute(
        "SELECT repair_run_id FROM repair_execution_groups WHERE "
        "action_group_kind IS NULL OR TRIM(action_group_kind)='' OR "
        "binding_ordinal IS NULL OR binding_ordinal<0 LIMIT 1"
    ).fetchone()
    if orphan is not None or invalid_binding is not None:
        raise sqlite3.IntegrityError("repair execution binding backfill is incomplete")
    for repair_run_id in known_repairs:
        ordinals = [
            int(row[0])
            for row in connection.execute(
                "SELECT binding_ordinal FROM repair_execution_groups "
                "WHERE repair_run_id=? ORDER BY binding_ordinal",
                (repair_run_id,),
            ).fetchall()
        ]
        if ordinals != list(range(len(ordinals))):
            raise sqlite3.IntegrityError(
                "repair execution binding ordinals are not contiguous"
            )

    table_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='repair_execution_groups'"
    ).fetchone()
    table_sql = str(table_sql_row[0] or "") if table_sql_row is not None else ""
    binding_info = {
        str(row[1]): row
        for row in connection.execute("PRAGMA table_info(repair_execution_groups)")
    }
    needs_rebuild = (
        not had_canonical_columns
        or not bool(binding_info["binding_ordinal"][3])
        or not bool(binding_info["action_group_kind"][3])
        or "CHECK(binding_ordinal >= 0)" not in table_sql
        or "CHECK(length(trim(action_group_kind)) > 0)" not in table_sql
    )
    if needs_rebuild:
        connection.execute("DROP INDEX IF EXISTS ix_repair_execution_run")
        connection.execute("DROP INDEX IF EXISTS ux_repair_execution_ordinal")
        connection.execute(
            "ALTER TABLE repair_execution_groups "
            "RENAME TO _v25_repair_execution_groups"
        )
        connection.execute(
            "CREATE TABLE repair_execution_groups ("
            "repair_run_id TEXT NOT NULL,action_group_id TEXT NOT NULL,"
            "binding_ordinal INTEGER NOT NULL CHECK(binding_ordinal >= 0),"
            "action_group_kind TEXT NOT NULL "
            "CHECK(length(trim(action_group_kind)) > 0),run_id TEXT NOT NULL,"
            "root_frame_id TEXT NOT NULL,branch_id TEXT NOT NULL,"
            "turn_id TEXT NOT NULL,execution_id TEXT NOT NULL,"
            "idempotency_key TEXT NOT NULL,request_sha256 TEXT NOT NULL,"
            "bound_at INTEGER NOT NULL,ledger_event_count INTEGER,"
            "ledger_sha256 TEXT,sealed_at INTEGER,"
            "PRIMARY KEY(repair_run_id,action_group_id),"
            "UNIQUE(action_group_id),UNIQUE(repair_run_id,binding_ordinal),"
            "UNIQUE(repair_run_id,idempotency_key),"
            "FOREIGN KEY(repair_run_id,run_id,root_frame_id,branch_id,turn_id,execution_id) "
            "REFERENCES repair_runs(repair_run_id,run_id,root_frame_id,branch_id,turn_id,execution_id) "
            "ON DELETE CASCADE,FOREIGN KEY(action_group_id) "
            "REFERENCES action_groups(group_id) ON DELETE RESTRICT)"
        )
        connection.execute(
            "INSERT INTO repair_execution_groups("
            "repair_run_id,action_group_id,binding_ordinal,action_group_kind,"
            "run_id,root_frame_id,branch_id,turn_id,execution_id,idempotency_key,"
            "request_sha256,bound_at,ledger_event_count,ledger_sha256,sealed_at) "
            "SELECT repair_run_id,action_group_id,binding_ordinal,action_group_kind,"
            "run_id,root_frame_id,branch_id,turn_id,execution_id,idempotency_key,"
            "request_sha256,bound_at,ledger_event_count,ledger_sha256,sealed_at "
            "FROM _v25_repair_execution_groups"
        )
        connection.execute("DROP TABLE _v25_repair_execution_groups")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_repair_execution_run "
        "ON repair_execution_groups(run_id,repair_run_id,binding_ordinal)"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_repair_execution_ordinal "
        "ON repair_execution_groups(repair_run_id,binding_ordinal)"
    )
    install_auto_mode_action_guards(connection)


def install_auto_mode_action_guards(connection: sqlite3.Connection) -> None:
    """Install cross-domain action-ledger guards once both schemas exist.

    On an upgrade, ``action_events`` already exists and migration v25 installs
    this inside its transaction.  On a fresh database the action repository is
    composed after numbered migrations, so :class:`Store` calls this once more
    immediately after that repository creates its tables.  The trigger is
    idempotent in both paths.
    """

    action_events_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='action_events'"
    ).fetchone()
    if action_events_exists is None:
        return
    # A repair action group is appendable only while its repair owner is
    # active. Completion seals the exact event/attempt ledger in the same
    # transaction; this database guard closes the race for every action-ledger
    # writer, including writers that do not call back through AutoModeRepository.
    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_repair_ledger_sealed "
        "BEFORE INSERT ON action_events "
        "WHEN EXISTS(SELECT 1 FROM repair_execution_groups "
        "WHERE action_group_id=NEW.group_id AND sealed_at IS NOT NULL) BEGIN "
        "SELECT RAISE(ABORT,'repair action ledger is sealed'); END"
    )
    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_repair_event_update_sealed "
        "BEFORE UPDATE ON action_events "
        "WHEN EXISTS(SELECT 1 FROM repair_execution_groups "
        "WHERE action_group_id=OLD.group_id AND sealed_at IS NOT NULL) "
        "OR EXISTS(SELECT 1 FROM repair_execution_groups "
        "WHERE action_group_id=NEW.group_id AND sealed_at IS NOT NULL) BEGIN "
        "SELECT RAISE(ABORT,'repair action ledger is sealed'); END"
    )
    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_repair_event_delete_sealed "
        "BEFORE DELETE ON action_events "
        "WHEN EXISTS(SELECT 1 FROM repair_execution_groups "
        "WHERE action_group_id=OLD.group_id AND sealed_at IS NOT NULL) BEGIN "
        "SELECT RAISE(ABORT,'repair action ledger is sealed'); END"
    )
    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_repair_attempt_insert_sealed "
        "BEFORE INSERT ON execution_attempts "
        "WHEN EXISTS(SELECT 1 FROM repair_execution_groups "
        "WHERE action_group_id=NEW.group_id AND sealed_at IS NOT NULL) BEGIN "
        "SELECT RAISE(ABORT,'repair action ledger is sealed'); END"
    )
    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_repair_attempt_update_sealed "
        "BEFORE UPDATE ON execution_attempts "
        "WHEN EXISTS(SELECT 1 FROM repair_execution_groups "
        "WHERE action_group_id=OLD.group_id AND sealed_at IS NOT NULL) "
        "OR EXISTS(SELECT 1 FROM repair_execution_groups "
        "WHERE action_group_id=NEW.group_id AND sealed_at IS NOT NULL) BEGIN "
        "SELECT RAISE(ABORT,'repair action ledger is sealed'); END"
    )
    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_repair_attempt_delete_sealed "
        "BEFORE DELETE ON execution_attempts "
        "WHEN EXISTS(SELECT 1 FROM repair_execution_groups "
        "WHERE action_group_id=OLD.group_id AND sealed_at IS NOT NULL) BEGIN "
        "SELECT RAISE(ABORT,'repair action ledger is sealed'); END"
    )


def _canonical(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("Auto Mode values must be canonical JSON") from error
    if len(encoded.encode("utf-8")) > _MAX_JSON_BYTES:
        raise ValueError("Auto Mode JSON value is too large")
    return encoded


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _load(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _text(name: str, value: Any, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    value = value.strip()
    if len(value) > maximum or "\x00" in value:
        raise ValueError(f"{name} is invalid")
    return value


def _sha(name: str, value: Any, *, optional: bool = False) -> str | None:
    if value in (None, "") and optional:
        return None
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _integer(name: str, value: Any, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _string_list(name: str, value: Any, *, limit: int = 10_000) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or len(value) > limit:
        raise ValueError(f"{name} must be a bounded list")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        item = _text(name, item, maximum=1024)
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


class _BranchStore:
    def __init__(
        self,
        get_branch: Callable[[str], dict[str, Any] | None],
        get_checkpoint: Callable[[str], dict[str, Any] | None],
    ) -> None:
        self._get_branch = get_branch
        self._get_checkpoint = get_checkpoint

    def get_session_branch(self, branch_id: str) -> dict[str, Any] | None:
        return self._get_branch(branch_id)

    def get_session_checkpoint(self, checkpoint_id: str) -> dict[str, Any] | None:
        return self._get_checkpoint(checkpoint_id)


class AutoModeRepository:
    """Transactional Auto Mode repository; construction performs no SQL."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        lock: Any,
        *,
        clock_ms: Callable[[], int],
        get_branch: Callable[[str], dict[str, Any] | None] | None = None,
        get_checkpoint: Callable[[str], dict[str, Any] | None] | None = None,
        checkpoint_is_restorable: Callable[[str], bool] | None = None,
        get_action_group: Callable[[str], Mapping[str, Any] | None] | None = None,
    ) -> None:
        self._connection = connection
        self._lock = lock
        self._clock_ms = clock_ms
        self._get_branch = get_branch
        self._get_checkpoint = get_checkpoint
        self._checkpoint_is_restorable = checkpoint_is_restorable
        self._get_action_group = get_action_group

    # ---------------------------------------------------------------- selection
    def get_selection(self, scope_kind: str, scope_id: str) -> dict[str, Any] | None:
        scope_kind = self._scope_kind(scope_kind)
        scope_id = _text("scope_id", scope_id)
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM auto_mode_selections WHERE scope_kind=? AND scope_id=?",
                (scope_kind, scope_id),
            ).fetchone()
        return self._decode_selection(row) if row else None

    def set_selection(
        self,
        scope_kind: str,
        scope_id: str,
        values: Mapping[str, Any],
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        scope_kind = self._scope_kind(scope_kind)
        scope_id = _text("scope_id", scope_id)
        expected_revision = _integer("expected_revision", expected_revision)
        normalized = self._normalize_selection(values)
        now = self._clock_ms()
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                if (
                    scope_kind == "frame"
                    and self._connection.execute(
                        "SELECT 1 FROM settings WHERE key=?",
                        (f"session:import-quarantine:{scope_id}",),
                    ).fetchone()
                    is not None
                ):
                    raise PermissionError(
                        "imported Session Auto Mode selection is read-only"
                    )
                row = self._connection.execute(
                    "SELECT * FROM auto_mode_selections WHERE scope_kind=? AND scope_id=?",
                    (scope_kind, scope_id),
                ).fetchone()
                current_revision = int(row["revision"]) if row else 0
                if current_revision != expected_revision:
                    raise AutoModeConflictError("auto mode selection revision conflict")
                revision = current_revision + 1
                is_set = bool(normalized)
                self._connection.execute(
                    "INSERT INTO auto_mode_selections("
                    "scope_kind,scope_id,is_set,preset,result_review_mode,"
                    "approvals_reviewer,budgets_json,revision,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(scope_kind,scope_id) DO UPDATE SET "
                    "is_set=excluded.is_set,preset=excluded.preset,"
                    "result_review_mode=excluded.result_review_mode,"
                    "approvals_reviewer=excluded.approvals_reviewer,"
                    "budgets_json=excluded.budgets_json,revision=excluded.revision,"
                    "updated_at=excluded.updated_at",
                    (
                        scope_kind,
                        scope_id,
                        1 if is_set else 0,
                        normalized.get("preset"),
                        normalized.get("result_review_mode"),
                        normalized.get("approvals_reviewer"),
                        (
                            _canonical(normalized.get("budgets"))
                            if normalized.get("budgets") is not None
                            else None
                        ),
                        revision,
                        now,
                    ),
                )
                result = self._connection.execute(
                    "SELECT * FROM auto_mode_selections WHERE scope_kind=? AND scope_id=?",
                    (scope_kind, scope_id),
                ).fetchone()
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return self._decode_selection(result)

    # -------------------------------------------------------------- core run/event
    def start_run(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        root_frame_id: str,
        branch_id: str,
        turn_id: str,
        execution_id: str,
        mode: str,
        selection: Mapping[str, Any],
        budgets: Mapping[str, Any],
        owner_instance_id: str,
        created_at: int | None = None,
    ) -> dict[str, Any]:
        (
            run_id,
            root_frame_id,
            branch_id,
            turn_id,
            execution_id,
        ) = self._identity(run_id, root_frame_id, branch_id, turn_id, execution_id)
        idempotency_key = _text("idempotency_key", idempotency_key, maximum=1024)
        mode = _text("mode", mode)
        if mode not in _RUN_MODES:
            raise ValueError("invalid Auto Mode run mode")
        selection_value = dict(selection)
        budgets_value = dict(budgets)
        owner_instance_id = _text("owner_instance_id", owner_instance_id)
        request = {
            "root_frame_id": root_frame_id,
            "branch_id": branch_id,
            "turn_id": turn_id,
            "execution_id": execution_id,
            "mode": mode,
            "selection": selection_value,
            "budgets": budgets_value,
        }
        request_sha256 = _digest(request)
        timestamp = self._time(created_at)
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                if (
                    self._connection.execute(
                        "SELECT 1 FROM settings WHERE key=?",
                        (f"session:import-quarantine:{root_frame_id}",),
                    ).fetchone()
                    is not None
                ):
                    raise PermissionError("imported Auto Mode history is inert")
                if (
                    self._connection.execute(
                        "SELECT 1 FROM settings WHERE key=?",
                        (revert_recovery_setting_key(root_frame_id),),
                    ).fetchone()
                    is not None
                ):
                    raise AutoModeConflictError(
                        "Session workspace revert requires recovery before Auto Mode start"
                    )
                root = self._connection.execute(
                    "SELECT frame_id,root_frame_id,parent_id FROM frames "
                    "WHERE frame_id=?",
                    (root_frame_id,),
                ).fetchone()
                if (
                    root is None
                    or root["frame_id"] != root_frame_id
                    or root["root_frame_id"] != root_frame_id
                    or root["parent_id"] is not None
                ):
                    raise AutoModeConflictError(
                        "Auto Mode requires a canonical root Session frame"
                    )
                if self._get_branch is not None:
                    branch = self._get_branch(branch_id)
                    if branch is None or branch.get("root_frame_id") != root_frame_id:
                        raise AutoModeConflictError(
                            "Auto Mode branch does not belong to its root Session"
                        )
                existing = self._connection.execute(
                    "SELECT * FROM auto_mode_runs WHERE root_frame_id=? AND idempotency_key=?",
                    (root_frame_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    if existing["abandoned_at"] is not None:
                        raise AutoModeConflictError(
                            "Auto Mode idempotency key belongs to an abandoned branch tail"
                        )
                    if existing["request_sha256"] != request_sha256:
                        raise AutoModeConflictError(
                            "auto run idempotency digest mismatch"
                        )
                    event = self._event_for_idempotency_locked(
                        str(existing["run_id"]),
                        idempotency_key,
                        expected_type="auto_run_started",
                        expected_request_sha256=request_sha256,
                    )
                    self._assert_run_replay_integrity_locked(existing)
                    self._connection.commit()
                    return self._transition(existing, event, created=False)
                projected = self.project_run(root_frame_id, branch_id=branch_id)
                projected_run = projected.get("run") if projected else None
                projected_owner = None
                physical_tail_cursor = None
                if isinstance(projected_run, Mapping):
                    projected_owner = self._connection.execute(
                        "SELECT * FROM auto_mode_runs WHERE run_id=?",
                        (projected_run.get("run_id"),),
                    ).fetchone()
                    physical_tail_cursor = self._connection.execute(
                        "SELECT MAX(event_cursor) FROM auto_mode_events WHERE run_id=?",
                        (projected_run.get("run_id"),),
                    ).fetchone()[0]
                if (
                    isinstance(projected_run, Mapping)
                    and projected_run.get("branch_id") == branch_id
                    and projected_owner is not None
                    and projected_owner["trust_state"] == "local"
                    and projected_owner["finished_at"] is None
                    and projected_owner["abandoned_at"] is None
                    and physical_tail_cursor == projected.get("last_event_ordinal")
                    and projected_run.get("finished_at") is None
                    and projected_run.get("status") not in _TERMINAL_STATUSES
                ):
                    raise AutoModeConflictError(
                        "Auto Mode branch already has a recovery-required active run"
                    )
                active = self._connection.execute(
                    "SELECT run_id FROM auto_mode_runs WHERE root_frame_id=? "
                    "AND branch_id=? AND trust_state='local' AND finished_at IS NULL "
                    "AND abandoned_at IS NULL "
                    "LIMIT 1",
                    (root_frame_id, branch_id),
                ).fetchone()
                if active is not None:
                    branch_head = (
                        self._get_branch(branch_id)
                        if self._get_branch is not None
                        else None
                    )
                    self._connection.execute(
                        "UPDATE auto_mode_runs SET abandoned_at=?,"
                        "abandoned_by_checkpoint_id=?,state_revision=state_revision+1,"
                        "updated_at=? WHERE run_id=? AND abandoned_at IS NULL",
                        (
                            timestamp,
                            (
                                branch_head.get("head_checkpoint_id")
                                if isinstance(branch_head, Mapping)
                                else None
                            ),
                            timestamp,
                            active["run_id"],
                        ),
                    )
                self._connection.execute(
                    "INSERT INTO auto_mode_runs("
                    "run_id,idempotency_key,root_frame_id,branch_id,turn_id,execution_id,"
                    "mode,selection_json,budgets_json,request_sha256,owner_instance_id,"
                    "trust_state,status,state_revision,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        run_id,
                        idempotency_key,
                        root_frame_id,
                        branch_id,
                        turn_id,
                        execution_id,
                        mode,
                        _canonical(selection_value),
                        _canonical(budgets_value),
                        request_sha256,
                        owner_instance_id,
                        "local",
                        "running",
                        1,
                        timestamp,
                        timestamp,
                    ),
                )
                run = self._run_locked(run_id)
                event, _ = self._append_event_locked(
                    run,
                    idempotency_key=idempotency_key,
                    event_type="auto_run_started",
                    request_sha256=request_sha256,
                    payload={
                        "mode": mode,
                        "status": "running",
                        "selection": selection_value,
                        "budgets": budgets_value,
                    },
                    created_at=timestamp,
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return self._transition(run, event, created=True)

    def record_candidate(
        self,
        run_id: str,
        *,
        idempotency_key: str,
        candidate_id: str,
        candidate_snapshot_sha256: str,
        evidence_snapshot_sha256: str,
        candidate_version_ids: Sequence[str],
        artifact_set_sha256: str | None = None,
        candidate_artifact_ids: Sequence[str] = (),
        created_at: int | None = None,
    ) -> dict[str, Any]:
        run_id = _text("run_id", run_id)
        idempotency_key = _text("idempotency_key", idempotency_key, maximum=1024)
        candidate_id = _text("candidate_id", candidate_id)
        candidate_snapshot_sha256 = str(
            _sha("candidate_snapshot_sha256", candidate_snapshot_sha256)
        )
        evidence_snapshot_sha256 = str(
            _sha("evidence_snapshot_sha256", evidence_snapshot_sha256)
        )
        artifact_set_sha256 = _sha(
            "artifact_set_sha256", artifact_set_sha256, optional=True
        )
        versions = _string_list("candidate_version_ids", candidate_version_ids)
        artifacts = _string_list("candidate_artifact_ids", candidate_artifact_ids)
        request = {
            "candidate_id": candidate_id,
            "candidate_snapshot_sha256": candidate_snapshot_sha256,
            "evidence_snapshot_sha256": evidence_snapshot_sha256,
            "artifact_set_sha256": artifact_set_sha256,
            "candidate_artifact_ids": artifacts,
            "candidate_version_ids": versions,
        }
        request_sha256 = _digest(request)
        timestamp = self._time(created_at)
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                run = self._run_locked(run_id)
                replay = self._idempotent_event_locked(
                    run, idempotency_key, request_sha256, "candidate_ready"
                )
                if replay is not None:
                    self._assert_run_replay_integrity_locked(run)
                    self._connection.commit()
                    return self._transition(run, replay, created=False)
                self._assert_mutable_run(run)
                if run["status"] not in {"running", "candidate"}:
                    raise AutoModeConflictError(
                        "candidate cannot change while an Auto Mode phase is active"
                    )
                prior_candidates = self._connection.execute(
                    "SELECT * FROM auto_mode_events WHERE run_id=? "
                    "AND type='candidate_ready'",
                    (run_id,),
                ).fetchall()
                for prior in prior_candidates:
                    prior_payload = self._decode_event(prior)["payload"]
                    if (
                        isinstance(prior_payload, Mapping)
                        and prior_payload.get("candidate_id") == candidate_id
                        and any(
                            prior_payload.get(field) != request.get(field)
                            for field in request
                        )
                    ):
                        raise AutoModeConflictError(
                            "candidate identity cannot bind different snapshots or artifacts"
                        )
                self._connection.execute(
                    "UPDATE auto_mode_runs SET status='candidate',state_revision=state_revision+1,"
                    "candidate_id=?,candidate_snapshot_sha256=?,evidence_snapshot_sha256=?,"
                    "artifact_set_sha256=?,candidate_artifact_ids_json=?,"
                    "candidate_version_ids_json=?,updated_at=? WHERE run_id=?",
                    (
                        candidate_id,
                        candidate_snapshot_sha256,
                        evidence_snapshot_sha256,
                        artifact_set_sha256,
                        _canonical(artifacts),
                        _canonical(versions),
                        timestamp,
                        run_id,
                    ),
                )
                run = self._run_locked(run_id)
                event, _ = self._append_event_locked(
                    run,
                    idempotency_key=idempotency_key,
                    event_type="candidate_ready",
                    request_sha256=request_sha256,
                    payload={**request, "status": "candidate"},
                    created_at=timestamp,
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return self._transition(run, event, created=True)

    def terminate_run(
        self,
        run_id: str,
        *,
        idempotency_key: str,
        status: str,
        reason: str,
        stop_reason: str | None = None,
        finished_at: int | None = None,
    ) -> dict[str, Any]:
        run_id = _text("run_id", run_id)
        idempotency_key = _text("idempotency_key", idempotency_key, maximum=1024)
        status = _text("status", status)
        if status not in _TERMINAL_STATUSES:
            raise ValueError("invalid Auto Mode terminal status")
        reason = _text("reason", reason, maximum=1024)
        if stop_reason is not None:
            stop_reason = _text("stop_reason", stop_reason, maximum=1024)
        request = {"status": status, "reason": reason, "stop_reason": stop_reason}
        request_sha256 = _digest(request)
        timestamp = self._time(finished_at)
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                run = self._run_locked(run_id)
                replay = self._idempotent_event_locked(
                    run, idempotency_key, request_sha256, "auto_run_terminal"
                )
                if replay is not None:
                    self._assert_run_replay_integrity_locked(run)
                    self._connection.commit()
                    return self._transition(run, replay, created=False)
                if (
                    run["finished_at"] is not None
                    or run["status"] in _TERMINAL_STATUSES
                ):
                    raise AutoModeConflictError("auto run terminal is immutable")
                self._assert_mutable_run(run)
                self._assert_no_active_phase_locked(run)
                if status == "verified":
                    self._assert_verified_locked(run)
                self._connection.execute(
                    "UPDATE auto_mode_runs SET status=?,state_revision=state_revision+1,"
                    "terminal_reason=?,stop_reason=?,terminal_idempotency_key=?,"
                    "terminal_request_sha256=?,updated_at=?,finished_at=? WHERE run_id=?",
                    (
                        status,
                        reason,
                        stop_reason,
                        idempotency_key,
                        request_sha256,
                        timestamp,
                        timestamp,
                        run_id,
                    ),
                )
                run = self._run_locked(run_id)
                event, _ = self._append_event_locked(
                    run,
                    idempotency_key=idempotency_key,
                    event_type="auto_run_terminal",
                    request_sha256=request_sha256,
                    payload={
                        "status": status,
                        "terminal_reason": reason,
                        "stop_reason": stop_reason,
                    },
                    created_at=timestamp,
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return self._transition(run, event, created=True)

    # --------------------------------------------------------------- result audits
    def start_review(
        self,
        run_id: str,
        *,
        review_run_id: str,
        audit_id: str,
        idempotency_key: str,
        candidate_id: str,
        candidate_snapshot_sha256: str,
        evidence_snapshot: Mapping[str, Any],
        evidence_snapshot_sha256: str,
        round_index: int,
        attempt: int,
        reviewer: Mapping[str, Any],
        started_at: int | None = None,
    ) -> dict[str, Any]:
        run_id = _text("run_id", run_id)
        review_run_id = _text("review_run_id", review_run_id)
        audit_id = _text("audit_id", audit_id)
        idempotency_key = _text("idempotency_key", idempotency_key, maximum=1024)
        candidate_id = _text("candidate_id", candidate_id)
        candidate_snapshot_sha256 = str(
            _sha("candidate_snapshot_sha256", candidate_snapshot_sha256)
        )
        evidence = dict(evidence_snapshot)
        evidence_snapshot_sha256 = str(
            _sha("evidence_snapshot_sha256", evidence_snapshot_sha256)
        )
        if _digest(evidence) != evidence_snapshot_sha256:
            raise AutoModeConflictError("evidence snapshot hash mismatch")
        round_index = _integer("round_index", round_index)
        attempt = _integer("attempt", attempt, minimum=1)
        reviewer_value = dict(reviewer)
        model_profile_id = _text(
            "reviewer.profile_id", reviewer_value.get("profile_id")
        )
        model_profile_revision = _integer(
            "reviewer.profile_revision",
            reviewer_value.get("profile_revision"),
            minimum=1,
        )
        model_fingerprint = _text(
            "reviewer.model_fingerprint",
            reviewer_value.get("model_fingerprint"),
            maximum=1024,
        )
        request = {
            "candidate_id": candidate_id,
            "candidate_snapshot_sha256": candidate_snapshot_sha256,
            "evidence_snapshot_sha256": evidence_snapshot_sha256,
            "round_index": round_index,
            "attempt": attempt,
            "reviewer": reviewer_value,
        }
        request_sha256 = _digest(request)
        audit_request_digest = _digest(
            {
                "subject_kind": "result_review",
                "subject_entity_kind": "candidate_evidence_snapshot",
                **request,
            }
        )
        timestamp = self._time(started_at)
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                run = self._run_locked(run_id)
                existing = self._connection.execute(
                    "SELECT * FROM review_runs WHERE run_id=? AND start_idempotency_key=?",
                    (run_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    if existing["start_request_sha256"] != request_sha256:
                        raise AutoModeConflictError(
                            "review idempotency digest mismatch"
                        )
                    event = self._event_for_idempotency_locked(
                        run_id,
                        idempotency_key,
                        expected_type="auto_audit_started",
                        expected_request_sha256=request_sha256,
                    )
                    self._assert_review_assessment_proof_locked(existing)
                    self._assert_run_replay_integrity_locked(run)
                    self._connection.commit()
                    return self._review_transition(existing, event, created=False)
                self._assert_mutable_run(run)
                if run["status"] != "candidate":
                    raise AutoModeConflictError(
                        "result review requires a durable candidate"
                    )
                self._assert_current_candidate_event_locked(run)
                if (
                    run["candidate_id"] != candidate_id
                    or run["candidate_snapshot_sha256"] != candidate_snapshot_sha256
                    or run["evidence_snapshot_sha256"] != evidence_snapshot_sha256
                ):
                    raise AutoModeConflictError("review candidate binding mismatch")
                if (
                    self._connection.execute(
                        "SELECT 1 FROM permission_review_assessments WHERE audit_id=?",
                        (audit_id,),
                    ).fetchone()
                    is not None
                ):
                    raise AutoModeConflictError(
                        "Auto Mode audit identity belongs to another subject"
                    )
                self._connection.execute(
                    "INSERT INTO review_runs("
                    "review_run_id,audit_id,run_id,root_frame_id,branch_id,turn_id,"
                    "execution_id,start_idempotency_key,start_request_sha256,"
                    "candidate_id,candidate_snapshot_sha256,evidence_snapshot_json,"
                    "evidence_snapshot_sha256,round_index,attempt,reviewer_json,"
                    "audit_request_digest,status,started_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        review_run_id,
                        audit_id,
                        run_id,
                        run["root_frame_id"],
                        run["branch_id"],
                        run["turn_id"],
                        run["execution_id"],
                        idempotency_key,
                        request_sha256,
                        candidate_id,
                        candidate_snapshot_sha256,
                        _canonical(evidence),
                        evidence_snapshot_sha256,
                        round_index,
                        attempt,
                        _canonical(reviewer_value),
                        audit_request_digest,
                        "started",
                        timestamp,
                    ),
                )
                self._connection.execute(
                    "UPDATE auto_mode_runs SET status='reviewing',"
                    "state_revision=state_revision+1,updated_at=? WHERE run_id=?",
                    (timestamp, run_id),
                )
                run = self._run_locked(run_id)
                event, _ = self._append_event_locked(
                    run,
                    idempotency_key=idempotency_key,
                    event_type="auto_audit_started",
                    request_sha256=request_sha256,
                    payload={
                        "audit_id": audit_id,
                        "review_run_id": review_run_id,
                        "subject_kind": "result_review",
                        "subject_entity_kind": "candidate_evidence_snapshot",
                        "subject_entity_id": candidate_id,
                        "candidate_id": candidate_id,
                        "candidate_snapshot_sha256": candidate_snapshot_sha256,
                        "evidence_snapshot_sha256": evidence_snapshot_sha256,
                        "model_profile_id": model_profile_id,
                        "model_profile_revision": model_profile_revision,
                        "model_fingerprint": model_fingerprint,
                        "audit_request_digest": audit_request_digest,
                        "round": round_index,
                        "attempt": attempt,
                        "status": "started",
                    },
                    created_at=timestamp,
                )
                row = self._connection.execute(
                    "SELECT * FROM review_runs WHERE review_run_id=?",
                    (review_run_id,),
                ).fetchone()
                self._assert_review_assessment_proof_locked(
                    row, completion_visible=False
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return self._review_transition(row, event, created=True)

    def complete_review(
        self,
        review_run_id: str,
        *,
        idempotency_key: str,
        status: str,
        verdict: str,
        assessment: Mapping[str, Any],
        findings: Sequence[Mapping[str, Any]],
        usage: Mapping[str, Any] | None = None,
        completed_at: int | None = None,
    ) -> dict[str, Any]:
        review_run_id = _text("review_run_id", review_run_id)
        idempotency_key = _text("idempotency_key", idempotency_key, maximum=1024)
        status = _text("status", status)
        if status not in {"completed", "unavailable", "failed"}:
            raise ValueError("invalid review completion status")
        verdict = _text("verdict", verdict, maximum=256).lower()
        if verdict not in _REVIEW_VERDICTS:
            raise ValueError("invalid review verdict")
        assessment_value = dict(assessment)
        # A Reviewer may discover a finding; it cannot mark its own finding as
        # resolved or user-accepted.  Those are separate, trusted lifecycle
        # facts, and accepted material risk can never qualify as Verified.
        finding_values = [
            {**self._normalize_finding(item), "status": "open"} for item in findings
        ]
        if status != "completed" and verdict == "pass":
            raise ValueError("a non-completed review cannot pass")
        if verdict == "pass" and any(
            str(finding["severity"]).lower()
            in {"material", "major", "high", "critical"}
            for finding in finding_values
        ):
            raise ValueError("a pass review cannot contain a material finding")
        usage_value = dict(usage or {})
        request = {
            "status": status,
            "verdict": verdict,
            "assessment": assessment_value,
            "findings": finding_values,
            "usage": usage_value,
        }
        request_sha256 = _digest(request)
        timestamp = self._time(completed_at)
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._connection.execute(
                    "SELECT * FROM review_runs WHERE review_run_id=?",
                    (review_run_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(review_run_id)
                run = self._run_locked(str(row["run_id"]))
                if row["completion_idempotency_key"] is not None:
                    if (
                        row["completion_idempotency_key"] != idempotency_key
                        or row["completion_request_sha256"] != request_sha256
                    ):
                        raise AutoModeConflictError(
                            "review completion idempotency digest mismatch"
                        )
                    event = self._event_for_idempotency_locked(
                        str(row["run_id"]),
                        idempotency_key,
                        expected_type="auto_audit_completed",
                        expected_request_sha256=request_sha256,
                    )
                    self._assert_review_assessment_proof_locked(
                        row, completion_visible=True
                    )
                    self._assert_run_replay_integrity_locked(run)
                    self._connection.commit()
                    return self._review_transition(row, event, created=False)
                self._assert_mutable_run(run)
                if row["status"] != "started":
                    raise AutoModeConflictError("review is already terminal")
                self._assert_review_assessment_proof_locked(
                    row, completion_visible=False
                )
                assessment_envelope = {
                    "audit_request_digest": row["audit_request_digest"],
                    "subject_kind": "result_review",
                    "subject_entity_kind": "candidate_evidence_snapshot",
                    "attempt": row["attempt"],
                    **request,
                    "durable": True,
                    "retry_state": "terminal",
                }
                assessment_digest = _digest(assessment_envelope)
                for finding_ordinal, finding in enumerate(finding_values):
                    self._connection.execute(
                        "INSERT INTO review_findings("
                        "finding_id,review_run_id,run_id,root_frame_id,branch_id,turn_id,"
                        "execution_id,candidate_id,finding_ordinal,fingerprint,severity,category,claim,"
                        "evidence_refs_json,artifact_ids_json,version_ids_json,cell_ids_json,"
                        "status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            finding["finding_id"],
                            review_run_id,
                            row["run_id"],
                            row["root_frame_id"],
                            row["branch_id"],
                            row["turn_id"],
                            row["execution_id"],
                            row["candidate_id"],
                            finding_ordinal,
                            finding["fingerprint"],
                            finding["severity"],
                            finding["category"],
                            finding["claim"],
                            _canonical(finding["evidence_refs"]),
                            _canonical(finding["artifact_ids"]),
                            _canonical(finding["version_ids"]),
                            _canonical(finding["cell_ids"]),
                            finding["status"],
                            timestamp,
                            timestamp,
                        ),
                    )
                public_summary = assessment_value.get(
                    "public_summary"
                ) or assessment_value.get("summary")
                if not isinstance(public_summary, str):
                    public_summary = None
                elif len(public_summary) > _MAX_TEXT:
                    public_summary = public_summary[:_MAX_TEXT]
                self._connection.execute(
                    "UPDATE review_runs SET completion_idempotency_key=?,"
                    "completion_request_sha256=?,status=?,verdict=?,assessment_json=?,"
                    "assessment_envelope_json=?,assessment_digest=?,usage_json=?,"
                    "public_summary=?,completed_at=? "
                    "WHERE review_run_id=?",
                    (
                        idempotency_key,
                        request_sha256,
                        status,
                        verdict,
                        _canonical(assessment_value),
                        _canonical(assessment_envelope),
                        assessment_digest,
                        _canonical(usage_value),
                        public_summary,
                        timestamp,
                        review_run_id,
                    ),
                )
                self._connection.execute(
                    "UPDATE auto_mode_runs SET status='candidate',"
                    "state_revision=state_revision+1,updated_at=? WHERE run_id=?",
                    (timestamp, row["run_id"]),
                )
                run = self._run_locked(str(row["run_id"]))
                event, _ = self._append_event_locked(
                    run,
                    idempotency_key=idempotency_key,
                    event_type="auto_audit_completed",
                    request_sha256=request_sha256,
                    payload={
                        "audit_id": row["audit_id"],
                        "review_run_id": review_run_id,
                        "subject_kind": "result_review",
                        "subject_entity_kind": "candidate_evidence_snapshot",
                        "subject_entity_id": row["candidate_id"],
                        "candidate_id": row["candidate_id"],
                        "audit_request_digest": row["audit_request_digest"],
                        "assessment_digest": assessment_digest,
                        "verdict": verdict,
                        "status": status,
                        "finding_count": len(finding_values),
                        "public_summary": public_summary,
                        "attempt": row["attempt"],
                    },
                    created_at=timestamp,
                )
                row = self._connection.execute(
                    "SELECT * FROM review_runs WHERE review_run_id=?",
                    (review_run_id,),
                ).fetchone()
                self._assert_review_assessment_proof_locked(
                    row, completion_visible=True
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return self._review_transition(row, event, created=True)

    def _assert_review_assessment_proof_locked(
        self,
        row: sqlite3.Row,
        *,
        completion_visible: bool | None = None,
        run: sqlite3.Row | None = None,
        event_pair: tuple[Mapping[str, Any], Mapping[str, Any] | None] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Bind a result-review owner and findings to its exact event pair."""

        def fail() -> None:
            raise AutoModeConflictError("review assessment proof is invalid")

        try:
            run = run or self._run_locked(str(row["run_id"]))
            if run["trust_state"] != "local":
                fail()
            identity_fields = (
                "run_id",
                "root_frame_id",
                "branch_id",
                "turn_id",
                "execution_id",
            )
            if any(row[name] != run[name] for name in identity_fields):
                fail()
            evidence = json.loads(row["evidence_snapshot_json"])
            reviewer = json.loads(row["reviewer_json"])
            if (
                not isinstance(evidence, Mapping)
                or not isinstance(reviewer, Mapping)
                or _canonical(dict(evidence)) != row["evidence_snapshot_json"]
                or _canonical(dict(reviewer)) != row["reviewer_json"]
                or _digest(dict(evidence)) != row["evidence_snapshot_sha256"]
                or _HEX64.fullmatch(str(row["candidate_snapshot_sha256"] or "")) is None
                or not isinstance(reviewer.get("profile_id"), str)
                or not reviewer.get("profile_id")
                or isinstance(reviewer.get("profile_revision"), bool)
                or not isinstance(reviewer.get("profile_revision"), int)
                or int(reviewer["profile_revision"]) < 1
                or not isinstance(reviewer.get("model_fingerprint"), str)
                or not reviewer.get("model_fingerprint")
                or isinstance(row["round_index"], bool)
                or int(row["round_index"]) < 0
                or isinstance(row["attempt"], bool)
                or int(row["attempt"]) < 1
            ):
                fail()
            start_request = {
                "candidate_id": row["candidate_id"],
                "candidate_snapshot_sha256": row["candidate_snapshot_sha256"],
                "evidence_snapshot_sha256": row["evidence_snapshot_sha256"],
                "round_index": row["round_index"],
                "attempt": row["attempt"],
                "reviewer": dict(reviewer),
            }
            start_request_sha256 = _digest(start_request)
            audit_request_digest = _digest(
                {
                    "subject_kind": "result_review",
                    "subject_entity_kind": "candidate_evidence_snapshot",
                    **start_request,
                }
            )
            if (
                row["start_request_sha256"] != start_request_sha256
                or row["audit_request_digest"] != audit_request_digest
            ):
                fail()
            if event_pair is None:
                event_rows = self._connection.execute(
                    "SELECT * FROM auto_mode_events WHERE run_id=? "
                    "AND type IN ('auto_audit_started','auto_audit_completed') "
                    "ORDER BY sequence",
                    (row["run_id"],),
                ).fetchall()
                starts: list[dict[str, Any]] = []
                completions: list[dict[str, Any]] = []
                for event_row in event_rows:
                    event = self._decode_event(event_row)
                    if event["payload"].get("audit_id") != row["audit_id"]:
                        continue
                    if event["type"] == "auto_audit_started":
                        starts.append(event)
                    else:
                        completions.append(event)
            else:
                starts = [dict(event_pair[0])]
                completions = [dict(event_pair[1])] if event_pair[1] is not None else []
            if len(starts) != 1:
                fail()
            start = starts[0]
            expected_start_payload = {
                "audit_id": row["audit_id"],
                "review_run_id": row["review_run_id"],
                "subject_kind": "result_review",
                "subject_entity_kind": "candidate_evidence_snapshot",
                "subject_entity_id": row["candidate_id"],
                "candidate_id": row["candidate_id"],
                "candidate_snapshot_sha256": row["candidate_snapshot_sha256"],
                "evidence_snapshot_sha256": row["evidence_snapshot_sha256"],
                "model_profile_id": reviewer["profile_id"],
                "model_profile_revision": reviewer["profile_revision"],
                "model_fingerprint": reviewer["model_fingerprint"],
                "audit_request_digest": audit_request_digest,
                "round": row["round_index"],
                "attempt": row["attempt"],
                "status": "started",
            }
            if (
                start["payload"] != expected_start_payload
                or start["idempotency_key"] != row["start_idempotency_key"]
                or start["request_sha256"] != start_request_sha256
                or start["created_at"] != row["started_at"]
                or any(start[name] != row[name] for name in identity_fields)
            ):
                fail()
            if completion_visible is False:
                return start, None

            is_terminal = row["completion_idempotency_key"] is not None
            if completion_visible is True and not is_terminal:
                fail()
            if not is_terminal:
                if (
                    row["status"] != "started"
                    or completions
                    or any(
                        row[name] is not None
                        for name in (
                            "completion_request_sha256",
                            "verdict",
                            "assessment_json",
                            "assessment_envelope_json",
                            "assessment_digest",
                            "usage_json",
                            "public_summary",
                            "completed_at",
                        )
                    )
                ):
                    fail()
                return start, None
            if (
                len(completions) != 1
                or row["status"]
                not in {
                    "completed",
                    "unavailable",
                    "failed",
                }
                or str(row["verdict"] or "").lower() not in _REVIEW_VERDICTS
            ):
                fail()
            assessment = json.loads(row["assessment_json"])
            usage = json.loads(row["usage_json"])
            envelope = json.loads(row["assessment_envelope_json"])
            if (
                not isinstance(assessment, Mapping)
                or not isinstance(usage, Mapping)
                or not isinstance(envelope, Mapping)
                or _canonical(dict(assessment)) != row["assessment_json"]
                or _canonical(dict(usage)) != row["usage_json"]
                or _canonical(dict(envelope)) != row["assessment_envelope_json"]
            ):
                fail()
            finding_rows = self._connection.execute(
                "SELECT * FROM review_findings WHERE review_run_id=? "
                "ORDER BY finding_ordinal,finding_id",
                (row["review_run_id"],),
            ).fetchall()
            if [item["finding_ordinal"] for item in finding_rows] != list(
                range(len(finding_rows))
            ):
                fail()
            finding_values: list[dict[str, Any]] = []
            for finding in finding_rows:
                if (
                    any(finding[name] != row[name] for name in identity_fields)
                    or finding["review_run_id"] != row["review_run_id"]
                    or finding["candidate_id"] != row["candidate_id"]
                    or finding["created_at"] != row["completed_at"]
                ):
                    fail()
                lists: dict[str, list[str]] = {}
                for column, key in (
                    ("evidence_refs_json", "evidence_refs"),
                    ("artifact_ids_json", "artifact_ids"),
                    ("version_ids_json", "version_ids"),
                    ("cell_ids_json", "cell_ids"),
                ):
                    values = json.loads(finding[column])
                    if (
                        not isinstance(values, list)
                        or any(
                            not isinstance(value, str) or not value for value in values
                        )
                        or _canonical(values) != finding[column]
                    ):
                        fail()
                    lists[key] = values
                finding_values.append(
                    {
                        "finding_id": finding["finding_id"],
                        "fingerprint": finding["fingerprint"],
                        "severity": finding["severity"],
                        "category": finding["category"],
                        "claim": finding["claim"],
                        **lists,
                        "status": "open",
                    }
                )
            verdict = str(row["verdict"]).lower()
            if row["status"] != "completed" and verdict == "pass":
                fail()
            if verdict == "pass" and any(
                str(finding["severity"]).lower()
                in {"material", "major", "high", "critical"}
                for finding in finding_values
            ):
                fail()
            completion_request = {
                "status": row["status"],
                "verdict": verdict,
                "assessment": dict(assessment),
                "findings": finding_values,
                "usage": dict(usage),
            }
            completion_request_sha256 = _digest(completion_request)
            expected_envelope = {
                "audit_request_digest": audit_request_digest,
                "subject_kind": "result_review",
                "subject_entity_kind": "candidate_evidence_snapshot",
                "attempt": row["attempt"],
                **completion_request,
                "durable": True,
                "retry_state": "terminal",
            }
            assessment_digest = _digest(expected_envelope)
            public_summary = assessment.get("public_summary") or assessment.get(
                "summary"
            )
            if not isinstance(public_summary, str):
                public_summary = None
            elif len(public_summary) > _MAX_TEXT:
                public_summary = public_summary[:_MAX_TEXT]
            completion = completions[0]
            expected_completion_payload = {
                "audit_id": row["audit_id"],
                "review_run_id": row["review_run_id"],
                "subject_kind": "result_review",
                "subject_entity_kind": "candidate_evidence_snapshot",
                "subject_entity_id": row["candidate_id"],
                "candidate_id": row["candidate_id"],
                "audit_request_digest": audit_request_digest,
                "assessment_digest": assessment_digest,
                "verdict": verdict,
                "status": row["status"],
                "finding_count": len(finding_values),
                "public_summary": public_summary,
                "attempt": row["attempt"],
            }
            if (
                dict(envelope) != expected_envelope
                or row["assessment_digest"] != assessment_digest
                or row["completion_request_sha256"] != completion_request_sha256
                or row["public_summary"] != public_summary
                or completion["payload"] != expected_completion_payload
                or completion["idempotency_key"] != row["completion_idempotency_key"]
                or completion["request_sha256"] != completion_request_sha256
                or completion["created_at"] != row["completed_at"]
                or any(completion[name] != row[name] for name in identity_fields)
                or start["sequence"] >= completion["sequence"]
            ):
                fail()
            return start, completion
        except AutoModeConflictError as error:
            if str(error) == "review assessment proof is invalid":
                raise
            raise AutoModeConflictError("review assessment proof is invalid") from error
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise AutoModeConflictError("review assessment proof is invalid") from error

    # --------------------------------------------------------------- repair audit
    def start_repair(
        self,
        run_id: str,
        *,
        repair_run_id: str,
        idempotency_key: str,
        finding_ids: Sequence[str],
        before_version_ids: Sequence[str],
        checkpoint_id: str,
        started_at: int | None = None,
    ) -> dict[str, Any]:
        run_id = _text("run_id", run_id)
        repair_run_id = _text("repair_run_id", repair_run_id)
        idempotency_key = _text("idempotency_key", idempotency_key, maximum=1024)
        finding_values = _string_list("finding_ids", finding_ids)
        before_values = _string_list("before_version_ids", before_version_ids)
        checkpoint_id = _text("checkpoint_id", checkpoint_id)
        if not finding_values:
            raise ValueError("repair requires at least one finding")
        request = {
            "finding_ids": finding_values,
            "before_version_ids": before_values,
            "checkpoint_id": checkpoint_id,
        }
        request_sha256 = _digest(request)
        timestamp = self._time(started_at)
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                run = self._run_locked(run_id)
                existing = self._connection.execute(
                    "SELECT * FROM repair_runs WHERE run_id=? AND start_idempotency_key=?",
                    (run_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    if existing["start_request_sha256"] != request_sha256:
                        raise AutoModeConflictError(
                            "repair idempotency digest mismatch"
                        )
                    event = self._event_for_idempotency_locked(
                        run_id,
                        idempotency_key,
                        expected_type="repair_started",
                        expected_request_sha256=request_sha256,
                    )
                    self._assert_repair_ledger_proof_locked(existing)
                    self._assert_run_replay_integrity_locked(run)
                    self._connection.commit()
                    return self._repair_transition(existing, event, created=False)
                self._assert_mutable_run(run)
                if run["status"] != "candidate":
                    raise AutoModeConflictError("repair requires a reviewed candidate")
                self._assert_current_candidate_event_locked(run)
                if list(_load(run["candidate_version_ids_json"], [])) != before_values:
                    raise AutoModeConflictError(
                        "repair before versions do not match the current candidate"
                    )
                if (
                    self._get_checkpoint is None
                    or self._get_branch is None
                    or self._checkpoint_is_restorable is None
                ):
                    raise AutoModeConflictError(
                        "repair requires checkpoint repository access"
                    )
                checkpoint = self._get_checkpoint(checkpoint_id)
                branch = self._get_branch(str(run["branch_id"]))
                try:
                    tree_restorable = bool(
                        checkpoint
                        and self._checkpoint_is_restorable(
                            str(checkpoint.get("workspace_tree_id") or "")
                        )
                    )
                except Exception:
                    tree_restorable = False
                if (
                    checkpoint is None
                    or branch is None
                    or checkpoint.get("root_frame_id") != run["root_frame_id"]
                    or checkpoint.get("branch_id") != run["branch_id"]
                    or branch.get("root_frame_id") != run["root_frame_id"]
                    or branch.get("head_checkpoint_id") != checkpoint_id
                    or _HEX64.fullmatch(str(checkpoint.get("workspace_tree_id") or ""))
                    is None
                    or not tree_restorable
                ):
                    raise AutoModeConflictError(
                        "repair requires the current restorable branch checkpoint"
                    )
                checkpoint_cursor = checkpoint.get("auto_event_cursor")
                visible_cursor = self.event_cursor(
                    str(run["root_frame_id"]),
                    branch_id=str(run["branch_id"]),
                )
                physical_cursor = self.event_cursor(str(run["root_frame_id"]))
                if (
                    type(checkpoint_cursor) is not int
                    or checkpoint_cursor < 0
                    or visible_cursor > checkpoint_cursor
                    or checkpoint_cursor > physical_cursor
                ):
                    raise AutoModeConflictError(
                        "repair checkpoint is not at the current event boundary"
                    )
                placeholders = ",".join("?" for _ in finding_values)
                count = self._connection.execute(
                    "SELECT COUNT(*) FROM review_findings f "
                    "JOIN review_runs r ON r.review_run_id=f.review_run_id "
                    "WHERE f.run_id=? AND f.candidate_id=? "
                    "AND lower(f.status) IN ('open','unaddressed') "
                    "AND r.status='completed' AND lower(r.verdict)!='pass' "
                    "AND f.finding_id IN (" + placeholders + ")",
                    (run_id, run["candidate_id"], *finding_values),
                ).fetchone()[0]
                if int(count) != len(finding_values):
                    raise AutoModeConflictError(
                        "repair findings do not belong to the current reviewed candidate"
                    )
                self._connection.execute(
                    "UPDATE review_findings SET status='claimed',updated_at=? "
                    "WHERE run_id=? AND finding_id IN (" + placeholders + ")",
                    (timestamp, run_id, *finding_values),
                )
                self._connection.execute(
                    "INSERT INTO repair_runs("
                    "repair_run_id,run_id,root_frame_id,branch_id,turn_id,execution_id,"
                    "start_idempotency_key,start_request_sha256,finding_ids_json,"
                    "before_version_ids_json,checkpoint_id,status,started_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        repair_run_id,
                        run_id,
                        run["root_frame_id"],
                        run["branch_id"],
                        run["turn_id"],
                        run["execution_id"],
                        idempotency_key,
                        request_sha256,
                        _canonical(finding_values),
                        _canonical(before_values),
                        checkpoint_id,
                        "started",
                        timestamp,
                    ),
                )
                self._connection.execute(
                    "UPDATE auto_mode_runs SET status='repairing',"
                    "state_revision=state_revision+1,updated_at=? WHERE run_id=?",
                    (timestamp, run_id),
                )
                run = self._run_locked(run_id)
                event, _ = self._append_event_locked(
                    run,
                    idempotency_key=idempotency_key,
                    event_type="repair_started",
                    request_sha256=request_sha256,
                    payload={
                        "repair_run_id": repair_run_id,
                        "finding_ids": finding_values,
                        "before_version_ids": before_values,
                        "checkpoint_id": checkpoint_id,
                        "status": "started",
                    },
                    created_at=timestamp,
                )
                row = self._connection.execute(
                    "SELECT * FROM repair_runs WHERE repair_run_id=?",
                    (repair_run_id,),
                ).fetchone()
                self._assert_repair_ledger_proof_locked(row, completion_visible=False)
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return self._repair_transition(row, event, created=True)

    def complete_repair(
        self,
        repair_run_id: str,
        *,
        idempotency_key: str,
        status: str,
        after_version_ids: Sequence[str],
        execution_group_ids: Sequence[str],
        verification_review_run_id: str | None = None,
        completed_at: int | None = None,
    ) -> dict[str, Any]:
        repair_run_id = _text("repair_run_id", repair_run_id)
        idempotency_key = _text("idempotency_key", idempotency_key, maximum=1024)
        status = _text("status", status)
        if status not in {"completed", "failed", "outcome_unknown"}:
            raise ValueError("invalid repair completion status")
        after_values = _string_list("after_version_ids", after_version_ids)
        group_values = _string_list("execution_group_ids", execution_group_ids)
        if verification_review_run_id is not None:
            _text("verification_review_run_id", verification_review_run_id)
            # A post-repair verification cannot exist until this repair has
            # durably completed and a fresh candidate has been recorded.
            # Stage 2 deliberately has no linking transition yet, so accepting
            # a caller-supplied ID here would let the Repair Agent attest to
            # itself (or name a nonexistent/pre-repair review).
            raise AutoModeConflictError(
                "repair completion cannot claim its own verification review"
            )
        request = {
            "status": status,
            "after_version_ids": after_values,
            "execution_group_ids": group_values,
            "verification_review_run_id": verification_review_run_id,
        }
        request_sha256 = _digest(request)
        timestamp = self._time(completed_at)
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._connection.execute(
                    "SELECT * FROM repair_runs WHERE repair_run_id=?",
                    (repair_run_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(repair_run_id)
                run = self._run_locked(str(row["run_id"]))
                if row["completion_idempotency_key"] is not None:
                    if (
                        row["completion_idempotency_key"] != idempotency_key
                        or row["completion_request_sha256"] != request_sha256
                    ):
                        raise AutoModeConflictError(
                            "repair completion idempotency digest mismatch"
                        )
                    event = self._event_for_idempotency_locked(
                        str(row["run_id"]),
                        idempotency_key,
                        expected_type="repair_completed",
                        expected_request_sha256=request_sha256,
                    )
                    self._assert_repair_ledger_proof_locked(
                        row, completion_visible=True
                    )
                    self._assert_run_replay_integrity_locked(run)
                    self._connection.commit()
                    return self._repair_transition(row, event, created=False)
                self._assert_mutable_run(run)
                if row["status"] != "started":
                    raise AutoModeConflictError("repair is already terminal")
                self._assert_repair_ledger_proof_locked(row, completion_visible=False)
                bound_groups = [
                    str(item["action_group_id"])
                    for item in self._connection.execute(
                        "SELECT action_group_id FROM repair_execution_groups "
                        "WHERE repair_run_id=? ORDER BY binding_ordinal",
                        (repair_run_id,),
                    ).fetchall()
                ]
                if group_values != bound_groups:
                    raise AutoModeConflictError(
                        "repair completion must name the exact pre-bound execution ledger"
                    )
                if status in {"completed", "outcome_unknown"} and not bound_groups:
                    raise AutoModeConflictError(
                        f"{status} repair requires a pre-bound execution ledger"
                    )
                for action_group_id in bound_groups:
                    pending = self._connection.execute(
                        "SELECT decision_id FROM permission_requests "
                        "WHERE action_group_id=? AND state='pending' LIMIT 1",
                        (action_group_id,),
                    ).fetchone()
                    if pending is not None:
                        raise AutoModeConflictError(
                            "repair execution has a pending permission decision"
                        )
                # BEGIN IMMEDIATE excludes a concurrent action-event writer.
                # Verify every successful repair group has a durable terminal
                # observation, hash the exact immutable ledger, then seal it
                # before publishing repair_completed.  The insert trigger
                # prevents a late side effect from appearing after this fact.
                has_unknown_effect = False
                has_known_failure = False
                has_committed_effect = False
                for action_group_id in bound_groups:
                    if status == "outcome_unknown":
                        self._terminalize_unknown_attempts_locked(
                            action_group_id, finished_at=timestamp
                        )
                        has_unknown_effect = (
                            self._repair_ledger_is_uncertain_locked(action_group_id)
                            or has_unknown_effect
                        )
                    if status == "failed":
                        has_known_failure = (
                            self._repair_ledger_has_known_failure_locked(
                                action_group_id
                            )
                            or has_known_failure
                        )
                        has_committed_effect = (
                            self._repair_ledger_has_committed_effect_locked(
                                action_group_id
                            )
                            or has_committed_effect
                        )
                    event_count, ledger_sha256 = self._repair_ledger_snapshot_locked(
                        action_group_id,
                        completion_status=status,
                    )
                    self._connection.execute(
                        "UPDATE repair_execution_groups SET ledger_event_count=?,"
                        "ledger_sha256=?,sealed_at=? WHERE repair_run_id=? "
                        "AND action_group_id=?",
                        (
                            event_count,
                            ledger_sha256,
                            timestamp,
                            repair_run_id,
                            action_group_id,
                        ),
                    )
                if status == "outcome_unknown" and not has_unknown_effect:
                    raise AutoModeConflictError(
                        "outcome_unknown repair lacks uncertain side-effect evidence"
                    )
                if status == "failed" and not has_known_failure:
                    raise AutoModeConflictError(
                        "failed repair ledger lacks a known failure"
                    )
                if bound_groups:
                    placeholders = ",".join("?" for _ in bound_groups)
                    self._connection.execute(
                        "UPDATE permission_requests SET continuation_required=0,"
                        "continuation_expires_at=NULL WHERE action_group_id IN ("
                        + placeholders
                        + ")",
                        tuple(bound_groups),
                    )
                self._connection.execute(
                    "UPDATE repair_runs SET completion_idempotency_key=?,"
                    "completion_request_sha256=?,after_version_ids_json=?,"
                    "execution_group_ids_json=?,verification_review_run_id=?,status=?,"
                    "completed_at=? WHERE repair_run_id=?",
                    (
                        idempotency_key,
                        request_sha256,
                        _canonical(after_values),
                        _canonical(group_values),
                        verification_review_run_id,
                        status,
                        timestamp,
                        repair_run_id,
                    ),
                )
                finding_ids = list(_load(row["finding_ids_json"], []))
                if finding_ids:
                    placeholders = ",".join("?" for _ in finding_ids)
                    finding_status = (
                        "addressed_pending_review"
                        if status == "completed"
                        else "unaddressed"
                    )
                    self._connection.execute(
                        "UPDATE review_findings SET status=?,updated_at=? "
                        "WHERE run_id=? AND finding_id IN (" + placeholders + ")",
                        (finding_status, timestamp, row["run_id"], *finding_ids),
                    )
                if status == "completed":
                    # Successful repair output is not itself a reviewed
                    # candidate. Clear the old binding so no pre-repair pass
                    # can authorize post-repair output as Verified.
                    self._connection.execute(
                        "UPDATE auto_mode_runs SET status='running',"
                        "state_revision=state_revision+1,candidate_id=NULL,"
                        "candidate_snapshot_sha256=NULL,evidence_snapshot_sha256=NULL,"
                        "artifact_set_sha256=NULL,candidate_artifact_ids_json='[]',"
                        "candidate_version_ids_json='[]',updated_at=? WHERE run_id=?",
                        (timestamp, row["run_id"]),
                    )
                elif status == "failed" and not has_committed_effect:
                    # A proven failure with no uncertain effect leaves the
                    # immutable candidate eligible for a bounded fresh repair.
                    self._connection.execute(
                        "UPDATE auto_mode_runs SET status='candidate',"
                        "state_revision=state_revision+1,updated_at=? WHERE run_id=?",
                        (timestamp, row["run_id"]),
                    )
                run = self._run_locked(str(row["run_id"]))
                event, _ = self._append_event_locked(
                    run,
                    idempotency_key=idempotency_key,
                    event_type="repair_completed",
                    request_sha256=request_sha256,
                    payload={
                        "repair_run_id": repair_run_id,
                        "status": status,
                        "after_version_ids": after_values,
                        "execution_group_ids": group_values,
                        "verification_review_run_id": verification_review_run_id,
                    },
                    created_at=timestamp,
                )
                if status == "outcome_unknown" or (
                    status == "failed" and has_committed_effect
                ):
                    # Unknown or partially committed effects are terminal run
                    # truth, not retryable candidates. Later reconciliation is
                    # an explicit fresh continuation and never reuses this
                    # repair or its one-shot authority.
                    self._assert_no_active_phase_locked(run)
                    terminal_reason = (
                        "outcome_unknown"
                        if status == "outcome_unknown"
                        else "repair_partial_commit"
                    )
                    terminal_key = (
                        f"repair-terminal:{repair_run_id}:{request_sha256[:16]}"
                    )
                    terminal_request = {
                        "status": "paused",
                        "reason": terminal_reason,
                        "stop_reason": None,
                    }
                    terminal_digest = _digest(terminal_request)
                    self._connection.execute(
                        "UPDATE auto_mode_runs SET status='paused',"
                        "state_revision=state_revision+1,terminal_reason=?,"
                        "stop_reason=NULL,terminal_idempotency_key=?,"
                        "terminal_request_sha256=?,updated_at=?,finished_at=? "
                        "WHERE run_id=?",
                        (
                            terminal_reason,
                            terminal_key,
                            terminal_digest,
                            timestamp,
                            timestamp,
                            row["run_id"],
                        ),
                    )
                    run = self._run_locked(str(row["run_id"]))
                    self._append_event_locked(
                        run,
                        idempotency_key=terminal_key,
                        event_type="auto_run_terminal",
                        request_sha256=terminal_digest,
                        payload={
                            "status": "paused",
                            "terminal_reason": terminal_reason,
                            "stop_reason": None,
                        },
                        created_at=timestamp,
                    )
                row = self._connection.execute(
                    "SELECT * FROM repair_runs WHERE repair_run_id=?",
                    (repair_run_id,),
                ).fetchone()
                self._assert_repair_ledger_proof_locked(row, completion_visible=True)
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return self._repair_transition(row, event, created=True)

    def bind_repair_execution_group(
        self,
        repair_run_id: str,
        *,
        action_group_id: str,
        idempotency_key: str,
        bound_at: int | None = None,
    ) -> dict[str, Any]:
        """Bind an empty action group before any repair side effect can run."""

        repair_run_id = _text("repair_run_id", repair_run_id)
        action_group_id = _text("action_group_id", action_group_id)
        idempotency_key = _text("idempotency_key", idempotency_key, maximum=1024)
        event_payload = {
            "repair_run_id": repair_run_id,
            "phase": "execution_group_bound",
            "action_group_id": action_group_id,
            "status": "started",
        }
        timestamp = self._time(bound_at)
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                repair = self._connection.execute(
                    "SELECT * FROM repair_runs WHERE repair_run_id=?",
                    (repair_run_id,),
                ).fetchone()
                if repair is None:
                    raise KeyError(repair_run_id)
                run = self._run_locked(str(repair["run_id"]))
                self._assert_mutable_run(run)
                if repair["status"] != "started" or run["status"] != "repairing":
                    raise AutoModeConflictError(
                        "repair execution group requires an active repair"
                    )
                group = self._connection.execute(
                    "SELECT root_frame_id,branch_id,turn_id,kind FROM action_groups "
                    "WHERE group_id=?",
                    (action_group_id,),
                ).fetchone()
                if (
                    group is None
                    or group["root_frame_id"] != repair["root_frame_id"]
                    or group["branch_id"] != repair["branch_id"]
                    or group["turn_id"] != repair["turn_id"]
                    or not isinstance(group["kind"], str)
                    or not group["kind"]
                ):
                    raise AutoModeConflictError(
                        "repair execution group belongs to another action scope"
                    )
                group_scope = {
                    "root_frame_id": group["root_frame_id"],
                    "branch_id": group["branch_id"],
                    "turn_id": group["turn_id"],
                    "kind": group["kind"],
                }
                request_sha256 = _digest(
                    {
                        "action_group_id": action_group_id,
                        "action_group_scope": group_scope,
                    }
                )
                existing = self._connection.execute(
                    "SELECT * FROM repair_execution_groups "
                    "WHERE repair_run_id=? AND idempotency_key=?",
                    (repair_run_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    if existing["request_sha256"] != request_sha256:
                        raise AutoModeConflictError(
                            "repair execution binding idempotency digest mismatch"
                        )
                    event = self._event_for_idempotency_locked(
                        str(repair["run_id"]),
                        idempotency_key,
                        expected_type="repair_started",
                        expected_request_sha256=request_sha256,
                    )
                    if (
                        event["payload"] != event_payload
                        or event["created_at"] != existing["bound_at"]
                        or any(
                            event[name] != existing[name]
                            for name in (
                                "run_id",
                                "root_frame_id",
                                "branch_id",
                                "turn_id",
                                "execution_id",
                            )
                        )
                    ):
                        raise AutoModeConflictError(
                            "repair execution binding event is invalid"
                        )
                    self._assert_repair_ledger_proof_locked(
                        repair, completion_visible=False
                    )
                    self._connection.commit()
                    return {
                        **dict(existing),
                        "event_id": event["event_id"],
                        "event_cursor": event["event_cursor"],
                        "created": False,
                    }
                if (
                    self._connection.execute(
                        "SELECT 1 FROM action_events WHERE group_id=? LIMIT 1",
                        (action_group_id,),
                    ).fetchone()
                    is not None
                ):
                    raise AutoModeConflictError(
                        "repair execution group must be bound before action events"
                    )
                if (
                    self._connection.execute(
                        "SELECT 1 FROM execution_attempts WHERE group_id=? LIMIT 1",
                        (action_group_id,),
                    ).fetchone()
                    is not None
                ):
                    raise AutoModeConflictError(
                        "repair execution group must be bound before execution attempts"
                    )
                claimed = self._connection.execute(
                    "SELECT repair_run_id FROM repair_execution_groups "
                    "WHERE action_group_id=?",
                    (action_group_id,),
                ).fetchone()
                if claimed is not None:
                    raise AutoModeConflictError(
                        "action group is already bound to another repair"
                    )
                binding_ordinal = int(
                    self._connection.execute(
                        "SELECT COALESCE(MAX(binding_ordinal),-1)+1 "
                        "FROM repair_execution_groups WHERE repair_run_id=?",
                        (repair_run_id,),
                    ).fetchone()[0]
                )
                self._connection.execute(
                    "INSERT INTO repair_execution_groups("
                    "repair_run_id,action_group_id,binding_ordinal,action_group_kind,run_id,root_frame_id,branch_id,"
                    "turn_id,execution_id,idempotency_key,request_sha256,bound_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        repair_run_id,
                        action_group_id,
                        binding_ordinal,
                        group["kind"],
                        repair["run_id"],
                        repair["root_frame_id"],
                        repair["branch_id"],
                        repair["turn_id"],
                        repair["execution_id"],
                        idempotency_key,
                        request_sha256,
                        timestamp,
                    ),
                )
                encoded_groups = repair["execution_group_ids_json"]
                try:
                    groups = (
                        [] if encoded_groups is None else json.loads(encoded_groups)
                    )
                except (TypeError, ValueError) as error:
                    raise AutoModeConflictError(
                        "repair execution binding order is invalid"
                    ) from error
                if (
                    not isinstance(groups, list)
                    or any(not isinstance(value, str) or not value for value in groups)
                    or len(groups) != len(set(groups))
                    or action_group_id in groups
                    or binding_ordinal != len(groups)
                ):
                    raise AutoModeConflictError(
                        "repair execution binding order is invalid"
                    )
                groups.append(action_group_id)
                self._connection.execute(
                    "UPDATE repair_runs SET execution_group_ids_json=? "
                    "WHERE repair_run_id=?",
                    (_canonical(groups), repair_run_id),
                )
                run = self._run_locked(str(repair["run_id"]))
                event, _ = self._append_event_locked(
                    run,
                    idempotency_key=idempotency_key,
                    event_type="repair_started",
                    request_sha256=request_sha256,
                    payload=event_payload,
                    created_at=timestamp,
                )
                row = self._connection.execute(
                    "SELECT * FROM repair_execution_groups WHERE repair_run_id=? "
                    "AND action_group_id=?",
                    (repair_run_id, action_group_id),
                ).fetchone()
                repair = self._connection.execute(
                    "SELECT * FROM repair_runs WHERE repair_run_id=?",
                    (repair_run_id,),
                ).fetchone()
                self._assert_repair_ledger_proof_locked(
                    repair, completion_visible=False
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return {
            **dict(row),
            "event_id": event["event_id"],
            "event_cursor": event["event_cursor"],
            "created": True,
        }

    def assert_repair_action_group_appendable(
        self, action_group_id: str, operation: str
    ) -> None:
        """Fail closed unless a bound repair group is current and active.

        Action-ledger writers call this inside their write transaction.  An
        unbound group is outside Auto Mode's repair authority and is accepted;
        a bound group must still belong to the physical and logical branch
        head, not merely to a repair owner row that a revert has hidden.
        """

        action_group_id = _text("action_group_id", action_group_id)
        operation = _text("operation", operation, maximum=128)
        with self._lock:
            action_group = self._connection.execute(
                "SELECT root_frame_id,branch_id,turn_id,kind FROM action_groups "
                "WHERE group_id=?",
                (action_group_id,),
            ).fetchone()
            if action_group is None:
                raise KeyError(f"unknown action group {action_group_id!r}")
            terminal_evidence = {
                "event:result",
                "event:observation",
                "event:completed",
                "event:failed",
                "event:denied",
                "event:timed_out",
                "event:cancelled",
                "event:permission_resolved:denied",
                "event:permission_resolved:timed_out",
                "event:permission_resolved:cancelled",
            }
            if (
                self._connection.execute(
                    "SELECT 1 FROM settings WHERE key=?",
                    (revert_recovery_setting_key(action_group["root_frame_id"]),),
                ).fetchone()
                is not None
                and operation not in terminal_evidence
            ):
                raise AutoModeConflictError(
                    "Session workspace revert requires recovery before new actions"
                )
            binding = self._connection.execute(
                "SELECT * FROM repair_execution_groups WHERE action_group_id=?",
                (action_group_id,),
            ).fetchone()
            if binding is None:
                return
            repair = self._connection.execute(
                "SELECT * FROM repair_runs WHERE repair_run_id=?",
                (binding["repair_run_id"],),
            ).fetchone()
            if repair is None:
                raise AutoModeConflictError("repair execution owner is missing")
            if (
                action_group["root_frame_id"] != binding["root_frame_id"]
                or action_group["branch_id"] != binding["branch_id"]
                or action_group["turn_id"] != binding["turn_id"]
                or action_group["kind"] != binding["action_group_kind"]
            ):
                raise AutoModeConflictError(
                    "repair execution group scope proof is invalid"
                )
            # The database triggers remain the authoritative immutable-ledger
            # guard and intentionally raise sqlite3.IntegrityError for sealed
            # event/attempt mutations.  This callback adds the branch-head
            # admission check needed only while a repair is still unsealed.
            if binding["sealed_at"] is not None:
                if operation in {
                    "activate_restart_continuation",
                    "consume_restart_once_grant",
                }:
                    raise AutoModeConflictError(
                        "sealed repair execution cannot authorize fresh work"
                    )
                return
            run = self._run_locked(str(binding["run_id"]))
            try:
                self._assert_mutable_run(run)
            except AutoModeConflictError:
                # A result that arrives after a revert is still durable truth
                # about an action admitted before that revert.  Recording it
                # cannot authorize new work; proposals, allocations, worker
                # binding/starts, and an allow decision remain fail closed.
                if operation in terminal_evidence:
                    return
                raise
            if (
                repair["status"] != "started"
                or run["status"] != "repairing"
                or any(
                    binding[name] != repair[name]
                    for name in (
                        "run_id",
                        "root_frame_id",
                        "branch_id",
                        "turn_id",
                        "execution_id",
                    )
                )
            ):
                raise AutoModeConflictError(
                    "repair execution group requires an active repair"
                )

    def assert_session_action_group_appendable(
        self, root_frame_id: str, branch_id: str, operation: str
    ) -> None:
        """Deny new action declarations while a revert outcome is unresolved."""

        root_frame_id = _text("root_frame_id", root_frame_id)
        _text("branch_id", branch_id)
        operation = _text("operation", operation, maximum=128)
        with self._lock:
            barrier = self._connection.execute(
                "SELECT value FROM settings WHERE key=?",
                (revert_recovery_setting_key(root_frame_id),),
            ).fetchone()
            if barrier is not None:
                # A committed revert still needs one exact, durable terminal
                # Timeline fact before its barrier can be released.  This is
                # not a general bypass: only the Store-owned domain-event path
                # can name this operation, and it must bind the same operation
                # and branch carried by the durable marker.
                terminal_prefix = "revert_terminal:"
                if operation.startswith(terminal_prefix):
                    try:
                        marker = json.loads(str(barrier["value"]))
                    except (TypeError, ValueError):
                        marker = None
                    if (
                        isinstance(marker, Mapping)
                        and marker.get("operation_id")
                        == operation[len(terminal_prefix) :]
                        and marker.get("branch_id") == branch_id
                        and marker.get("state")
                        in {
                            "reverting",
                            "recovery_required",
                            "committed_reconciled",
                        }
                    ):
                        return
                raise AutoModeConflictError(
                    "Session workspace revert requires recovery before new actions"
                )

    def reconcile_orphaned_runs(
        self, *, owner_instance_id: str, now: int
    ) -> list[dict[str, Any]]:
        """Drive runs left behind by a dead daemon to a definite terminal.

        ``shadow_after_turn`` commits start_run, record_candidate and
        start_review before the reviewer is even called, and complete_review
        after it answers -- four separate transactions, so a ``kill -9``
        between any two of them leaves a row in ``running``, ``candidate``,
        ``reviewing`` or ``repairing`` -- none of which is in
        ``_TERMINAL_STATUSES``.  ``start_run`` then refuses every later turn on
        that branch with "already has a recovery-required active run", the
        shadow caller swallows that conflict, and Auto Mode is silently dead
        for the session: no route, no sweeper and no boot step ever clears the
        row.

        Recovery closes the record; it never repeats the work.  Each stranded
        phase is closed through its own completion method, so the owner row,
        its event and the digest chain end up exactly as consistent as a live
        completion leaves them, and the run then terminates as
        ``review_unavailable`` with reason ``daemon_restart``.  No outcome is
        invented: an interrupted review is ``unavailable`` rather than a
        verdict, and an interrupted repair is ``outcome_unknown``, which
        ``complete_repair`` seals as its own ``paused`` terminal because a
        half-applied repair is not a retryable candidate.

        Ownership is the whole discriminator.  A run carrying THIS instance's
        id is in flight and must not be touched; only a foreign owner proves
        that the daemon which held it is gone.
        """

        owner_instance_id = _text("owner_instance_id", owner_instance_id)
        now = _integer("now", now)
        outcomes: list[dict[str, Any]] = []
        # The lock is re-entrant and the scan opens no transaction of its own,
        # so every completion below still gets its own BEGIN IMMEDIATE while a
        # concurrent writer cannot interleave between the scan and the close.
        with self._lock:
            run_ids = [
                str(row["run_id"])
                for row in self._connection.execute(
                    "SELECT run_id FROM auto_mode_runs WHERE trust_state='local' "
                    "AND finished_at IS NULL AND abandoned_at IS NULL "
                    "AND owner_instance_id<>? ORDER BY created_at,run_id",
                    (owner_instance_id,),
                ).fetchall()
            ]
            for run_id in run_ids:
                # Isolation belongs HERE, around the call, not only inside it.
                # The per-run method can only guard what happens after control
                # enters it; a failure raised on the way in -- or one its own
                # handlers re-raise -- would still escape the loop and deny
                # every remaining session the recovery this sweep exists to
                # give it. Boot-time recovery is all-or-nothing exactly once:
                # per row.
                try:
                    outcomes.append(
                        self._reconcile_orphaned_run_locked(run_id, now=now)
                    )
                except Exception as unexpected:  # noqa: BLE001
                    try:
                        self._connection.rollback()
                    except Exception:  # noqa: BLE001
                        pass
                    outcomes.append(
                        {"run_id": run_id, "unreconciled": str(unexpected)[:300]}
                    )
        return outcomes

    def _reconcile_orphaned_run_locked(
        self, run_id: str, *, now: int
    ) -> dict[str, Any]:
        """Close one orphaned run's open phases, then seal its terminal."""

        try:
            self._close_orphaned_phases_locked(run_id, now=now)
            run = self._run_locked(run_id)
            if run["finished_at"] is not None or run["status"] in _TERMINAL_STATUSES:
                # ``complete_repair`` seals its own terminal for an unknown
                # outcome.  That is already a definite failure terminal, and
                # relabelling it here would claim evidence nobody gathered.
                return {
                    "run_id": run_id,
                    "status": str(run["status"]),
                    "terminal_reason": run["terminal_reason"],
                }
            self.terminate_run(
                run_id,
                idempotency_key=f"daemon-restart:terminal:{run_id}",
                status="review_unavailable",
                reason="daemon_restart",
                finished_at=now,
            )
            return {
                "run_id": run_id,
                "status": "review_unavailable",
                "terminal_reason": "daemon_restart",
            }
        except (AutoModeConflictError, ValueError, PermissionError, KeyError) as exc:
            # A phase that no completion method can represent (a repair whose
            # ledger was never bound, say) must not leave the branch wedged.
            # Abandoning claims nothing about what ran; it only says this tail
            # is no longer current, which is what releases `start_run`.
            try:
                return self._release_unreconcilable_run_locked(
                    run_id, now=now, error=exc
                )
            except Exception as release_error:  # noqa: BLE001
                # One unrecoverable row must not cost every other session its
                # recovery, so the sweep reports this one and keeps going.
                self._connection.rollback()
                return {
                    "run_id": run_id,
                    "unreconciled": str(release_error)[:300],
                    "error": str(exc)[:300],
                }
        except Exception as unexpected:  # noqa: BLE001
            # The tuple above names the failures a phase can represent. Anything
            # else -- a locked database, an integrity violation, a shape nobody
            # anticipated -- was propagating out of the per-run call and out of
            # the sweep loop, so ONE bad row denied every other session the
            # recovery this method exists to give it. That is the same
            # all-or-nothing the docstring above already rejects.
            self._connection.rollback()
            return {"run_id": run_id, "unreconciled": str(unexpected)[:300]}

    def _close_orphaned_phases_locked(self, run_id: str, *, now: int) -> None:
        """Terminalize every durable phase the dead daemon left ``started``."""

        for review in self._connection.execute(
            "SELECT review_run_id FROM review_runs WHERE run_id=? "
            "AND status='started' ORDER BY started_at,review_run_id",
            (run_id,),
        ).fetchall():
            review_run_id = str(review["review_run_id"])
            self.complete_review(
                review_run_id,
                idempotency_key=f"daemon-restart:review:{review_run_id}",
                status="unavailable",
                verdict="review_unavailable",
                assessment={
                    "public_summary": (
                        "The reviewing daemon exited before this audit "
                        "returned a verdict."
                    ),
                    "reconciled_by": "daemon_restart",
                },
                findings=[],
                usage={},
                completed_at=now,
            )
        for assessment in self._connection.execute(
            "SELECT assessment_id FROM permission_review_assessments "
            "WHERE run_id=? AND status='started' ORDER BY started_at,assessment_id",
            (run_id,),
        ).fetchall():
            assessment_id = str(assessment["assessment_id"])
            self.complete_permission_review(
                assessment_id,
                idempotency_key=f"daemon-restart:permission:{assessment_id}",
                status="unavailable",
                outcome="unavailable",
                risk="unknown",
                assessment={
                    "public_summary": (
                        "The reviewing daemon exited before this approval "
                        "assessment returned."
                    ),
                    "reconciled_by": "daemon_restart",
                },
                completed_at=now,
            )
        for repair in self._connection.execute(
            "SELECT repair_run_id FROM repair_runs WHERE run_id=? "
            "AND status='started' ORDER BY started_at,repair_run_id",
            (run_id,),
        ).fetchall():
            repair_run_id = str(repair["repair_run_id"])
            bound = [
                str(row["action_group_id"])
                for row in self._connection.execute(
                    "SELECT action_group_id FROM repair_execution_groups "
                    "WHERE repair_run_id=? ORDER BY binding_ordinal",
                    (repair_run_id,),
                ).fetchall()
            ]
            self.complete_repair(
                repair_run_id,
                idempotency_key=f"daemon-restart:repair:{repair_run_id}",
                status="outcome_unknown",
                after_version_ids=[],
                execution_group_ids=bound,
                completed_at=now,
            )

    def _release_unreconcilable_run_locked(
        self, run_id: str, *, now: int, error: Exception
    ) -> dict[str, Any]:
        """Release a branch whose run cannot reach a terminal truthfully."""

        run = self._run_locked(run_id)
        if (
            self._connection.execute(
                "SELECT 1 FROM settings WHERE key=?",
                (revert_recovery_setting_key(str(run["root_frame_id"])),),
            ).fetchone()
            is not None
        ):
            # An unresolved workspace revert is a deliberate hold with its own
            # recovery path.  Clearing it from boot would defeat that guard.
            return {
                "run_id": run_id,
                "status": str(run["status"]),
                "deferred": "revert_recovery_required",
                "error": str(error)[:300],
            }
        self._connection.execute(
            "UPDATE auto_mode_runs SET abandoned_at=?,"
            "state_revision=state_revision+1,updated_at=? "
            "WHERE run_id=? AND abandoned_at IS NULL",
            (now, now, run_id),
        )
        self._connection.commit()
        return {
            "run_id": run_id,
            "status": str(run["status"]),
            "abandoned_at": now,
            "error": str(error)[:300],
        }

    def abandon_active_run_for_revert(
        self,
        *,
        root_frame_id: str,
        branch_id: str,
        target_checkpoint_id: str,
        revert_checkpoint_id: str,
        reverted_at: int,
    ) -> None:
        """Invalidate active work in the transaction that publishes a revert.

        Auto events alone cannot observe action-ledger writes made inside an
        already-bound repair group. Marking the active run abandoned at the
        checkpoint publication boundary makes append-vs-revert linearizable:
        either the action commits first and is abandoned, or the revert commits
        first and admission rejects the action. The historical rows remain
        intact for audit and late terminal evidence may still be recorded.
        """

        del target_checkpoint_id  # The immutable checkpoint row holds this link.
        root_frame_id = _text("root_frame_id", root_frame_id)
        branch_id = _text("branch_id", branch_id)
        revert_checkpoint_id = _text("revert_checkpoint_id", revert_checkpoint_id)
        reverted_at = _integer("reverted_at", reverted_at)
        with self._lock:
            rows = self._connection.execute(
                "SELECT run_id FROM auto_mode_runs WHERE root_frame_id=? "
                "AND branch_id=? AND trust_state='local' AND finished_at IS NULL "
                "AND abandoned_at IS NULL",
                (root_frame_id, branch_id),
            ).fetchall()
            for row in rows:
                run_id = str(row["run_id"])
                self._connection.execute(
                    "UPDATE auto_mode_runs SET abandoned_at=?,"
                    "abandoned_by_checkpoint_id=?,state_revision=state_revision+1,"
                    "updated_at=? WHERE run_id=? AND abandoned_at IS NULL",
                    (
                        reverted_at,
                        revert_checkpoint_id,
                        reverted_at,
                        run_id,
                    ),
                )
                # Restart continuations authorize fresh execution. A revert
                # invalidates every such capability associated with this run,
                # even if the permission row was allowed before the rollback.
                self._connection.execute(
                    "UPDATE permission_requests SET continuation_required=0,"
                    "continuation_expires_at=NULL WHERE action_group_id IN ("
                    "SELECT action_group_id FROM repair_execution_groups "
                    "WHERE run_id=?)",
                    (run_id,),
                )

    def _repair_ledger_snapshot_locked(
        self,
        action_group_id: str,
        *,
        completion_status: str | None,
    ) -> tuple[int, str]:
        """Return the exact action-ledger proof to seal for one repair group."""

        if completion_status is not None and completion_status not in {
            "completed",
            "failed",
            "outcome_unknown",
        }:
            raise ValueError("invalid repair ledger completion status")

        group = self._connection.execute(
            "SELECT * FROM action_groups WHERE group_id=?", (action_group_id,)
        ).fetchone()
        if group is None:
            raise AutoModeConflictError("repair action group no longer exists")
        events = self._connection.execute(
            "SELECT * FROM action_events WHERE group_id=? ORDER BY sequence,event_id",
            (action_group_id,),
        ).fetchall()
        attempts = self._connection.execute(
            "SELECT * FROM execution_attempts WHERE group_id=? "
            "ORDER BY attempt_ordinal,attempt_id",
            (action_group_id,),
        ).fetchall()
        kind = str(group["kind"] or "")
        failure_types = {"failed", "denied", "cancelled"}
        uncertain_types = {"timed_out"}

        def result_state(event: sqlite3.Row) -> str:
            try:
                result = json.loads(event["result"])
            except (TypeError, ValueError):
                return "unknown"
            if not isinstance(result, Mapping):
                return "unknown"
            state = str(result.get("status") or "").lower()
            if result.get("is_error") is True or state in {
                "error",
                "failed",
                "denied",
                "cancelled",
                "timed_out",
            }:
                return "failure"
            if result.get("is_error") is False or state in {
                "ok",
                "completed",
                "succeeded",
                "success",
                "allowed",
            }:
                return "success"
            return "unknown"

        proposed: list[str] = []
        terminal: list[str] = []
        failed_terminal: list[str] = []
        unknown_terminal: list[str] = []
        side_effect_by_identity: dict[str, bool] = {}
        explicit_no_commit: set[str] = set()
        explicit_committed: set[str] = set()
        for event in events:
            identity = event["tool_call_id"] or event["action_id"]
            event_type = str(event["type"])
            if event_type == "proposed":
                if not identity:
                    raise AutoModeConflictError(
                        "repair action proposal lacks a stable identity"
                    )
                identity_value = str(identity)
                proposed.append(identity_value)
                side_effect_by_identity[identity_value] = str(
                    event["side_effect_class"] or ""
                ).lower() not in {"none", "read", "read_only"}
            elif event_type == "result" and identity:
                identity_value = str(identity)
                terminal.append(identity_value)
                state = result_state(event)
                if state == "failure":
                    failed_terminal.append(identity_value)
                elif state == "unknown":
                    unknown_terminal.append(identity_value)
                try:
                    result = json.loads(event["result"])
                except (TypeError, ValueError):
                    result = None
                if (
                    isinstance(result, Mapping)
                    and result.get("output_committed") is False
                ):
                    explicit_no_commit.add(identity_value)
                if (
                    isinstance(result, Mapping)
                    and result.get("output_committed") is True
                ):
                    explicit_committed.add(identity_value)
            elif event_type in failure_types and identity:
                identity_value = str(identity)
                terminal.append(identity_value)
                failed_terminal.append(identity_value)
                if event_type in {"denied", "cancelled"}:
                    explicit_no_commit.add(identity_value)
            elif event_type in uncertain_types and identity:
                identity_value = str(identity)
                terminal.append(identity_value)
                unknown_terminal.append(identity_value)
            elif event_type == "permission_resolved" and identity:
                try:
                    permission_result = json.loads(event["result"])
                except (TypeError, ValueError):
                    permission_result = None
                permission_state = (
                    str(permission_result.get("state") or "").lower()
                    if isinstance(permission_result, Mapping)
                    else ""
                )
                if permission_state in {"denied", "timed_out", "cancelled"}:
                    identity_value = str(identity)
                    terminal.append(identity_value)
                    failed_terminal.append(identity_value)
                    explicit_no_commit.add(identity_value)

        if completion_status == "completed":
            if any(str(event["type"]) in failure_types for event in events):
                raise AutoModeConflictError(
                    "completed repair action ledger records a failed action"
                )
            if attempts and any(
                attempt["terminal_state"] != "completed"
                or attempt["finished_at"] is None
                for attempt in attempts
            ):
                raise AutoModeConflictError(
                    "completed repair execution attempt is not successful"
                )
            if kind in {"native_tools", "finalize"}:
                if failed_terminal or unknown_terminal:
                    raise AutoModeConflictError(
                        "completed repair action ledger records a non-success result"
                    )
                if not proposed or any(
                    terminal.count(identity) < proposed.count(identity)
                    for identity in set(proposed)
                ):
                    raise AutoModeConflictError(
                        "completed repair action ledger is not terminal"
                    )
            elif kind in {"code", "cell"}:
                if not any(
                    str(event["type"]) in {"observation", "completed"}
                    for event in events
                ):
                    raise AutoModeConflictError(
                        "completed repair action ledger is not terminal"
                    )
                if not attempts:
                    raise AutoModeConflictError(
                        "completed repair execution attempt is not successful"
                    )
            elif not any(str(event["type"]) == "completed" for event in events):
                raise AutoModeConflictError(
                    "completed repair action ledger is not terminal"
                )
        elif completion_status == "failed":
            if any(
                attempt["finished_at"] is None or attempt["terminal_state"] is None
                for attempt in attempts
            ):
                raise AutoModeConflictError(
                    "failed repair execution attempt is not terminal"
                )
            successful_attempt = any(
                str(attempt["terminal_state"]).lower()
                in {"completed", "succeeded", "ok", "success"}
                for attempt in attempts
            )
            if successful_attempt and kind in {"code", "cell"}:
                raise AutoModeConflictError(
                    "failed repair ledger records a possibly mutating successful attempt"
                )
            if proposed and any(
                terminal.count(identity) < proposed.count(identity)
                for identity in set(proposed)
            ):
                raise AutoModeConflictError(
                    "failed repair action ledger has an uncertain effect"
                )
            if unknown_terminal:
                raise AutoModeConflictError(
                    "failed repair action ledger has an uncertain effect"
                )
            if any(
                side_effect_by_identity.get(identity, False)
                and identity not in explicit_no_commit
                and identity not in explicit_committed
                for identity in set(proposed)
            ):
                raise AutoModeConflictError(
                    "failed repair side effect lacks explicit commit-state evidence"
                )
        elif completion_status == "outcome_unknown":
            if any(
                attempt["finished_at"] is None or attempt["terminal_state"] is None
                for attempt in attempts
            ):
                raise AutoModeConflictError(
                    "unknown repair execution attempt is not durably terminal"
                )
        proof = {
            "group": dict(group),
            "events": [dict(event) for event in events],
            "attempts": [dict(attempt) for attempt in attempts],
        }
        return len(events), _digest(proof)

    def _repair_ledger_is_uncertain_locked(self, action_group_id: str) -> bool:
        events = self._connection.execute(
            "SELECT * FROM action_events WHERE group_id=? ORDER BY sequence,event_id",
            (action_group_id,),
        ).fetchall()
        attempts = self._connection.execute(
            "SELECT started_at,terminal_state FROM execution_attempts WHERE group_id=?",
            (action_group_id,),
        ).fetchall()
        has_unknown_attempt = any(
            str(attempt["terminal_state"] or "").lower() == "outcome_unknown"
            for attempt in attempts
        )
        proposed: list[str] = []
        terminal: list[str] = []
        possible_effect: set[str] = set()
        unknown_result: set[str] = set()
        for event in events:
            identity = event["tool_call_id"] or event["action_id"]
            if not identity:
                continue
            identity_value = str(identity)
            if event["type"] == "proposed":
                proposed.append(identity_value)
                if str(event["side_effect_class"] or "").lower() not in {
                    "none",
                    "read",
                    "read_only",
                }:
                    possible_effect.add(identity_value)
            elif event["type"] == "result":
                terminal.append(identity_value)
                try:
                    result = json.loads(event["result"])
                except (TypeError, ValueError):
                    result = None
                if not isinstance(result, Mapping) or str(
                    result.get("status") or ""
                ).lower() in {"timed_out", "unknown", "outcome_unknown"}:
                    unknown_result.add(identity_value)
            elif event["type"] == "timed_out":
                terminal.append(identity_value)
                unknown_result.add(identity_value)
            elif event["type"] == "permission_resolved":
                try:
                    permission_result = json.loads(event["result"])
                except (TypeError, ValueError):
                    permission_result = None
                permission_state = (
                    str(permission_result.get("state") or "").lower()
                    if isinstance(permission_result, Mapping)
                    else ""
                )
                if permission_state in {"denied", "timed_out", "cancelled"}:
                    terminal.append(identity_value)
        missing_terminal = any(
            terminal.count(identity) < proposed.count(identity)
            for identity in set(proposed)
        )
        event_uncertainty = bool(
            possible_effect
            and (
                bool(possible_effect & unknown_result)
                or (missing_terminal and not attempts)
            )
        )
        return has_unknown_attempt or event_uncertainty

    def _repair_ledger_has_known_failure_locked(self, action_group_id: str) -> bool:
        """Return whether one group contains durable, non-ambiguous failure."""

        events = self._connection.execute(
            "SELECT type,result FROM action_events WHERE group_id=?",
            (action_group_id,),
        ).fetchall()
        for event in events:
            event_type = str(event["type"] or "").lower()
            if event_type in {"failed", "denied", "cancelled"}:
                return True
            try:
                result = json.loads(event["result"])
            except (TypeError, ValueError):
                result = None
            if not isinstance(result, Mapping):
                continue
            state = str(result.get("status") or result.get("state") or "").lower()
            if result.get("is_error") is True or state in {
                "error",
                "failed",
                "denied",
                "cancelled",
                "timed_out",
            }:
                return True
        attempts = self._connection.execute(
            "SELECT terminal_state FROM execution_attempts WHERE group_id=?",
            (action_group_id,),
        ).fetchall()
        return any(
            str(attempt["terminal_state"] or "").lower()
            in {"failed", "denied", "cancelled", "abandoned"}
            for attempt in attempts
        )

    def _repair_ledger_has_committed_effect_locked(self, action_group_id: str) -> bool:
        """Return whether a side-effecting action explicitly committed output."""

        events = self._connection.execute(
            "SELECT type,result,side_effect_class FROM action_events WHERE group_id=?",
            (action_group_id,),
        ).fetchall()
        for event in events:
            if str(event["type"] or "").lower() != "result":
                continue
            effect = str(event["side_effect_class"] or "").lower()
            if effect in {"none", "read", "read_only"}:
                continue
            try:
                result = json.loads(event["result"])
            except (TypeError, ValueError):
                result = None
            if isinstance(result, Mapping) and result.get("output_committed") is True:
                return True
        return False

    def _terminalize_unknown_attempts_locked(
        self, action_group_id: str, *, finished_at: int
    ) -> None:
        """Freeze in-flight attempts as unknown before their ledger is sealed."""

        rows = self._connection.execute(
            "SELECT * FROM execution_attempts WHERE group_id=?",
            (action_group_id,),
        ).fetchall()
        for attempt in rows:
            has_terminal = attempt["terminal_state"] is not None
            has_finished_at = attempt["finished_at"] is not None
            if has_terminal != has_finished_at:
                raise AutoModeConflictError(
                    "repair execution attempt has inconsistent terminal state"
                )
            if has_terminal:
                continue
            was_started = attempt["started_at"] is not None
            terminal_state = "outcome_unknown" if was_started else "cancelled"
            error_json = _canonical(
                {
                    "type": terminal_state,
                    "message": (
                        "repair outcome requires bounded reconciliation"
                        if was_started
                        else "repair attempt was never started"
                    ),
                }
            )
            terminal_at = max(
                [
                    finished_at,
                    *[
                        int(attempt[name])
                        for name in (
                            "allocated_at",
                            "started_at",
                            "response_at",
                            "capture_at",
                        )
                        if attempt[name] is not None
                    ],
                ]
            )
            self._connection.execute(
                "UPDATE execution_attempts SET finished_at=?,"
                "terminal_state=?,error=? "
                "WHERE attempt_id=? AND finished_at IS NULL "
                "AND terminal_state IS NULL",
                (
                    terminal_at,
                    terminal_state,
                    error_json,
                    attempt["attempt_id"],
                ),
            )

    def _assert_repair_ledger_proof_locked(
        self,
        row: sqlite3.Row,
        *,
        completion_visible: bool | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Validate repair owner/event proof and every sealed action ledger."""

        def fail() -> None:
            raise AutoModeConflictError("repair ledger proof is invalid")

        try:
            run = self._run_locked(str(row["run_id"]))
            if run["trust_state"] != "local":
                fail()
            identity_fields = (
                "run_id",
                "root_frame_id",
                "branch_id",
                "turn_id",
                "execution_id",
            )
            if any(row[name] != run[name] for name in identity_fields):
                fail()
            finding_ids = json.loads(row["finding_ids_json"])
            before_ids = json.loads(row["before_version_ids_json"])
            if (
                not isinstance(finding_ids, list)
                or not finding_ids
                or not isinstance(before_ids, list)
                or _canonical(finding_ids) != row["finding_ids_json"]
                or _canonical(before_ids) != row["before_version_ids_json"]
                or any(
                    not isinstance(value, str) or not value
                    for value in (*finding_ids, *before_ids)
                )
            ):
                fail()
            start_request = {
                "finding_ids": finding_ids,
                "before_version_ids": before_ids,
                "checkpoint_id": row["checkpoint_id"],
            }
            start_digest = _digest(start_request)
            if row["start_request_sha256"] != start_digest:
                fail()
            event_rows = self._connection.execute(
                "SELECT * FROM auto_mode_events WHERE run_id=? "
                "AND type IN ('repair_started','repair_completed') "
                "ORDER BY sequence",
                (row["run_id"],),
            ).fetchall()
            starts: list[dict[str, Any]] = []
            binding_events: list[dict[str, Any]] = []
            completions: list[dict[str, Any]] = []
            for event_row in event_rows:
                event = self._decode_event(event_row)
                if event["payload"].get("repair_run_id") != row["repair_run_id"]:
                    continue
                if event["type"] == "repair_started":
                    if event["payload"].get("phase") == "execution_group_bound":
                        binding_events.append(event)
                    else:
                        starts.append(event)
                else:
                    completions.append(event)
            if len(starts) != 1:
                fail()
            start = starts[0]
            if (
                start["payload"]
                != {
                    "repair_run_id": row["repair_run_id"],
                    **start_request,
                    "status": "started",
                }
                or start["idempotency_key"] != row["start_idempotency_key"]
                or start["request_sha256"] != start_digest
                or start["created_at"] != row["started_at"]
                or any(start[name] != row[name] for name in identity_fields)
            ):
                fail()
            bindings = self._connection.execute(
                "SELECT * FROM repair_execution_groups WHERE repair_run_id=? "
                "ORDER BY binding_ordinal",
                (row["repair_run_id"],),
            ).fetchall()
            encoded_group_ids = row["execution_group_ids_json"]
            group_ids = (
                [] if encoded_group_ids is None else json.loads(encoded_group_ids)
            )
            if (
                not isinstance(group_ids, list)
                or any(not isinstance(value, str) or not value for value in group_ids)
                or len(group_ids) != len(set(group_ids))
                or (
                    encoded_group_ids is not None
                    and _canonical(group_ids) != encoded_group_ids
                )
                or [str(binding["action_group_id"]) for binding in bindings]
                != group_ids
                or [int(binding["binding_ordinal"]) for binding in bindings]
                != list(range(len(bindings)))
                or len(binding_events) != len(bindings)
                or [event["payload"].get("action_group_id") for event in binding_events]
                != group_ids
            ):
                fail()
            binding_by_group: dict[str, dict[str, Any]] = {}
            for binding_event in binding_events:
                action_group_id = binding_event["payload"].get("action_group_id")
                if (
                    not isinstance(action_group_id, str)
                    or action_group_id in binding_by_group
                ):
                    fail()
                binding_by_group[action_group_id] = binding_event
            has_known_failure = False
            has_unknown_effect = False
            has_committed_effect = False
            for binding in bindings:
                action_group_id = str(binding["action_group_id"])
                binding_event = binding_by_group.get(action_group_id)
                action_group = self._connection.execute(
                    "SELECT root_frame_id,branch_id,turn_id,kind FROM action_groups "
                    "WHERE group_id=?",
                    (action_group_id,),
                ).fetchone()
                if (
                    action_group is None
                    or action_group["root_frame_id"] != binding["root_frame_id"]
                    or action_group["branch_id"] != binding["branch_id"]
                    or action_group["turn_id"] != binding["turn_id"]
                    or action_group["kind"] != binding["action_group_kind"]
                    or not isinstance(binding["action_group_kind"], str)
                    or not binding["action_group_kind"]
                ):
                    fail()
                binding_request = {
                    "action_group_id": action_group_id,
                    "action_group_scope": {
                        "root_frame_id": binding["root_frame_id"],
                        "branch_id": binding["branch_id"],
                        "turn_id": binding["turn_id"],
                        "kind": binding["action_group_kind"],
                    },
                }
                binding_digest = _digest(binding_request)
                if (
                    binding_event is None
                    or binding_event["payload"]
                    != {
                        "repair_run_id": row["repair_run_id"],
                        "phase": "execution_group_bound",
                        "action_group_id": action_group_id,
                        "status": "started",
                    }
                    or binding["request_sha256"] != binding_digest
                    or binding_event["request_sha256"] != binding_digest
                    or binding_event["idempotency_key"] != binding["idempotency_key"]
                    or binding_event["created_at"] != binding["bound_at"]
                    or any(
                        binding[name] != row[name] or binding_event[name] != row[name]
                        for name in identity_fields
                    )
                    or start["sequence"] >= binding_event["sequence"]
                ):
                    fail()
            if completion_visible is False:
                return start, None

            is_terminal = row["completion_idempotency_key"] is not None
            if completion_visible is True and not is_terminal:
                fail()
            if not is_terminal:
                if (
                    row["status"] != "started"
                    or completions
                    or any(
                        row[name] is not None
                        for name in (
                            "completion_request_sha256",
                            "after_version_ids_json",
                            "verification_review_run_id",
                            "completed_at",
                        )
                    )
                ):
                    fail()
                return start, None
            if len(completions) != 1 or row["status"] not in {
                "completed",
                "failed",
                "outcome_unknown",
            }:
                fail()
            after_ids = json.loads(row["after_version_ids_json"])
            if (
                not isinstance(after_ids, list)
                or _canonical(after_ids) != row["after_version_ids_json"]
                or any(
                    not isinstance(value, str) or not value
                    for value in (*after_ids, *group_ids)
                )
                or row["verification_review_run_id"] is not None
                or (row["status"] == "completed" and not group_ids)
            ):
                fail()
            completion_request = {
                "status": row["status"],
                "after_version_ids": after_ids,
                "execution_group_ids": group_ids,
                "verification_review_run_id": None,
            }
            completion_digest = _digest(completion_request)
            completion = completions[0]
            if (
                row["completion_request_sha256"] != completion_digest
                or completion["payload"]
                != {
                    "repair_run_id": row["repair_run_id"],
                    **completion_request,
                }
                or completion["idempotency_key"] != row["completion_idempotency_key"]
                or completion["request_sha256"] != completion_digest
                or completion["created_at"] != row["completed_at"]
                or any(completion[name] != row[name] for name in identity_fields)
                or start["sequence"] >= completion["sequence"]
                or any(
                    binding_event["sequence"] >= completion["sequence"]
                    for binding_event in binding_events
                )
            ):
                fail()
            for binding in bindings:
                if (
                    any(binding[name] != row[name] for name in identity_fields)
                    or binding["sealed_at"] is None
                    or binding["ledger_event_count"] is None
                    or not isinstance(binding["ledger_sha256"], str)
                    or _HEX64.fullmatch(binding["ledger_sha256"]) is None
                ):
                    fail()
                event_count, ledger_sha256 = self._repair_ledger_snapshot_locked(
                    str(binding["action_group_id"]),
                    completion_status=str(row["status"]),
                )
                pending = self._connection.execute(
                    "SELECT 1 FROM permission_requests WHERE action_group_id=? "
                    "AND state='pending' LIMIT 1",
                    (binding["action_group_id"],),
                ).fetchone()
                if pending is not None:
                    fail()
                if row["status"] == "failed":
                    has_known_failure = (
                        self._repair_ledger_has_known_failure_locked(
                            str(binding["action_group_id"])
                        )
                        or has_known_failure
                    )
                    has_committed_effect = (
                        self._repair_ledger_has_committed_effect_locked(
                            str(binding["action_group_id"])
                        )
                        or has_committed_effect
                    )
                elif row["status"] == "outcome_unknown":
                    has_unknown_effect = (
                        self._repair_ledger_is_uncertain_locked(
                            str(binding["action_group_id"])
                        )
                        or has_unknown_effect
                    )
                reusable = self._connection.execute(
                    "SELECT 1 FROM permission_requests WHERE action_group_id=? "
                    "AND continuation_required=1 LIMIT 1",
                    (binding["action_group_id"],),
                ).fetchone()
                if reusable is not None:
                    fail()
                if (
                    int(binding["ledger_event_count"]) != event_count
                    or binding["ledger_sha256"] != ledger_sha256
                ):
                    fail()
            if row["status"] == "failed" and not has_known_failure:
                fail()
            if row["status"] == "outcome_unknown" and not has_unknown_effect:
                fail()
            if row["status"] == "failed" and has_committed_effect:
                owner_run = self._run_locked(str(row["run_id"]))
                if (
                    owner_run["status"] != "paused"
                    or owner_run["terminal_reason"] != "repair_partial_commit"
                    or owner_run["finished_at"] is None
                ):
                    fail()
            return start, completion
        except AutoModeConflictError as error:
            if str(error) == "repair ledger proof is invalid":
                raise
            raise AutoModeConflictError("repair ledger proof is invalid") from error
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise AutoModeConflictError("repair ledger proof is invalid") from error

    # ------------------------------------------------------------ permission audit
    def start_permission_review(
        self,
        run_id: str,
        *,
        assessment_id: str,
        audit_id: str,
        decision_id: str,
        action_digest: str,
        policy_version: str,
        idempotency_key: str,
        started_at: int | None = None,
    ) -> dict[str, Any]:
        run_id = _text("run_id", run_id)
        assessment_id = _text("assessment_id", assessment_id)
        audit_id = _text("audit_id", audit_id)
        decision_id = _text("decision_id", decision_id)
        action_digest = str(_sha("action_digest", action_digest))
        policy_version = _text("policy_version", policy_version)
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", policy_version) is None:
            raise ValueError("invalid permission policy version")
        idempotency_key = _text("idempotency_key", idempotency_key, maximum=1024)
        request = {
            "decision_id": decision_id,
            "action_digest": action_digest,
            "policy_version": policy_version,
        }
        request_sha256 = _digest(request)
        audit_request_digest = _digest(
            {
                "subject_kind": "permission_review",
                "subject_entity_kind": "approval_action",
                **request,
            }
        )
        timestamp = self._time(started_at)
        deadline_now = self._time(None)
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                run = self._run_locked(run_id)
                existing = self._connection.execute(
                    "SELECT * FROM permission_review_assessments "
                    "WHERE run_id=? AND start_idempotency_key=?",
                    (run_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    if existing["start_request_sha256"] != request_sha256:
                        raise AutoModeConflictError(
                            "permission review idempotency digest mismatch"
                        )
                    event = self._event_for_idempotency_locked(
                        run_id,
                        idempotency_key,
                        expected_type="auto_audit_started",
                        expected_request_sha256=request_sha256,
                    )
                    self._assert_permission_assessment_proof_locked(existing)
                    self._assert_run_replay_integrity_locked(run)
                    self._connection.commit()
                    return self._permission_transition(existing, event, created=False)
                self._assert_mutable_run(run)
                claimed = self._connection.execute(
                    "SELECT assessment_id FROM permission_review_assessments "
                    "WHERE decision_id=?",
                    (decision_id,),
                ).fetchone()
                if claimed is not None:
                    raise AutoModeConflictError(
                        "permission decision already has an Auto Mode assessment"
                    )
                permission = self._connection.execute(
                    "SELECT * FROM permission_requests WHERE decision_id=?",
                    (decision_id,),
                ).fetchone()
                permission_group = (
                    self._get_action_group(str(permission["action_group_id"]))
                    if permission is not None
                    and permission["action_group_id"]
                    and self._get_action_group is not None
                    else None
                )
                if (
                    permission is None
                    or permission["state"] != "pending"
                    or (
                        permission["expires_at"] is not None
                        and int(permission["expires_at"]) <= deadline_now
                    )
                    or str(permission["root_frame_id"] or "")
                    != str(run["root_frame_id"])
                    or permission_group is None
                    or permission_group.get("root_frame_id") != run["root_frame_id"]
                    or permission_group.get("branch_id") != run["branch_id"]
                    or permission_group.get("turn_id") != run["turn_id"]
                ):
                    raise AutoModeConflictError(
                        "permission audit requires the exact pending action scope"
                    )
                try:
                    expected_action_digest = canonical_permission_action_digest(
                        permission
                    )
                except ValueError as error:
                    raise AutoModeConflictError(
                        "permission audit action envelope is invalid"
                    ) from error
                if action_digest != expected_action_digest:
                    raise AutoModeConflictError(
                        "permission audit action digest does not bind the exact action"
                    )
                if (
                    self._connection.execute(
                        "SELECT 1 FROM review_runs WHERE audit_id=?", (audit_id,)
                    ).fetchone()
                    is not None
                ):
                    raise AutoModeConflictError(
                        "Auto Mode audit identity belongs to another subject"
                    )
                self._connection.execute(
                    "INSERT INTO permission_review_assessments("
                    "assessment_id,audit_id,run_id,root_frame_id,branch_id,turn_id,"
                    "execution_id,decision_id,action_digest,policy_version,"
                    "start_idempotency_key,start_request_sha256,audit_request_digest,"
                    "status,started_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        assessment_id,
                        audit_id,
                        run_id,
                        run["root_frame_id"],
                        run["branch_id"],
                        run["turn_id"],
                        run["execution_id"],
                        decision_id,
                        action_digest,
                        policy_version,
                        idempotency_key,
                        request_sha256,
                        audit_request_digest,
                        "started",
                        timestamp,
                    ),
                )
                event, _ = self._append_event_locked(
                    run,
                    idempotency_key=idempotency_key,
                    event_type="auto_audit_started",
                    request_sha256=request_sha256,
                    payload={
                        "audit_id": audit_id,
                        "assessment_id": assessment_id,
                        "decision_id": decision_id,
                        "action_digest": action_digest,
                        "subject_kind": "permission_review",
                        "subject_entity_kind": "approval_action",
                        "subject_entity_id": decision_id,
                        "policy_version": policy_version,
                        "audit_request_digest": audit_request_digest,
                        "status": "started",
                    },
                    created_at=timestamp,
                )
                row = self._connection.execute(
                    "SELECT * FROM permission_review_assessments WHERE assessment_id=?",
                    (assessment_id,),
                ).fetchone()
                self._assert_permission_assessment_proof_locked(
                    row, completion_visible=False
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return self._permission_transition(row, event, created=True)

    def complete_permission_review(
        self,
        assessment_id: str,
        *,
        idempotency_key: str,
        status: str,
        outcome: str,
        risk: str,
        assessment: Mapping[str, Any],
        completed_at: int | None = None,
    ) -> dict[str, Any]:
        assessment_id = _text("assessment_id", assessment_id)
        idempotency_key = _text("idempotency_key", idempotency_key, maximum=1024)
        status = _text("status", status)
        if status not in {"completed", "unavailable", "failed"}:
            raise ValueError("invalid permission review status")
        outcome = _text("outcome", outcome, maximum=256).lower()
        risk = _text("risk", risk, maximum=256).lower()
        if outcome not in _PERMISSION_OUTCOMES:
            raise ValueError("invalid permission review outcome")
        if risk not in _RISK_LEVELS:
            raise ValueError("invalid permission review risk")
        if status != "completed" and outcome in {
            "allow",
            "allow_once",
            "allowed",
            "shadow_allow",
        }:
            raise ValueError(
                "failed or unavailable permission review cannot allow an action"
            )
        assessment_value = dict(assessment)
        request = {
            "status": status,
            "outcome": outcome,
            "risk": risk,
            "assessment": assessment_value,
        }
        request_sha256 = _digest(request)
        timestamp = self._time(completed_at)
        deadline_now = self._time(None)
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._connection.execute(
                    "SELECT * FROM permission_review_assessments WHERE assessment_id=?",
                    (assessment_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(assessment_id)
                run = self._run_locked(str(row["run_id"]))
                if row["completion_idempotency_key"] is not None:
                    if (
                        row["completion_idempotency_key"] != idempotency_key
                        or row["completion_request_sha256"] != request_sha256
                    ):
                        raise AutoModeConflictError(
                            "permission completion idempotency digest mismatch"
                        )
                    event = self._event_for_idempotency_locked(
                        str(row["run_id"]),
                        idempotency_key,
                        expected_type="auto_audit_completed",
                        expected_request_sha256=request_sha256,
                    )
                    self._assert_permission_assessment_proof_locked(
                        row, completion_visible=True
                    )
                    self._assert_run_replay_integrity_locked(run)
                    self._connection.commit()
                    return self._permission_transition(row, event, created=False)
                if row["status"] != "started":
                    raise AutoModeConflictError("permission assessment is terminal")
                self._assert_permission_assessment_proof_locked(
                    row, completion_visible=False
                )
                self._assert_mutable_run(run)
                permission = self._connection.execute(
                    "SELECT state,expires_at FROM permission_requests "
                    "WHERE decision_id=?",
                    (row["decision_id"],),
                ).fetchone()
                if status == "completed" and (
                    permission is None
                    or permission["state"] != "pending"
                    or (
                        permission["expires_at"] is not None
                        and int(permission["expires_at"]) <= deadline_now
                    )
                ):
                    raise AutoModeConflictError(
                        "permission decision is no longer pending and unexpired"
                    )
                assessment_envelope = {
                    "audit_request_digest": row["audit_request_digest"],
                    "subject_kind": "permission_review",
                    "subject_entity_kind": "approval_action",
                    "subject_entity_id": row["decision_id"],
                    **request,
                    "durable": True,
                    "retry_state": "terminal",
                }
                assessment_digest = _digest(assessment_envelope)
                public_summary = assessment_value.get("public_summary")
                if not isinstance(public_summary, str):
                    public_summary = None
                elif len(public_summary) > _MAX_TEXT:
                    public_summary = public_summary[:_MAX_TEXT]
                self._connection.execute(
                    "UPDATE permission_review_assessments SET "
                    "completion_idempotency_key=?,completion_request_sha256=?,"
                    "assessment_json=?,assessment_envelope_json=?,assessment_digest=?,outcome=?,risk=?,"
                    "public_summary=?,status=?,completed_at=? WHERE assessment_id=?",
                    (
                        idempotency_key,
                        request_sha256,
                        _canonical(assessment_value),
                        _canonical(assessment_envelope),
                        assessment_digest,
                        outcome,
                        risk,
                        public_summary,
                        status,
                        timestamp,
                        assessment_id,
                    ),
                )
                event, _ = self._append_event_locked(
                    run,
                    idempotency_key=idempotency_key,
                    event_type="auto_audit_completed",
                    request_sha256=request_sha256,
                    payload={
                        "audit_id": row["audit_id"],
                        "assessment_id": assessment_id,
                        "decision_id": row["decision_id"],
                        "action_digest": row["action_digest"],
                        "subject_kind": "permission_review",
                        "subject_entity_kind": "approval_action",
                        "subject_entity_id": row["decision_id"],
                        "audit_request_digest": row["audit_request_digest"],
                        "assessment_digest": assessment_digest,
                        "outcome": outcome,
                        "risk": risk,
                        "status": status,
                        "public_summary": public_summary,
                    },
                    created_at=timestamp,
                )
                row = self._connection.execute(
                    "SELECT * FROM permission_review_assessments WHERE assessment_id=?",
                    (assessment_id,),
                ).fetchone()
                self._assert_permission_assessment_proof_locked(
                    row, completion_visible=True
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return self._permission_transition(row, event, created=True)

    def _assert_permission_assessment_proof_locked(
        self,
        row: sqlite3.Row,
        *,
        completion_visible: bool | None = None,
        run: sqlite3.Row | None = None,
        event_pair: tuple[Mapping[str, Any], Mapping[str, Any] | None] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Bind one permission owner row to its exact immutable event pair.

        A Guardian assessment is audit evidence only, but an ``allow``-shaped
        projection is still security-sensitive.  Exact replay and export must
        never trust mutable owner columns when their canonical start/completion
        events disagree. ``completion_visible=False`` validates only the start
        half for a historical prefix whose later completion is intentionally
        hidden.
        """

        def fail() -> None:
            raise AutoModeConflictError("permission assessment proof is invalid")

        try:
            run = run or self._run_locked(str(row["run_id"]))
            if run["trust_state"] != "local":
                fail()
            identity_fields = (
                "run_id",
                "root_frame_id",
                "branch_id",
                "turn_id",
                "execution_id",
            )
            if any(row[name] != run[name] for name in identity_fields):
                fail()
            action_digest = str(row["action_digest"] or "")
            policy_version = str(row["policy_version"] or "")
            if (
                _HEX64.fullmatch(action_digest) is None
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", policy_version)
                is None
            ):
                fail()
            permission = self._connection.execute(
                "SELECT * FROM permission_requests WHERE decision_id=?",
                (row["decision_id"],),
            ).fetchone()
            if permission is None:
                fail()
            try:
                current_action_digest = canonical_permission_action_digest(permission)
                permission_resources = json.loads(permission["resource_keys"] or "[]")
            except (TypeError, ValueError):
                fail()
            if (
                current_action_digest != action_digest
                or permission["root_frame_id"] != run["root_frame_id"]
                or not isinstance(permission_resources, list)
                or any(
                    not isinstance(value, str) or not value
                    for value in permission_resources
                )
                or not permission["action_group_id"]
                or self._get_action_group is None
            ):
                fail()
            permission_group = self._get_action_group(
                str(permission["action_group_id"])
            )
            if (
                permission_group is None
                or permission_group.get("root_frame_id") != run["root_frame_id"]
                or permission_group.get("branch_id") != run["branch_id"]
                or permission_group.get("turn_id") != run["turn_id"]
            ):
                fail()
            pending_events = [
                event
                for event in permission_group.get("events") or ()
                if event.get("type") == "permission_pending"
                and (event.get("canonical_arguments") or {}).get("decision_id")
                == row["decision_id"]
            ]
            if len(pending_events) != 1:
                fail()
            pending_event = pending_events[0]
            if (
                pending_event.get("action_id") != permission["action_id"]
                or pending_event.get("tool_call_id") != permission["tool_call_id"]
                or pending_event.get("side_effect_class")
                != permission["side_effect_class"]
                or pending_event.get("resource_keys") != permission_resources
                or pending_event.get("result")
                != {
                    "decision_id": row["decision_id"],
                    "state": "pending",
                    "tool": permission["tool"],
                    "target": permission["target"],
                }
            ):
                fail()
            start_request = {
                "decision_id": row["decision_id"],
                "action_digest": action_digest,
                "policy_version": policy_version,
            }
            start_request_sha256 = _digest(start_request)
            audit_request_digest = _digest(
                {
                    "subject_kind": "permission_review",
                    "subject_entity_kind": "approval_action",
                    **start_request,
                }
            )
            if (
                row["start_request_sha256"] != start_request_sha256
                or row["audit_request_digest"] != audit_request_digest
            ):
                fail()
            if event_pair is None:
                event_rows = self._connection.execute(
                    "SELECT * FROM auto_mode_events WHERE run_id=? "
                    "AND type IN ('auto_audit_started','auto_audit_completed') "
                    "ORDER BY sequence",
                    (row["run_id"],),
                ).fetchall()
                starts: list[dict[str, Any]] = []
                completions: list[dict[str, Any]] = []
                for event_row in event_rows:
                    event = self._decode_event(event_row)
                    if event["payload"].get("audit_id") != row["audit_id"]:
                        continue
                    if event["type"] == "auto_audit_started":
                        starts.append(event)
                    else:
                        completions.append(event)
            else:
                starts = [dict(event_pair[0])]
                completions = [dict(event_pair[1])] if event_pair[1] is not None else []
            if len(starts) != 1:
                fail()
            start = starts[0]
            expected_start_payload = {
                "audit_id": row["audit_id"],
                "assessment_id": row["assessment_id"],
                "decision_id": row["decision_id"],
                "action_digest": action_digest,
                "subject_kind": "permission_review",
                "subject_entity_kind": "approval_action",
                "subject_entity_id": row["decision_id"],
                "policy_version": policy_version,
                "audit_request_digest": audit_request_digest,
                "status": "started",
            }
            if (
                start["payload"] != expected_start_payload
                or start["idempotency_key"] != row["start_idempotency_key"]
                or start["request_sha256"] != start_request_sha256
                or start["created_at"] != row["started_at"]
                or any(start[name] != row[name] for name in identity_fields)
            ):
                fail()
            if completion_visible is False:
                return start, None

            is_terminal = row["completion_idempotency_key"] is not None
            if completion_visible is True and not is_terminal:
                fail()
            if not is_terminal:
                if (
                    row["status"] != "started"
                    or completions
                    or any(
                        row[name] is not None
                        for name in (
                            "completion_request_sha256",
                            "assessment_json",
                            "assessment_envelope_json",
                            "assessment_digest",
                            "outcome",
                            "risk",
                            "public_summary",
                            "completed_at",
                        )
                    )
                ):
                    fail()
                return start, None
            if len(completions) != 1 or row["status"] not in {
                "completed",
                "unavailable",
                "failed",
            }:
                fail()
            outcome = str(row["outcome"] or "").lower()
            risk = str(row["risk"] or "").lower()
            if (
                outcome not in _PERMISSION_OUTCOMES
                or risk not in _RISK_LEVELS
                or (
                    row["status"] != "completed"
                    and outcome in {"allow", "allow_once", "allowed", "shadow_allow"}
                )
            ):
                fail()
            assessment = json.loads(row["assessment_json"])
            envelope = json.loads(row["assessment_envelope_json"])
            if (
                not isinstance(assessment, Mapping)
                or not isinstance(envelope, Mapping)
                or _canonical(dict(assessment)) != row["assessment_json"]
                or _canonical(dict(envelope)) != row["assessment_envelope_json"]
            ):
                fail()
            completion_request = {
                "status": row["status"],
                "outcome": outcome,
                "risk": risk,
                "assessment": dict(assessment),
            }
            completion_request_sha256 = _digest(completion_request)
            expected_envelope = {
                "audit_request_digest": audit_request_digest,
                "subject_kind": "permission_review",
                "subject_entity_kind": "approval_action",
                "subject_entity_id": row["decision_id"],
                **completion_request,
                "durable": True,
                "retry_state": "terminal",
            }
            assessment_digest = _digest(expected_envelope)
            public_summary = assessment.get("public_summary")
            if not isinstance(public_summary, str):
                public_summary = None
            elif len(public_summary) > _MAX_TEXT:
                public_summary = public_summary[:_MAX_TEXT]
            completion = completions[0]
            expected_completion_payload = {
                "audit_id": row["audit_id"],
                "assessment_id": row["assessment_id"],
                "decision_id": row["decision_id"],
                "action_digest": action_digest,
                "subject_kind": "permission_review",
                "subject_entity_kind": "approval_action",
                "subject_entity_id": row["decision_id"],
                "audit_request_digest": audit_request_digest,
                "assessment_digest": assessment_digest,
                "outcome": outcome,
                "risk": risk,
                "status": row["status"],
                "public_summary": public_summary,
            }
            if (
                dict(envelope) != expected_envelope
                or row["assessment_digest"] != assessment_digest
                or row["completion_request_sha256"] != completion_request_sha256
                or row["public_summary"] != public_summary
                or completion["payload"] != expected_completion_payload
                or completion["idempotency_key"] != row["completion_idempotency_key"]
                or completion["request_sha256"] != completion_request_sha256
                or completion["created_at"] != row["completed_at"]
                or any(completion[name] != row[name] for name in identity_fields)
                or start["sequence"] >= completion["sequence"]
            ):
                fail()
            return start, completion
        except AutoModeConflictError as error:
            if str(error) == "permission assessment proof is invalid":
                raise
            raise AutoModeConflictError(
                "permission assessment proof is invalid"
            ) from error
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise AutoModeConflictError(
                "permission assessment proof is invalid"
            ) from error

    # -------------------------------------------------------------- read projection
    def event_cursor(self, root_frame_id: str, *, branch_id: str | None = None) -> int:
        root_frame_id = _text("root_frame_id", root_frame_id)
        if branch_id is None:
            with self._lock:
                row = self._connection.execute(
                    "SELECT COALESCE(MAX(event_cursor),0) AS cursor "
                    "FROM auto_mode_events WHERE root_frame_id=?",
                    (root_frame_id,),
                ).fetchone()
            return int(row["cursor"] if row else 0)
        events = self.list_events(root_frame_id, branch_id=branch_id)
        return max((int(event["event_cursor"]) for event in events), default=0)

    def list_events(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None = None,
        after_cursor: int | None = None,
        upto_cursor: int | None = None,
        limit: int = 100_000,
    ) -> list[dict[str, Any]]:
        root_frame_id = _text("root_frame_id", root_frame_id)
        if after_cursor is not None:
            after_cursor = _integer("after_cursor", after_cursor)
        if upto_cursor is not None:
            upto_cursor = _integer("upto_cursor", upto_cursor)
        limit = min(_integer("limit", limit, minimum=1), 100_000)

        def local(selected: str) -> list[dict[str, Any]]:
            with self._lock:
                rows = self._connection.execute(
                    "SELECT * FROM auto_mode_events WHERE root_frame_id=? AND branch_id=? "
                    "ORDER BY event_cursor,event_id",
                    (root_frame_id, selected),
                ).fetchall()
            return [self._decode_event(row) for row in rows]

        if branch_id is None:
            with self._lock:
                rows = self._connection.execute(
                    "SELECT * FROM auto_mode_events WHERE root_frame_id=? "
                    "ORDER BY event_cursor,event_id",
                    (root_frame_id,),
                ).fetchall()
            projected = [self._decode_event(row) for row in rows]
        else:
            branch_id = _text("branch_id", branch_id)
            if self._get_branch is None or self._get_checkpoint is None:
                projected = local(branch_id)
            else:
                projected = list(
                    project_branch_records(
                        _BranchStore(self._get_branch, self._get_checkpoint),
                        root_frame_id,
                        branch_id,
                        list_local=local,
                        record_position=lambda row: int(row["event_cursor"]),
                        cursor_key="auto_event_cursor",
                    )
                )
        return [
            event
            for event in projected
            if (after_cursor is None or int(event["event_cursor"]) > after_cursor)
            and (upto_cursor is None or int(event["event_cursor"]) <= upto_cursor)
        ][:limit]

    def project_run(
        self,
        root_frame_id: str,
        branch_id: str,
        *,
        upto_event_cursor: int | None = None,
    ) -> dict[str, Any]:
        # Proof validation spans event, owner, review, and finding tables. Keep
        # them in one SQLite snapshot so a cross-connection completion/import
        # cannot manufacture a mixed read (or a false safety downgrade).
        with self._lock:
            owns_transaction = not self._connection.in_transaction
            try:
                if owns_transaction:
                    self._connection.execute("BEGIN")
                result = self._project_run_locked(
                    root_frame_id,
                    branch_id,
                    upto_event_cursor=upto_event_cursor,
                )
                if owns_transaction:
                    self._connection.commit()
            except Exception:
                if owns_transaction:
                    self._connection.rollback()
                raise
        return result

    def _project_run_locked(
        self,
        root_frame_id: str,
        branch_id: str,
        *,
        upto_event_cursor: int | None,
    ) -> dict[str, Any]:
        events = self.list_events(
            root_frame_id,
            branch_id=branch_id,
            upto_cursor=upto_event_cursor,
        )
        starts = [event for event in events if event["type"] == "auto_run_started"]
        if not starts:
            return {
                "run": None,
                "last_event_id": None,
                "last_event_ordinal": 0,
                "events": [],
            }
        selected = starts[-1]["run_id"]
        run_events = [event for event in events if event["run_id"] == selected]
        run = self._overlay_inert_import(self._reduce_run(run_events))
        run, run_events = self._verified_read_projection(run, run_events)
        tail = run_events[-1]
        return {
            "run": run,
            "last_event_id": tail["event_id"],
            "last_event_ordinal": tail["event_cursor"],
            "events": run_events,
        }

    def list_audits(
        self,
        root_frame_id: str,
        branch_id: str,
        *,
        subject_kind: str | None = None,
        before: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        root_frame_id = _text("root_frame_id", root_frame_id)
        branch_id = _text("branch_id", branch_id)
        if subject_kind is not None and subject_kind not in _SUBJECT_ENTITY:
            raise ValueError("invalid audit subject_kind")
        limit = min(_integer("limit", limit, minimum=1), 501)
        before_cursor: int | None = None
        before_audit_id: str | None = None
        if before is not None:
            before = _text("before", before, maximum=128)
            if before.isascii() and before.isdigit():
                before_cursor = int(before)
            else:
                before_audit_id = before
        with self._lock:
            owns_transaction = not self._connection.in_transaction
            try:
                if owns_transaction:
                    self._connection.execute("BEGIN")
                events = self.list_events(root_frame_id, branch_id=branch_id)
                starts: dict[str, dict[str, Any]] = {}
                completions: dict[str, dict[str, Any]] = {}
                invalid_pairs: set[str] = set()
                for event in events:
                    if event.get("type") not in {
                        "auto_audit_started",
                        "auto_audit_completed",
                    }:
                        continue
                    payload = event.get("payload")
                    if not isinstance(payload, Mapping):
                        continue
                    audit_id = payload.get("audit_id")
                    if not isinstance(audit_id, str) or not audit_id:
                        continue
                    kind = payload.get("subject_kind")
                    entity = payload.get("subject_entity_kind")
                    request_digest = payload.get("audit_request_digest")
                    if (
                        kind not in _SUBJECT_ENTITY
                        or entity != _SUBJECT_ENTITY.get(kind)
                        or not isinstance(request_digest, str)
                        or _HEX64.fullmatch(request_digest) is None
                    ):
                        invalid_pairs.add(audit_id)
                    target = (
                        starts if event["type"] == "auto_audit_started" else completions
                    )
                    if audit_id in target:
                        invalid_pairs.add(audit_id)
                    else:
                        target[audit_id] = event

                rows: list[dict[str, Any]] = []
                pairs: dict[str, tuple[dict[str, Any], dict[str, Any] | None]] = {}

                def scalar(value: Any) -> str | None:
                    return value if isinstance(value, str) else None

                for audit_id, started in starts.items():
                    start_payload = started["payload"]
                    kind = start_payload.get("subject_kind")
                    if subject_kind is not None and kind != subject_kind:
                        continue
                    completed = completions.get(audit_id)
                    final_payload = completed["payload"] if completed else start_payload
                    if completed is not None and (
                        final_payload.get("audit_request_digest")
                        != start_payload.get("audit_request_digest")
                        or final_payload.get("subject_kind") != kind
                        or final_payload.get("subject_entity_kind")
                        != start_payload.get("subject_entity_kind")
                    ):
                        invalid_pairs.add(audit_id)
                    public_summary = scalar(final_payload.get("public_summary"))
                    row = {
                        "audit_id": audit_id,
                        "run_id": started["run_id"],
                        "root_frame_id": started["root_frame_id"],
                        "branch_id": started["branch_id"],
                        "turn_id": started["turn_id"],
                        "execution_id": started["execution_id"],
                        "subject_kind": scalar(kind),
                        "subject_entity_kind": scalar(
                            start_payload.get("subject_entity_kind")
                        ),
                        "subject_entity_id": scalar(
                            final_payload.get("subject_entity_id")
                            or start_payload.get("subject_entity_id")
                            or final_payload.get("candidate_id")
                            or final_payload.get("decision_id")
                        ),
                        "status": scalar(final_payload.get("status")) or "started",
                        "audit_request_digest": scalar(
                            start_payload.get("audit_request_digest")
                        ),
                        "assessment_digest": scalar(
                            final_payload.get("assessment_digest")
                        ),
                        "action_digest": scalar(final_payload.get("action_digest")),
                        "candidate_id": scalar(final_payload.get("candidate_id")),
                        "verdict": scalar(final_payload.get("verdict")),
                        "outcome": scalar(final_payload.get("outcome")),
                        "risk": scalar(final_payload.get("risk")),
                        "public_summary": public_summary,
                        "created_at": started["created_at"],
                        "completed_at": (
                            completed.get("created_at") if completed else None
                        ),
                        "event_ordinal": (
                            completed["event_cursor"]
                            if completed
                            else started["event_cursor"]
                        ),
                    }
                    rows.append(row)
                    pairs[audit_id] = (started, completed)

                if before_audit_id is not None:
                    cursor_row = next(
                        (item for item in rows if item["audit_id"] == before_audit_id),
                        None,
                    )
                    if cursor_row is None:
                        raise ValueError("invalid Auto Mode audit cursor")
                    before_cursor = int(cursor_row["event_ordinal"])
                if before_cursor is not None:
                    # Pair start/completion first, then page complete audit
                    # records. Filtering events first can turn an overlapping
                    # completion into a duplicate synthetic started row.
                    rows = [
                        item
                        for item in rows
                        if int(item["event_ordinal"]) < before_cursor
                    ]
                rows.sort(
                    key=lambda item: (
                        int(item["event_ordinal"]),
                        item["audit_id"],
                    ),
                    reverse=True,
                )
                page = rows[:limit]
                run_cache: dict[str, sqlite3.Row] = {}

                def integrity_failure(row: dict[str, Any]) -> None:
                    for unsafe in (
                        "assessment_digest",
                        "verdict",
                        "outcome",
                        "risk",
                        "public_summary",
                    ):
                        row.pop(unsafe, None)
                    row.update(
                        {
                            "status": "failed",
                            "risk": "unknown",
                            "error_kind": "integrity_failure",
                            "public_summary": "Audit integrity validation failed.",
                        }
                    )

                for row in page:
                    audit_id = str(row["audit_id"])
                    started, completed = pairs[audit_id]
                    start_payload = started["payload"]
                    try:
                        if audit_id in invalid_pairs:
                            raise AutoModeConflictError("audit event pair is invalid")
                        run_id = str(started["run_id"])
                        owner_run = run_cache.get(run_id)
                        if owner_run is None:
                            owner_run = self._run_locked(run_id)
                            run_cache[run_id] = owner_run
                        if owner_run["trust_state"] == "local":
                            if start_payload["subject_kind"] == "result_review":
                                assessment = self._connection.execute(
                                    "SELECT * FROM review_runs "
                                    "WHERE audit_id=? AND run_id=?",
                                    (audit_id, run_id),
                                ).fetchone()
                                if assessment is None:
                                    raise AutoModeConflictError(
                                        "review assessment proof is invalid"
                                    )
                                self._assert_review_assessment_proof_locked(
                                    assessment,
                                    completion_visible=completed is not None,
                                    run=owner_run,
                                    event_pair=(started, completed),
                                )
                            else:
                                assessment = self._connection.execute(
                                    "SELECT * FROM permission_review_assessments "
                                    "WHERE audit_id=? AND run_id=?",
                                    (audit_id, run_id),
                                ).fetchone()
                                if assessment is None:
                                    raise AutoModeConflictError(
                                        "permission assessment proof is invalid"
                                    )
                                self._assert_permission_assessment_proof_locked(
                                    assessment,
                                    completion_visible=completed is not None,
                                    run=owner_run,
                                    event_pair=(started, completed),
                                )
                        else:
                            # Imported assessments are historical claims only.
                            for unsafe in (
                                "assessment_digest",
                                "verdict",
                                "outcome",
                                "risk",
                            ):
                                row.pop(unsafe, None)
                            row.update(
                                {
                                    "status": "unverified_import",
                                    "error_kind": "quarantined_import",
                                }
                            )
                    except (AutoModeConflictError, KeyError):
                        # Public audit truth may acknowledge corruption, but it
                        # never preserves an allow/pass-shaped claim.
                        integrity_failure(row)
                result = [
                    {key: value for key, value in row.items() if value is not None}
                    for row in page
                ]
                if owns_transaction:
                    self._connection.commit()
                return result
            except Exception:
                if owns_transaction:
                    self._connection.rollback()
                raise

    def export_projection(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None = None,
        upto_event_cursor: int | None = None,
    ) -> dict[str, Any]:
        # A package/share projection must be one SQLite snapshot.  Otherwise a
        # cross-connection writer can commit audit_completed after the event
        # read but before the owner-row read, producing an impossible torn DTO.
        with self._lock:
            try:
                self._connection.execute("BEGIN")
                result = self._export_projection_locked(
                    root_frame_id,
                    branch_id=branch_id,
                    upto_event_cursor=upto_event_cursor,
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return result

    def _export_projection_locked(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None,
        upto_event_cursor: int | None,
    ) -> dict[str, Any]:
        events = self.list_events(
            root_frame_id,
            branch_id=branch_id,
            upto_cursor=upto_event_cursor,
        )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            grouped.setdefault(str(event["run_id"]), []).append(event)
        runs: list[dict[str, Any]] = []
        safe_events_by_id: dict[str, dict[str, Any]] = {}
        for run_events in grouped.values():
            if any(event["type"] == "auto_run_started" for event in run_events):
                starts_at = next(
                    index
                    for index, event in enumerate(run_events)
                    if event["type"] == "auto_run_started"
                )
                reduced_events = run_events[starts_at:]
                reduced = self._overlay_inert_import(self._reduce_run(reduced_events))
                reduced, reduced_events = self._verified_read_projection(
                    reduced, reduced_events
                )
                if (
                    reduced.get("trust_state", "local") == "local"
                    and reduced.get("source_claimed_status") is not None
                ):
                    raise AutoModeConflictError(
                        "Auto Mode export projection integrity failed"
                    )
                runs.append(reduced)
                safe_events_by_id.update(
                    {str(event["event_id"]): event for event in reduced_events}
                )
        if safe_events_by_id:
            events = [
                safe_events_by_id.get(str(event["event_id"]), event) for event in events
            ]
        runs.sort(
            key=lambda item: (
                int(item.get("start_event_ordinal") or 0),
                item["run_id"],
            )
        )
        audit_ids = {
            str((event.get("payload") or {}).get("audit_id"))
            for event in events
            if (event.get("payload") or {}).get("audit_id")
        }
        completed_audit_ids = {
            str((event.get("payload") or {}).get("audit_id"))
            for event in events
            if event.get("type") == "auto_audit_completed"
            and (event.get("payload") or {}).get("audit_id")
        }
        repair_ids = {
            str((event.get("payload") or {}).get("repair_run_id"))
            for event in events
            if (event.get("payload") or {}).get("repair_run_id")
        }
        completed_repair_ids = {
            str((event.get("payload") or {}).get("repair_run_id"))
            for event in events
            if event.get("type") == "repair_completed"
            and (event.get("payload") or {}).get("repair_run_id")
        }
        visible_repair_groups: dict[str, list[str]] = {}
        for event in events:
            payload = event.get("payload") or {}
            if (
                event.get("type") != "repair_started"
                or payload.get("phase") != "execution_group_bound"
            ):
                continue
            repair_id = str(payload.get("repair_run_id") or "")
            action_group_id = str(payload.get("action_group_id") or "")
            if not repair_id or not action_group_id:
                raise AutoModeConflictError("repair execution binding event is invalid")
            groups = visible_repair_groups.setdefault(repair_id, [])
            if action_group_id in groups:
                raise AutoModeConflictError("duplicate repair execution binding event")
            groups.append(action_group_id)
        audit_start_cursor = {
            str((event.get("payload") or {}).get("audit_id")): int(
                event["event_cursor"]
            )
            for event in events
            if event.get("type") == "auto_audit_started"
            and (event.get("payload") or {}).get("audit_id")
        }
        repair_start_cursor = {
            str((event.get("payload") or {}).get("repair_run_id")): int(
                event["event_cursor"]
            )
            for event in events
            if event.get("type") == "repair_started"
            and (event.get("payload") or {}).get("repair_run_id")
            and (event.get("payload") or {}).get("phase") != "execution_group_bound"
        }
        review_rows: list[dict[str, Any]] = []
        permission_rows: list[dict[str, Any]] = []
        repair_rows: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []
        if audit_ids:
            marks = ",".join("?" for _ in audit_ids)
            with self._lock:
                raw_reviews = self._connection.execute(
                    "SELECT * FROM review_runs WHERE audit_id IN (" + marks + ")",
                    tuple(sorted(audit_ids)),
                ).fetchall()
                raw_permissions = self._connection.execute(
                    "SELECT * FROM permission_review_assessments WHERE audit_id IN ("
                    + marks
                    + ")",
                    tuple(sorted(audit_ids)),
                ).fetchall()
            for raw in raw_reviews:
                owner_run = self._run_locked(str(raw["run_id"]))
                if owner_run["trust_state"] == "local":
                    self._assert_review_assessment_proof_locked(
                        raw,
                        completion_visible=(
                            str(raw["audit_id"]) in completed_audit_ids
                        ),
                    )
                review = self._decode_review(raw)
                review_findings = review.pop("findings", [])
                if str(review["audit_id"]) not in completed_audit_ids:
                    review.update(
                        {
                            "completion_idempotency_key": None,
                            "completion_request_sha256": None,
                            "status": "started",
                            "verdict": None,
                            "assessment": None,
                            "assessment_digest": None,
                            "usage": None,
                            "public_summary": None,
                            "completed_at": None,
                            "finished_at": None,
                        }
                    )
                else:
                    findings.extend(review_findings)
                review_rows.append(review)
            for raw in raw_permissions:
                owner_run = self._run_locked(str(raw["run_id"]))
                if owner_run["trust_state"] == "local":
                    self._assert_permission_assessment_proof_locked(
                        raw,
                        completion_visible=(
                            str(raw["audit_id"]) in completed_audit_ids
                        ),
                    )
                assessment = self._decode_permission(raw)
                if str(assessment["audit_id"]) not in completed_audit_ids:
                    assessment.update(
                        {
                            "completion_idempotency_key": None,
                            "completion_request_sha256": None,
                            "assessment": None,
                            "assessment_digest": None,
                            "outcome": None,
                            "risk": None,
                            "public_summary": None,
                            "status": "started",
                            "completed_at": None,
                            "finished_at": None,
                        }
                    )
                permission_rows.append(assessment)
        if repair_ids:
            marks = ",".join("?" for _ in repair_ids)
            with self._lock:
                raw_repairs = self._connection.execute(
                    "SELECT * FROM repair_runs WHERE repair_run_id IN (" + marks + ")",
                    tuple(sorted(repair_ids)),
                ).fetchall()
            for raw in raw_repairs:
                owner_run = self._run_locked(str(raw["run_id"]))
                if owner_run["trust_state"] == "local":
                    self._assert_repair_ledger_proof_locked(
                        raw,
                        completion_visible=(
                            str(raw["repair_run_id"]) in completed_repair_ids
                        ),
                    )
                repair = self._decode_repair(raw)
                repair_id = str(repair["repair_run_id"])
                visible_groups = visible_repair_groups.get(repair_id, [])
                if (
                    repair_id in completed_repair_ids
                    and list(repair.get("execution_group_ids") or []) != visible_groups
                ):
                    raise AutoModeConflictError(
                        "repair completion references an invisible execution group"
                    )
                repair["execution_group_ids"] = visible_groups
                if repair_id not in completed_repair_ids:
                    repair.update(
                        {
                            "completion_idempotency_key": None,
                            "completion_request_sha256": None,
                            "after_version_ids": [],
                            "verification_review_run_id": None,
                            "status": "started",
                            "completed_at": None,
                            "finished_at": None,
                        }
                    )
                repair_rows.append(repair)

        missing_cursor = 1 << 63
        review_rows.sort(
            key=lambda item: (
                audit_start_cursor.get(str(item["audit_id"]), missing_cursor),
                str(item["review_run_id"]),
            )
        )
        permission_rows.sort(
            key=lambda item: (
                audit_start_cursor.get(str(item["audit_id"]), missing_cursor),
                str(item["assessment_id"]),
            )
        )
        repair_rows.sort(
            key=lambda item: (
                repair_start_cursor.get(str(item["repair_run_id"]), missing_cursor),
                str(item["repair_run_id"]),
            )
        )
        review_start_cursor = {
            str(review["review_run_id"]): audit_start_cursor.get(
                str(review["audit_id"]), missing_cursor
            )
            for review in review_rows
        }
        findings.sort(
            key=lambda item: (
                review_start_cursor.get(str(item["review_run_id"]), missing_cursor),
                int(item.get("finding_ordinal") or 0),
                str(item["finding_id"]),
            )
        )

        # Finding rows are mutable lifecycle records. Rebuild their status and
        # timestamp from only the visible event prefix so a checkpoint/share
        # cannot leak a later repair start or completion.
        finding_by_id = {str(item["finding_id"]): item for item in findings}
        for finding in findings:
            finding["status"] = "open"
            finding["updated_at"] = finding["created_at"]
        repair_findings: dict[str, list[str]] = {}
        for event in events:
            payload = event.get("payload") or {}
            repair_id = payload.get("repair_run_id")
            if (
                event.get("type") == "repair_started"
                and isinstance(repair_id, str)
                and payload.get("phase") != "execution_group_bound"
            ):
                repair_findings[repair_id] = [
                    str(value) for value in payload.get("finding_ids") or []
                ]
                for finding_id in repair_findings[repair_id]:
                    finding = finding_by_id.get(finding_id)
                    if finding is not None:
                        finding["status"] = "claimed"
                        finding["updated_at"] = event["created_at"]
            elif event.get("type") == "repair_completed" and isinstance(repair_id, str):
                state = (
                    "addressed_pending_review"
                    if payload.get("status") == "completed"
                    else "unaddressed"
                )
                for finding_id in repair_findings.get(repair_id, []):
                    finding = finding_by_id.get(finding_id)
                    if finding is not None:
                        finding["status"] = state
                        finding["updated_at"] = event["created_at"]
        trust_state = "local"
        if grouped:
            marks = ",".join("?" for _ in grouped)
            with self._lock:
                states = {
                    str(row["trust_state"])
                    for row in self._connection.execute(
                        "SELECT trust_state FROM auto_mode_runs WHERE run_id IN ("
                        + marks
                        + ")",
                        tuple(grouped),
                    ).fetchall()
                }
            if states == {"quarantined_import"}:
                trust_state = "quarantined_import"
        historical_selection = runs[-1].get("selection") if runs else None
        return {
            "schema_version": 1,
            "trust_state": trust_state,
            "historical_selection": historical_selection,
            "runs": runs,
            "events": events,
            "review_runs": review_rows,
            "findings": findings,
            "repair_runs": repair_rows,
            "permission_assessments": permission_rows,
        }

    def _verified_read_projection(
        self,
        run: Mapping[str, Any],
        events: Sequence[Mapping[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Never project Verified after its durable proof stops validating.

        This is a read-only safety overlay: physical audit rows remain intact
        for diagnosis, while REST/reopen/package/share receive an internally
        consistent ``failed / safety_boundary`` terminal view.  A later integrity
        repair can therefore recover the original audit without GET mutating
        evidence or laundering a broken proof as Verified.
        """

        projected_run = dict(run)
        projected_events = [dict(event) for event in events]
        owner: sqlite3.Row | None = None
        try:
            owner = self._connection.execute(
                "SELECT * FROM auto_mode_runs WHERE run_id=?",
                (projected_run.get("run_id"),),
            ).fetchone()
            if owner is None:
                raise AutoModeConflictError("projected run owner is unavailable")
            if owner["trust_state"] != "local":
                return projected_run, projected_events
            self._assert_visible_run_projection_locked(
                owner, projected_run, projected_events
            )
            owner_status = str(owner["status"])
            projected_status = str(projected_run.get("status") or "")
            owner_terminal = owner_status in _TERMINAL_STATUSES
            projected_terminal = projected_status in _TERMINAL_STATUSES
            if not owner_terminal and not projected_terminal:
                return projected_run, projected_events
            if not owner_terminal:
                raise AutoModeConflictError(
                    "terminal projection disagrees with its durable owner"
                )
            terminal_key = owner["terminal_idempotency_key"]
            if not isinstance(terminal_key, str) or not terminal_key:
                raise AutoModeConflictError("terminal owner pointer is invalid")
            pointer = self._connection.execute(
                "SELECT event_id FROM auto_mode_events "
                "WHERE run_id=? AND idempotency_key=?",
                (owner["run_id"], terminal_key),
            ).fetchone()
            if pointer is None:
                raise AutoModeConflictError("terminal owner event is unavailable")
            pointer_visible = any(
                event.get("event_id") == pointer["event_id"]
                for event in projected_events
            )
            if not pointer_visible:
                if projected_terminal:
                    raise AutoModeConflictError(
                        "terminal projection disagrees with its durable owner"
                    )
                # A checkpoint, branch revert, or explicit prefix can
                # legitimately hide a later physical terminal event.
                return projected_run, projected_events
            if not projected_terminal:
                raise AutoModeConflictError(
                    "terminal projection disagrees with its durable owner"
                )
            self._assert_terminal_event_locked(owner, expected_status=owner_status)
            if (
                projected_status != owner_status
                or projected_run.get("terminal_reason") != owner["terminal_reason"]
                or projected_run.get("stop_reason") != owner["stop_reason"]
                or projected_run.get("finished_at") != owner["finished_at"]
            ):
                raise AutoModeConflictError(
                    "terminal projection disagrees with its durable owner"
                )
            if owner_status == "verified":
                self._assert_verified_locked(owner, require_terminal=True)
            return projected_run, projected_events
        except Exception:
            projected_claim = str(projected_run.get("status") or "")
            owner_claim = str(owner["status"]) if owner is not None else ""
            if "verified" in {projected_claim, owner_claim}:
                claimed_status = "verified"
            elif projected_claim in _RUN_STATUSES:
                claimed_status = projected_claim
            elif owner_claim in _RUN_STATUSES:
                claimed_status = owner_claim
            else:
                claimed_status = "failed"
            projected_run.update(
                {
                    "source_claimed_status": claimed_status,
                    "status": "failed",
                    "terminal_reason": "safety_boundary",
                }
            )
            safe_events: list[dict[str, Any]] = []
            for event in projected_events:
                safe_event = dict(event)
                if event.get("type") == "auto_run_terminal":
                    payload = dict(event.get("payload") or {})
                    payload.update(
                        {
                            "status": "failed",
                            "terminal_reason": "safety_boundary",
                        }
                    )
                    safe_event["payload"] = payload
                    safe_event["payload_sha256"] = hashlib.sha256(
                        _canonical(payload).encode("utf-8")
                    ).hexdigest()
                safe_events.append(safe_event)
            return projected_run, safe_events

    @classmethod
    def _assert_no_private_import_fields(cls, value: Any) -> None:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                normalized = str(key).strip().lower().replace("-", "_")
                if normalized in _PRIVATE_IMPORT_FIELDS or any(
                    normalized.endswith(suffix)
                    for suffix in (
                        "_api_key",
                        "_access_token",
                        "_credential",
                        "_secret",
                        "_password",
                    )
                ):
                    raise ValueError(
                        "private or reusable authorization material is not portable"
                    )
                cls._assert_no_private_import_fields(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                cls._assert_no_private_import_fields(nested)

    @classmethod
    def _normalize_import_historical_selection(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("historical_selection must be an object")
        cls._assert_no_private_import_fields(value)
        result: dict[str, Any] = {}
        preset = value.get("preset")
        if preset is not None:
            preset = _text("historical_selection.preset", preset, maximum=64)
            if preset not in {"off", "autonomous"}:
                raise ValueError("invalid historical Auto Mode preset")
            result["preset"] = preset
        review_mode = value.get("result_review_mode")
        if review_mode is not None:
            review_mode = _text(
                "historical_selection.result_review_mode",
                review_mode,
                maximum=64,
            )
            if review_mode not in _RUN_MODES:
                raise ValueError("invalid historical result review mode")
            result["result_review_mode"] = review_mode
        approvals = value.get("approvals_reviewer")
        if approvals is not None:
            approvals = _text(
                "historical_selection.approvals_reviewer",
                approvals,
                maximum=64,
            )
            if approvals not in {"user", "auto_review"}:
                raise ValueError("invalid historical approvals reviewer")
            result["approvals_reviewer"] = approvals
        source = value.get("source")
        if source is not None:
            result["source"] = _text("historical_selection.source", source, maximum=128)
        return result

    @staticmethod
    def _validate_import_timestamps(collections: Sequence[Sequence[Any]]) -> None:
        fields = (
            "created_at",
            "updated_at",
            "started_at",
            "completed_at",
            "finished_at",
            "terminal_at",
            "occurred_at",
        )
        for collection in collections:
            for item in collection:
                if not isinstance(item, Mapping):
                    continue
                for field in fields:
                    if field in item and item[field] is not None:
                        _integer(field, item[field])

    def import_quarantined_projection(
        self,
        source: Mapping[str, Any],
        *,
        root_frame_id: str,
        project_id: str | None = None,
        branch_id: str | None = None,
        branch_id_map: Mapping[str, str] | None = None,
        turn_id_map: Mapping[str, str] | None = None,
        artifact_id_map: Mapping[str, str] | None = None,
        version_id_map: Mapping[str, str] | None = None,
        cell_id_map: Mapping[str, str] | None = None,
        action_group_id_map: Mapping[str, str] | None = None,
        action_id_map: Mapping[str, str] | None = None,
        artifact_version_id_map: Mapping[str, str] | None = None,
        resume_execution: bool = False,
        imported_at: int | None = None,
    ) -> dict[str, Any]:
        del action_id_map
        if resume_execution is not False:
            raise PermissionError("imported Auto Mode history cannot resume execution")
        if not isinstance(source, Mapping) or source.get("schema_version", 1) != 1:
            raise ValueError("invalid Auto Mode import projection")
        source_trust_state = source.get("trust_state", "local")
        if not isinstance(source_trust_state, str) or source_trust_state not in {
            "local",
            "quarantined_import",
        }:
            raise ValueError("invalid Auto Mode import trust_state")
        historical_selection = self._normalize_import_historical_selection(
            source.get("historical_selection")
        )
        root_frame_id = _text("root_frame_id", root_frame_id)
        project_id = _text("project_id", project_id)
        branch_default = _text("branch_id", branch_id or root_frame_id)
        timestamp = self._time(imported_at)
        runs = source.get("runs") or []
        events = source.get("events") or []
        reviews = source.get("review_runs") or []
        findings = source.get("findings") or []
        repairs = source.get("repair_runs") or []
        permissions = source.get("permission_assessments") or []
        for name, values, limit in (
            ("runs", runs, 25_000),
            ("events", events, 100_000),
            ("review_runs", reviews, 50_000),
            ("findings", findings, 100_000),
            ("repair_runs", repairs, 25_000),
            ("permission_assessments", permissions, 100_000),
        ):
            if not isinstance(values, list) or len(values) > limit:
                raise ValueError(f"invalid imported Auto Mode {name}")
        self._validate_import_timestamps(
            (runs, events, reviews, findings, repairs, permissions)
        )
        branch_map_supplied = branch_id_map is not None
        turn_map_supplied = turn_id_map is not None
        branch_map = dict(branch_id_map or {})
        turn_map = dict(turn_id_map or {})
        artifact_map = dict(artifact_id_map or {})
        version_map = dict(version_id_map or artifact_version_id_map or {})
        cell_map = dict(cell_id_map or {})
        group_map = dict(action_group_id_map or {})
        source_run_ids = self._unique_ids(runs, "run_id", "run")
        self._unique_ids(events, "event_id", "event")
        source_review_ids = self._unique_ids(reviews, "review_run_id", "review")
        source_finding_ids = self._unique_ids(findings, "finding_id", "finding")
        source_repair_ids = self._unique_ids(repairs, "repair_run_id", "repair")
        source_assessment_ids = self._unique_ids(
            permissions, "assessment_id", "permission assessment"
        )
        self._unique_ids(permissions, "decision_id", "permission decision")
        source_run_by_id = {str(item["run_id"]): item for item in runs}
        run_map = {
            value: f"auto-run-{uuid.uuid4().hex[:20]}" for value in source_run_ids
        }
        event_map = {
            str(item["event_id"]): f"auto-event-{uuid.uuid4().hex[:20]}"
            for item in events
        }
        audit_map: dict[str, str] = {}
        for collection in (reviews, permissions):
            for item in collection:
                source_audit_id = _text("audit_id", item.get("audit_id"))
                if source_audit_id in audit_map:
                    raise AutoModeConflictError(
                        "duplicate imported Auto Mode audit identity"
                    )
                audit_map[source_audit_id] = f"audit-{uuid.uuid4().hex[:20]}"
        candidate_maps: dict[str, dict[str, str]] = {
            run_id: {} for run_id in source_run_ids
        }
        execution_map: dict[str, str] = {}
        review_map = {
            value: f"review-{uuid.uuid4().hex[:20]}" for value in source_review_ids
        }
        finding_map = {
            value: f"finding-{uuid.uuid4().hex[:20]}" for value in source_finding_ids
        }
        repair_map = {
            value: f"repair-{uuid.uuid4().hex[:20]}" for value in source_repair_ids
        }
        assessment_map = {
            value: f"assessment-{uuid.uuid4().hex[:20]}"
            for value in source_assessment_ids
        }
        decision_map = {
            str(item["decision_id"]): f"decision-{uuid.uuid4().hex[:20]}"
            for item in permissions
            if isinstance(item, Mapping) and item.get("decision_id")
        }
        for event in events:
            if not isinstance(event, Mapping):
                raise ValueError("invalid imported Auto Mode event")
            payload = event.get("payload") or {}
            if not isinstance(payload, Mapping):
                raise ValueError("invalid imported Auto Mode event payload")
            supplied_digest = _sha("payload_sha256", event.get("payload_sha256"))
            if supplied_digest != _digest(dict(payload)):
                raise AutoModeConflictError("imported event payload hash mismatch")
            if payload.get("audit_id"):
                if str(payload["audit_id"]) not in audit_map:
                    raise ValueError(
                        "imported audit event references an unknown subject"
                    )
            source_run = _text("run_id", event.get("run_id"))
            if source_run not in candidate_maps:
                raise ValueError("imported Auto Mode event references unknown run")
            if payload.get("candidate_id"):
                candidate_maps[source_run].setdefault(
                    str(payload["candidate_id"]),
                    f"candidate-{uuid.uuid4().hex[:20]}",
                )
        for run in runs:
            source_run = _text("run_id", run.get("run_id"))
            run_trust_state = run.get("trust_state", source_trust_state)
            if not isinstance(run_trust_state, str) or run_trust_state not in {
                "local",
                "quarantined_import",
            }:
                raise ValueError("invalid imported Auto Mode run trust_state")
            if run.get("candidate_id"):
                candidate_maps[source_run].setdefault(
                    str(run["candidate_id"]),
                    f"candidate-{uuid.uuid4().hex[:20]}",
                )
        cursors = [
            _integer("event_cursor", item.get("event_cursor"), minimum=1)
            for item in events
        ]
        if cursors != sorted(set(cursors)):
            raise ValueError("imported Auto Mode cursors must be unique and ordered")
        events_by_source_run: dict[str, list[Mapping[str, Any]]] = {
            run_id: [] for run_id in source_run_ids
        }
        for event in events:
            source_run = _text("run_id", event.get("run_id"))
            parent = source_run_by_id.get(source_run)
            if parent is None:
                raise ValueError("imported Auto Mode event references unknown run")
            for field in ("root_frame_id", "branch_id", "turn_id", "execution_id"):
                if _text(field, event.get(field)) != _text(field, parent.get(field)):
                    raise AutoModeConflictError(
                        "imported Auto Mode event scope does not match its run"
                    )
            if event.get("type") not in _EVENT_TYPES:
                raise ValueError("invalid imported Auto Mode event type")
            events_by_source_run[source_run].append(event)
        for source_run, run_events in events_by_source_run.items():
            if (
                not run_events
                or run_events[0].get("type") != "auto_run_started"
                or sum(event.get("type") == "auto_run_started" for event in run_events)
                != 1
            ):
                raise AutoModeConflictError(
                    "imported Auto Mode run lacks one canonical start event"
                )
            terminals = [
                index
                for index, event in enumerate(run_events)
                if event.get("type") == "auto_run_terminal"
            ]
            if len(terminals) > 1 or (
                terminals and terminals[0] != len(run_events) - 1
            ):
                raise AutoModeConflictError(
                    "imported Auto Mode terminal event is not unique and last"
                )
            source_owner = source_run_by_id[source_run]
            source_status = _text("status", source_owner.get("status"))
            if terminals:
                terminal_payload = run_events[terminals[0]].get("payload") or {}
                if (
                    terminal_payload.get("status") != source_status
                    or terminal_payload.get("terminal_reason")
                    != source_owner.get("terminal_reason")
                    or terminal_payload.get("stop_reason")
                    != source_owner.get("stop_reason")
                    or source_status not in _TERMINAL_STATUSES | {"unverified"}
                ):
                    raise AutoModeConflictError(
                        "imported Auto Mode terminal event disagrees with its owner"
                    )
            else:
                portable_inert = (
                    source_status == "unverified"
                    and source_owner.get("source_claimed_status")
                    in {"running", "candidate", "reviewing", "repairing"}
                    and source_owner.get("terminal_reason")
                    == "portable_execution_inert"
                    and source_owner.get("stop_reason") is None
                )
                quarantined_inert = (
                    source.get("trust_state") == "quarantined_import"
                    and source_status == "unverified_import"
                    and source_owner.get("terminal_reason") == "quarantined_import"
                    and source_owner.get("stop_reason") is None
                )
                if not (portable_inert or quarantined_inert) and (
                    source_status
                    not in {"running", "candidate", "reviewing", "repairing"}
                    or source_owner.get("terminal_reason") is not None
                    or source_owner.get("stop_reason") is not None
                ):
                    raise AutoModeConflictError(
                        "imported nonterminal Auto Mode run has terminal owner state"
                    )

        def mapped_identity(item: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
            source_run = _text("run_id", item.get("run_id"))
            if source_run not in run_map:
                raise ValueError("imported Auto Mode record references unknown run")
            parent = source_run_by_id[source_run]
            for field in ("root_frame_id", "branch_id", "turn_id", "execution_id"):
                if _text(field, item.get(field)) != _text(field, parent.get(field)):
                    raise AutoModeConflictError(
                        "imported Auto Mode record identity does not match its run"
                    )
            source_branch = _text("branch_id", item.get("branch_id"))
            if branch_map_supplied and source_branch not in branch_map:
                raise ValueError("imported Auto Mode branch identity is unmapped")
            mapped_branch = branch_map.get(source_branch, branch_default)
            source_turn = _text("turn_id", item.get("turn_id"))
            if turn_map_supplied and source_turn not in turn_map:
                raise ValueError("imported Auto Mode turn identity is unmapped")
            mapped_turn = turn_map.setdefault(
                source_turn, f"turn-{uuid.uuid4().hex[:20]}"
            )
            source_execution = _text("execution_id", item.get("execution_id"))
            mapped_execution = execution_map.setdefault(
                source_execution, f"execution-{uuid.uuid4().hex[:20]}"
            )
            return (
                run_map[source_run],
                root_frame_id,
                mapped_branch,
                mapped_turn,
                mapped_execution,
            )

        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                quarantine_row = self._connection.execute(
                    "SELECT value FROM settings WHERE key=?",
                    (f"session:import-quarantine:{root_frame_id}",),
                ).fetchone()
                try:
                    quarantine = (
                        json.loads(quarantine_row["value"])
                        if quarantine_row is not None
                        else None
                    )
                except (TypeError, ValueError) as error:
                    raise PermissionError(
                        "Auto Mode import requires a valid Session quarantine"
                    ) from error
                if (
                    not isinstance(quarantine, Mapping)
                    or quarantine.get("state") != "quarantined"
                ):
                    raise PermissionError(
                        "Auto Mode import requires a valid Session quarantine"
                    )
                root = self._connection.execute(
                    "SELECT f.frame_id,f.root_frame_id,f.parent_id,f.project_id,"
                    "p.project_id AS existing_project_id FROM frames f "
                    "LEFT JOIN projects p ON p.project_id=f.project_id "
                    "WHERE f.frame_id=?",
                    (root_frame_id,),
                ).fetchone()
                if (
                    root is None
                    or root["frame_id"] != root_frame_id
                    or root["root_frame_id"] != root_frame_id
                    or root["parent_id"] is not None
                    or root["project_id"] != project_id
                    or root["existing_project_id"] != project_id
                ):
                    raise PermissionError(
                        "Auto Mode import requires a canonical quarantined Session root"
                    )
                selection_scope_ids = [root_frame_id]
                selection_scope_ids.append(project_id)
                placeholders = ",".join("?" for _ in selection_scope_ids)
                selection = self._connection.execute(
                    "SELECT 1 FROM auto_mode_selections WHERE scope_id IN ("
                    + placeholders
                    + ") LIMIT 1",
                    tuple(selection_scope_ids),
                ).fetchone()
                if selection is not None:
                    raise AutoModeConflictError(
                        "target Session already has an Auto Mode selection"
                    )
                existing = self._connection.execute(
                    "SELECT 1 FROM auto_mode_runs WHERE root_frame_id=? LIMIT 1",
                    (root_frame_id,),
                ).fetchone()
                if existing is not None:
                    raise AutoModeConflictError(
                        "target Session already has Auto Mode history"
                    )
                for item in runs:
                    if not isinstance(item, Mapping):
                        raise ValueError("invalid imported Auto Mode run")
                    identity = mapped_identity(item)
                    mode = _text("mode", item.get("mode"), maximum=64)
                    if mode not in _RUN_MODES:
                        raise ValueError("invalid imported Auto Mode mode")
                    source_candidate = item.get("candidate_id")
                    candidate_id = None
                    if source_candidate:
                        source_run = _text("run_id", item.get("run_id"))
                        candidate_id = candidate_maps[source_run].setdefault(
                            str(source_candidate), f"candidate-{uuid.uuid4().hex[:20]}"
                        )
                    source_claimed_status = item.get("source_claimed_status")
                    if source_claimed_status is not None:
                        source_claimed_status = _text(
                            "source_claimed_status",
                            source_claimed_status,
                            maximum=128,
                        )
                        if source_claimed_status not in _RUN_STATUSES | {"unverified"}:
                            raise ValueError("invalid imported Auto Mode source status")
                    source_terminal_reason = item.get("source_terminal_reason")
                    if source_terminal_reason is not None:
                        source_terminal_reason = _text(
                            "source_terminal_reason",
                            source_terminal_reason,
                            maximum=1024,
                        )
                    source_versions = _string_list(
                        "candidate_version_ids", item.get("candidate_version_ids") or []
                    )
                    source_artifacts = _string_list(
                        "candidate_artifact_ids",
                        item.get("candidate_artifact_ids") or [],
                    )
                    self._connection.execute(
                        "INSERT INTO auto_mode_runs("
                        "run_id,idempotency_key,root_frame_id,branch_id,turn_id,execution_id,"
                        "mode,selection_json,budgets_json,request_sha256,owner_instance_id,"
                        "trust_state,status,state_revision,candidate_id,"
                        "candidate_snapshot_sha256,evidence_snapshot_sha256,"
                        "artifact_set_sha256,candidate_artifact_ids_json,"
                        "candidate_version_ids_json,terminal_reason,"
                        "source_claimed_status,source_terminal_reason,"
                        "created_at,updated_at,finished_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            identity[0],
                            f"import:{identity[0]}",
                            *identity[1:],
                            mode,
                            _canonical(historical_selection),
                            _canonical({}),
                            _digest(dict(item)),
                            "quarantined-import",
                            "quarantined_import",
                            "unverified_import",
                            1,
                            candidate_id,
                            _sha(
                                "candidate_snapshot_sha256",
                                item.get("candidate_snapshot_sha256"),
                                optional=True,
                            ),
                            _sha(
                                "evidence_snapshot_sha256",
                                item.get("evidence_snapshot_sha256"),
                                optional=True,
                            ),
                            _sha(
                                "artifact_set_sha256",
                                item.get("artifact_set_sha256"),
                                optional=True,
                            ),
                            _canonical(
                                self._mapped_import_list(
                                    "candidate_artifact_ids",
                                    source_artifacts,
                                    artifact_map,
                                )
                            ),
                            _canonical(
                                self._mapped_import_list(
                                    "candidate_version_ids",
                                    source_versions,
                                    version_map,
                                )
                            ),
                            "quarantined_import",
                            source_claimed_status,
                            source_terminal_reason,
                            timestamp,
                            timestamp,
                            timestamp,
                        ),
                    )
                sequence_by_run: dict[str, int] = {}
                audit_event_index: dict[tuple[str, str], dict[str, Any]] = {}
                repair_event_index: dict[tuple[str, str], dict[str, Any]] = {}
                for item in events:
                    identity = mapped_identity(item)
                    source_run = _text("run_id", item.get("run_id"))
                    event_type = str(item.get("type") or "")
                    if event_type not in _EVENT_TYPES:
                        raise ValueError("invalid imported Auto Mode event type")
                    payload = dict(item.get("payload") or {})
                    supplied_digest = _sha("payload_sha256", item.get("payload_sha256"))
                    if supplied_digest != _digest(payload):
                        raise AutoModeConflictError(
                            "imported event payload hash mismatch"
                        )
                    payload = self._remap_import_payload(
                        payload,
                        run_map=run_map,
                        audit_map=audit_map,
                        candidate_map=candidate_maps[source_run],
                        review_map=review_map,
                        finding_map=finding_map,
                        repair_map=repair_map,
                        assessment_map=assessment_map,
                        decision_map=decision_map,
                        artifact_map=artifact_map,
                        version_map=version_map,
                        cell_map=cell_map,
                        group_map=group_map,
                    )
                    if event_type == "auto_run_terminal":
                        payload["status"] = "unverified_import"
                        payload["terminal_reason"] = "quarantined_import"
                    run_id = identity[0]
                    event_cursor = int(item["event_cursor"])
                    if event_type in {
                        "auto_audit_started",
                        "auto_audit_completed",
                    }:
                        audit_id = _text("audit_id", payload.get("audit_id"))
                        pair = audit_event_index.setdefault((run_id, audit_id), {})
                        slot = (
                            "started"
                            if event_type == "auto_audit_started"
                            else "completed"
                        )
                        if slot in pair:
                            raise AutoModeConflictError(
                                "duplicate imported Auto Mode audit event"
                            )
                        pair[slot] = (dict(payload), event_cursor)
                    elif event_type in {"repair_started", "repair_completed"}:
                        repair_id = _text("repair_run_id", payload.get("repair_run_id"))
                        pair = repair_event_index.setdefault((run_id, repair_id), {})
                        if (
                            event_type == "repair_started"
                            and payload.get("phase") == "execution_group_bound"
                        ):
                            pair.setdefault("bindings", []).append(
                                (dict(payload), event_cursor)
                            )
                        else:
                            slot = (
                                "started"
                                if event_type == "repair_started"
                                else "completed"
                            )
                            if slot in pair:
                                raise AutoModeConflictError(
                                    "duplicate imported Auto Mode repair event"
                                )
                            pair[slot] = (dict(payload), event_cursor)
                    sequence_by_run[run_id] = sequence_by_run.get(run_id, 0) + 1
                    payload_json = _canonical(payload)
                    self._connection.execute(
                        "INSERT INTO auto_mode_events("
                        "event_id,root_frame_id,event_cursor,run_id,branch_id,turn_id,"
                        "execution_id,sequence,idempotency_key,type,request_sha256,"
                        "payload_json,payload_sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            event_map[str(item["event_id"])],
                            root_frame_id,
                            event_cursor,
                            run_id,
                            identity[2],
                            identity[3],
                            identity[4],
                            sequence_by_run[run_id],
                            f"import:{event_map[str(item['event_id'])]}",
                            event_type,
                            _digest({"source_event_id": item["event_id"]}),
                            payload_json,
                            hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
                            timestamp,
                        ),
                    )
                self._assert_imported_run_graph_locked(tuple(run_map.values()))
                self._import_review_rows_locked(
                    reviews,
                    findings,
                    mapped_identity,
                    review_map,
                    finding_map,
                    audit_map,
                    candidate_maps,
                    audit_event_index,
                    artifact_map,
                    version_map,
                    cell_map,
                    timestamp,
                )
                self._import_repair_rows_locked(
                    repairs,
                    mapped_identity,
                    repair_map,
                    finding_map,
                    review_map,
                    version_map,
                    group_map,
                    repair_event_index,
                    timestamp,
                )
                self._import_permission_rows_locked(
                    permissions,
                    mapped_identity,
                    assessment_map,
                    audit_map,
                    decision_map,
                    audit_event_index,
                    timestamp,
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return self.export_projection(root_frame_id)

    # -------------------------------------------------------------- internal helpers
    def _assert_imported_run_graph_locked(self, run_ids: Sequence[str]) -> None:
        """Validate the remapped event graph before any quarantined commit.

        Package preflight is defense in depth, not a prerequisite of this
        public Store boundary.  This reducer rejects phase inversions,
        abandoned active owners, mutable candidate identities, and row/event
        truth splits after remapping but before the transaction can commit.
        """

        for run_id in run_ids:
            run = self._run_locked(run_id)
            rows = self._connection.execute(
                "SELECT * FROM auto_mode_events WHERE run_id=? ORDER BY sequence",
                (run_id,),
            ).fetchall()
            if not rows:
                raise AutoModeConflictError("imported Auto Mode run has no events")
            phase: str | None = None
            terminal_seen = False
            open_audits: set[str] = set()
            current: dict[str, Any] | None = None
            bindings: dict[str, dict[str, Any]] = {}
            for expected_sequence, row in enumerate(rows, start=1):
                event = self._decode_event(row)
                if int(event["sequence"]) != expected_sequence:
                    raise AutoModeConflictError(
                        "imported Auto Mode event sequence is not contiguous"
                    )
                event_type = str(event["type"])
                payload = event["payload"]
                if terminal_seen:
                    raise AutoModeConflictError(
                        "imported Auto Mode run has events after terminal"
                    )
                if phase is None:
                    if event_type != "auto_run_started":
                        raise AutoModeConflictError(
                            "imported Auto Mode run does not begin with its start event"
                        )
                    if payload.get("mode") != run["mode"] or payload.get(
                        "status"
                    ) not in {None, "running"}:
                        raise AutoModeConflictError(
                            "imported Auto Mode start disagrees with its owner"
                        )
                    phase = "running"
                    continue
                if event_type == "auto_run_started":
                    raise AutoModeConflictError(
                        "imported Auto Mode run has duplicate start events"
                    )
                if event_type in {"auto_audit_started", "auto_audit_completed"}:
                    audit_id = _text("audit_id", payload.get("audit_id"))
                    if event_type == "auto_audit_started":
                        if audit_id in open_audits:
                            raise AutoModeConflictError(
                                "imported Auto Mode audit starts more than once"
                            )
                        open_audits.add(audit_id)
                    elif audit_id not in open_audits:
                        raise AutoModeConflictError(
                            "imported Auto Mode audit completion has no start"
                        )
                    else:
                        open_audits.remove(audit_id)
                if event_type == "candidate_ready":
                    if phase not in {"running", "candidate"}:
                        raise AutoModeConflictError(
                            "imported Auto Mode candidate is out of phase"
                        )
                    if payload.get("status") not in {None, "candidate"}:
                        raise AutoModeConflictError(
                            "imported Auto Mode candidate status is invalid"
                        )
                    candidate_id = _text("candidate_id", payload.get("candidate_id"))
                    binding = {
                        "candidate_id": candidate_id,
                        "candidate_snapshot_sha256": _sha(
                            "candidate_snapshot_sha256",
                            payload.get("candidate_snapshot_sha256"),
                        ),
                        "evidence_snapshot_sha256": _sha(
                            "evidence_snapshot_sha256",
                            payload.get("evidence_snapshot_sha256"),
                        ),
                        "artifact_set_sha256": _sha(
                            "artifact_set_sha256",
                            payload.get("artifact_set_sha256"),
                            optional=True,
                        ),
                        "candidate_artifact_ids": _string_list(
                            "candidate_artifact_ids",
                            payload.get("candidate_artifact_ids") or [],
                        ),
                        "candidate_version_ids": _string_list(
                            "candidate_version_ids",
                            payload.get("candidate_version_ids") or [],
                        ),
                    }
                    previous = bindings.get(candidate_id)
                    if previous is not None and previous != binding:
                        raise AutoModeConflictError(
                            "imported candidate identity changes its immutable binding"
                        )
                    bindings[candidate_id] = binding
                    current = binding
                    phase = "candidate"
                elif (
                    event_type == "auto_audit_started"
                    and payload.get("subject_kind") == "result_review"
                ):
                    if phase != "candidate":
                        raise AutoModeConflictError(
                            "imported result review starts out of phase"
                        )
                    phase = "reviewing"
                elif (
                    event_type == "auto_audit_completed"
                    and payload.get("subject_kind") == "result_review"
                ):
                    if phase != "reviewing":
                        raise AutoModeConflictError(
                            "imported result review completes out of phase"
                        )
                    phase = "candidate"
                elif event_type == "repair_started":
                    if payload.get("phase") == "execution_group_bound":
                        if phase != "repairing":
                            raise AutoModeConflictError(
                                "imported repair binding is out of phase"
                            )
                        _text(
                            "action_group_id",
                            payload.get("action_group_id"),
                            maximum=1024,
                        )
                        if payload.get("status") != "started":
                            raise AutoModeConflictError(
                                "imported repair binding status is invalid"
                            )
                        continue
                    if phase != "candidate" or current is None:
                        raise AutoModeConflictError(
                            "imported repair starts out of phase"
                        )
                    finding_ids = _string_list(
                        "finding_ids", payload.get("finding_ids") or []
                    )
                    if not finding_ids:
                        raise AutoModeConflictError(
                            "imported repair must bind at least one finding"
                        )
                    if (
                        _string_list(
                            "before_version_ids",
                            payload.get("before_version_ids") or [],
                        )
                        != current["candidate_version_ids"]
                    ):
                        raise AutoModeConflictError(
                            "imported repair does not bind current candidate versions"
                        )
                    phase = "repairing"
                elif event_type == "repair_completed":
                    if phase != "repairing":
                        raise AutoModeConflictError(
                            "imported repair completes out of phase"
                        )
                    repair_status = payload.get("status")
                    if repair_status not in {
                        "completed",
                        "failed",
                        "outcome_unknown",
                    }:
                        raise AutoModeConflictError(
                            "imported repair completion status is invalid"
                        )
                    if repair_status == "completed":
                        current = None
                        phase = "running"
                    else:
                        phase = "candidate"
                elif event_type == "auto_run_terminal":
                    if phase in {"reviewing", "repairing"} or open_audits:
                        raise AutoModeConflictError(
                            "imported terminal event strands an active phase"
                        )
                    if payload.get("status") not in _TERMINAL_STATUSES:
                        raise AutoModeConflictError(
                            "imported terminal status is invalid"
                        )
                    terminal_seen = True
                    phase = str(payload["status"])
                candidate_id = payload.get("candidate_id")
                if candidate_id is not None and (
                    current is None or candidate_id != current["candidate_id"]
                ):
                    raise AutoModeConflictError(
                        "imported event does not bind the current candidate"
                    )
            expected = current or {
                "candidate_id": None,
                "candidate_snapshot_sha256": None,
                "evidence_snapshot_sha256": None,
                "artifact_set_sha256": None,
                "candidate_artifact_ids": [],
                "candidate_version_ids": [],
            }
            actual = {
                "candidate_id": run["candidate_id"],
                "candidate_snapshot_sha256": run["candidate_snapshot_sha256"],
                "evidence_snapshot_sha256": run["evidence_snapshot_sha256"],
                "artifact_set_sha256": run["artifact_set_sha256"],
                "candidate_artifact_ids": _load(run["candidate_artifact_ids_json"], []),
                "candidate_version_ids": _load(run["candidate_version_ids_json"], []),
            }
            if actual != expected:
                raise AutoModeConflictError(
                    "imported Auto Mode run candidate disagrees with its events"
                )

    @staticmethod
    def _unique_ids(
        records: Sequence[Mapping[str, Any]], key: str, label: str
    ) -> list[str]:
        values: list[str] = []
        seen: set[str] = set()
        for record in records:
            if not isinstance(record, Mapping):
                raise ValueError(f"invalid imported Auto Mode {label}")
            value = _text(key, record.get(key))
            if value in seen:
                raise AutoModeConflictError(
                    f"duplicate imported Auto Mode {label} identity"
                )
            seen.add(value)
            values.append(value)
        return values

    @staticmethod
    def _mapped_import_list(
        name: str, value: Any, mapping: Mapping[str, str]
    ) -> list[str]:
        result: list[str] = []
        for source_id in _string_list(name, value or []):
            target_id = mapping.get(source_id)
            if target_id is None:
                raise ValueError(
                    f"imported Auto Mode {name} references an unmapped identity"
                )
            result.append(_text(name, target_id, maximum=1024))
        return result

    @classmethod
    def _remap_import_payload(
        cls,
        source: Mapping[str, Any],
        *,
        run_map: Mapping[str, str],
        audit_map: Mapping[str, str],
        candidate_map: Mapping[str, str],
        review_map: Mapping[str, str],
        finding_map: Mapping[str, str],
        repair_map: Mapping[str, str],
        assessment_map: Mapping[str, str],
        decision_map: Mapping[str, str],
        artifact_map: Mapping[str, str],
        version_map: Mapping[str, str],
        cell_map: Mapping[str, str],
        group_map: Mapping[str, str],
    ) -> dict[str, Any]:
        """Reduce an imported event to the public vocabulary and remap IDs.

        Imported event dictionaries are attacker-controlled even after a
        package preflight.  This second closed projection prevents prompts,
        permission payloads, reusable authorization, and hidden rationale from
        becoming durable merely because a caller bypassed the package layer.
        """

        if not isinstance(source, Mapping):
            raise ValueError("invalid imported Auto Mode event payload")
        text_fields = {
            "mode",
            "status",
            "terminal_reason",
            "stop_reason",
            "reason_code",
            "subject_kind",
            "subject_entity_kind",
            "verdict",
            "decision",
            "outcome",
            "risk",
            "failure_kind",
            "policy_version",
            "model_profile_id",
            "model_fingerprint",
            "public_summary",
            "phase",
        }
        result: dict[str, Any] = {}
        for key in text_fields:
            if key not in source or source[key] is None:
                continue
            maximum = _MAX_TEXT if key == "public_summary" else 1024
            result[key] = _text(key, source[key], maximum=maximum)
        if result.get("mode") is not None and result["mode"] not in _RUN_MODES:
            raise ValueError("invalid imported Auto Mode mode")
        if "retryable" in source and source["retryable"] is not None:
            if type(source["retryable"]) is not bool:
                raise ValueError("retryable must be a boolean")
            result["retryable"] = source["retryable"]
        for key, minimum in (
            ("round", 0),
            ("attempt", 1),
            ("finding_count", 0),
            ("model_profile_revision", 1),
            ("profile_revision", 1),
        ):
            if key in source and source[key] is not None:
                result[key] = _integer(key, source[key], minimum=minimum)
        if "counts" in source and source["counts"] is not None:
            counts = source["counts"]
            if not isinstance(counts, Mapping):
                raise ValueError("counts must be an object")
            cls._assert_no_private_import_fields(counts)
            normalized_counts: dict[str, int] = {}
            for key, value in counts.items():
                key = _text("counts key", key, maximum=64)
                if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,63}", key) is None:
                    raise ValueError("invalid imported count name")
                normalized_counts[key] = _integer(f"counts.{key}", value)
            result["counts"] = normalized_counts
        for key in (
            "candidate_snapshot_sha256",
            "evidence_snapshot_sha256",
            "artifact_set_sha256",
        ):
            if source.get(key) is not None:
                result[key] = _sha(key, source[key])

        single_maps = {
            "run_id": run_map,
            "audit_id": audit_map,
            "candidate_id": candidate_map,
            "review_run_id": review_map,
            "finding_id": finding_map,
            "repair_run_id": repair_map,
            "assessment_id": assessment_map,
            "decision_id": decision_map,
            "artifact_id": artifact_map,
            "version_id": version_map,
            "cell_id": cell_map,
            "producing_cell_id": cell_map,
            "execution_group_id": group_map,
            "action_group_id": group_map,
        }
        for key, mapping in single_maps.items():
            if source.get(key) is None:
                continue
            source_id = _text(key, source[key], maximum=1024)
            target_id = mapping.get(source_id)
            if target_id is None:
                raise ValueError(
                    f"imported Auto Mode {key} references an unmapped identity"
                )
            result[key] = _text(key, target_id, maximum=1024)

        list_maps = {
            "finding_ids": finding_map,
            "artifact_ids": artifact_map,
            "candidate_artifact_ids": artifact_map,
            "version_ids": version_map,
            "candidate_version_ids": version_map,
            "before_version_ids": version_map,
            "after_version_ids": version_map,
            "cell_ids": cell_map,
            "execution_group_ids": group_map,
            "action_group_ids": group_map,
        }
        for key, mapping in list_maps.items():
            if key in source:
                result[key] = cls._mapped_import_list(key, source[key], mapping)

        if source.get("verification_review_run_id") is not None:
            source_id = _text(
                "verification_review_run_id",
                source["verification_review_run_id"],
            )
            if source_id not in review_map:
                raise ValueError(
                    "imported repair verification references an unmapped review"
                )
            result["verification_review_run_id"] = review_map[source_id]

        subject_id = source.get("subject_entity_id")
        if subject_id is not None:
            subject_id = _text("subject_entity_id", subject_id, maximum=1024)
            subject_map = (
                candidate_map
                if source.get("subject_kind") == "result_review"
                else decision_map
            )
            if subject_id not in subject_map:
                raise ValueError("imported audit subject identity is unmapped")
            result["subject_entity_id"] = subject_map[subject_id]

        if source.get("action_digest") is not None:
            result["action_digest"] = _sha("action_digest", source.get("action_digest"))
        if source.get("audit_request_digest") is not None:
            source_digest = str(
                _sha("audit_request_digest", source.get("audit_request_digest"))
            )
            mapped_audit = result.get("audit_id")
            if not isinstance(mapped_audit, str):
                raise ValueError("imported audit digest has no mapped audit identity")
            result["audit_request_digest"] = _digest(
                {
                    "trust_state": "quarantined_import",
                    "audit_id": mapped_audit,
                    "source_audit_request_digest": source_digest,
                }
            )
        if source.get("assessment_digest") is not None:
            source_digest = str(
                _sha("assessment_digest", source.get("assessment_digest"))
            )
            mapped_audit = result.get("audit_id")
            if not isinstance(mapped_audit, str):
                raise ValueError(
                    "imported assessment digest has no mapped audit identity"
                )
            result["assessment_digest"] = _digest(
                {
                    "trust_state": "quarantined_import",
                    "audit_id": mapped_audit,
                    "source_assessment_digest": source_digest,
                }
            )
        _canonical(result)
        return result

    @staticmethod
    def _audit_payloads_from_index(
        index: Mapping[tuple[str, str], Mapping[str, Any]],
        run_id: str,
        audit_id: str,
        subject_kind: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        expected_entity = _SUBJECT_ENTITY[subject_kind]
        pair = index.get((run_id, audit_id))
        if pair is None or "started" not in pair:
            raise AutoModeConflictError("imported audit event pair is incomplete")
        started_value = pair["started"]
        completed_value = pair.get("completed")
        started = dict(started_value[0])
        completed = dict(completed_value[0]) if completed_value is not None else None
        if (
            started.get("subject_kind") != subject_kind
            or started.get("subject_entity_kind") != expected_entity
            or (
                completed is not None
                and (
                    completed.get("subject_kind") != subject_kind
                    or completed.get("subject_entity_kind") != expected_entity
                    or completed.get("subject_entity_id")
                    != started.get("subject_entity_id")
                )
            )
        ):
            raise AutoModeConflictError("imported audit subject binding mismatch")
        if completed is not None and completed.get(
            "audit_request_digest"
        ) != started.get("audit_request_digest"):
            raise AutoModeConflictError("imported audit digest changed across its pair")
        if completed_value is not None and int(started_value[1]) >= int(
            completed_value[1]
        ):
            raise AutoModeConflictError("imported audit completion precedes its start")
        _sha("audit_request_digest", started.get("audit_request_digest"))
        if completed is not None:
            _sha("assessment_digest", completed.get("assessment_digest"))
        return started, completed

    @staticmethod
    def _repair_payloads_from_index(
        index: Mapping[tuple[str, str], Mapping[str, Any]],
        run_id: str,
        repair_run_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, Any]]]:
        pair = index.get((run_id, repair_run_id))
        if pair is None or "started" not in pair:
            raise AutoModeConflictError("imported repair event pair is incomplete")
        started_value = pair["started"]
        completed_value = pair.get("completed")
        raw_bindings = pair.get("bindings") or []
        if not isinstance(raw_bindings, list):
            raise AutoModeConflictError("imported repair binding index is invalid")
        binding_values: list[dict[str, Any]] = []
        last_cursor = int(started_value[1])
        for value in raw_bindings:
            if (
                not isinstance(value, tuple)
                or len(value) != 2
                or not isinstance(value[0], Mapping)
            ):
                raise AutoModeConflictError("imported repair binding is invalid")
            cursor = int(value[1])
            if cursor <= last_cursor:
                raise AutoModeConflictError(
                    "imported repair binding precedes its start"
                )
            last_cursor = cursor
            binding_values.append(dict(value[0]))
        if completed_value is not None and int(started_value[1]) >= int(
            completed_value[1]
        ):
            raise AutoModeConflictError("imported repair completion precedes its start")
        if completed_value is not None and any(
            int(value[1]) >= int(completed_value[1]) for value in raw_bindings
        ):
            raise AutoModeConflictError(
                "imported repair binding does not precede completion"
            )
        return (
            dict(started_value[0]),
            dict(completed_value[0]) if completed_value is not None else None,
            binding_values,
        )

    def _import_review_rows_locked(
        self,
        reviews: Sequence[Mapping[str, Any]],
        findings: Sequence[Mapping[str, Any]],
        mapped_identity: Callable[[Mapping[str, Any]], tuple[str, str, str, str, str]],
        review_map: Mapping[str, str],
        finding_map: Mapping[str, str],
        audit_map: Mapping[str, str],
        candidate_maps: Mapping[str, Mapping[str, str]],
        audit_event_index: Mapping[tuple[str, str], Mapping[str, Any]],
        artifact_map: Mapping[str, str],
        version_map: Mapping[str, str],
        cell_map: Mapping[str, str],
        timestamp: int,
    ) -> None:
        finding_count_by_review: dict[str, int] = {}
        for finding in findings:
            source_owner = _text("review_run_id", finding.get("review_run_id"))
            finding_count_by_review[source_owner] = (
                finding_count_by_review.get(source_owner, 0) + 1
            )
        for item in reviews:
            identity = mapped_identity(item)
            source_run_id = _text("run_id", item.get("run_id"))
            candidate_map = candidate_maps.get(source_run_id)
            source_review_id = _text("review_run_id", item.get("review_run_id"))
            source_audit_id = _text("audit_id", item.get("audit_id"))
            source_candidate_id = _text("candidate_id", item.get("candidate_id"))
            if (
                candidate_map is None
                or source_review_id not in review_map
                or source_audit_id not in audit_map
                or source_candidate_id not in candidate_map
            ):
                raise ValueError("imported review identity is unmapped")
            review_id = review_map[source_review_id]
            audit_id = audit_map[source_audit_id]
            candidate_id = candidate_map[source_candidate_id]
            started, completed = self._audit_payloads_from_index(
                audit_event_index, identity[0], audit_id, "result_review"
            )
            if (
                started.get("review_run_id") != review_id
                or started.get("subject_entity_id") != candidate_id
            ):
                raise AutoModeConflictError("imported review event owner mismatch")
            if started.get("candidate_id") not in {None, candidate_id}:
                raise AutoModeConflictError("imported review candidate mismatch")
            candidate_sha = str(
                _sha(
                    "candidate_snapshot_sha256",
                    item.get("candidate_snapshot_sha256"),
                )
            )
            evidence_sha = str(
                _sha(
                    "evidence_snapshot_sha256",
                    item.get("evidence_snapshot_sha256"),
                )
            )
            reviewer = {
                "trust_state": "quarantined_import",
                "profile_id": _text(
                    "model_profile_id", item.get("model_profile_id"), maximum=1024
                ),
                "profile_revision": _integer(
                    "model_profile_revision",
                    item.get("model_profile_revision"),
                    minimum=1,
                ),
                "model_fingerprint": _text(
                    "model_fingerprint",
                    item.get("model_fingerprint"),
                    maximum=1024,
                ),
            }
            round_index = _integer("round", item.get("round"))
            attempt = _integer("attempt", item.get("attempt"), minimum=1)
            source_audit_digest = str(
                _sha(
                    "audit_request_digest",
                    item.get("audit_request_digest"),
                )
            )
            mapped_audit_digest = _digest(
                {
                    "trust_state": "quarantined_import",
                    "audit_id": audit_id,
                    "source_audit_request_digest": source_audit_digest,
                }
            )
            if (
                started.get("candidate_id") != candidate_id
                or started.get("candidate_snapshot_sha256") != candidate_sha
                or started.get("evidence_snapshot_sha256") != evidence_sha
                or started.get("round") != round_index
                or started.get("attempt") != attempt
                or started.get("model_profile_id") != reviewer["profile_id"]
                or started.get("model_profile_revision") != reviewer["profile_revision"]
                or started.get("model_fingerprint") != reviewer["model_fingerprint"]
                or started.get("audit_request_digest") != mapped_audit_digest
                or started.get("status") != "started"
            ):
                raise AutoModeConflictError(
                    "imported review start disagrees with its owner"
                )
            verdict = item.get("verdict")
            if verdict is not None:
                verdict = _text("verdict", verdict, maximum=256).lower()
                if verdict not in _REVIEW_VERDICTS:
                    raise ValueError("invalid imported review verdict")
            source_status = _text("status", item.get("status"))
            if source_status not in {
                "started",
                "completed",
                "unavailable",
                "failed",
                "unverified_import",
            }:
                raise ValueError("invalid imported review status")
            public_summary = item.get("public_summary")
            if public_summary is not None:
                public_summary = _text(
                    "public_summary", public_summary, maximum=_MAX_TEXT
                )
            if completed is None:
                if (
                    source_status not in {"started", "unverified_import"}
                    or any(value is not None for value in (verdict, public_summary))
                    or finding_count_by_review.get(source_review_id, 0)
                ):
                    raise AutoModeConflictError(
                        "started imported review claims a completion"
                    )
            elif (
                completed.get("status")
                not in {
                    "completed",
                    "unavailable",
                    "failed",
                }
                or source_status
                not in {
                    str(completed.get("status")),
                    "unverified_import",
                }
                or verdict is None
                or completed.get("review_run_id") != review_id
                or completed.get("candidate_id") != candidate_id
                or completed.get("subject_entity_id") != candidate_id
                or completed.get("attempt") != attempt
                or completed.get("verdict") != verdict
                or completed.get("finding_count")
                != finding_count_by_review.get(source_review_id, 0)
                or completed.get("public_summary") != public_summary
            ):
                raise AutoModeConflictError(
                    "imported review completion disagrees with its owner"
                )
            if completed is not None:
                source_assessment_digest = str(
                    _sha(
                        "assessment_digest",
                        item.get("assessment_digest"),
                    )
                )
                mapped_assessment_digest = _digest(
                    {
                        "trust_state": "quarantined_import",
                        "audit_id": audit_id,
                        "source_assessment_digest": source_assessment_digest,
                    }
                )
                if completed.get("assessment_digest") != mapped_assessment_digest:
                    raise AutoModeConflictError(
                        "imported review assessment digest disagrees with its owner"
                    )
            completion_key = f"import:{review_id}:complete" if completed else None
            self._connection.execute(
                "INSERT INTO review_runs("
                "review_run_id,audit_id,run_id,root_frame_id,branch_id,turn_id,"
                "execution_id,start_idempotency_key,start_request_sha256,"
                "completion_idempotency_key,completion_request_sha256,candidate_id,"
                "candidate_snapshot_sha256,evidence_snapshot_json,"
                "evidence_snapshot_sha256,round_index,attempt,reviewer_json,"
                "audit_request_digest,status,verdict,assessment_json,"
                "assessment_digest,usage_json,public_summary,started_at,completed_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    review_id,
                    audit_id,
                    *identity,
                    f"import:{review_id}:start",
                    _digest({"imported_review": source_review_id, "phase": "start"}),
                    completion_key,
                    (
                        _digest(
                            {"imported_review": source_review_id, "phase": "complete"}
                        )
                        if completed
                        else None
                    ),
                    candidate_id,
                    candidate_sha,
                    _canonical(
                        {
                            "trust_state": "quarantined_import",
                            "source_evidence_snapshot_sha256": evidence_sha,
                        }
                    ),
                    evidence_sha,
                    round_index,
                    attempt,
                    _canonical(reviewer),
                    started["audit_request_digest"],
                    "unverified_import",
                    verdict,
                    (
                        _canonical({"trust_state": "quarantined_import"})
                        if completed
                        else None
                    ),
                    completed.get("assessment_digest") if completed else None,
                    _canonical({}),
                    public_summary,
                    timestamp,
                    timestamp if completed else None,
                ),
            )

        finding_ordinals: dict[str, int] = {}
        for item in findings:
            identity = mapped_identity(item)
            source_run_id = _text("run_id", item.get("run_id"))
            candidate_map = candidate_maps.get(source_run_id)
            source_finding_id = _text("finding_id", item.get("finding_id"))
            source_review_id = _text("review_run_id", item.get("review_run_id"))
            source_candidate_id = _text("candidate_id", item.get("candidate_id"))
            if (
                candidate_map is None
                or source_finding_id not in finding_map
                or source_review_id not in review_map
                or source_candidate_id not in candidate_map
            ):
                raise ValueError("imported finding identity is unmapped")
            owner = self._connection.execute(
                "SELECT run_id,candidate_id FROM review_runs WHERE review_run_id=?",
                (review_map[source_review_id],),
            ).fetchone()
            candidate_id = candidate_map[source_candidate_id]
            if (
                owner is None
                or owner["run_id"] != identity[0]
                or owner["candidate_id"] != candidate_id
            ):
                raise AutoModeConflictError("imported finding owner mismatch")
            finding_ordinal = finding_ordinals.get(source_review_id, 0)
            finding_ordinals[source_review_id] = finding_ordinal + 1
            normalized = self._normalize_finding(item)
            artifacts = self._mapped_import_list(
                "artifact_ids", normalized["artifact_ids"], artifact_map
            )
            versions = self._mapped_import_list(
                "version_ids", normalized["version_ids"], version_map
            )
            cells = self._mapped_import_list(
                "cell_ids", normalized["cell_ids"], cell_map
            )
            evidence_refs = [
                cell_map.get(reference, reference)
                for reference in normalized["evidence_refs"]
            ]
            self._connection.execute(
                "INSERT INTO review_findings("
                "finding_id,review_run_id,run_id,root_frame_id,branch_id,turn_id,"
                "execution_id,candidate_id,finding_ordinal,fingerprint,severity,category,claim,"
                "evidence_refs_json,artifact_ids_json,version_ids_json,cell_ids_json,"
                "status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    finding_map[source_finding_id],
                    review_map[source_review_id],
                    *identity,
                    candidate_id,
                    finding_ordinal,
                    normalized["fingerprint"],
                    normalized["severity"],
                    normalized["category"],
                    normalized["claim"],
                    _canonical(evidence_refs),
                    _canonical(artifacts),
                    _canonical(versions),
                    _canonical(cells),
                    normalized["status"],
                    timestamp,
                    timestamp,
                ),
            )

    def _import_repair_rows_locked(
        self,
        repairs: Sequence[Mapping[str, Any]],
        mapped_identity: Callable[[Mapping[str, Any]], tuple[str, str, str, str, str]],
        repair_map: Mapping[str, str],
        finding_map: Mapping[str, str],
        review_map: Mapping[str, str],
        version_map: Mapping[str, str],
        group_map: Mapping[str, str],
        repair_event_index: Mapping[tuple[str, str], Mapping[str, Any]],
        timestamp: int,
    ) -> None:
        for item in repairs:
            identity = mapped_identity(item)
            source_repair_id = _text("repair_run_id", item.get("repair_run_id"))
            if source_repair_id not in repair_map:
                raise ValueError("imported repair identity is unmapped")
            repair_id = repair_map[source_repair_id]
            source_findings = _string_list("finding_ids", item.get("finding_ids") or [])
            if not source_findings:
                raise AutoModeConflictError(
                    "imported repair must bind at least one finding"
                )
            if any(value not in finding_map for value in source_findings):
                raise ValueError("imported repair references an unmapped finding")
            findings = [finding_map[value] for value in source_findings]
            for finding_id in findings:
                owner = self._connection.execute(
                    "SELECT run_id FROM review_findings WHERE finding_id=?",
                    (finding_id,),
                ).fetchone()
                if owner is None or owner["run_id"] != identity[0]:
                    raise AutoModeConflictError(
                        "imported repair finding owner mismatch"
                    )
            before = self._mapped_import_list(
                "before_version_ids", item.get("before_version_ids") or [], version_map
            )
            after = self._mapped_import_list(
                "after_version_ids", item.get("after_version_ids") or [], version_map
            )
            groups = self._mapped_import_list(
                "execution_group_ids",
                item.get("execution_group_ids") or [],
                group_map,
            )
            for group_id in groups:
                group = self._connection.execute(
                    "SELECT root_frame_id,branch_id,turn_id FROM action_groups "
                    "WHERE group_id=?",
                    (group_id,),
                ).fetchone()
                if (
                    group is None
                    or group["root_frame_id"] != identity[1]
                    or group["branch_id"] != identity[2]
                    or group["turn_id"] != identity[3]
                ):
                    raise AutoModeConflictError(
                        "imported repair execution group owner mismatch"
                    )
            source_verification = item.get("verification_review_run_id")
            if source_verification is not None:
                raise AutoModeConflictError(
                    "Stage 2 import cannot trust a repair verification link"
                )
            started, completed, binding_payloads = self._repair_payloads_from_index(
                repair_event_index, identity[0], repair_id
            )
            if (
                list(started.get("finding_ids") or []) != findings
                or list(started.get("before_version_ids") or []) != before
                or started.get("status") != "started"
            ):
                raise AutoModeConflictError(
                    "imported repair start disagrees with its owner"
                )
            binding_groups = [
                _text(
                    "action_group_id",
                    binding.get("action_group_id"),
                    maximum=1024,
                )
                for binding in binding_payloads
            ]
            if binding_groups != groups or any(
                binding.get("repair_run_id") != repair_id
                or binding.get("phase") != "execution_group_bound"
                or binding.get("status") != "started"
                for binding in binding_payloads
            ):
                raise AutoModeConflictError(
                    "imported repair bindings disagree with their owner"
                )
            source_status = _text("status", item.get("status"))
            if completed is None:
                if source_status not in {"started", "unverified_import"}:
                    raise AutoModeConflictError(
                        "imported repair claims a missing completion"
                    )
                if after:
                    raise AutoModeConflictError(
                        "started imported repair has terminal version output"
                    )
            else:
                if completed.get("status") not in {
                    "completed",
                    "failed",
                    "outcome_unknown",
                }:
                    raise AutoModeConflictError(
                        "imported repair completion status is invalid"
                    )
                if completed.get("status") == "completed" and not groups:
                    raise AutoModeConflictError(
                        "completed imported repair requires an execution ledger"
                    )
                if source_status not in {
                    str(completed.get("status")),
                    "unverified_import",
                }:
                    raise AutoModeConflictError(
                        "imported repair completion disagrees with its owner"
                    )
                if (
                    list(completed.get("after_version_ids") or []) != after
                    or list(completed.get("execution_group_ids") or []) != groups
                    or completed.get("verification_review_run_id") is not None
                ):
                    raise AutoModeConflictError(
                        "imported repair output disagrees with its event"
                    )
            self._connection.execute(
                "INSERT INTO repair_runs("
                "repair_run_id,run_id,root_frame_id,branch_id,turn_id,execution_id,"
                "start_idempotency_key,start_request_sha256,"
                "completion_idempotency_key,completion_request_sha256,"
                "finding_ids_json,before_version_ids_json,after_version_ids_json,"
                "execution_group_ids_json,verification_review_run_id,checkpoint_id,"
                "status,started_at,completed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    repair_id,
                    *identity,
                    f"import:{repair_id}:start",
                    _digest({"imported_repair": source_repair_id, "phase": "start"}),
                    f"import:{repair_id}:complete" if completed else None,
                    (
                        _digest(
                            {
                                "imported_repair": source_repair_id,
                                "phase": "complete",
                            }
                        )
                        if completed
                        else None
                    ),
                    _canonical(findings),
                    _canonical(before),
                    _canonical(after),
                    _canonical(groups),
                    None,
                    None,
                    "unverified_import",
                    timestamp,
                    timestamp if completed else None,
                ),
            )
            for binding_ordinal, group_id in enumerate(groups):
                action_group = self._connection.execute(
                    "SELECT kind FROM action_groups WHERE group_id=?", (group_id,)
                ).fetchone()
                if (
                    action_group is None
                    or not isinstance(action_group["kind"], str)
                    or not action_group["kind"]
                ):
                    raise AutoModeConflictError(
                        "imported repair action group is unavailable"
                    )
                event_count, ledger_sha256 = self._repair_ledger_snapshot_locked(
                    group_id,
                    completion_status=None,
                )
                self._connection.execute(
                    "INSERT INTO repair_execution_groups("
                    "repair_run_id,action_group_id,binding_ordinal,action_group_kind,run_id,root_frame_id,branch_id,"
                    "turn_id,execution_id,idempotency_key,request_sha256,bound_at,"
                    "ledger_event_count,ledger_sha256,sealed_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        repair_id,
                        group_id,
                        binding_ordinal,
                        action_group["kind"],
                        *identity,
                        f"import:{repair_id}:group:{group_id}",
                        _digest(
                            {
                                "imported_repair": source_repair_id,
                                "action_group_id": group_id,
                            }
                        ),
                        timestamp,
                        event_count,
                        ledger_sha256,
                        timestamp,
                    ),
                )

    def _import_permission_rows_locked(
        self,
        permissions: Sequence[Mapping[str, Any]],
        mapped_identity: Callable[[Mapping[str, Any]], tuple[str, str, str, str, str]],
        assessment_map: Mapping[str, str],
        audit_map: Mapping[str, str],
        decision_map: Mapping[str, str],
        audit_event_index: Mapping[tuple[str, str], Mapping[str, Any]],
        timestamp: int,
    ) -> None:
        for item in permissions:
            identity = mapped_identity(item)
            source_assessment = _text("assessment_id", item.get("assessment_id"))
            source_audit = _text("audit_id", item.get("audit_id"))
            source_decision = _text("decision_id", item.get("decision_id"))
            if (
                source_assessment not in assessment_map
                or source_audit not in audit_map
                or source_decision not in decision_map
            ):
                raise ValueError("imported permission assessment identity is unmapped")
            assessment_id = assessment_map[source_assessment]
            audit_id = audit_map[source_audit]
            decision_id = decision_map[source_decision]
            started, completed = self._audit_payloads_from_index(
                audit_event_index, identity[0], audit_id, "permission_review"
            )
            if (
                started.get("assessment_id") != assessment_id
                or started.get("decision_id") != decision_id
                or started.get("subject_entity_id") != decision_id
            ):
                raise AutoModeConflictError(
                    "imported permission assessment event owner mismatch"
                )
            action_digest = str(_sha("action_digest", item.get("action_digest")))
            policy_version = _text(
                "policy_version", item.get("policy_version"), maximum=128
            )
            if (
                re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", policy_version)
                is None
            ):
                raise ValueError("invalid imported permission policy version")
            source_audit_digest = str(
                _sha(
                    "audit_request_digest",
                    item.get("audit_request_digest"),
                )
            )
            mapped_audit_digest = _digest(
                {
                    "trust_state": "quarantined_import",
                    "audit_id": audit_id,
                    "source_audit_request_digest": source_audit_digest,
                }
            )
            if (
                started.get("action_digest") != action_digest
                or started.get("policy_version") != policy_version
                or started.get("audit_request_digest") != mapped_audit_digest
                or started.get("status") != "started"
            ):
                raise AutoModeConflictError(
                    "imported permission action digest mismatch"
                )
            outcome = item.get("outcome")
            risk = item.get("risk")
            if outcome is not None:
                outcome = _text("outcome", outcome, maximum=256)
            if risk is not None:
                risk = _text("risk", risk, maximum=256).lower()
                if risk not in _RISK_LEVELS:
                    raise ValueError("invalid imported permission risk")
            if outcome is not None:
                outcome = str(outcome).lower()
                if outcome not in _PERMISSION_OUTCOMES:
                    raise ValueError("invalid imported permission outcome")
            source_status = _text("status", item.get("status"))
            if source_status not in {
                "started",
                "completed",
                "unavailable",
                "failed",
                "unverified_import",
            }:
                raise ValueError("invalid imported permission review status")
            public_summary = item.get("public_summary")
            if public_summary is not None:
                public_summary = _text(
                    "public_summary", public_summary, maximum=_MAX_TEXT
                )
            if completed is None:
                if source_status not in {"started", "unverified_import"} or any(
                    value is not None for value in (outcome, risk, public_summary)
                ):
                    raise AutoModeConflictError(
                        "started imported permission audit claims a completion"
                    )
            elif (
                completed.get("status")
                not in {
                    "completed",
                    "unavailable",
                    "failed",
                }
                or source_status
                not in {
                    str(completed.get("status")),
                    "unverified_import",
                }
                or outcome is None
                or risk is None
                or completed.get("assessment_id") != assessment_id
                or completed.get("decision_id") != decision_id
                or completed.get("subject_entity_id") != decision_id
                or completed.get("action_digest") != action_digest
                or completed.get("outcome") != outcome
                or completed.get("risk") != risk
                or completed.get("public_summary") != public_summary
            ):
                raise AutoModeConflictError(
                    "imported permission completion disagrees with its owner"
                )
            if completed is not None:
                source_assessment_digest = str(
                    _sha(
                        "assessment_digest",
                        item.get("assessment_digest"),
                    )
                )
                mapped_assessment_digest = _digest(
                    {
                        "trust_state": "quarantined_import",
                        "audit_id": audit_id,
                        "source_assessment_digest": source_assessment_digest,
                    }
                )
                if completed.get("assessment_digest") != mapped_assessment_digest:
                    raise AutoModeConflictError(
                        "imported permission assessment digest disagrees with its owner"
                    )
                if str(completed.get("status")) != "completed" and outcome in {
                    "allow",
                    "allow_once",
                    "allowed",
                    "shadow_allow",
                }:
                    raise AutoModeConflictError(
                        "failed imported permission assessment cannot allow"
                    )
            self._connection.execute(
                "INSERT INTO permission_review_assessments("
                "assessment_id,audit_id,run_id,root_frame_id,branch_id,turn_id,"
                "execution_id,decision_id,action_digest,policy_version,"
                "start_idempotency_key,start_request_sha256,"
                "completion_idempotency_key,completion_request_sha256,"
                "audit_request_digest,assessment_json,assessment_digest,outcome,risk,"
                "public_summary,status,started_at,completed_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    assessment_id,
                    audit_id,
                    *identity,
                    decision_id,
                    action_digest,
                    policy_version,
                    f"import:{assessment_id}:start",
                    _digest(
                        {"imported_assessment": source_assessment, "phase": "start"}
                    ),
                    f"import:{assessment_id}:complete" if completed else None,
                    (
                        _digest(
                            {
                                "imported_assessment": source_assessment,
                                "phase": "complete",
                            }
                        )
                        if completed
                        else None
                    ),
                    started["audit_request_digest"],
                    (
                        _canonical({"trust_state": "quarantined_import"})
                        if completed
                        else None
                    ),
                    completed.get("assessment_digest") if completed else None,
                    outcome,
                    risk,
                    public_summary,
                    "unverified_import",
                    timestamp,
                    timestamp if completed else None,
                ),
            )

    def _audit_public_summary(self, audit_id: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT public_summary FROM review_runs WHERE audit_id=?",
                (audit_id,),
            ).fetchone()
            if row is None:
                row = self._connection.execute(
                    "SELECT public_summary FROM permission_review_assessments "
                    "WHERE audit_id=?",
                    (audit_id,),
                ).fetchone()
        if row is None or not isinstance(row["public_summary"], str):
            return None
        return str(row["public_summary"])[:_MAX_TEXT]

    def _append_event_locked(
        self,
        run: sqlite3.Row,
        *,
        idempotency_key: str,
        event_type: str,
        request_sha256: str,
        payload: Mapping[str, Any],
        created_at: int,
        event_id: str | None = None,
        event_cursor: int | None = None,
    ) -> tuple[dict[str, Any], bool]:
        if event_type not in _EVENT_TYPES:
            raise ValueError("invalid Auto Mode event type")
        replay = self._idempotent_event_locked(
            run, idempotency_key, request_sha256, event_type
        )
        if replay is not None:
            return replay, False
        root = str(run["root_frame_id"])
        if event_cursor is None:
            event_cursor = int(
                self._connection.execute(
                    "SELECT COALESCE(MAX(event_cursor),0)+1 FROM auto_mode_events "
                    "WHERE root_frame_id=?",
                    (root,),
                ).fetchone()[0]
            )
        sequence = int(
            self._connection.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM auto_mode_events WHERE run_id=?",
                (run["run_id"],),
            ).fetchone()[0]
        )
        payload_json = _canonical(dict(payload))
        payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        event_id = event_id or f"auto-event-{uuid.uuid4().hex[:20]}"
        self._connection.execute(
            "INSERT INTO auto_mode_events("
            "event_id,root_frame_id,event_cursor,run_id,branch_id,turn_id,execution_id,"
            "sequence,idempotency_key,type,request_sha256,payload_json,payload_sha256,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event_id,
                root,
                event_cursor,
                run["run_id"],
                run["branch_id"],
                run["turn_id"],
                run["execution_id"],
                sequence,
                idempotency_key,
                event_type,
                request_sha256,
                payload_json,
                payload_sha256,
                created_at,
            ),
        )
        row = self._connection.execute(
            "SELECT * FROM auto_mode_events WHERE event_id=?", (event_id,)
        ).fetchone()
        return self._decode_event(row), True

    def _idempotent_event_locked(
        self,
        run: sqlite3.Row,
        idempotency_key: str,
        request_sha256: str,
        event_type: str,
    ) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM auto_mode_events WHERE run_id=? AND idempotency_key=?",
            (run["run_id"], idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if row["request_sha256"] != request_sha256 or row["type"] != event_type:
            raise AutoModeConflictError("Auto Mode idempotency digest mismatch")
        return self._decode_event(row)

    def _event_for_idempotency_locked(
        self,
        run_id: str,
        idempotency_key: str,
        *,
        expected_type: str,
        expected_request_sha256: str,
    ) -> dict[str, Any]:
        row = self._connection.execute(
            "SELECT * FROM auto_mode_events WHERE run_id=? AND idempotency_key=?",
            (run_id, idempotency_key),
        ).fetchone()
        if row is None:
            raise RuntimeError("Auto Mode idempotent row has no event")
        event = self._decode_event(row)
        if (
            event["type"] != expected_type
            or event["request_sha256"] != expected_request_sha256
        ):
            raise AutoModeConflictError(
                "Auto Mode idempotent event disagrees with its durable owner"
            )
        return event

    def _run_locked(self, run_id: str) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM auto_mode_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return row

    def _assert_mutable_run(self, run: sqlite3.Row) -> None:
        if run["trust_state"] != "local":
            raise PermissionError("imported Auto Mode history is inert")
        if (
            self._connection.execute(
                "SELECT 1 FROM settings WHERE key=?",
                (revert_recovery_setting_key(str(run["root_frame_id"])),),
            ).fetchone()
            is not None
        ):
            raise AutoModeConflictError(
                "Session workspace revert requires recovery before Auto Mode mutation"
            )
        if run["abandoned_at"] is not None:
            raise AutoModeConflictError(
                "Auto Mode run belongs to an abandoned branch tail and is not current at the branch head"
            )
        if run["finished_at"] is not None or run["status"] in _TERMINAL_STATUSES:
            raise AutoModeConflictError("auto run terminal is immutable")
        projected = self.project_run(
            str(run["root_frame_id"]),
            branch_id=str(run["branch_id"]),
        )
        projected_run = projected.get("run") if projected else None
        physical_tail = self._connection.execute(
            "SELECT MAX(event_cursor) FROM auto_mode_events WHERE run_id=?",
            (run["run_id"],),
        ).fetchone()[0]
        if (
            not isinstance(projected_run, Mapping)
            or projected_run.get("run_id") != run["run_id"]
            or projected.get("last_event_ordinal") != physical_tail
            or projected_run.get("source_claimed_status") is not None
        ):
            raise AutoModeConflictError(
                "Auto Mode run is not current at the branch head"
            )

    def _assert_run_replay_integrity_locked(self, run: sqlite3.Row) -> None:
        status = str(run["status"])
        if run["trust_state"] != "local":
            raise PermissionError("imported Auto Mode history is inert")
        projected = self.project_run(
            str(run["root_frame_id"]), branch_id=str(run["branch_id"])
        )
        physical_tail = self._connection.execute(
            "SELECT MAX(event_cursor) FROM auto_mode_events WHERE run_id=?",
            (run["run_id"],),
        ).fetchone()[0]
        projected_run = projected.get("run") if projected else None
        if (
            not isinstance(projected_run, Mapping)
            or projected_run.get("run_id") != run["run_id"]
            or projected.get("last_event_ordinal") != physical_tail
            or projected_run.get("source_claimed_status") is not None
        ):
            raise AutoModeConflictError(
                "Auto Mode replay does not belong to the current branch tail"
            )
        if status in _TERMINAL_STATUSES:
            self._assert_terminal_event_locked(run, expected_status=status)
            if status == "verified":
                self._assert_verified_locked(run, require_terminal=True)

    def _assert_visible_run_projection_locked(
        self,
        owner: sqlite3.Row,
        projected_run: Mapping[str, Any],
        events: Sequence[Mapping[str, Any]],
    ) -> None:
        """Validate one logical event prefix and its full-tail owner binding."""

        if not events:
            raise AutoModeConflictError("Auto Mode projection has no event proof")
        identity_fields = (
            "run_id",
            "root_frame_id",
            "branch_id",
            "turn_id",
            "execution_id",
        )
        if any(
            event.get(name) != owner[name]
            for event in events
            for name in identity_fields
        ):
            raise AutoModeConflictError("Auto Mode projection identity is invalid")
        sequences = [int(event.get("sequence") or 0) for event in events]
        cursors = [int(event.get("event_cursor") or 0) for event in events]
        if (
            sequences != list(range(1, len(events) + 1))
            or cursors != sorted(cursors)
            or len(cursors) != len(set(cursors))
        ):
            raise AutoModeConflictError("Auto Mode visible event order is invalid")
        start = self._assert_run_start_event_locked(owner)
        if events[0].get("event_id") != start["event_id"]:
            raise AutoModeConflictError("Auto Mode projection start is invalid")
        repair_visibility: dict[str, bool] = {}
        for event in events:
            if event.get("type") not in {"repair_started", "repair_completed"}:
                continue
            payload = event.get("payload")
            if not isinstance(payload, Mapping):
                raise AutoModeConflictError("Auto Mode repair event is invalid")
            repair_id = payload.get("repair_run_id")
            if not isinstance(repair_id, str) or not repair_id:
                raise AutoModeConflictError("Auto Mode repair event is invalid")
            repair_visibility.setdefault(repair_id, False)
            if event.get("type") == "repair_completed":
                repair_visibility[repair_id] = True
        for repair_id, completion_visible in repair_visibility.items():
            repair = self._connection.execute(
                "SELECT * FROM repair_runs WHERE repair_run_id=? AND run_id=?",
                (repair_id, owner["run_id"]),
            ).fetchone()
            if repair is None:
                raise AutoModeConflictError("Auto Mode repair owner is unavailable")
            self._assert_repair_ledger_proof_locked(
                repair, completion_visible=completion_visible
            )
        audit_visibility: dict[
            str, tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]
        ] = {}
        for event in events:
            if event.get("type") not in {
                "auto_audit_started",
                "auto_audit_completed",
            }:
                continue
            payload = event.get("payload")
            if not isinstance(payload, Mapping):
                raise AutoModeConflictError("Auto Mode audit event is invalid")
            audit_id = payload.get("audit_id")
            subject_kind = payload.get("subject_kind")
            if (
                not isinstance(audit_id, str)
                or not audit_id
                or subject_kind not in _SUBJECT_ENTITY
                or payload.get("subject_entity_kind") != _SUBJECT_ENTITY[subject_kind]
            ):
                raise AutoModeConflictError("Auto Mode audit event is invalid")
            started, completed = audit_visibility.get(audit_id, (None, None))
            if event.get("type") == "auto_audit_started":
                if started is not None:
                    raise AutoModeConflictError("Auto Mode audit event is duplicated")
                started = event
            else:
                if completed is not None:
                    raise AutoModeConflictError("Auto Mode audit event is duplicated")
                completed = event
            audit_visibility[audit_id] = (started, completed)
        for audit_id, (started, completed) in audit_visibility.items():
            if started is None:
                raise AutoModeConflictError("Auto Mode audit start is unavailable")
            subject_kind = started["payload"]["subject_kind"]
            if subject_kind == "result_review":
                assessment = self._connection.execute(
                    "SELECT * FROM review_runs WHERE audit_id=? AND run_id=?",
                    (audit_id, owner["run_id"]),
                ).fetchone()
                if assessment is None:
                    raise AutoModeConflictError("Auto Mode review owner is unavailable")
                self._assert_review_assessment_proof_locked(
                    assessment,
                    completion_visible=completed is not None,
                    run=owner,
                    event_pair=(started, completed),
                )
            else:
                assessment = self._connection.execute(
                    "SELECT * FROM permission_review_assessments "
                    "WHERE audit_id=? AND run_id=?",
                    (audit_id, owner["run_id"]),
                ).fetchone()
                if assessment is None:
                    raise AutoModeConflictError(
                        "Auto Mode permission owner is unavailable"
                    )
                self._assert_permission_assessment_proof_locked(
                    assessment,
                    completion_visible=completed is not None,
                    run=owner,
                    event_pair=(started, completed),
                )
        physical_tail = self._connection.execute(
            "SELECT MAX(event_cursor) FROM auto_mode_events WHERE run_id=?",
            (owner["run_id"],),
        ).fetchone()[0]
        if physical_tail is None or cursors[-1] > int(physical_tail):
            raise AutoModeConflictError("Auto Mode projection tail is invalid")
        if cursors[-1] != int(physical_tail):
            # A checkpoint/fork/revert prefix is allowed to hide a later tail;
            # only its visible events, not the mutable latest owner columns,
            # describe that historical state.
            return
        self._assert_run_event_chain_locked(owner)
        decoded = self._decode_run(owner)
        comparisons = {
            "run_id": owner["run_id"],
            "root_frame_id": owner["root_frame_id"],
            "branch_id": owner["branch_id"],
            "turn_id": owner["turn_id"],
            "execution_id": owner["execution_id"],
            "mode": owner["mode"],
            "selection": decoded["selection"],
            "budgets": decoded["budgets"],
            "status": owner["status"],
            "candidate_id": owner["candidate_id"],
            "candidate_snapshot_sha256": owner["candidate_snapshot_sha256"],
            "evidence_snapshot_sha256": owner["evidence_snapshot_sha256"],
            "artifact_set_sha256": owner["artifact_set_sha256"],
            "candidate_artifact_ids": decoded["candidate_artifact_ids"],
            "candidate_version_ids": decoded["candidate_version_ids"],
            "terminal_reason": owner["terminal_reason"],
            "stop_reason": owner["stop_reason"],
            "created_at": owner["created_at"],
            "finished_at": owner["finished_at"],
        }
        if any(
            (
                (projected_run.get(name) or [])
                if name in {"candidate_artifact_ids", "candidate_version_ids"}
                else projected_run.get(name)
            )
            != value
            for name, value in comparisons.items()
        ):
            raise AutoModeConflictError(
                "Auto Mode projection disagrees with its durable owner"
            )

    def _assert_no_active_phase_locked(self, run: sqlite3.Row) -> None:
        """Refuse a terminal event while a durable phase still needs closure.

        A terminal run is immutable.  Allowing it to strand a started review,
        repair, or permission assessment would make post-crash reconciliation
        impossible: the matching completion method must first be allowed to
        commit its owner row and completion event.
        """

        run_id = str(run["run_id"])
        for table in (
            "review_runs",
            "repair_runs",
            "permission_review_assessments",
        ):
            active = self._connection.execute(
                f"SELECT 1 FROM {table} WHERE run_id=? AND status='started' LIMIT 1",
                (run_id,),
            ).fetchone()
            if active is not None:
                raise AutoModeConflictError(
                    "auto run has an active durable phase requiring reconciliation"
                )

    def _assert_verified_locked(
        self, run: sqlite3.Row, *, require_terminal: bool = False
    ) -> None:
        self._assert_run_event_chain_locked(run)
        self._assert_run_start_event_locked(run)
        candidate_event = self._assert_current_candidate_event_locked(run)
        candidate_id = run["candidate_id"]
        if not candidate_id:
            raise AutoModeConflictError("verified requires a durable candidate")
        candidate_reviews: list[tuple[int, sqlite3.Row]] = []
        reviews = self._connection.execute(
            "SELECT * FROM review_runs WHERE run_id=?",
            (run["run_id"],),
        ).fetchall()
        for review in reviews:
            start_event, completion_event = self._assert_review_assessment_proof_locked(
                review
            )
            if completion_event is None:
                raise AutoModeConflictError(
                    "verified cannot strand an incomplete result review"
                )
            if review["candidate_id"] != candidate_id:
                continue
            if (
                review["candidate_snapshot_sha256"] != run["candidate_snapshot_sha256"]
                or review["evidence_snapshot_sha256"] != run["evidence_snapshot_sha256"]
                or not (
                    candidate_event["sequence"]
                    < start_event["sequence"]
                    < completion_event["sequence"]
                )
            ):
                raise AutoModeConflictError(
                    "result review does not bind the current candidate"
                )
            candidate_reviews.append((int(completion_event["sequence"]), review))
        if not candidate_reviews:
            raise AutoModeConflictError("verified requires an independent pass review")
        _sequence, latest_review = max(candidate_reviews, key=lambda item: item[0])
        if (
            latest_review["status"] != "completed"
            or str(latest_review["verdict"]).lower() != "pass"
        ):
            raise AutoModeConflictError("verified requires an independent pass review")
        material = self._connection.execute(
            "SELECT 1 FROM review_findings WHERE run_id=? "
            "AND candidate_id=? "
            "AND lower(severity) IN ('material','major','high','critical') LIMIT 1",
            (run["run_id"], candidate_id),
        ).fetchone()
        if material is not None:
            raise AutoModeConflictError(
                "verified conflicts with material findings for this candidate"
            )

        if require_terminal:
            self._assert_terminal_event_locked(run, expected_status="verified")

    def _assert_run_event_chain_locked(self, run: sqlite3.Row) -> None:
        rows = self._connection.execute(
            "SELECT * FROM auto_mode_events WHERE run_id=? ORDER BY sequence",
            (run["run_id"],),
        ).fetchall()
        events = [self._decode_event(row) for row in rows]
        sequences = [int(event["sequence"]) for event in events]
        cursors = [int(event["event_cursor"]) for event in events]
        if (
            not events
            or sequences != list(range(1, len(events) + 1))
            or cursors != sorted(cursors)
            or len(cursors) != len(set(cursors))
        ):
            raise AutoModeConflictError("durable Auto Mode event order is invalid")

    def _assert_run_start_event_locked(self, run: sqlite3.Row) -> dict[str, Any]:
        rows = self._connection.execute(
            "SELECT * FROM auto_mode_events WHERE run_id=? "
            "AND type='auto_run_started' ORDER BY sequence",
            (run["run_id"],),
        ).fetchall()
        if len(rows) != 1:
            raise AutoModeConflictError("run lacks one durable start event")
        event = self._decode_event(rows[0])
        try:
            selection = json.loads(run["selection_json"])
            budgets = json.loads(run["budgets_json"])
        except (TypeError, ValueError) as error:
            raise AutoModeConflictError("run start request is invalid") from error
        if (
            not isinstance(selection, Mapping)
            or not isinstance(budgets, Mapping)
            or _canonical(dict(selection)) != run["selection_json"]
            or _canonical(dict(budgets)) != run["budgets_json"]
        ):
            raise AutoModeConflictError("run start request is invalid")
        request = {
            "root_frame_id": run["root_frame_id"],
            "branch_id": run["branch_id"],
            "turn_id": run["turn_id"],
            "execution_id": run["execution_id"],
            "mode": run["mode"],
            "selection": dict(selection),
            "budgets": dict(budgets),
        }
        expected_payload = {
            "mode": run["mode"],
            "status": "running",
            "selection": dict(selection),
            "budgets": dict(budgets),
        }
        expected_digest = _digest(request)
        if (
            event["sequence"] != 1
            or event["idempotency_key"] != run["idempotency_key"]
            or event["request_sha256"] != expected_digest
            or run["request_sha256"] != expected_digest
            or event["payload"] != expected_payload
            or event["created_at"] != run["created_at"]
        ):
            raise AutoModeConflictError(
                "run start event disagrees with its durable owner"
            )
        return event

    def _assert_terminal_event_locked(
        self, run: sqlite3.Row, *, expected_status: str
    ) -> None:
        if (
            run["trust_state"] != "local"
            or run["status"] != expected_status
            or run["finished_at"] is None
            or run["terminal_reason"] is None
            or run["terminal_idempotency_key"] is None
            or run["terminal_request_sha256"] is None
        ):
            raise AutoModeConflictError("terminal owner is invalid")
        rows = self._connection.execute(
            "SELECT * FROM auto_mode_events WHERE run_id=? "
            "AND type='auto_run_terminal' ORDER BY sequence",
            (run["run_id"],),
        ).fetchall()
        if len(rows) != 1:
            raise AutoModeConflictError("run lacks one terminal event")
        event = self._decode_event(rows[0])
        last_sequence = self._connection.execute(
            "SELECT MAX(sequence) FROM auto_mode_events WHERE run_id=?",
            (run["run_id"],),
        ).fetchone()[0]
        request = {
            "status": expected_status,
            "reason": run["terminal_reason"],
            "stop_reason": run["stop_reason"],
        }
        expected_payload = {
            "status": expected_status,
            "terminal_reason": run["terminal_reason"],
            "stop_reason": run["stop_reason"],
        }
        expected_digest = _digest(request)
        if (
            event["sequence"] != last_sequence
            or event["idempotency_key"] != run["terminal_idempotency_key"]
            or event["request_sha256"] != expected_digest
            or run["terminal_request_sha256"] != expected_digest
            or event["payload"] != expected_payload
            or event["created_at"] != run["finished_at"]
            or run["updated_at"] != run["finished_at"]
        ):
            raise AutoModeConflictError(
                "terminal event disagrees with its durable owner"
            )

    def _assert_current_candidate_event_locked(
        self, run: sqlite3.Row
    ) -> dict[str, Any]:
        row = self._connection.execute(
            "SELECT * FROM auto_mode_events WHERE run_id=? "
            "AND type='candidate_ready' ORDER BY sequence DESC LIMIT 1",
            (run["run_id"],),
        ).fetchone()
        if row is None:
            raise AutoModeConflictError(
                "current candidate has no durable candidate event"
            )
        event = self._decode_event(row)
        payload = event["payload"]
        candidate_lists: dict[str, list[str]] = {}
        for column, field in (
            ("candidate_artifact_ids_json", "candidate_artifact_ids"),
            ("candidate_version_ids_json", "candidate_version_ids"),
        ):
            encoded = run[column]
            try:
                values = json.loads(encoded)
            except (TypeError, ValueError) as error:
                raise AutoModeConflictError(
                    "current candidate reference list is invalid"
                ) from error
            if (
                not isinstance(values, list)
                or any(not isinstance(value, str) or not value for value in values)
                or len(values) != len(set(values))
                or _canonical(values) != encoded
            ):
                raise AutoModeConflictError(
                    "current candidate reference list is invalid"
                )
            candidate_lists[field] = values
        expected = {
            "candidate_id": run["candidate_id"],
            "candidate_snapshot_sha256": run["candidate_snapshot_sha256"],
            "evidence_snapshot_sha256": run["evidence_snapshot_sha256"],
            "artifact_set_sha256": run["artifact_set_sha256"],
            "candidate_artifact_ids": candidate_lists["candidate_artifact_ids"],
            "candidate_version_ids": candidate_lists["candidate_version_ids"],
        }
        expected_payload = {**expected, "status": "candidate"}
        if (
            payload != expected_payload
            or event["request_sha256"] != _digest(expected)
            or not isinstance(event["idempotency_key"], str)
            or not event["idempotency_key"]
        ):
            raise AutoModeConflictError(
                "current candidate row disagrees with its durable event"
            )
        return event

    @staticmethod
    def _identity(
        run_id: Any,
        root_frame_id: Any,
        branch_id: Any,
        turn_id: Any,
        execution_id: Any,
    ) -> tuple[str, str, str, str, str]:
        return (
            _text("run_id", run_id),
            _text("root_frame_id", root_frame_id),
            _text("branch_id", branch_id),
            _text("turn_id", turn_id),
            _text("execution_id", execution_id),
        )

    @staticmethod
    def _scope_kind(value: Any) -> str:
        value = _text("scope_kind", value)
        if value not in {"frame", "project"}:
            raise ValueError("scope_kind must be frame or project")
        return value

    @staticmethod
    def _normalize_selection(values: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(values, Mapping):
            raise ValueError("Auto Mode selection must be an object")
        allowed = {"preset", "result_review_mode", "approvals_reviewer", "budgets"}
        if set(values) - allowed:
            raise ValueError("unsupported Auto Mode selection field")
        if not values:
            return {}
        result = dict(values)
        if result.get("preset") not in {None, "off", "autonomous"}:
            raise ValueError("invalid Auto Mode preset")
        if result.get("result_review_mode") not in {
            None,
            "off",
            "review_only",
            "auto_fix",
        }:
            raise ValueError("invalid result review mode")
        if result.get("approvals_reviewer") not in {None, "user", "auto_review"}:
            raise ValueError("invalid approvals reviewer")
        if result.get("budgets") is not None and not isinstance(
            result.get("budgets"), Mapping
        ):
            raise ValueError("budgets must be an object")
        return result

    def _time(self, value: int | None) -> int:
        return self._clock_ms() if value is None else _integer("timestamp", value)

    @staticmethod
    def _decode_selection(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "scope_kind": str(row["scope_kind"]),
            "scope_id": str(row["scope_id"]),
            "is_set": bool(row["is_set"]),
            "preset": row["preset"],
            "result_review_mode": row["result_review_mode"],
            "approvals_reviewer": row["approvals_reviewer"],
            "budgets": _load(row["budgets_json"], None),
            "revision": int(row["revision"]),
            "updated_at": int(row["updated_at"]),
        }

    @staticmethod
    def _decode_run(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        for source, target, default in (
            ("selection_json", "selection", {}),
            ("budgets_json", "budgets", {}),
            ("candidate_artifact_ids_json", "candidate_artifact_ids", []),
            ("candidate_version_ids_json", "candidate_version_ids", []),
        ):
            result[target] = _load(result.pop(source, None), default)
        return result

    @staticmethod
    def _decode_event(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        encoded = result.pop("payload_json", None)
        try:
            payload = json.loads(encoded)
        except (TypeError, ValueError) as error:
            raise AutoModeConflictError(
                "durable Auto Mode event payload is invalid"
            ) from error
        if not isinstance(payload, Mapping):
            raise AutoModeConflictError(
                "durable Auto Mode event payload is not an object"
            )
        canonical_payload = _canonical(dict(payload))
        digest = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
        if digest != result.get("payload_sha256"):
            raise AutoModeConflictError("durable Auto Mode event payload hash mismatch")
        result["payload"] = dict(payload)
        result["event_ordinal"] = int(result["event_cursor"])
        result["occurred_at"] = int(result["created_at"])
        return result

    def _transition(
        self,
        run: sqlite3.Row | Mapping[str, Any],
        event: Mapping[str, Any],
        *,
        created: bool,
    ) -> dict[str, Any]:
        return {
            **self._decode_run(run),
            "event": dict(event),
            "event_id": event["event_id"],
            "event_cursor": event["event_cursor"],
            "created": bool(created),
        }

    def _review_transition(
        self,
        row: sqlite3.Row | Mapping[str, Any],
        event: Mapping[str, Any],
        *,
        created: bool,
    ) -> dict[str, Any]:
        result = self._decode_review(row)
        result.update(
            {
                "event": dict(event),
                "event_id": event["event_id"],
                "event_cursor": event["event_cursor"],
                "created": bool(created),
            }
        )
        return result

    def _repair_transition(
        self,
        row: sqlite3.Row | Mapping[str, Any],
        event: Mapping[str, Any],
        *,
        created: bool,
    ) -> dict[str, Any]:
        result = self._decode_repair(row)
        result.update(
            {
                "event": dict(event),
                "event_id": event["event_id"],
                "event_cursor": event["event_cursor"],
                "created": bool(created),
            }
        )
        return result

    def _permission_transition(
        self,
        row: sqlite3.Row | Mapping[str, Any],
        event: Mapping[str, Any],
        *,
        created: bool,
    ) -> dict[str, Any]:
        result = self._decode_permission(row)
        result.update(
            {
                "event": dict(event),
                "event_id": event["event_id"],
                "event_cursor": event["event_cursor"],
                "created": bool(created),
            }
        )
        return result

    def _decode_review(self, row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        for source, target, default in (
            ("evidence_snapshot_json", "evidence_snapshot", {}),
            ("reviewer_json", "reviewer", {}),
            ("assessment_json", "assessment", None),
            ("usage_json", "usage", {}),
        ):
            result[target] = _load(result.pop(source, None), default)
        with self._lock:
            findings = self._connection.execute(
                "SELECT * FROM review_findings WHERE review_run_id=? "
                "ORDER BY finding_ordinal,finding_id",
                (result["review_run_id"],),
            ).fetchall()
        result["findings"] = [self._decode_finding(item) for item in findings]
        reviewer = result.get("reviewer")
        if isinstance(reviewer, Mapping):
            result["model_profile_id"] = reviewer.get("profile_id")
            result["model_profile_revision"] = reviewer.get("profile_revision")
            result["model_fingerprint"] = reviewer.get("model_fingerprint")
        result["round"] = result.get("round_index")
        result["created_at"] = result.get("started_at")
        result["finished_at"] = result.get("completed_at")
        return result

    @staticmethod
    def _decode_finding(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        for source, target in (
            ("evidence_refs_json", "evidence_refs"),
            ("artifact_ids_json", "artifact_ids"),
            ("version_ids_json", "version_ids"),
            ("cell_ids_json", "cell_ids"),
        ):
            result[target] = _load(result.pop(source, None), [])
        return result

    @staticmethod
    def _decode_repair(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        for source, target in (
            ("finding_ids_json", "finding_ids"),
            ("before_version_ids_json", "before_version_ids"),
            ("after_version_ids_json", "after_version_ids"),
            ("execution_group_ids_json", "execution_group_ids"),
        ):
            result[target] = _load(result.pop(source, None), [])
        result["created_at"] = result.get("started_at")
        result["finished_at"] = result.get("completed_at")
        return result

    @staticmethod
    def _decode_permission(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["assessment"] = _load(result.pop("assessment_json", None), None)
        result["created_at"] = result.get("started_at")
        result["finished_at"] = result.get("completed_at")
        return result

    @staticmethod
    def _normalize_finding(value: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError("review finding must be an object")
        severity = _text("severity", value.get("severity"), maximum=64).lower()
        if severity not in {
            "info",
            "minor",
            "major",
            "material",
            "high",
            "critical",
        }:
            raise ValueError("invalid finding severity")
        status = str(value.get("status") or "open").strip().lower()
        if status not in {
            "open",
            "claimed",
            "unaddressed",
            "resolved",
            "accepted",
            "addressed_pending_review",
        }:
            raise ValueError("invalid finding status")
        return {
            "finding_id": _text("finding_id", value.get("finding_id")),
            "fingerprint": _text("fingerprint", value.get("fingerprint"), maximum=512),
            "severity": severity,
            "category": _text("category", value.get("category"), maximum=256),
            "claim": _text("claim", value.get("claim"), maximum=_MAX_TEXT),
            "evidence_refs": _string_list(
                "evidence_refs", value.get("evidence_refs") or []
            ),
            "artifact_ids": _string_list(
                "artifact_ids", value.get("artifact_ids") or []
            ),
            "version_ids": _string_list("version_ids", value.get("version_ids") or []),
            "cell_ids": _string_list("cell_ids", value.get("cell_ids") or []),
            "status": status,
        }

    def _overlay_inert_import(self, run: dict[str, Any]) -> dict[str, Any]:
        """Keep quarantined history visibly inert after event reduction.

        Imported event history is retained for audit, so its last historical
        event may be nonterminal (for example ``candidate_ready``).  The
        durable run row is the quarantine boundary; never let a reducer turn
        that historical phase back into an apparently resumable live run.
        """

        with self._lock:
            row = self._connection.execute(
                "SELECT trust_state,status,terminal_reason,source_claimed_status,"
                "source_terminal_reason,finished_at "
                "FROM auto_mode_runs WHERE run_id=?",
                (run["run_id"],),
            ).fetchone()
        if row is None or row["trust_state"] != "quarantined_import":
            return run
        run.update(
            {
                "trust_state": "quarantined_import",
                "status": "unverified_import",
                "terminal_reason": row["terminal_reason"] or "quarantined_import",
                "finished_at": row["finished_at"],
                "recovery_required": False,
            }
        )
        if row["source_claimed_status"] is not None:
            run["source_claimed_status"] = row["source_claimed_status"]
        if row["source_terminal_reason"] is not None:
            run["source_terminal_reason"] = row["source_terminal_reason"]
        run.pop("recovery_state", None)
        return run

    @staticmethod
    def _reduce_run(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        if not events or events[0].get("type") != "auto_run_started":
            raise ValueError("Auto Mode run projection lacks its start event")
        first = events[0]
        payload = (
            first.get("payload") if isinstance(first.get("payload"), Mapping) else {}
        )
        run: dict[str, Any] = {
            "run_id": first["run_id"],
            "root_frame_id": first["root_frame_id"],
            "branch_id": first["branch_id"],
            "turn_id": first["turn_id"],
            "execution_id": first["execution_id"],
            "mode": payload.get("mode"),
            "selection": payload.get("selection") or {},
            "budgets": payload.get("budgets") or {},
            "status": "running",
            "started_at": first["created_at"],
            "created_at": first["created_at"],
            "start_event_ordinal": first["event_cursor"],
        }
        for event in events:
            event_payload = (
                event.get("payload")
                if isinstance(event.get("payload"), Mapping)
                else {}
            )
            kind = event.get("type")
            if kind == "candidate_ready":
                run.update(
                    {
                        key: event_payload.get(key)
                        for key in (
                            "candidate_id",
                            "candidate_snapshot_sha256",
                            "evidence_snapshot_sha256",
                            "artifact_set_sha256",
                            "candidate_artifact_ids",
                            "candidate_version_ids",
                        )
                    }
                )
                run["status"] = "candidate"
            elif (
                kind == "auto_audit_started"
                and event_payload.get("subject_kind") == "result_review"
            ):
                run["status"] = "reviewing"
            elif (
                kind == "auto_audit_completed"
                and event_payload.get("subject_kind") == "result_review"
            ):
                run["status"] = "candidate"
            elif kind == "repair_started":
                run["status"] = "repairing"
            elif kind == "repair_completed":
                if event_payload.get("status") == "completed":
                    run.update(
                        {
                            "status": "running",
                            "candidate_id": None,
                            "candidate_snapshot_sha256": None,
                            "evidence_snapshot_sha256": None,
                            "artifact_set_sha256": None,
                            "candidate_artifact_ids": [],
                            "candidate_version_ids": [],
                        }
                    )
                elif event_payload.get("status") == "failed":
                    run["status"] = "candidate"
                else:
                    run["status"] = "repairing"
            elif kind == "auto_run_terminal":
                run["status"] = event_payload.get("status")
                run["terminal_reason"] = event_payload.get("terminal_reason")
                run["stop_reason"] = event_payload.get("stop_reason")
                run["terminal_at"] = event["created_at"]
                run["finished_at"] = event["created_at"]
            run["last_event_id"] = event["event_id"]
            run["last_event_ordinal"] = event["event_cursor"]
            run["updated_at"] = event["created_at"]
        run["recovery_required"] = run.get("status") not in _TERMINAL_STATUSES
        if run["recovery_required"]:
            run["recovery_state"] = "committed_phase_requires_reconciliation"
        return run


__all__ = [
    "AUTO_MODE_SCHEMA",
    "AutoModeConflictError",
    "AutoModeRepository",
    "create_auto_mode_schema",
]
