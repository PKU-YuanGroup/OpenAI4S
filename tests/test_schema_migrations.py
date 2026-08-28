"""Versioned, transactional schema migrations.

The database carried no version marker at all. Every open re-probed every table
with PRAGMA table_info and ALTERed in whatever was missing, each ALTER wrapped
in a bare `except sqlite3.OperationalError: pass`. So:

  * "is this database current?" was not a question the code could answer;
  * a genuinely failed ALTER was indistinguishable from the benign "duplicate
    column name" of a re-run, and the process carried on against a schema
    missing a column it believed it had; and
  * the set was not atomic — the ALTERs ran outside any transaction, so an
    upgrade that failed part-way left a partially-migrated schema with nothing
    to detect it by.

The invariant these tests pin: a database is either fully at version N or still
fully at version N-1. Never in between.

The retrofit rests on one property, asserted below: the legacy pass is
idempotent *by predicate* (it adds only absent columns; every backfill is
guarded by a WHERE selecting only rows that still need it). That is what makes
it safe to define version 1 as "the legacy baseline has run" and stamp it,
without reconstructing which ALTERs an existing install had already applied —
history that is simply not recorded anywhere.
"""

import json
import re
import sqlite3
from pathlib import Path

import pytest

from openai4s.config import Config
from openai4s.storage.migrations import (
    SCHEMA_VERSION,
    MigrationError,
    _is_duplicate_column,
    applied_migrations,
    backup_database,
    current_version,
    integrity_ok,
    run_migrations,
)
from openai4s.store import Store, get_store


def _schema_sql() -> str:
    src = Path("openai4s/store.py").read_text()
    return re.search(
        r'_SCHEMA\s*=\s*(?:r?"""|\'\'\')(.*?)(?:"""|\'\'\')', src, re.S
    ).group(1)


