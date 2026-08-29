"""Durable Stage-2 Auto Mode state, events, and quarantine contracts."""

from __future__ import annotations

import hashlib
import inspect
import json
import sqlite3
import threading

import pytest

from openai4s.storage.auto_mode import (
    AUTO_MODE_SCHEMA,
    AutoModeConflictError,
    AutoModeRepository,
)
from openai4s.storage.migrations import SCHEMA_VERSION
from openai4s.store import Store


def _store(tmp_path, name: str = "openai4s.db") -> Store:
    return Store(tmp_path / name)


def _selection() -> dict:
    return {
        "preset": "autonomous",
        "result_review_mode": "auto_fix",
        "approvals_reviewer": "auto_review",
        "source": "frame",
    }


def _start(store: Store, **overrides):
    fields = {
        "run_id": "auto-run-1",
        "idempotency_key": "turn-1:auto-run",
        "root_frame_id": "root-1",
        "branch_id": "root-1",
        "turn_id": "turn-1",
        "execution_id": "execution-1",
        "mode": "auto_fix",
        "selection": _selection(),
        "budgets": {"max_review_attempts": 2, "max_repair_rounds": 2},
        "owner_instance_id": "daemon-1",
        "created_at": 100,
    }
    fields.update(overrides)
    root = fields["root_frame_id"]
    if store.get_frame(root) is None:
        project_id = f"project-{root}"
        store.create_project(name="Auto Mode storage test", project_id=project_id)
        store._conn.execute(
            "INSERT INTO frames(frame_id,parent_id,project_id,root_frame_id,kind,"
            "status,depth,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (root, None, project_id, root, "turn", "processing", 0, 1, 1),
        )
        store._conn.commit()
    store.ensure_session_branch(root_frame_id=root, branch_id=fields["branch_id"])
    return store.start_auto_mode_run(**fields)