@pytest.fixture
def plain_db(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t(a TEXT)")
    conn.execute("INSERT INTO t VALUES('precious-data')")
    conn.commit()
    yield conn, db
    conn.close()


# --------------------------------------------------------------------------
# versioning
# --------------------------------------------------------------------------


def test_a_new_store_is_stamped_and_recorded(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    state = store.schema_state()
    assert state["version"] == SCHEMA_VERSION
    assert state["current"] is True
    assert [m["name"] for m in state["applied"]] == [
        "legacy_baseline",
        "compute_job_states",
        "compute_job_manifest",
        "artifact_env_identity",
        "artifact_source",
        "compute_job_pgid",
        "env_snapshot_generation",
        "env_snapshot_provenance",
        "compute_job_owner",
        # D2: a session binds `profile_id + revision` rather than storing a
        # model string that says which name, not which configuration.
        "frame_model_binding",
        # The idempotency namespace was installation-wide while every other view
        # of `compute_jobs` is per-owner, so one session's key blocked every
        # other session's and the duplicate refusal handed back the other
        # session's job id and status. Replacing an index is not additive, so it
        # needs a step rather than the idempotent catch-up pass.
        "compute_job_idem_owner",
        # A pin is a statement about one picture; binding it to the artifact
        # let a re-plot between the pin and the send change what the model saw.
        "annotation_version_binding",
        # Retention asks when a memory was last *touched*, and until there was
        # an edit path there was nothing to record but the first write. With
        # only `created_at`, correcting a stale instruction left the correction
        # expiring on the original's clock.
        "memory_updated_at",
        # Which in-flight request holds a pin. Admission has to be exactly-once,
        # and it cannot be without somewhere durable to record the claim -- so
        # this is a column, not a process-local set. It arrived first in the
        # ad-hoc add-column pass alone, which meant a fresh database had it and
        # every database with data in it did not.
        "annotation_reservation",
        # A reservation column says a pin is held; it cannot say by which
        # request, for which job, or whether the answer reached the client.
        # After a lost response that is the only question worth asking.
        "annotation_admission_ledger",
        # Successful DataPro structured content is stored losslessly and
        # projected into literal-search entries without a field whitelist.
        "datapro_content_index",
        # Repairs the short-lived development state where v16 could be
        # stamped even though its index tables were absent.
        "datapro_content_index_repair",
        # Team-mode identity: users, login sessions, and the audit log
        # (docs/team-server-plan.md M1-2). Additive only.
        "team_users",
        # Session ownership for team-mode visibility filtering (M1-6).
        # Existing sessions get no row, which reads as admin-only.
        "session_owners",
        # Team governance: membership, invites, usage ledger, quotas (M2).
        "team_governance",
        # Cluster workloads and allocations (M3a). Carries the partial unique
        # index that enforces one live allocation per workload.
        "orchestration_workloads",
        # Session leases and session↔workload bindings (M3b-4). Two empty
        # tables on a single-user install.
        "orchestration_leases",
        # Per-user LLM credential *references* (M4-1). The keys themselves
        # stay in the SecretBroker.
        "user_llm_keys",
        # Same-head byte reuse keeps per-Cell observations, and final prose is
        # atomically bound to its exact Artifact manifest.
        "artifact_observations_and_completion_delivery",
        # Durable Auto Run/audit state and the exact checkpoint event cursor
        # advance as one rollback-safe schema boundary.
        "auto_mode_durable_state",
        # PDF/HTML annotation locators next to image pins.
        "annotation_locators",
        # Exact Artifact inputs survive remote-job restart and are stamped on
        # harvested versions rather than inferred from a later poll.
        "compute_job_input_versions",
        # Delegated child cells are recorded directly (no execution_attempts
        # row), so the log row itself can name its kernel generation; and a
        # child's derived completion contract lands beside its lifecycle
        # status.
        "delegation_generation_and_task_status",
        # Atomic Auto Mode budget reservations and the persistent progress
        # circuit. Additive; existing runs stay legacy/read-only.
        "auto_mode_budget_admission",
        # Durable request identity separate from attempt identity. Restore
        # does not auto-resume; only an explicit continue creates the next
        # attempt. Additive; rollback does not delete Artifact versions.
        "delegation_requests_and_attempts",
    ]
    assert state["applied"][0]["checksum"]
    assert state["applied"][0]["applied_at"] > 0


def test_an_unversioned_database_reports_version_zero(plain_db):
    conn, _ = plain_db
    assert current_version(conn) == 0


def test_reopening_a_current_database_does_no_work(tmp_path, monkeypatch):
    """The fast path is a user_version read. Previously every open re-derived
    the whole schema shape with a table_info scan per table."""
    path = Config(data_dir=tmp_path).db_path
    get_store(path).close()

    import openai4s.storage.migrations as migrations

    calls = []
    monkeypatch.setattr(
        migrations, "integrity_ok", lambda c: calls.append(1) or True, raising=True
    )
    monkeypatch.setattr(
        migrations,
        "backup_database",
        lambda *a: calls.append("backup"),
        raising=True,
    )
    Store(path).close()
    assert calls == [], "an already-current database must not be probed or backed up"


def test_v24_installs_both_halves_of_trusted_artifact_delivery(tmp_path):
    db = Config(data_dir=tmp_path).db_path
    store = get_store(db)
    store.close()
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DROP TABLE completion_delivery_artifacts")
        conn.execute("DROP TABLE artifact_capture_observations")
        conn.execute("DROP TABLE completion_deliveries")
        conn.execute("DELETE FROM schema_migrations WHERE version=24")
        conn.execute("PRAGMA user_version = 23")
        conn.commit()
    finally:
        conn.close()

    upgraded = Store(db)
    try:
        tables = {
            row[0]
            for row in upgraded._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "artifact_capture_observations" in tables
        assert "completion_deliveries" in tables
        assert "completion_delivery_artifacts" in tables
        migration = upgraded._conn.execute(
            "SELECT name FROM schema_migrations WHERE version=24"
        ).fetchone()
        assert migration["name"] == "artifact_observations_and_completion_delivery"
        assert upgraded.schema_state()["version"] == SCHEMA_VERSION
    finally:
        upgraded.close()


def test_v24_combined_schema_step_rolls_back_if_second_half_fails(tmp_path):
    db = tmp_path / "v23.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA user_version = 23")
    conn.commit()

    from openai4s.storage.artifact_observations import (
        create_artifact_observations_schema,
    )

    def interrupted_step(connection):
        create_artifact_observations_schema(connection)
        raise RuntimeError("killed between delivery schemas")

    try:
        with pytest.raises(MigrationError, match="killed between delivery schemas"):
            run_migrations(
                conn,
                db,
                {
                    24: (
                        "artifact_observations_and_completion_delivery",
                        interrupted_step,
                    )
                },
                target=24,
            )
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "artifact_capture_observations" not in tables
        assert current_version(conn) == 23
    finally:
        conn.close()


_AUTO_MODE_TABLES = {
    "auto_mode_selections",
    "auto_mode_runs",
    "auto_mode_events",
    "review_runs",
    "review_findings",
    "repair_runs",
    "repair_execution_groups",
    "permission_review_assessments",
}


def _downgrade_current_store_to_v24(tmp_path, name="v24.db"):
    """Build a real current Store, then remove only the v25 schema boundary.

    Starting from a Store-produced database means every v1-v24 table and
    migration record has the same shape as an actual installation.  The
    checkpoint row and setting are canaries for data preservation across the
    one migration under test.
    """

    db = tmp_path / name
    store = Store(db)
    store.set_setting("v24-preservation-canary", "precious-data")
    store.ensure_session_branch(root_frame_id="root-v24", branch_id="root-v24")
    store.create_session_checkpoint(
        checkpoint_id="checkpoint-v24",
        root_frame_id="root-v24",
        branch_id="root-v24",
        reason="pre-auto-mode",
        workspace_tree_id=None,
        action_cursor=7,
        message_cursor=8,
        cell_cursor=9,
        auto_event_cursor=41,
    )
    store.create_permission_request(
        decision_id="decision-v24",
        root_frame_id="root-v24",
        project_id="default",
        tool="files.write",
        target="legacy.txt",
        payload={"summary": "legacy request"},
    )
    store.close()

    connection = sqlite3.connect(str(db))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute("DROP TRIGGER IF EXISTS trg_permission_action_immutable")
    for trigger in (
        "trg_repair_ledger_sealed",
        "trg_repair_event_update_sealed",
        "trg_repair_event_delete_sealed",
        "trg_repair_attempt_insert_sealed",
        "trg_repair_attempt_update_sealed",
        "trg_repair_attempt_delete_sealed",
    ):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for table in (
        "repair_execution_groups",
        "review_findings",
        "review_runs",
        "repair_runs",
        "permission_review_assessments",
        "auto_mode_events",
        "auto_mode_runs",
        "auto_mode_selections",
    ):
        connection.execute(f"DROP TABLE {table}")
    # v24 never had an Auto event boundary. SQLite fills the column with zero
    # on the subsequent v25 ADD COLUMN; the old checkpoint's other cursors and
    # envelope must survive byte-for-byte.
    # Rebuild instead of relying on ALTER TABLE DROP COLUMN: OpenAI4S supports
    # Python/SQLite combinations old enough that the newer shorthand is not a
    # portable way to manufacture a v24 fixture.
    connection.execute(
        "ALTER TABLE session_checkpoints RENAME TO _v25_session_checkpoints"
    )
    connection.execute("DROP INDEX ix_session_checkpoint_branch")
    connection.execute("DROP INDEX ux_session_checkpoint_source")
    connection.execute(
        "CREATE TABLE session_checkpoints ("
        "checkpoint_id TEXT PRIMARY KEY,root_frame_id TEXT NOT NULL,"
        "branch_id TEXT NOT NULL,parent_checkpoint_id TEXT,source_kind TEXT,"
        "source_id TEXT,internal INTEGER NOT NULL DEFAULT 0,reason TEXT NOT NULL,"
        "action_cursor INTEGER,message_cursor INTEGER,cell_cursor INTEGER,"
        "workspace_tree_id TEXT,artifact_versions TEXT NOT NULL,"
        "environment_pins TEXT NOT NULL,generation_refs TEXT NOT NULL,"
        "capability_state TEXT NOT NULL,permission_state TEXT NOT NULL,"
        "recovery_recipe TEXT NOT NULL,metadata TEXT NOT NULL,"
        "created_at INTEGER NOT NULL)"
    )
    connection.execute(
        "INSERT INTO session_checkpoints("
        "checkpoint_id,root_frame_id,branch_id,parent_checkpoint_id,source_kind,"
        "source_id,internal,reason,action_cursor,message_cursor,cell_cursor,"
        "workspace_tree_id,artifact_versions,environment_pins,generation_refs,"
        "capability_state,permission_state,recovery_recipe,metadata,created_at) "
        "SELECT checkpoint_id,root_frame_id,branch_id,parent_checkpoint_id,"
        "source_kind,source_id,internal,reason,action_cursor,message_cursor,"
        "cell_cursor,workspace_tree_id,artifact_versions,environment_pins,"
        "generation_refs,capability_state,permission_state,recovery_recipe,"
        "metadata,created_at FROM _v25_session_checkpoints"
    )
    connection.execute("DROP TABLE _v25_session_checkpoints")
    connection.execute(
        "CREATE INDEX ix_session_checkpoint_branch "
        "ON session_checkpoints(root_frame_id,branch_id,created_at)"
    )
    connection.execute(
        "CREATE UNIQUE INDEX ux_session_checkpoint_source "
        "ON session_checkpoints(root_frame_id,source_kind,source_id) "
        "WHERE source_kind IS NOT NULL AND source_id IS NOT NULL"
    )
    # These exact-action columns and their immutability trigger are also v25.
    # Rebuild the table so the fixture represents an actual v24 database rather
    # than a current schema carrying a downgraded version number.
    connection.execute(
        "ALTER TABLE permission_requests RENAME TO _v25_permission_requests"
    )
    connection.execute("DROP INDEX ix_permission_request_root")
    connection.execute(
        "CREATE TABLE permission_requests ("
        "decision_id TEXT PRIMARY KEY,root_frame_id TEXT,frame_id TEXT,"
        "project_id TEXT,action_group_id TEXT,action_id TEXT,tool_call_id TEXT,"
        "tool TEXT NOT NULL,target TEXT NOT NULL DEFAULT '',"
        "side_effect_class TEXT,resource_keys TEXT,payload TEXT,"
        "state TEXT NOT NULL DEFAULT 'pending',scope TEXT,pattern TEXT,"
        "message TEXT,resolution_context TEXT,"
        "continuation_required INTEGER NOT NULL DEFAULT 0,"
        "continuation_expires_at INTEGER,continuation_consumed_at INTEGER,"
        "created_at INTEGER NOT NULL,expires_at INTEGER,resolved_at INTEGER)"
    )
    connection.execute(
        "INSERT INTO permission_requests("
        "decision_id,root_frame_id,frame_id,project_id,action_group_id,action_id,"
        "tool_call_id,tool,target,side_effect_class,resource_keys,payload,state,"
        "scope,pattern,message,resolution_context,continuation_required,"
        "continuation_expires_at,continuation_consumed_at,created_at,expires_at,"
        "resolved_at) SELECT decision_id,root_frame_id,frame_id,project_id,"
        "action_group_id,action_id,tool_call_id,tool,target,side_effect_class,"
        "resource_keys,payload,state,scope,pattern,message,resolution_context,"
        "continuation_required,continuation_expires_at,"
        "continuation_consumed_at,created_at,expires_at,resolved_at "
        "FROM _v25_permission_requests"
    )
    connection.execute("DROP TABLE _v25_permission_requests")
    connection.execute(
        "CREATE INDEX ix_permission_request_root "
        "ON permission_requests(root_frame_id,state,created_at)"
    )
    connection.execute("DELETE FROM schema_migrations WHERE version=25")
    connection.execute("PRAGMA user_version=24")
    connection.commit()
    connection.close()
    return db


def test_v25_upgrades_a_real_v24_store_preserving_data_and_checkpoint_envelope(
    tmp_path,
):
    db = _downgrade_current_store_to_v24(tmp_path)

    upgraded = Store(db)
    try:
        tables = {
            row["name"]
            for row in upgraded._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert _AUTO_MODE_TABLES <= tables
        assert {
            row["name"]
            for row in upgraded._conn.execute(
                "PRAGMA table_info(session_checkpoints)"
            ).fetchall()
        } >= {"action_cursor", "message_cursor", "cell_cursor", "auto_event_cursor"}
        assert {
            row["name"]
            for row in upgraded._conn.execute(
                "PRAGMA table_info(auto_mode_runs)"
            ).fetchall()
        } >= {
            "terminal_idempotency_key",
            "terminal_request_sha256",
            "source_claimed_status",
            "source_terminal_reason",
            "abandoned_at",
            "abandoned_by_checkpoint_id",
        }
        assert {
            row["name"]
            for row in upgraded._conn.execute(
                "PRAGMA table_info(repair_execution_groups)"
            ).fetchall()
        } >= {
            "binding_ordinal",
            "action_group_kind",
            "ledger_event_count",
            "ledger_sha256",
            "sealed_at",
        }
        assert {
            row["name"]
            for row in upgraded._conn.execute(
                "PRAGMA table_info(permission_requests)"
            ).fetchall()
        } >= {
            "dangerous",
            "canonical_arguments_sha256",
            "action_digest",
        }
        permission = upgraded._conn.execute(
            "SELECT dangerous,canonical_arguments_sha256,action_digest "
            "FROM permission_requests WHERE decision_id='decision-v24'"
        ).fetchone()
        assert tuple(permission) == (0, None, None)
        with pytest.raises(ValueError, match="envelope is malformed"):
            upgraded.permission_request_action_digest("decision-v24")
        with pytest.raises(
            sqlite3.IntegrityError, match="action identity is immutable"
        ):
            upgraded._conn.execute(
                "UPDATE permission_requests SET target='rewritten.txt' "
                "WHERE decision_id='decision-v24'"
            )
        upgraded._conn.rollback()
        assert (
            upgraded.resolve_permission_request("decision-v24", state="denied")["state"]
            == "denied"
        )
        assert (
            upgraded._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='trigger' "
                "AND name='trg_permission_action_immutable'"
            ).fetchone()
            is not None
        )
        assert {
            row["name"]
            for row in upgraded._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND name LIKE 'trg_repair_%_sealed'"
            ).fetchall()
        } == {
            "trg_repair_ledger_sealed",
            "trg_repair_event_update_sealed",
            "trg_repair_event_delete_sealed",
            "trg_repair_attempt_insert_sealed",
            "trg_repair_attempt_update_sealed",
            "trg_repair_attempt_delete_sealed",
        }
        checkpoint = upgraded.get_session_checkpoint("checkpoint-v24")
        assert checkpoint is not None
        assert {
            key: checkpoint[key]
            for key in (
                "checkpoint_id",
                "root_frame_id",
                "branch_id",
                "action_cursor",
                "message_cursor",
                "cell_cursor",
                "auto_event_cursor",
            )
        } == {
            "checkpoint_id": "checkpoint-v24",
            "root_frame_id": "root-v24",
            "branch_id": "root-v24",
            "action_cursor": 7,
            "message_cursor": 8,
            "cell_cursor": 9,
            "auto_event_cursor": 0,
        }
        assert upgraded.get_setting("v24-preservation-canary") == "precious-data"
        assert (
            upgraded._conn.execute("PRAGMA user_version").fetchone()[0]
            == SCHEMA_VERSION
        )
        migration = upgraded._conn.execute(
            "SELECT name FROM schema_migrations WHERE version=25"
        ).fetchone()
        assert migration["name"] == "auto_mode_durable_state"
        locator = upgraded._conn.execute(
            "SELECT name FROM schema_migrations WHERE version=26"
        ).fetchone()
        assert locator["name"] == "annotation_locators"
        compute_inputs = upgraded._conn.execute(
            "SELECT name FROM schema_migrations WHERE version=27"
        ).fetchone()
        assert compute_inputs["name"] == "compute_job_input_versions"
    finally:
        upgraded.close()


def test_v25_late_failure_rolls_back_all_tables_triggers_cursor_and_version(tmp_path):
    db = _downgrade_current_store_to_v24(tmp_path, "v24-failure.db")
    connection = sqlite3.connect(str(db))
    connection.row_factory = sqlite3.Row

    from openai4s.storage.auto_mode import create_auto_mode_schema

    def fail_after_complete_v25_schema(conn):
        create_auto_mode_schema(conn)
        # Prove the fault is genuinely late: every table and the checkpoint
        # cursor exist inside the still-uncommitted migration transaction.
        live_tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert _AUTO_MODE_TABLES <= live_tables
        assert "auto_event_cursor" in {
            row["name"]
            for row in conn.execute("PRAGMA table_info(session_checkpoints)").fetchall()
        }
        assert {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND name LIKE 'trg_%_audit_id_global'"
            ).fetchall()
        } == {
            "trg_review_audit_id_global",
            "trg_permission_audit_id_global",
        }
        assert {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND name LIKE 'trg_repair_%_sealed'"
            ).fetchall()
        } == {
            "trg_repair_ledger_sealed",
            "trg_repair_event_update_sealed",
            "trg_repair_event_delete_sealed",
            "trg_repair_attempt_insert_sealed",
            "trg_repair_attempt_update_sealed",
            "trg_repair_attempt_delete_sealed",
        }
        assert {
            row["name"]
            for row in conn.execute("PRAGMA table_info(permission_requests)")
        } >= {
            "dangerous",
            "canonical_arguments_sha256",
            "action_digest",
        }
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='trigger' "
                "AND name='trg_permission_action_immutable'"
            ).fetchone()
            is not None
        )
        assert {
            row["name"]
            for row in conn.execute("PRAGMA table_info(repair_execution_groups)")
        } >= {"binding_ordinal", "action_group_kind"}
        raise RuntimeError("killed after complete v25 DDL")

    try:
        with pytest.raises(MigrationError, match="killed after complete v25 DDL"):
            run_migrations(
                connection,
                db,
                {25: ("auto_mode_durable_state", fail_after_complete_v25_schema)},
                target=25,
            )

        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert _AUTO_MODE_TABLES.isdisjoint(tables)
        assert "auto_event_cursor" not in {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(session_checkpoints)"
            ).fetchall()
        }
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' "
                "AND name LIKE 'trg_%_audit_id_global'"
            ).fetchone()[0]
            == 0
        )
        assert {
            row["name"]
            for row in connection.execute("PRAGMA table_info(permission_requests)")
        }.isdisjoint({"dangerous", "canonical_arguments_sha256", "action_digest"})
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' "
                "AND name='trg_permission_action_immutable'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' "
                "AND name LIKE 'trg_repair_%_sealed'"
            ).fetchone()[0]
            == 0
        )
        assert current_version(connection) == 24
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version=25"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT value FROM settings WHERE key='v24-preservation-canary'"
            ).fetchone()[0]
            == "precious-data"
        )
        checkpoint = connection.execute(
            "SELECT action_cursor,message_cursor,cell_cursor "
            "FROM session_checkpoints WHERE checkpoint_id='checkpoint-v24'"
        ).fetchone()
        assert tuple(checkpoint) == (7, 8, 9)
    finally:
        connection.close()


def test_partial_v25_repair_bindings_are_backfilled_and_rebuilt_canonically(
    tmp_path,
):
    from openai4s.storage.auto_mode import create_auto_mode_schema
    from openai4s.storage.snapshots import WorkspaceCAS
    from tests.test_auto_mode_faults import _issue_review, _rooted_store, _start

    store, root = _rooted_store(tmp_path, "partial-v25.db")
    _start(store, root)
    _issue_review(store)
    workspace = tmp_path / "partial-v25-workspace"
    workspace.mkdir()
    (workspace / "result.txt").write_text("candidate\n", encoding="utf-8")
    tree = WorkspaceCAS(tmp_path / "workspace-cas").capture(workspace)
    checkpoint = store.create_session_checkpoint(
        checkpoint_id="partial-v25-checkpoint",
        root_frame_id=root,
        branch_id=root,
        reason="pre_repair",
        workspace_tree_id=tree["tree_id"],
        auto_event_cursor=store.auto_mode_event_cursor(root),
    )
    store.start_auto_mode_repair(
        "run-1",
        repair_run_id="partial-v25-repair",
        idempotency_key="partial-v25-repair:start",
        finding_ids=["finding-1"],
        before_version_ids=["version-before"],
        checkpoint_id=checkpoint["checkpoint_id"],
    )
    groups = [
        store.append_action_group(
            root_frame_id=root,
            branch_id=root,
            turn_id="turn-1",
            kind=kind,
            assistant_content=f"repair group {kind}",
        )
        for kind in ("native_tools", "python_cell")
    ]
    for group in groups:
        store.bind_auto_mode_repair_execution_group(
            "partial-v25-repair",
            action_group_id=group["group_id"],
            idempotency_key=f"partial-v25:bind:{group['group_id']}",
        )

    connection = store._conn
    connection.execute("PRAGMA foreign_keys=OFF")
    trigger_names = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND sql LIKE '%repair_execution_groups%'"
        ).fetchall()
    ]
    for trigger_name in trigger_names:
        connection.execute(f'DROP TRIGGER "{trigger_name}"')
    connection.execute(
        "ALTER TABLE repair_execution_groups "
        "RENAME TO _canonical_repair_execution_groups"
    )
    connection.execute(
        "CREATE TABLE repair_execution_groups ("
        "repair_run_id TEXT NOT NULL,action_group_id TEXT NOT NULL,"
        "run_id TEXT NOT NULL,root_frame_id TEXT NOT NULL,branch_id TEXT NOT NULL,"
        "turn_id TEXT NOT NULL,execution_id TEXT NOT NULL,"
        "idempotency_key TEXT NOT NULL,request_sha256 TEXT NOT NULL,"
        "bound_at INTEGER NOT NULL,ledger_event_count INTEGER,ledger_sha256 TEXT,"
        "sealed_at INTEGER,PRIMARY KEY(repair_run_id,action_group_id),"
        "UNIQUE(action_group_id),UNIQUE(repair_run_id,idempotency_key))"
    )
    connection.execute(
        "INSERT INTO repair_execution_groups("
        "repair_run_id,action_group_id,run_id,root_frame_id,branch_id,turn_id,"
        "execution_id,idempotency_key,request_sha256,bound_at,ledger_event_count,"
        "ledger_sha256,sealed_at) SELECT repair_run_id,action_group_id,run_id,"
        "root_frame_id,branch_id,turn_id,execution_id,idempotency_key,"
        "request_sha256,bound_at,ledger_event_count,ledger_sha256,sealed_at "
        "FROM _canonical_repair_execution_groups ORDER BY bound_at DESC"
    )
    connection.execute("DROP TABLE _canonical_repair_execution_groups")

    create_auto_mode_schema(connection)
    connection.commit()
    table_info = {
        str(row[1]): row
        for row in connection.execute("PRAGMA table_info(repair_execution_groups)")
    }
    assert table_info["binding_ordinal"][3] == 1
    assert table_info["action_group_kind"][3] == 1
    rows = connection.execute(
        "SELECT action_group_id,binding_ordinal,action_group_kind "
        "FROM repair_execution_groups ORDER BY binding_ordinal"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        (groups[0]["group_id"], 0, "native_tools"),
        (groups[1]["group_id"], 1, "python_cell"),
    ]
    owner_groups = json.loads(
        connection.execute(
            "SELECT execution_group_ids_json FROM repair_runs "
            "WHERE repair_run_id='partial-v25-repair'"
        ).fetchone()[0]
    )
    assert owner_groups == [group["group_id"] for group in groups]
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "UPDATE repair_execution_groups SET binding_ordinal=-1 "
            "WHERE action_group_id=?",
            (groups[0]["group_id"],),
        )
    connection.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "UPDATE repair_execution_groups SET action_group_kind='' "
            "WHERE action_group_id=?",
            (groups[0]["group_id"],),
        )
    connection.rollback()
    store.close()