def _sha(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _pass_review(
    store: Store,
    *,
    review_run_id: str = "review-pass-1",
    audit_id: str = "audit-pass-1",
    candidate_id: str = "candidate-1",
    candidate_snapshot_sha256: str = "a" * 64,
    evidence_snapshot: dict | None = None,
) -> dict:
    evidence = evidence_snapshot or {"candidate_id": candidate_id, "complete": True}
    evidence_sha256 = _sha(evidence)
    store.start_auto_mode_review(
        "auto-run-1",
        review_run_id=review_run_id,
        audit_id=audit_id,
        idempotency_key=f"{review_run_id}:start",
        candidate_id=candidate_id,
        candidate_snapshot_sha256=candidate_snapshot_sha256,
        evidence_snapshot=evidence,
        evidence_snapshot_sha256=evidence_sha256,
        round_index=0,
        attempt=1,
        reviewer={
            "profile_id": "scientific-reviewer",
            "profile_revision": 1,
            "model_fingerprint": "reviewer-model",
        },
    )
    return store.complete_auto_mode_review(
        review_run_id,
        idempotency_key=f"{review_run_id}:complete",
        status="completed",
        verdict="pass",
        assessment={"public_summary": "Independent review passed."},
        findings=[],
    )


def _verified_projection(tmp_path, name: str = "portable-source.db") -> dict:
    store = _store(tmp_path, name)
    _start(store)
    evidence = {"candidate_id": "candidate-1", "complete": True}
    store.record_auto_mode_candidate(
        "auto-run-1",
        idempotency_key="candidate:portable",
        candidate_id="candidate-1",
        candidate_snapshot_sha256="a" * 64,
        evidence_snapshot_sha256=_sha(evidence),
        candidate_version_ids=["version-source"],
    )
    _pass_review(store, evidence_snapshot=evidence)
    store.terminate_auto_mode_run(
        "auto-run-1",
        idempotency_key="terminal:portable",
        status="verified",
        reason="review_passed",
    )
    projection = store.export_auto_mode_projection("root-1", branch_id="root-1")
    store.close()
    return projection


def _auto_row_counts(store: Store) -> dict[str, int]:
    tables = (
        "auto_mode_selections",
        "auto_mode_runs",
        "auto_mode_events",
        "review_runs",
        "review_findings",
        "repair_runs",
        "repair_execution_groups",
        "permission_review_assessments",
    )
    return {
        table: int(store._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _quarantine_value() -> str:
    return json.dumps(
        {"reason": "test_import", "state": "quarantined"},
        sort_keys=True,
        separators=(",", ":"),
    )


def test_v25_installs_all_auto_mode_tables_and_repository_constructor_is_passive(
    tmp_path,
):
    store = _store(tmp_path)
    assert store.schema_state()["version"] == SCHEMA_VERSION == 31
    tables = {
        row["name"]
        for row in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {
        "auto_mode_selections",
        "auto_mode_runs",
        "auto_mode_events",
        "auto_mode_budget_state",
        "auto_mode_budget_reservations",
        "auto_mode_budget_events",
        "review_runs",
        "review_findings",
        "repair_runs",
        "repair_execution_groups",
        "permission_review_assessments",
    } <= tables
    assert "artifact_capture_observations" in tables
    assert "artifact_capture_observations" not in AUTO_MODE_SCHEMA
    assert "auto_event_cursor" in {
        row["name"]
        for row in store._conn.execute(
            "PRAGMA table_info(session_checkpoints)"
        ).fetchall()
    }
    store.close()

    connection = sqlite3.connect(":memory:")
    statements: list[str] = []
    connection.set_trace_callback(statements.append)
    AutoModeRepository(connection, threading.RLock(), clock_ms=lambda: 1)
    assert statements == []
    assert "executescript" not in inspect.getsource(AutoModeRepository.__init__)
    connection.close()


def test_start_run_creates_budget_state_and_missing_row_is_legacy(tmp_path):
    store = _store(tmp_path)
    started = _start(store)
    assert started["run_id"] == "auto-run-1"
    state = store.get_auto_mode_budget_state("auto-run-1")
    assert state is not None
    assert state["root_run_id"] == "auto-run-1"
    assert state["review_rounds"] == 0
    store._conn.execute("DELETE FROM auto_mode_budget_state WHERE run_id='auto-run-1'")
    store._conn.commit()
    assert store.get_auto_mode_budget_state("auto-run-1") is None
    assert store.project_auto_mode_budget("auto-run-1") is None
    store.close()


def test_selection_is_nullable_revisioned_and_compare_and_swap(tmp_path):
    store = _store(tmp_path)
    created = store.set_auto_mode_selection(
        "frame",
        "root-1",
        {
            "preset": None,
            "result_review_mode": "review_only",
            "approvals_reviewer": None,
            "budgets": None,
        },
        expected_revision=0,
    )
    assert created["revision"] == 1
    assert {
        key: created[key]
        for key in ("preset", "result_review_mode", "approvals_reviewer", "budgets")
    } == {
        "preset": None,
        "result_review_mode": "review_only",
        "approvals_reviewer": None,
        "budgets": None,
    }
    assert store.get_auto_mode_selection("frame", "root-1") == created

    updated = store.set_auto_mode_selection(
        "frame",
        "root-1",
        {
            "preset": None,
            "result_review_mode": "review_only",
            "approvals_reviewer": "user",
            "budgets": None,
        },
        expected_revision=1,
    )
    assert updated["revision"] == 2
    with pytest.raises(AutoModeConflictError, match="revision"):
        store.set_auto_mode_selection(
            "frame",
            "root-1",
            {
                "preset": "autonomous",
                "result_review_mode": "review_only",
                "approvals_reviewer": "user",
                "budgets": None,
            },
            expected_revision=1,
        )
    assert store.get_auto_mode_selection("frame", "root-1") == updated
    cleared = store.set_auto_mode_selection("frame", "root-1", {}, expected_revision=2)
    assert cleared["revision"] == 3
    assert cleared["is_set"] is False
    with pytest.raises(AutoModeConflictError, match="revision"):
        store.set_auto_mode_selection(
            "frame", "root-1", {"preset": "off"}, expected_revision=2
        )
    store.close()


def test_run_candidate_and_terminal_transitions_are_atomic_idempotent_and_immutable(
    tmp_path,
):
    store = _store(tmp_path)
    first = _start(store)
    repeated = _start(store)
    assert first["created"] is True
    assert repeated["created"] is False
    assert repeated["event_id"] == first["event_id"]
    assert first["status"] == "running"
    assert first["request_sha256"] == _sha(
        {
            "budgets": {"max_repair_rounds": 2, "max_review_attempts": 2},
            "branch_id": "root-1",
            "execution_id": "execution-1",
            "mode": "auto_fix",
            "root_frame_id": "root-1",
            "selection": _selection(),
            "turn_id": "turn-1",
        }
    )
    with pytest.raises(AutoModeConflictError, match="idempotency"):
        _start(store, mode="review_only")

    candidate = store.record_auto_mode_candidate(
        "auto-run-1",
        idempotency_key="turn-1:candidate:1",
        candidate_id="candidate-1",
        candidate_snapshot_sha256="a" * 64,
        evidence_snapshot_sha256="b" * 64,
        artifact_set_sha256="c" * 64,
        candidate_version_ids=["version-1"],
        created_at=110,
    )
    assert candidate["candidate_id"] == "candidate-1"
    assert (
        store.record_auto_mode_candidate(
            "auto-run-1",
            idempotency_key="turn-1:candidate:1",
            candidate_id="candidate-1",
            candidate_snapshot_sha256="a" * 64,
            evidence_snapshot_sha256="b" * 64,
            artifact_set_sha256="c" * 64,
            candidate_version_ids=["version-1"],
            created_at=999,
        )["event_id"]
        == candidate["event_id"]
    )
    with pytest.raises(AutoModeConflictError, match="idempotency"):
        store.record_auto_mode_candidate(
            "auto-run-1",
            idempotency_key="turn-1:candidate:1",
            candidate_id="candidate-other",
            candidate_snapshot_sha256="d" * 64,
            evidence_snapshot_sha256="b" * 64,
            candidate_version_ids=["version-1"],
        )

    terminal = store.terminate_auto_mode_run(
        "auto-run-1",
        idempotency_key="turn-1:terminal",
        status="completed_with_issues",
        reason="review_not_requested",
        finished_at=120,
    )
    assert terminal["status"] == "completed_with_issues"
    assert terminal["finished_at"] == 120
    assert (
        store.terminate_auto_mode_run(
            "auto-run-1",
            idempotency_key="turn-1:terminal",
            status="completed_with_issues",
            reason="review_not_requested",
            finished_at=999,
        )["event_id"]
        == terminal["event_id"]
    )
    with pytest.raises(AutoModeConflictError, match="terminal"):
        store.terminate_auto_mode_run(
            "auto-run-1",
            idempotency_key="turn-1:terminal-other",
            status="failed",
            reason="rewrite",
        )
    with pytest.raises(AutoModeConflictError, match="terminal"):
        store.record_auto_mode_candidate(
            "auto-run-1",
            idempotency_key="turn-1:candidate:2",
            candidate_id="candidate-2",
            candidate_snapshot_sha256="a" * 64,
            evidence_snapshot_sha256="b" * 64,
            candidate_version_ids=[],
        )

    events = store.list_auto_mode_events("root-1", branch_id="root-1")
    assert [event["type"] for event in events] == [
        "auto_run_started",
        "candidate_ready",
        "auto_run_terminal",
    ]
    assert [event["event_cursor"] for event in events] == [1, 2, 3]
    assert all(event["payload_sha256"] == _sha(event["payload"]) for event in events)
    assert events[-1]["payload"] == {
        "status": "completed_with_issues",
        "terminal_reason": "review_not_requested",
        "stop_reason": None,
    }
    legacy_terminal_request = {
        "status": "completed_with_issues",
        "reason": "review_not_requested",
        "stop_reason": None,
    }
    owner = store._conn.execute(  # noqa: SLF001 - legacy digest compatibility
        "SELECT terminal_request_sha256 FROM auto_mode_runs WHERE run_id=?",
        ("auto-run-1",),
    ).fetchone()
    assert owner["terminal_request_sha256"] == _sha(legacy_terminal_request)
    assert events[-1]["request_sha256"] == _sha(legacy_terminal_request)
    assert store.auto_mode_event_cursor("root-1", "root-1") == 3
    store.close()


def test_review_completion_and_findings_roll_back_with_its_durable_event(tmp_path):
    store = _store(tmp_path)
    _start(store)
    evidence = {"candidate_id": "candidate-1", "complete": True}
    evidence_sha256 = _sha(evidence)
    store.record_auto_mode_candidate(
        "auto-run-1",
        idempotency_key="candidate",
        candidate_id="candidate-1",
        candidate_snapshot_sha256="a" * 64,
        evidence_snapshot_sha256=evidence_sha256,
        candidate_version_ids=["version-1"],
    )
    review = store.start_auto_mode_review(
        "auto-run-1",
        review_run_id="review-1",
        audit_id="audit-1",
        idempotency_key="review:start:1",
        candidate_id="candidate-1",
        candidate_snapshot_sha256="a" * 64,
        evidence_snapshot=evidence,
        evidence_snapshot_sha256=evidence_sha256,
        round_index=0,
        attempt=1,
        reviewer={
            "profile_id": "scientific-reviewer",
            "profile_revision": 1,
            "model_fingerprint": "m1",
        },
        started_at=120,
    )
    assert review["status"] == "started"

    store._conn.execute(
        "CREATE TRIGGER fail_review_completed BEFORE INSERT ON auto_mode_events "
        "WHEN NEW.type='auto_audit_completed' BEGIN "
        "SELECT RAISE(ABORT,'injected review event failure'); END"
    )
    store._conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        store.complete_auto_mode_review(
            "review-1",
            idempotency_key="review:complete:1",
            status="completed",
            verdict="completed_with_issues",
            assessment={"summary": "one material issue"},
            findings=[
                {
                    "finding_id": "finding-1",
                    "fingerprint": "stable-fingerprint",
                    "severity": "major",
                    "category": "evidence",
                    "claim": "unsupported claim",
                    "evidence_refs": ["cell-1"],
                    "artifact_ids": ["artifact-1"],
                    "version_ids": ["version-1"],
                    "cell_ids": ["cell-1"],
                }
            ],
            usage={"input_tokens": 10, "output_tokens": 5},
            completed_at=130,
        )
    assert (
        store._conn.execute(
            "SELECT status FROM review_runs WHERE review_run_id='review-1'"
        ).fetchone()["status"]
        == "started"
    )
    assert (
        store._conn.execute("SELECT COUNT(*) AS n FROM review_findings").fetchone()["n"]
        == 0
    )

    store._conn.execute("DROP TRIGGER fail_review_completed")
    store._conn.commit()
    completed = store.complete_auto_mode_review(
        "review-1",
        idempotency_key="review:complete:1",
        status="completed",
        verdict="completed_with_issues",
        assessment={"summary": "one material issue"},
        findings=[
            {
                "finding_id": "finding-1",
                "fingerprint": "stable-fingerprint",
                "severity": "major",
                "category": "evidence",
                "claim": "unsupported claim",
                "evidence_refs": ["cell-1"],
                "artifact_ids": ["artifact-1"],
                "version_ids": ["version-1"],
                "cell_ids": ["cell-1"],
            }
        ],
        usage={"input_tokens": 10, "output_tokens": 5},
        completed_at=130,
    )
    assert completed["status"] == "completed"
    assert completed["verdict"] == "completed_with_issues"
    assert [item["fingerprint"] for item in completed["findings"]] == [
        "stable-fingerprint"
    ]
    repeated = store.complete_auto_mode_review(
        "review-1",
        idempotency_key="review:complete:1",
        status="completed",
        verdict="completed_with_issues",
        assessment={"summary": "one material issue"},
        findings=[
            {
                "finding_id": "finding-1",
                "fingerprint": "stable-fingerprint",
                "severity": "major",
                "category": "evidence",
                "claim": "unsupported claim",
                "evidence_refs": ["cell-1"],
                "artifact_ids": ["artifact-1"],
                "version_ids": ["version-1"],
                "cell_ids": ["cell-1"],
            }
        ],
        usage={"input_tokens": 10, "output_tokens": 5},
        completed_at=999,
    )
    assert repeated["created"] is False
    assert repeated["event_id"] == completed["event_id"]
    assert repeated["findings"] == completed["findings"]
    store.close()


def test_checkpoint_cursor_and_portable_projection_survive_reopen_and_import_quarantine(
    tmp_path,
):
    source = _store(tmp_path, "source.db")
    _start(source)
    evidence = {"candidate_id": "candidate-1", "complete": True}
    evidence_sha256 = _sha(evidence)
    source.record_auto_mode_candidate(
        "auto-run-1",
        idempotency_key="candidate",
        candidate_id="candidate-1",
        candidate_snapshot_sha256="a" * 64,
        evidence_snapshot_sha256=evidence_sha256,
        candidate_version_ids=["version-source"],
    )
    _pass_review(source, evidence_snapshot=evidence)
    source.terminate_auto_mode_run(
        "auto-run-1",
        idempotency_key="terminal",
        status="verified",
        reason="review_passed",
    )
    cursor = source.auto_mode_event_cursor("root-1", "root-1")
    checkpoint = source.create_session_checkpoint(
        checkpoint_id="checkpoint-1",
        root_frame_id="root-1",
        branch_id="root-1",
        reason="auto_mode_boundary",
        workspace_tree_id=None,
        auto_event_cursor=cursor,
    )
    assert checkpoint["auto_event_cursor"] == cursor == 5
    exported = source.export_auto_mode_projection(
        "root-1", branch_id="root-1", upto_event_cursor=cursor
    )
    assert exported["schema_version"] == 1
    assert exported["trust_state"] == "local"
    assert exported["historical_selection"] == _selection()
    assert [event["event_id"] for event in exported["events"]] == [
        event["event_id"]
        for event in source.list_auto_mode_events("root-1", branch_id="root-1")
    ]
    assert set(exported) == {
        "schema_version",
        "trust_state",
        "historical_selection",
        "runs",
        "events",
        "review_runs",
        "findings",
        "repair_runs",
        "permission_assessments",
    }
    source.close()

    reopened = _store(tmp_path, "source.db")
    assert reopened.get_session_checkpoint("checkpoint-1")["auto_event_cursor"] == 5
    projected = reopened.project_auto_mode_run("root-1", "root-1")
    assert projected["run"]["status"] == "verified"
    assert projected["events"] == exported["events"]
    reopened.close()

    target = _store(tmp_path, "target.db")
    created = target.create_quarantined_import_session(
        project_id="project-imported",
        quarantine_value=json.dumps(
            {"state": "quarantined", "reason": "test_import"},
            sort_keys=True,
        ),
    )
    imported_root = created["root_frame_id"]
    imported = target.import_quarantined_auto_mode_projection(
        exported,
        root_frame_id=imported_root,
        project_id=created["project_id"],
        branch_id=imported_root,
        artifact_version_id_map={"version-source": "version-imported"},
        imported_at=500,
    )
    assert imported["trust_state"] == "quarantined_import"
    assert imported["runs"][0]["root_frame_id"] == imported_root
    assert imported["runs"][0]["status"] == "unverified_import"
    assert target.get_auto_mode_selection("frame", imported_root) is None
    assert all(event["root_frame_id"] == imported_root for event in imported["events"])
    assert all(
        event["payload_sha256"] == _sha(event["payload"])
        for event in imported["events"]
    )
    assert imported["runs"][0]["candidate_version_ids"] == ["version-imported"]
    with pytest.raises(PermissionError):
        target.query("SELECT * FROM auto_mode_runs")
    target.close()


@pytest.mark.parametrize("quarantine_state", ["missing", "orphan_setting"])
def test_direct_import_requires_a_canonical_quarantined_session_and_writes_nothing(
    tmp_path,
    quarantine_state,
):
    projection = _verified_projection(tmp_path)
    target = _store(tmp_path, f"target-{quarantine_state}.db")
    root_frame_id = f"root-{quarantine_state}"
    if quarantine_state == "orphan_setting":
        # A setting with the right-looking payload is not a Session quarantine:
        # only the atomic project/root/quarantine aggregate created by the
        # package-import boundary is authoritative.
        target.set_setting(
            f"session:import-quarantine:{root_frame_id}", _quarantine_value()
        )
    before = _auto_row_counts(target)

    with pytest.raises(PermissionError, match="quarantine"):
        target.import_quarantined_auto_mode_projection(
            projection,
            root_frame_id=root_frame_id,
            project_id=f"project-{quarantine_state}",
            branch_id=root_frame_id,
            version_id_map={"version-source": "version-imported"},
        )

    assert _auto_row_counts(target) == before
    target.close()


@pytest.mark.parametrize("preexisting", ["frame_selection", "history"])
def test_direct_import_rejects_existing_selection_or_history_without_partial_writes(
    tmp_path,
    preexisting,
):
    projection = _verified_projection(tmp_path, f"source-{preexisting}.db")
    target = _store(tmp_path, f"collision-{preexisting}.db")
    project = target.create_project(
        project_id=f"project-{preexisting}", name="collision target"
    )
    root_frame_id = target.new_frame(project_id=project["project_id"], status="done")
    if preexisting == "frame_selection":
        target.set_auto_mode_selection(
            "frame",
            root_frame_id,
            {
                "preset": "off",
                "result_review_mode": "off",
                "approvals_reviewer": "user",
                "budgets": None,
            },
            expected_revision=0,
        )
    else:
        _start(
            target,
            run_id="existing-local-run",
            idempotency_key="existing-local-run:start",
            root_frame_id=root_frame_id,
            branch_id=root_frame_id,
            turn_id="existing-turn",
            execution_id="existing-execution",
        )
    target.set_setting(
        f"session:import-quarantine:{root_frame_id}", _quarantine_value()
    )
    before = _auto_row_counts(target)

    with pytest.raises(AutoModeConflictError, match="selection|history"):
        target.import_quarantined_auto_mode_projection(
            projection,
            root_frame_id=root_frame_id,
            project_id=project["project_id"],
            branch_id=root_frame_id,
            version_id_map={"version-source": "version-imported"},
        )

    assert _auto_row_counts(target) == before
    target.close()


def test_successful_direct_import_remains_inert_for_selection_and_local_runs(tmp_path):
    projection = _verified_projection(tmp_path)
    target = _store(tmp_path, "inert-import.db")
    created = target.create_quarantined_import_session(
        project_id="project-import-inert",
        quarantine_value=_quarantine_value(),
    )
    root_frame_id = created["root_frame_id"]
    imported = target.import_quarantined_auto_mode_projection(
        projection,
        root_frame_id=root_frame_id,
        project_id=created["project_id"],
        branch_id=root_frame_id,
        version_id_map={"version-source": "version-imported"},
    )
    assert imported["trust_state"] == "quarantined_import"
    before = _auto_row_counts(target)

    with pytest.raises(PermissionError, match="read-only"):
        target.set_auto_mode_selection(
            "frame",
            root_frame_id,
            {
                "preset": "autonomous",
                "result_review_mode": "auto_fix",
                "approvals_reviewer": "auto_review",
                "budgets": None,
            },
            expected_revision=0,
        )
    with pytest.raises(PermissionError, match="inert"):
        target.start_auto_mode_run(
            run_id="forbidden-local-run",
            idempotency_key="forbidden-local-run:start",
            root_frame_id=root_frame_id,
            branch_id=root_frame_id,
            turn_id="forbidden-turn",
            execution_id="forbidden-execution",
            mode="auto_fix",
            selection=_selection(),
            budgets={"max_review_attempts": 1},
            owner_instance_id="daemon-local",
        )

    assert _auto_row_counts(target) == before
    target.close()


@pytest.mark.parametrize(
    "tamper",
    [
        "reviewer_profile",
        "reviewer_revision",
        "reviewer_fingerprint",
        "review_round",
        "review_attempt",
        "start_event",
    ],
)
def test_verified_rejects_tampered_reviewer_identity_attempt_and_start_event(
    tmp_path,
    tamper,
):
    store = _store(tmp_path)
    _start(store)
    evidence = {"candidate_id": "candidate-1", "complete": True}
    store.record_auto_mode_candidate(
        "auto-run-1",
        idempotency_key="candidate:verified-proof",
        candidate_id="candidate-1",
        candidate_snapshot_sha256="a" * 64,
        evidence_snapshot_sha256=_sha(evidence),
        candidate_version_ids=[],
    )
    _pass_review(store, evidence_snapshot=evidence)

    if tamper.startswith("reviewer_"):
        row = store._conn.execute(
            "SELECT reviewer_json FROM review_runs "
            "WHERE review_run_id='review-pass-1'"
        ).fetchone()
        reviewer = json.loads(row["reviewer_json"])
        field, value = {
            "reviewer_profile": ("profile_id", "different-reviewer"),
            "reviewer_revision": ("profile_revision", 2),
            "reviewer_fingerprint": ("model_fingerprint", "different-model"),
        }[tamper]
        reviewer[field] = value
        store._conn.execute(
            "UPDATE review_runs SET reviewer_json=? WHERE review_run_id=?",
            (
                json.dumps(
                    reviewer,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "review-pass-1",
            ),
        )
    elif tamper == "review_round":
        store._conn.execute(
            "UPDATE review_runs SET round_index=round_index+1 "
            "WHERE review_run_id='review-pass-1'"
        )
    elif tamper == "review_attempt":
        store._conn.execute(
            "UPDATE review_runs SET attempt=attempt+1 "
            "WHERE review_run_id='review-pass-1'"
        )
    else:
        row = store._conn.execute(
            "SELECT event_id,payload_json FROM auto_mode_events "
            "WHERE run_id='auto-run-1' AND type='auto_audit_started'"
        ).fetchone()
        payload = json.loads(row["payload_json"])
        payload["model_profile_id"] = "tampered-start-profile"
        payload_json = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        # Keep the event internally checksum-valid: Verified must reject the
        # semantic mismatch with the durable review row, not merely malformed
        # bytes.
        store._conn.execute(
            "UPDATE auto_mode_events SET payload_json=?,payload_sha256=? "
            "WHERE event_id=?",
            (payload_json, _sha(payload), row["event_id"]),
        )
    store._conn.commit()
    before = _auto_row_counts(store)

    with pytest.raises(AutoModeConflictError):
        store.terminate_auto_mode_run(
            "auto-run-1",
            idempotency_key=f"terminal:tampered:{tamper}",
            status="verified",
            reason="forged_review_pass",
        )

    assert _auto_row_counts(store) == before
    assert (
        store._conn.execute(
            "SELECT COUNT(*) FROM auto_mode_events WHERE run_id='auto-run-1' "
            "AND type='auto_run_terminal'"
        ).fetchone()[0]
        == 0
    )
    store.close()


@pytest.mark.parametrize(
    "tamper",
    [
        "assessment_json",
        "usage_json",
        "finding_content",
        "finding_order",
        "assessment_envelope_json",
        "completion_request_sha256",
        "completion_event_timestamp",
    ],
)
def test_verified_rejects_tampered_completed_review_proof(tmp_path, tamper):
    store = _store(tmp_path)
    _start(store)
    evidence = {"candidate_id": "candidate-1", "complete": True}
    store.record_auto_mode_candidate(
        "auto-run-1",
        idempotency_key="candidate:completed-review-proof",
        candidate_id="candidate-1",
        candidate_snapshot_sha256="a" * 64,
        evidence_snapshot_sha256=_sha(evidence),
        candidate_version_ids=[],
    )
    store.start_auto_mode_review(
        "auto-run-1",
        review_run_id="review-proof-1",
        audit_id="audit-proof-1",
        idempotency_key="review-proof:start",
        candidate_id="candidate-1",
        candidate_snapshot_sha256="a" * 64,
        evidence_snapshot=evidence,
        evidence_snapshot_sha256=_sha(evidence),
        round_index=0,
        attempt=1,
        reviewer={
            "profile_id": "scientific-reviewer",
            "profile_revision": 1,
            "model_fingerprint": "reviewer-model",
        },
        started_at=120,
    )
    store.complete_auto_mode_review(
        "review-proof-1",
        idempotency_key="review-proof:complete",
        status="completed",
        verdict="pass",
        assessment={
            "public_summary": "Independent review passed with minor notes.",
            "confidence": "high",
        },
        findings=[
            {
                "finding_id": "review-proof-finding-1",
                "fingerprint": "review-proof-fingerprint-1",
                "severity": "minor",
                "category": "clarity",
                "claim": "Clarify the first supporting note.",
                "evidence_refs": ["cell-1"],
                "artifact_ids": [],
                "version_ids": [],
                "cell_ids": ["cell-1"],
            },
            {
                "finding_id": "review-proof-finding-2",
                "fingerprint": "review-proof-fingerprint-2",
                "severity": "info",
                "category": "presentation",
                "claim": "Clarify the second supporting note.",
                "evidence_refs": ["cell-2"],
                "artifact_ids": [],
                "version_ids": [],
                "cell_ids": ["cell-2"],
            },
        ],
        usage={"input_tokens": 31, "output_tokens": 17},
        completed_at=130,
    )

    if tamper in {"assessment_json", "usage_json", "assessment_envelope_json"}:
        row = store._conn.execute(
            f"SELECT {tamper} FROM review_runs WHERE review_run_id=?",
            ("review-proof-1",),
        ).fetchone()
        value = json.loads(row[tamper])
        value["tampered"] = True
        store._conn.execute(
            f"UPDATE review_runs SET {tamper}=? WHERE review_run_id=?",
            (_canonical(value), "review-proof-1"),
        )
    elif tamper == "finding_content":
        store._conn.execute(
            "UPDATE review_findings SET claim=? WHERE finding_id=?",
            ("Tampered supporting claim.", "review-proof-finding-1"),
        )
    elif tamper == "finding_order":
        # Move through an unused ordinal so the UNIQUE(review, ordinal)
        # constraint remains valid while the durable order is reversed.
        store._conn.execute(
            "UPDATE review_findings SET finding_ordinal=2 WHERE finding_id=?",
            ("review-proof-finding-1",),
        )
        store._conn.execute(
            "UPDATE review_findings SET finding_ordinal=0 WHERE finding_id=?",
            ("review-proof-finding-2",),
        )
        store._conn.execute(
            "UPDATE review_findings SET finding_ordinal=1 WHERE finding_id=?",
            ("review-proof-finding-1",),
        )
    elif tamper == "completion_request_sha256":
        store._conn.execute(
            "UPDATE review_runs SET completion_request_sha256=? "
            "WHERE review_run_id=?",
            ("b" * 64, "review-proof-1"),
        )
    else:
        store._conn.execute(
            "UPDATE auto_mode_events SET created_at=created_at+1 "
            "WHERE run_id=? AND type='auto_audit_completed'",
            ("auto-run-1",),
        )
    store._conn.commit()
    before = _auto_row_counts(store)

    with pytest.raises(AutoModeConflictError):
        store.terminate_auto_mode_run(
            "auto-run-1",
            idempotency_key=f"terminal:tampered-completion:{tamper}",
            status="verified",
            reason="forged_review_pass",
        )

    assert _auto_row_counts(store) == before
    assert (
        store._conn.execute(
            "SELECT status FROM auto_mode_runs WHERE run_id='auto-run-1'"
        ).fetchone()[0]
        == "candidate"
    )
    assert (
        store._conn.execute(
            "SELECT COUNT(*) FROM auto_mode_events WHERE run_id='auto-run-1' "
            "AND type='auto_run_terminal'"
        ).fetchone()[0]
        == 0
    )
    store.close()


@pytest.mark.parametrize(
    "tamper_sql",
    [
        "UPDATE review_runs SET assessment_json='{}' WHERE review_run_id='review-pass-1'",
        "UPDATE auto_mode_events SET request_sha256='f' || substr(request_sha256, 2) "
        "WHERE run_id='auto-run-1' AND type='auto_run_started'",
        "UPDATE auto_mode_events SET request_sha256='f' || substr(request_sha256, 2) "
        "WHERE run_id='auto-run-1' AND type='candidate_ready'",
        "UPDATE auto_mode_events SET request_sha256='f' || substr(request_sha256, 2) "
        "WHERE run_id='auto-run-1' AND type='auto_run_terminal'",
        "UPDATE auto_mode_events SET idempotency_key='tampered-terminal-key' "
        "WHERE run_id='auto-run-1' AND type='auto_run_terminal'",
        "UPDATE auto_mode_runs SET status='failed' WHERE run_id='auto-run-1'",
        "UPDATE auto_mode_runs SET terminal_request_sha256='f' || "
        "substr(terminal_request_sha256, 2) WHERE run_id='auto-run-1'",
        "UPDATE auto_mode_runs SET terminal_reason='tampered-reason' "
        "WHERE run_id='auto-run-1'",
    ],
)
def test_verified_read_boundaries_fail_closed_after_proof_tamper(tmp_path, tamper_sql):
    store = _store(tmp_path)
    _start(store)
    evidence = {"candidate_id": "candidate-1", "complete": True}
    store.record_auto_mode_candidate(
        "auto-run-1",
        idempotency_key="candidate:read-proof",
        candidate_id="candidate-1",
        candidate_snapshot_sha256="a" * 64,
        evidence_snapshot_sha256=_sha(evidence),
        candidate_version_ids=[],
    )
    _pass_review(store, evidence_snapshot=evidence)
    store.terminate_auto_mode_run(
        "auto-run-1",
        idempotency_key="terminal:read-proof",
        status="verified",
        reason="review_passed",
        finished_at=150,
    )
    store._conn.execute(tamper_sql)
    store._conn.commit()
    before = _auto_row_counts(store)

    projected = store.project_auto_mode_run("root-1", "root-1")
    with pytest.raises(AutoModeConflictError, match="proof|projection|terminal"):
        store.export_auto_mode_projection("root-1", branch_id="root-1")

    assert projected["run"]["source_claimed_status"] == "verified"
    assert projected["run"]["status"] == "failed"
    assert projected["run"]["terminal_reason"] == "safety_boundary"
    assert projected["events"][-1]["payload"]["status"] == "failed"
    assert projected["events"][-1]["payload"]["terminal_reason"] == "safety_boundary"
    assert _auto_row_counts(store) == before
    store.close()


def test_started_review_reopens_idempotently_and_blocks_terminal_until_completed(
    tmp_path,
):
    db_name = "review-restart.db"
    store = _store(tmp_path, db_name)
    started_run = _start(store)
    evidence = {"candidate_id": "candidate-1", "complete": True}
    store.record_auto_mode_candidate(
        "auto-run-1",
        idempotency_key="candidate:restart",
        candidate_id="candidate-1",
        candidate_snapshot_sha256="a" * 64,
        evidence_snapshot_sha256=_sha(evidence),
        candidate_version_ids=[],
    )
    started_review = store.start_auto_mode_review(
        "auto-run-1",
        review_run_id="review-restart-1",
        audit_id="audit-restart-1",
        idempotency_key="review-restart:start",
        candidate_id="candidate-1",
        candidate_snapshot_sha256="a" * 64,
        evidence_snapshot=evidence,
        evidence_snapshot_sha256=_sha(evidence),
        round_index=0,
        attempt=1,
        reviewer={
            "profile_id": "scientific-reviewer",
            "profile_revision": 1,
            "model_fingerprint": "reviewer-model",
        },
        started_at=120,
    )
    assert started_review["created"] is True
    store.close()

    reopened = _store(tmp_path, db_name)
    with pytest.raises(AutoModeConflictError, match="active durable phase"):
        reopened.terminate_auto_mode_run(
            "auto-run-1",
            idempotency_key="terminal:before-review-complete",
            status="failed",
            reason="must_not_strand_review",
        )
    assert (
        reopened._conn.execute(
            "SELECT COUNT(*) FROM auto_mode_events WHERE type='auto_run_terminal'"
        ).fetchone()[0]
        == 0
    )

    replayed_run = _start(reopened)
    assert replayed_run["created"] is False
    assert replayed_run["event_id"] == started_run["event_id"]
    replayed_review = reopened.start_auto_mode_review(
        "auto-run-1",
        review_run_id="review-restart-1",
        audit_id="audit-restart-1",
        idempotency_key="review-restart:start",
        candidate_id="candidate-1",
        candidate_snapshot_sha256="a" * 64,
        evidence_snapshot=evidence,
        evidence_snapshot_sha256=_sha(evidence),
        round_index=0,
        attempt=1,
        reviewer={
            "profile_id": "scientific-reviewer",
            "profile_revision": 1,
            "model_fingerprint": "reviewer-model",
        },
        started_at=999,
    )
    assert replayed_review["created"] is False
    assert replayed_review["event_id"] == started_review["event_id"]

    completed = reopened.complete_auto_mode_review(
        "review-restart-1",
        idempotency_key="review-restart:complete",
        status="completed",
        verdict="pass",
        assessment={"public_summary": "Restarted review passed."},
        findings=[],
        completed_at=130,
    )
    replayed_completion = reopened.complete_auto_mode_review(
        "review-restart-1",
        idempotency_key="review-restart:complete",
        status="completed",
        verdict="pass",
        assessment={"public_summary": "Restarted review passed."},
        findings=[],
        completed_at=999,
    )
    assert completed["created"] is True
    assert replayed_completion["created"] is False
    assert replayed_completion["event_id"] == completed["event_id"]
    assert (
        reopened._conn.execute(
            "SELECT COUNT(*) FROM review_runs WHERE review_run_id='review-restart-1'"
        ).fetchone()[0]
        == 1
    )
    assert (
        reopened._conn.execute(
            "SELECT COUNT(*) FROM auto_mode_events WHERE run_id='auto-run-1' "
            "AND type='auto_audit_started'"
        ).fetchone()[0]
        == 1
    )
    assert (
        reopened._conn.execute(
            "SELECT COUNT(*) FROM auto_mode_events WHERE run_id='auto-run-1' "
            "AND type='auto_audit_completed'"
        ).fetchone()[0]
        == 1
    )
    assert (
        reopened._conn.execute(
            "SELECT owner_instance_id FROM auto_mode_runs WHERE run_id='auto-run-1'"
        ).fetchone()[0]
        == "daemon-1"
    )

    terminal = reopened.terminate_auto_mode_run(
        "auto-run-1",
        idempotency_key="terminal:after-review-complete",
        status="verified",
        reason="review_passed_after_restart",
    )
    assert terminal["status"] == "verified"
    reopened.close()