_EXEC_V27_COLUMNS = (
    "producing_cell_id,frame_id,root_frame_id,project_id,cell_seq,"
    "cell_index,state_revision,kernel_id,language,status,origin,code,"
    "code_hash,visibility,pin,replay_policy,variable_reads,"
    "variable_writes,variable_deletes,mutation_uncertain,stdout,stderr,"
    "error,figures,files_read,files_written,interrupted,wall_s,cpu_s,"
    "peak_rss_kb,created_at"
)

_CHILD_V27_COLUMNS = (
    "root_frame_id,child_id,parent_child_id,parent_frame_id,frame_id,"
    "name,depth,status,owner_instance_id,runner_instance_id,"
    "overrides_json,result_json,error,stop_reason,turn_boundary,"
    "max_turns,last_progress_at,created_at,started_at,finished_at"
)


def _downgrade_current_store_to_v27(tmp_path, name="v27.db"):
    """Build a real current Store, then remove only the v28 schema boundary.

    Starting from a Store-produced database means every v1-v27 table and
    migration record has the same shape as an actual installation. The
    execution_log rows and the terminal delegation child are canaries for
    data preservation across the one migration under test.
    """

    db = tmp_path / name
    store = Store(db)
    store.set_setting("v27-preservation-canary", "precious-data")
    root = store.new_frame(kind="turn", project_id="science")
    store.log_cell(
        frame_id=root,
        root_frame_id=root,
        code="print('v27 ok')",
        result={"id": "cell-v27-ok", "stdout": "v27 ok\n"},
        cell_index=1,
    )
    store.log_cell(
        frame_id=root,
        root_frame_id=root,
        code="boom()",
        result={"id": "cell-v27-err", "error": "NameError: boom"},
        cell_index=2,
    )
    store.restore_delegation_tree(
        root_frame_id=root,
        owner_instance_id="owner-v27",
        runner_instance_id="runner-v27",
        budget_limit=4,
    )
    reserved = store.reserve_delegation_children(
        root_frame_id=root,
        owner_instance_id="owner-v27",
        runner_instance_id="runner-v27",
        count=1,
        depth=1,
        parent_child_id=None,
    )
    child_id = reserved["child_ids"][0]
    store.persist_delegation_child(
        root_frame_id=root,
        owner_instance_id="owner-v27",
        runner_instance_id="runner-v27",
        child={
            "child_id": child_id,
            "name": "worker",
            "status": "done",
            "depth": 1,
            "stop_reason": "submitted",
            "result": {"output": "child result", "stop_reason": "submitted"},
            "created_at": 1.0,
        },
    )
    store.close()

    connection = sqlite3.connect(str(db))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=OFF")
    # Rebuild instead of relying on ALTER TABLE DROP COLUMN: OpenAI4S supports
    # Python/SQLite combinations old enough that the newer shorthand is not a
    # portable way to manufacture a v27 fixture.
    connection.execute("ALTER TABLE execution_log RENAME TO _v28_execution_log")
    connection.execute("DROP INDEX ix_exec_frame")
    connection.execute("DROP INDEX ix_exec_root")
    connection.execute(
        "CREATE TABLE execution_log ("
        "producing_cell_id TEXT PRIMARY KEY,frame_id TEXT,root_frame_id TEXT,"
        "project_id TEXT NOT NULL DEFAULT 'default',cell_seq INTEGER,"
        "cell_index INTEGER,state_revision INTEGER,kernel_id TEXT,"
        "language TEXT,status TEXT,origin TEXT,code TEXT NOT NULL,"
        "code_hash TEXT NOT NULL,visibility TEXT NOT NULL DEFAULT 'scientific' "
        "CHECK (visibility IN ('scientific','scratch','recovery','system')),"
        "pin INTEGER NOT NULL DEFAULT 0 CHECK (pin IN (0,1)),"
        "replay_policy TEXT NOT NULL DEFAULT 'conditional' "
        "CHECK (replay_policy IN ('safe','conditional','never')),"
        "variable_reads TEXT NOT NULL DEFAULT '[]',"
        "variable_writes TEXT NOT NULL DEFAULT '[]',"
        "variable_deletes TEXT NOT NULL DEFAULT '[]',"
        "mutation_uncertain INTEGER NOT NULL DEFAULT 0 "
        "CHECK (mutation_uncertain IN (0,1)),stdout TEXT,stderr TEXT,"
        "error TEXT,figures TEXT,files_read TEXT,files_written TEXT,"
        "interrupted INTEGER NOT NULL DEFAULT 0,wall_s REAL,cpu_s REAL,"
        "peak_rss_kb INTEGER,created_at INTEGER NOT NULL)"
    )
    connection.execute(
        f"INSERT INTO execution_log({_EXEC_V27_COLUMNS}) "
        f"SELECT {_EXEC_V27_COLUMNS} FROM _v28_execution_log"
    )
    connection.execute("DROP TABLE _v28_execution_log")
    connection.execute("CREATE INDEX ix_exec_frame ON execution_log(frame_id)")
    connection.execute("CREATE INDEX ix_exec_root ON execution_log(root_frame_id)")

    connection.execute(
        "ALTER TABLE delegation_children RENAME TO _v28_delegation_children"
    )
    connection.execute("DROP INDEX ix_delegation_children_root")
    connection.execute("DROP INDEX ix_delegation_children_live")
    connection.execute(
        "CREATE TABLE delegation_children ("
        "root_frame_id TEXT NOT NULL,child_id TEXT NOT NULL,"
        "parent_child_id TEXT,parent_frame_id TEXT,frame_id TEXT,name TEXT,"
        "depth INTEGER NOT NULL CHECK (depth >= 0),"
        "status TEXT NOT NULL CHECK (status IN "
        "('pending','running','done','failed','stopped')),"
        "owner_instance_id TEXT NOT NULL,runner_instance_id TEXT NOT NULL,"
        "overrides_json TEXT NOT NULL DEFAULT '{}',result_json TEXT,"
        "error TEXT,stop_reason TEXT,"
        "turn_boundary INTEGER NOT NULL DEFAULT 0 CHECK (turn_boundary >= 0),"
        "max_turns INTEGER,last_progress_at REAL,created_at REAL NOT NULL,"
        "started_at REAL,finished_at REAL,PRIMARY KEY(root_frame_id,child_id))"
    )
    connection.execute(
        f"INSERT INTO delegation_children({_CHILD_V27_COLUMNS}) "
        f"SELECT {_CHILD_V27_COLUMNS} FROM _v28_delegation_children"
    )
    connection.execute("DROP TABLE _v28_delegation_children")
    connection.execute(
        "CREATE INDEX ix_delegation_children_root "
        "ON delegation_children(root_frame_id, created_at, child_id)"
    )
    connection.execute(
        "CREATE INDEX ix_delegation_children_live "
        "ON delegation_children(root_frame_id, status, runner_instance_id)"
    )
    connection.execute("DELETE FROM schema_migrations WHERE version=28")
    connection.execute("PRAGMA user_version=27")
    connection.commit()
    connection.close()
    return db, root, child_id


def test_v28_upgrades_a_real_v27_store_preserving_rows_and_new_columns(tmp_path):
    db, root, child_id = _downgrade_current_store_to_v27(tmp_path)

    with sqlite3.connect(str(db)) as probe:
        assert "generation_id" not in {
            r[1] for r in probe.execute("PRAGMA table_info(execution_log)")
        }
        assert "task_status" not in {
            r[1] for r in probe.execute("PRAGMA table_info(delegation_children)")
        }

    upgraded = Store(db)
    try:
        assert "generation_id" in {
            r[1] for r in upgraded._conn.execute("PRAGMA table_info(execution_log)")
        }
        assert "task_status" in {
            r[1]
            for r in upgraded._conn.execute("PRAGMA table_info(delegation_children)")
        }
        migration = upgraded._conn.execute(
            "SELECT name FROM schema_migrations WHERE version=28"
        ).fetchone()
        assert migration["name"] == "delegation_generation_and_task_status"
        assert upgraded.schema_state()["version"] == SCHEMA_VERSION

        # Historical rows are preserved and keep NULL for both new columns:
        # inventing a generation or a task status would be provenance that is
        # wrong rather than absent.
        ok = upgraded.cell_detail("cell-v27-ok")
        assert ok["stdout"] == "v27 ok\n"
        assert ok["generation_id"] is None
        err = upgraded.cell_detail("cell-v27-err")
        assert err["status"] == "error"
        child = upgraded.delegation_tree(root)["children"][0]
        assert child["child_id"] == child_id
        assert child["status"] == "done"
        assert child["stop_reason"] == "submitted"
        assert child["task_status"] is None
        assert upgraded.get_setting("v27-preservation-canary") == "precious-data"

        # The upgraded database accepts the new provenance going forward.
        upgraded.log_cell(
            frame_id=root,
            root_frame_id=root,
            code="post()",
            result={"id": "cell-post-upgrade"},
            cell_index=3,
            generation_id="gen-post-upgrade",
        )
        assert (
            upgraded.cell_detail("cell-post-upgrade")["generation_id"]
            == "gen-post-upgrade"
        )
    finally:
        upgraded.close()


# --------------------------------------------------------------------------
# the retrofit onto a real old database
# --------------------------------------------------------------------------


def _make_legacy_db(tmp_path) -> Path:
    """A database as it existed before the branch_id migration, with data."""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(_schema_sql())
    # Roll `messages` back to its pre-branch_id shape.
    conn.execute(
        "CREATE TABLE _tmp AS SELECT message_id,frame_id,root_frame_id,role,"
        "content,seq,created_at,metadata FROM messages"
    )
    conn.execute("DROP TABLE messages")
    conn.execute("ALTER TABLE _tmp RENAME TO messages")
    conn.execute(
        "INSERT INTO messages(message_id,frame_id,root_frame_id,role,content,"
        "seq,created_at) VALUES('m1','f1','f1','user','hi',1,1)"
    )
    conn.execute(
        "INSERT INTO frames(frame_id,root_frame_id,project_id,kind,status,"
        "created_at,updated_at) VALUES('f1','f1','proj-x','turn','done',1,1)"
    )
    conn.commit()
    conn.close()
    return db


def test_an_upgraded_db_gains_owner_key_and_can_create_compute_jobs(tmp_path):
    """Migration 9 must run on an *existing* install.

    The offline suite otherwise only ever sees a fresh DB, whose CREATE TABLE
    already carries owner_key — so a forgotten SCHEMA_VERSION bump left the
    migration unreachable and every upgraded install unable to submit a compute
    job, because the INSERT names owner_key the old table lacks. This is the
    fresh-vs-upgraded blind spot; the test opens a real pre-owner_key database.
    """
    db = tmp_path / "v8.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(_schema_sql())
    # Roll compute_jobs back to its pre-owner_key shape, as a released install has.
    cols = [
        r[1]
        for r in conn.execute("PRAGMA table_info(compute_jobs)")
        if r[1] != "owner_key"
    ]
    conn.execute(f"CREATE TABLE _cj AS SELECT {','.join(cols)} FROM compute_jobs")
    conn.execute("DROP TABLE compute_jobs")
    conn.execute("ALTER TABLE _cj RENAME TO compute_jobs")
    conn.execute("PRAGMA user_version = 8")
    conn.commit()
    conn.close()

    with sqlite3.connect(str(db)) as probe:
        assert "owner_key" not in {
            r[1] for r in probe.execute("PRAGMA table_info(compute_jobs)")
        }

    store = Store(db)
    try:
        after = {r[1] for r in store._conn.execute("PRAGMA table_info(compute_jobs)")}
        assert "owner_key" in after, "migration 9 did not run on the upgraded DB"
        assert store.schema_state()["version"] == SCHEMA_VERSION
        # And a compute job can actually be created (the INSERT names owner_key).
        row = store.create_compute_job(
            job_id="j1", provider="ssh:lab", owner_key="/ws/a"
        )
        assert row["job_id"] == "j1"
    finally:
        store.close()


def test_legacy_database_is_migrated_and_stamped(tmp_path):
    db = _make_legacy_db(tmp_path)
    with sqlite3.connect(str(db)) as probe:
        assert "branch_id" not in {
            r[1] for r in probe.execute("PRAGMA table_info(messages)")
        }

    store = Store(db)
    try:
        cols = {r[1] for r in store._conn.execute("PRAGMA table_info(messages)")}
        assert "branch_id" in cols
        assert store.schema_state()["version"] == SCHEMA_VERSION
    finally:
        store.close()


def test_legacy_migration_preserves_data_and_backfills(tmp_path):
    db = _make_legacy_db(tmp_path)
    store = Store(db)
    try:
        row = store._conn.execute(
            "SELECT message_id,branch_id FROM messages"
        ).fetchone()
        assert row["message_id"] == "m1"
        # Backfilled from root_frame_id by the baseline's guarded UPDATE.
        assert row["branch_id"] == "f1"
    finally:
        store.close()


def test_the_legacy_baseline_is_idempotent(tmp_path):
    """The property the whole retrofit rests on. If running the baseline twice
    were not a no-op, defining version 1 as "it has run" would be unsound."""
    db = _make_legacy_db(tmp_path)
    first = Store(db)
    shape_1 = sorted(r[1] for r in first._conn.execute("PRAGMA table_info(messages)"))
    rows_1 = first._conn.execute("SELECT * FROM messages").fetchall()
    first.close()

    # Force the baseline to run a second time against the already-migrated db.
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()

    second = Store(db)
    try:
        shape_2 = sorted(
            r[1] for r in second._conn.execute("PRAGMA table_info(messages)")
        )
        rows_2 = second._conn.execute("SELECT * FROM messages").fetchall()
        assert shape_2 == shape_1
        assert [tuple(r) for r in rows_2] == [tuple(r) for r in rows_1]
    finally:
        second.close()


def test_an_upgrade_repairs_bad_rows_before_stamping_the_version(tmp_path):
    """No real database loses the legacy repairs by gaining a version.

    The baseline is not only ALTERs — it also repairs historical rows (child
    frames that kept project_id='default' instead of inheriting the root's).
    That repair used to run on every open; it now runs once. The property that
    makes that safe is the ordering asserted here: an un-versioned database
    runs the repair and is only stamped afterwards, so the upgrade cannot
    strand anyone with unrepaired data.
    """
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(_schema_sql())
    conn.execute(
        "INSERT INTO frames(frame_id,root_frame_id,project_id,kind,status,"
        "created_at,updated_at) VALUES('root','root','project-x','turn','done',1,1)"
    )
    # The historical shape: a child that kept 'default' rather than inheriting.
    conn.execute(
        "INSERT INTO frames(frame_id,parent_id,root_frame_id,project_id,kind,"
        "status,created_at,updated_at) "
        "VALUES('child','root','root','default','delegate','done',1,1)"
    )
    conn.commit()
    conn.close()

    store = Store(db)
    try:
        assert store.get_frame("child")["project_id"] == "project-x"
        assert store.schema_state()["version"] == SCHEMA_VERSION
    finally:
        store.close()


def test_successful_migration_cleans_up_its_backup(tmp_path):
    db = _make_legacy_db(tmp_path)
    store = Store(db)
    try:
        assert list(db.parent.glob("*.bak")) == []
    finally:
        store.close()


# --------------------------------------------------------------------------
# atomicity — the scorecard's "a mid-migration kill leaves no unrecognizable state"
# --------------------------------------------------------------------------


def test_a_failing_step_rolls_back_the_whole_set(plain_db):
    """Not just the failing step: the database must land fully on the old
    version, so there is no in-between state to have to recognise."""
    conn, db = plain_db

    def step_ok(c):
        c.execute("ALTER TABLE t ADD COLUMN b TEXT")
        c.execute("UPDATE t SET b='migrated'")

    def step_boom(c):
        c.execute("ALTER TABLE t ADD COLUMN c TEXT")
        raise RuntimeError("killed mid-migration")

    with pytest.raises(MigrationError):
        run_migrations(conn, db, {1: ("ok", step_ok), 2: ("boom", step_boom)}, target=2)

    assert sorted(r[1] for r in conn.execute("PRAGMA table_info(t)")) == ["a"]
    assert current_version(conn) == 0
    assert conn.execute("SELECT a FROM t").fetchone()[0] == "precious-data"


def test_ddl_really_is_transactional(plain_db):
    """The load-bearing mechanism: a rolled-back ALTER must un-add its column.

    SQLite supports this, but only inside an explicit transaction — DDL outside
    one runs in autocommit and survives the ROLLBACK. That is what the explicit
    BEGIN in run_migrations buys, and what this pins.
    """
    conn, db = plain_db

    def add_then_fail(c):
        c.execute("ALTER TABLE t ADD COLUMN gone TEXT")
        raise RuntimeError("boom")

    with pytest.raises(MigrationError):
        run_migrations(conn, db, {1: ("x", add_then_fail)}, target=1)
    assert "gone" not in {r[1] for r in conn.execute("PRAGMA table_info(t)")}


def test_bare_ddl_outside_a_transaction_would_not_roll_back(plain_db):
    """The negative control that gives the test above its meaning.

    Without an explicit BEGIN, an ALTER commits itself and a later ROLLBACK
    cannot undo it. Pinned so nobody 'simplifies' the BEGIN away and leaves the
    atomicity tests passing for the wrong reason.
    """
    conn, _ = plain_db
    conn.execute("ALTER TABLE t ADD COLUMN survives TEXT")
    conn.rollback()
    assert "survives" in {r[1] for r in conn.execute("PRAGMA table_info(t)")}


def test_a_migration_leaves_no_transaction_open(plain_db):
    """The connection is handed straight to every repository afterwards; a
    dangling transaction would hold locks for the life of the process."""
    conn, db = plain_db
    run_migrations(conn, db, {1: ("noop", lambda c: None)}, target=1)
    assert conn.in_transaction is False


def test_a_failed_migration_leaves_no_transaction_open(plain_db):
    conn, db = plain_db

    def boom(c):
        c.execute("ALTER TABLE t ADD COLUMN x TEXT")
        raise RuntimeError("boom")

    with pytest.raises(MigrationError):
        run_migrations(conn, db, {1: ("boom", boom)}, target=1)
    assert conn.in_transaction is False


def test_a_failed_migration_keeps_its_backup(plain_db):
    conn, db = plain_db

    def boom(c):
        raise RuntimeError("boom")

    with pytest.raises(MigrationError):
        run_migrations(conn, db, {1: ("boom", boom)}, target=1)
    assert [p.name for p in db.parent.glob("*.bak")] == ["t.db.v0.bak"]


def test_rerunning_after_a_failure_is_safe(plain_db):
    conn, db = plain_db
    state = {"fail": True}

    def flaky(c):
        c.execute("ALTER TABLE t ADD COLUMN b TEXT")
        if state["fail"]:
            raise RuntimeError("transient")

    with pytest.raises(MigrationError):
        run_migrations(conn, db, {1: ("flaky", flaky)}, target=1)
    state["fail"] = False
    report = run_migrations(conn, db, {1: ("flaky", flaky)}, target=1)
    assert report["migrated"] is True
    assert current_version(conn) == 1


# --------------------------------------------------------------------------
# error classification — no more blanket swallow
# --------------------------------------------------------------------------


def test_duplicate_column_is_the_only_benign_operational_error():
    assert _is_duplicate_column(
        sqlite3.OperationalError("duplicate column name: branch_id")
    )
    for hostile in (
        "database is locked",
        "no such table: frames",
        'near "TEXTT": syntax error',
        "attempt to write a readonly database",
    ):
        assert not _is_duplicate_column(sqlite3.OperationalError(hostile)), hostile


def test_a_real_alter_failure_is_not_swallowed(tmp_path, monkeypatch):
    """The old `except OperationalError: pass` hid this, and the process
    continued against a schema missing a column it believed it had.

    Driven with a genuinely malformed column declaration rather than a mocked
    error, so what is under test is the real classification path: SQLite raises
    OperationalError, and it is not "duplicate column name", so it must
    propagate rather than be absorbed.
    """
    db = _make_legacy_db(tmp_path)
    monkeypatch.setattr(
        Store, "_MIGRATIONS", {"messages": [("broken", "NOT-A-TYPE(((")]}
    )
    with pytest.raises(MigrationError, match="ADD COLUMN"):
        Store(db)


def test_the_database_is_untouched_after_a_failed_alter(tmp_path, monkeypatch):
    """Failing loudly is only half of it — the failure must also leave nothing
    behind to be half-migrated."""
    db = _make_legacy_db(tmp_path)
    monkeypatch.setattr(
        Store,
        "_MIGRATIONS",
        {"messages": [("added_ok", "TEXT"), ("broken", "NOT-A-TYPE(((")]},
    )
    with pytest.raises(MigrationError):
        Store(db)

    probe = sqlite3.connect(str(db))
    try:
        cols = {r[1] for r in probe.execute("PRAGMA table_info(messages)")}
        assert "added_ok" not in cols, "the earlier successful ALTER must roll back too"
        assert probe.execute("PRAGMA user_version").fetchone()[0] == 0
        assert probe.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    finally:
        probe.close()


def test_v16_datapro_tables_are_created_only_inside_the_migration_transaction(
    tmp_path, monkeypatch
):
    """A failed v16 upgrade must leave a genuinely v15 database behind.

    ``Store`` executes its compatibility baseline before numbered migrations.
    Putting the new tables in that baseline would create them outside the v16
    backup/transaction, so a later failure left user_version at 15 while the
    v16 schema had already leaked onto disk.
    """

    db = tmp_path / "v15.db"
    seeded = Store(db)
    seeded.close()
    conn = sqlite3.connect(str(db))
    conn.execute("DROP TABLE datapro_index_entries")
    conn.execute("DROP TABLE datapro_index_batches")
    conn.execute("DELETE FROM schema_migrations WHERE version=16")
    conn.execute("PRAGMA user_version=15")
    conn.commit()
    conn.close()

    def create_then_fail(self, connection):
        del self
        connection.execute("CREATE TABLE datapro_index_batches(leaked TEXT)")
        raise RuntimeError("injected v16 failure")

    monkeypatch.setattr(Store, "_apply_datapro_content_index", create_then_fail)
    with pytest.raises(MigrationError, match="injected v16 failure"):
        Store(db)

    probe = sqlite3.connect(str(db))
    try:
        assert probe.execute("PRAGMA user_version").fetchone()[0] == 15
        tables = {
            row[0]
            for row in probe.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "datapro_index_batches" not in tables
        assert "datapro_index_entries" not in tables
    finally:
        probe.close()


def test_v17_repairs_a_v16_database_missing_datapro_index_tables(tmp_path):
    db = tmp_path / "missing-v16-datapro-tables.db"
    seeded = Store(db)
    seeded.close()
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DROP TABLE datapro_index_entries")
        conn.execute("DROP TABLE datapro_index_batches")
        conn.execute("DELETE FROM schema_migrations WHERE version=17")
        conn.execute("PRAGMA user_version=16")
        conn.commit()
    finally:
        conn.close()

    repaired = Store(db)
    try:
        assert repaired.schema_state()["version"] == SCHEMA_VERSION
        receipt = repaired.index_datapro_result(
            query="repair probe",
            structured_content={"code": 0, "items": [{"marker": "repaired"}]},
        )
        assert receipt["complete"] is True
    finally:
        repaired.close()


# --------------------------------------------------------------------------
# integrity + backup
# --------------------------------------------------------------------------


def test_migration_refuses_a_corrupt_database(plain_db, monkeypatch):
    """Migrating an already-corrupt database turns a recoverable problem into
    a confusing one."""
    conn, db = plain_db
    import openai4s.storage.migrations as migrations

    monkeypatch.setattr(migrations, "integrity_ok", lambda c: False)
    with pytest.raises(MigrationError, match="integrity_check"):
        run_migrations(conn, db, {1: ("x", lambda c: None)}, target=1)
    assert current_version(conn) == 0


def test_integrity_ok_on_a_healthy_database(plain_db):
    conn, _ = plain_db
    assert integrity_ok(conn) is True


def test_backup_uses_the_sqlite_api_not_a_file_copy(plain_db):
    """A `cp` of a live database can capture a hot journal or torn pages — a
    backup that only fails to restore later, when it is needed."""
    conn, db = plain_db
    backup = backup_database(db, 0)
    assert backup is not None and backup.exists()
    restored = sqlite3.connect(str(backup))
    try:
        assert restored.execute("SELECT a FROM t").fetchone()[0] == "precious-data"
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        restored.close()


def test_no_backup_for_a_database_with_nothing_to_lose(tmp_path):
    assert backup_database(tmp_path / "absent.db", 0) is None


def test_backup_is_owner_only(plain_db):
    """The backup carries the same plaintext credentials as the database."""
    import os

    if os.name != "posix":
        pytest.skip("POSIX modes only")
    from openai4s.security.permissions import is_owner_only

    conn, db = plain_db
    backup = backup_database(db, 0)
    assert is_owner_only(backup)


# --------------------------------------------------------------------------
# PRAGMA policy
# --------------------------------------------------------------------------


def test_foreign_keys_is_on(tmp_path):
    """A no-op today — the schema declares no REFERENCES — but the pragma is
    per-connection and OFF by default, so without this the day someone adds a
    foreign key it would be silently unenforced and read as documentation."""
    store = get_store(Config(data_dir=tmp_path).db_path)
    assert store._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_busy_timeout_is_set(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    assert store._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_synchronous_stays_full(tmp_path):
    """FULL is the safe end, and this database holds an audit ledger. Pinned so
    a future 'performance' change has to argue for the durability trade."""
    store = get_store(Config(data_dir=tmp_path).db_path)
    assert store._conn.execute("PRAGMA synchronous").fetchone()[0] == 2


# --------------------------------------------------------------------------
# version 7: a generation attribution that cannot be verified is cleared
# --------------------------------------------------------------------------


def _legacy_env_snapshot(db: Path, snapshot_id: str, generation_id: str) -> None:
    """Insert a row addressed the way version 6 addressed it.

    The point is the id: before version 7 the address did not include the
    generation, so a row created by generation 1 was reused verbatim by
    generation 2 and kept naming the first.
    """
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO env_snapshots(snapshot_id,created_at,kind,python_version,"
            "implementation,platform,package_count,packages_json,interpreter,"
            "environment_name,generation_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                snapshot_id,
                1,
                "python",
                "3.12.0",
                "CPython",
                "macOS",
                0,
                "[]",
                "/usr/bin/python3",
                "base",
                generation_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _rewind_to_version_six(db: Path) -> None:
    """Put the database back where an installation upgrading from v0.1 is."""
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DELETE FROM schema_migrations WHERE version >= 7")
        conn.execute("PRAGMA user_version = 6")
        conn.commit()
    finally:
        conn.close()


def test_an_unverifiable_generation_attribution_is_labelled_not_erased(tmp_path):
    """A row whose id predates the generation-aware address may have been
    shared by a second kernel, and which rows actually were is not recoverable
    — sharing leaves no trace.

    Clearing the attribution would trade one wrong answer for a missing one and
    discard provenance that is right far more often than not. It is kept and
    qualified instead: a reader that needs certainty filters on the label."""
    db = Config(data_dir=tmp_path).db_path
    store = get_store(db)
    _legacy_env_snapshot(db, "env-legacyaddress", "gen-1")
    store.close()
    _rewind_to_version_six(db)

    upgraded = get_store(db)
    assert upgraded.schema_state()["version"] == SCHEMA_VERSION
    row = upgraded.get_env_snapshot("env-legacyaddress")
    assert row is not None, "the environment itself is untouched"
    assert row["generation_id"] == "gen-1", "provenance is never silently dropped"
    assert row["generation_confidence"] == "legacy_unverified"
    assert row["interpreter"] == "/usr/bin/python3"


def test_a_correctly_addressed_row_is_labelled_verified(tmp_path):
    """Idempotence: a row written after the fix hashes to its own id, so a
    re-run of the migration must leave it alone."""
    from openai4s.storage.artifacts import env_snapshot_id

    db = Config(data_dir=tmp_path).db_path
    store = get_store(db)
    correct = env_snapshot_id(
        kind="python",
        python_version="3.12.0",
        implementation="CPython",
        platform="macOS",
        interpreter="/usr/bin/python3",
        environment_name="base",
        generation_id="gen-1",
        packages_json="[]",
        remote_json="[]",
    )
    _legacy_env_snapshot(db, correct, "gen-1")
    store.close()
    _rewind_to_version_six(db)

    row = get_store(db).get_env_snapshot(correct)
    assert row["generation_id"] == "gen-1"
    assert (
        row["generation_confidence"] == "verified"
    ), "an address that includes its generation cannot have been shared"
