"""Fault, concurrency, and recovery contracts for Stage-2 Auto Mode state."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from openai4s.storage.auto_mode import AutoModeConflictError
from openai4s.storage.snapshots import WorkspaceCAS
from openai4s.store import Store


def _sha(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _rooted_store(tmp_path: Path, name: str = "openai4s.db") -> tuple[Store, str]:
    store = Store(tmp_path / name)
    project = store.create_project(name="Auto Mode fault test")
    root = store.new_frame(project_id=project["project_id"], kind="turn")
    store.ensure_session_branch(root_frame_id=root, branch_id=root)
    return store, root


def _start(
    store: Store,
    root: str,
    *,
    run_id: str = "run-1",
    turn_id: str = "turn-1",
    execution_id: str = "execution-1",
    owner_instance_id: str = "daemon-test",
) -> dict:
    return store.start_auto_mode_run(
        run_id=run_id,
        idempotency_key=f"{turn_id}:auto-run",
        root_frame_id=root,
        branch_id=root,
        turn_id=turn_id,
        execution_id=execution_id,
        mode="auto_fix",
        selection={
            "preset": "autonomous",
            "result_review_mode": "auto_fix",
            "approvals_reviewer": "auto_review",
            "source": "frame",
        },
        budgets={"max_review_attempts": 2, "max_repair_rounds": 2},
        owner_instance_id=owner_instance_id,
    )


def _candidate(store: Store, *, run_id: str = "run-1") -> dict:
    evidence = {"candidate_id": "candidate-1", "complete": True}
    evidence_digest = _sha(evidence)
    store.record_auto_mode_candidate(
        run_id,
        idempotency_key="candidate:1",
        candidate_id="candidate-1",
        candidate_snapshot_sha256="a" * 64,
        evidence_snapshot_sha256=evidence_digest,
        artifact_set_sha256="b" * 64,
        candidate_version_ids=["version-before"],
    )
    return {"evidence": evidence, "evidence_digest": evidence_digest}


def _start_result_review(
    store: Store,
    *,
    run_id: str = "run-1",
    review_run_id: str = "review-pass",
    audit_id: str = "audit-pass",
) -> dict:
    candidate = _candidate(store, run_id=run_id)
    return store.start_auto_mode_review(
        run_id,
        review_run_id=review_run_id,
        audit_id=audit_id,
        idempotency_key=f"{review_run_id}:start",
        candidate_id="candidate-1",
        candidate_snapshot_sha256="a" * 64,
        evidence_snapshot=candidate["evidence"],
        evidence_snapshot_sha256=candidate["evidence_digest"],
        round_index=0,
        attempt=1,
        reviewer={
            "profile_id": "scientific-reviewer",
            "profile_revision": 7,
            "model_fingerprint": "review-model-v7",
        },
    )


def _issue_review(store: Store, *, run_id: str = "run-1") -> dict:
    candidate = _candidate(store, run_id=run_id)
    store.start_auto_mode_review(
        run_id,
        review_run_id="review-issues",
        audit_id="audit-issues",
        idempotency_key="review:start",
        candidate_id="candidate-1",
        candidate_snapshot_sha256="a" * 64,
        evidence_snapshot=candidate["evidence"],
        evidence_snapshot_sha256=candidate["evidence_digest"],
        round_index=0,
        attempt=1,
        reviewer={
            "profile_id": "scientific-reviewer",
            "profile_revision": 7,
            "model_fingerprint": "review-model-v7",
        },
    )
    return store.complete_auto_mode_review(
        "review-issues",
        idempotency_key="review:complete",
        status="completed",
        verdict="completed_with_issues",
        assessment={"public_summary": "One material issue."},
        findings=[
            {
                "finding_id": "finding-1",
                "fingerprint": "stable-finding-1",
                "severity": "major",
                "category": "evidence",
                "claim": "The result needs an independent recomputation.",
                "evidence_refs": ["cell-1"],
                "artifact_ids": [],
                "version_ids": ["version-before"],
                "cell_ids": ["cell-1"],
            }
        ],
    )


def _pending_permission(
    store: Store,
    root: str,
    *,
    decision_id: str,
    expires_at: int | None = None,
) -> dict:
    group = store.append_action_group(
        root_frame_id=root,
        branch_id=root,
        turn_id="turn-1",
        kind="native_tools",
        assistant_content="Propose one exact action",
    )
    store.create_permission_request(
        decision_id=decision_id,
        root_frame_id=root,
        frame_id=root,
        action_group_id=group["group_id"],
        action_id=f"action-{decision_id}",
        tool="write_file",
        target="result.txt",
        side_effect_class="workspace_write",
        resource_keys=["workspace:result.txt"],
        canonical_arguments=[
            {
                "path": "result.txt",
                "content": f"exact content for {decision_id}",
            }
        ],
        expires_at=expires_at,
    )
    return group


def _bound_repair(
    store: Store, root: str, tmp_path: Path, *, repair_id: str = "repair-1"
) -> dict:
    _start(store, root)
    _issue_review(store)
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / "result.txt").write_text("candidate\n", encoding="utf-8")
    tree = WorkspaceCAS(tmp_path / "workspace-cas").capture(workspace)
    checkpoint = store.create_session_checkpoint(
        checkpoint_id=f"checkpoint-{repair_id}",
        root_frame_id=root,
        branch_id=root,
        reason="pre_repair",
        workspace_tree_id=tree["tree_id"],
        auto_event_cursor=store.auto_mode_event_cursor(root),
    )
    store.start_auto_mode_repair(
        "run-1",
        repair_run_id=repair_id,
        idempotency_key=f"{repair_id}:start",
        finding_ids=["finding-1"],
        before_version_ids=["version-before"],
        checkpoint_id=checkpoint["checkpoint_id"],
    )
    group = store.append_action_group(
        root_frame_id=root,
        branch_id=root,
        turn_id="turn-1",
        kind="native_tools",
        assistant_content="bounded repair",
    )
    store.bind_auto_mode_repair_execution_group(
        repair_id,
        action_group_id=group["group_id"],
        idempotency_key=f"{repair_id}:bind:{group['group_id']}",
    )
    return group


def test_permission_audit_fails_closed_for_expired_or_raced_decision(tmp_path):
    store, root = _rooted_store(tmp_path)
    _start(store, root)
    _pending_permission(store, root, decision_id="expired", expires_at=1)
    with pytest.raises(AutoModeConflictError, match="exact pending action scope"):
        store.start_permission_review_assessment(
            "run-1",
            assessment_id="assessment-expired",
            audit_id="audit-expired",
            decision_id="expired",
            action_digest=store.permission_request_action_digest("expired"),
            policy_version="guardian-v1",
            idempotency_key="permission:expired:start",
        )
    assert (
        store._conn.execute(
            "SELECT COUNT(*) FROM permission_review_assessments"
        ).fetchone()[0]
        == 0
    )

    _pending_permission(store, root, decision_id="race", expires_at=10**15)
    started = store.start_permission_review_assessment(
        "run-1",
        assessment_id="assessment-race",
        audit_id="audit-race",
        decision_id="race",
        action_digest=store.permission_request_action_digest("race"),
        policy_version="guardian-v1",
        idempotency_key="permission:race:start",
    )
    assert started["status"] == "started"
    store.resolve_permission_request("race", state="denied")
    with pytest.raises(AutoModeConflictError, match="no longer pending"):
        store.complete_permission_review_assessment(
            "assessment-race",
            idempotency_key="permission:race:complete",
            status="completed",
            outcome="allow_once",
            risk="low",
            assessment={"public_summary": "stale allow must not commit"},
        )
    row = store._conn.execute(
        "SELECT status,completion_idempotency_key FROM "
        "permission_review_assessments WHERE assessment_id='assessment-race'"
    ).fetchone()
    assert dict(row) == {"status": "started", "completion_idempotency_key": None}

    closed = store.complete_permission_review_assessment(
        "assessment-race",
        idempotency_key="permission:race:failed",
        status="failed",
        outcome="denied",
        risk="high",
        assessment={"public_summary": "Underlying request was already terminal."},
    )
    assert closed["status"] == "failed"
    assert store.get_permission_request("race")["state"] == "denied"
    with pytest.raises(ValueError, match="cannot allow"):
        store.complete_permission_review_assessment(
            "assessment-race",
            idempotency_key="permission:race:rewrite",
            status="failed",
            outcome="allow",
            risk="low",
            assessment={},
        )
    store.close()


def test_permission_assessment_is_audit_only_and_idempotent(tmp_path):
    store, root = _rooted_store(tmp_path)
    _start(store, root)
    _pending_permission(store, root, decision_id="decision-1", expires_at=10**15)
    first = store.start_permission_review_assessment(
        "run-1",
        assessment_id="assessment-1",
        audit_id="permission-audit-1",
        decision_id="decision-1",
        action_digest=store.permission_request_action_digest("decision-1"),
        policy_version="guardian-v1",
        idempotency_key="permission:start",
    )
    replay = store.start_permission_review_assessment(
        "run-1",
        assessment_id="ignored-on-replay",
        audit_id="ignored-on-replay",
        decision_id="decision-1",
        action_digest=store.permission_request_action_digest("decision-1"),
        policy_version="guardian-v1",
        idempotency_key="permission:start",
    )
    assert first["created"] is True
    assert replay["created"] is False
    assert replay["assessment_id"] == "assessment-1"
    completed = store.complete_permission_review_assessment(
        "assessment-1",
        idempotency_key="permission:complete",
        status="completed",
        outcome="denied",
        risk="high",
        assessment={"public_summary": "Denied by policy."},
    )
    replayed = store.complete_permission_review_assessment(
        "assessment-1",
        idempotency_key="permission:complete",
        status="completed",
        outcome="denied",
        risk="high",
        assessment={"public_summary": "Denied by policy."},
    )
    assert completed["created"] is True
    assert replayed["created"] is False
    assert replayed["event_id"] == completed["event_id"]
    # Stage 2 records evidence only. It neither resolves the request nor creates
    # a reusable allow rule or an allow-once capability.
    assert store.get_permission_request("decision-1")["state"] == "pending"
    assert store.get_permission_rules(scope="conversation", scope_id=root) == []
    store.close()


def test_permission_owner_tamper_fails_closed_on_replay_export_and_audit(tmp_path):
    store, root = _rooted_store(tmp_path)
    _start(store, root)
    _pending_permission(store, root, decision_id="decision-proof", expires_at=10**15)
    store.start_permission_review_assessment(
        "run-1",
        assessment_id="assessment-proof",
        audit_id="permission-audit-proof",
        decision_id="decision-proof",
        action_digest=store.permission_request_action_digest("decision-proof"),
        policy_version="guardian-v1",
        idempotency_key="permission:proof:start",
    )
    store.complete_permission_review_assessment(
        "assessment-proof",
        idempotency_key="permission:proof:complete",
        status="completed",
        outcome="denied",
        risk="high",
        assessment={"public_summary": "Denied by exact-action policy."},
    )
    store._conn.execute(
        "UPDATE permission_review_assessments SET outcome='allow_once',risk='low' "
        "WHERE assessment_id='assessment-proof'"
    )
    store._conn.commit()

    with pytest.raises(AutoModeConflictError, match="permission assessment proof"):
        store.complete_permission_review_assessment(
            "assessment-proof",
            idempotency_key="permission:proof:complete",
            status="completed",
            outcome="denied",
            risk="high",
            assessment={"public_summary": "Denied by exact-action policy."},
        )
    with pytest.raises(
        AutoModeConflictError,
        match="permission assessment proof|export projection integrity",
    ):
        store.export_auto_mode_projection(root, branch_id=root)

    # Public audit truth acknowledges the broken proof but never preserves an
    # allow/pass-shaped claim from either side of the mismatch.
    audits = store.list_auto_mode_audits(root, root)
    assert len(audits) == 1
    assert audits[0]["status"] == "failed"
    assert audits[0]["risk"] == "unknown"
    assert audits[0]["error_kind"] == "integrity_failure"
    assert "outcome" not in audits[0]
    store.close()


def test_permission_action_tamper_after_audit_start_fails_closed(tmp_path):
    store, root = _rooted_store(tmp_path)
    _start(store, root)
    _pending_permission(
        store, root, decision_id="decision-action-tamper", expires_at=10**15
    )
    store.start_permission_review_assessment(
        "run-1",
        assessment_id="assessment-action-tamper",
        audit_id="permission-audit-action-tamper",
        decision_id="decision-action-tamper",
        action_digest=store.permission_request_action_digest("decision-action-tamper"),
        policy_version="guardian-v1",
        idempotency_key="permission:action-tamper:start",
    )

    with pytest.raises(sqlite3.IntegrityError, match="action identity is immutable"):
        store._conn.execute(
            "UPDATE permission_requests SET target='other.txt' "
            "WHERE decision_id='decision-action-tamper'"
        )
    store._conn.rollback()
    assert store.get_permission_request("decision-action-tamper")["target"] == (
        "result.txt"
    )

    # Model an offline-corrupted database below the application trigger. The
    # completion path must re-read and validate the exact durable envelope.
    store._conn.execute("DROP TRIGGER trg_permission_action_immutable")
    store._conn.execute(
        "UPDATE permission_requests SET canonical_arguments_sha256=? "
        "WHERE decision_id='decision-action-tamper'",
        ("0" * 64,),
    )
    store._conn.commit()
    with pytest.raises(AutoModeConflictError, match="permission assessment proof"):
        store.complete_permission_review_assessment(
            "assessment-action-tamper",
            idempotency_key="permission:action-tamper:complete",
            status="completed",
            outcome="allow_once",
            risk="low",
            assessment={"public_summary": "Corrupt action must not be allowed."},
        )

    assessment = store._conn.execute(
        "SELECT status,completion_idempotency_key FROM "
        "permission_review_assessments WHERE assessment_id=?",
        ("assessment-action-tamper",),
    ).fetchone()
    assert dict(assessment) == {
        "status": "started",
        "completion_idempotency_key": None,
    }
    audits = store.list_auto_mode_audits(root, root)
    assert len(audits) == 1
    assert audits[0]["status"] == "failed"
    assert audits[0]["risk"] == "unknown"
    assert audits[0]["error_kind"] == "integrity_failure"
    assert "outcome" not in audits[0]
    with pytest.raises(
        AutoModeConflictError,
        match="permission assessment proof|export projection integrity",
    ):
        store.export_auto_mode_projection(root, branch_id=root)
    store.close()


@pytest.mark.parametrize(
    ("subject_kind", "owner_table", "unsafe_claim"),
    [
        ("result_review", "review_runs", "verdict"),
        ("permission_review", "permission_review_assessments", "outcome"),
    ],
)
def test_missing_audit_owner_fails_closed_on_projection_and_public_audit(
    tmp_path, subject_kind: str, owner_table: str, unsafe_claim: str
):
    store, root = _rooted_store(tmp_path)
    _start(store, root)
    if subject_kind == "result_review":
        _start_result_review(store)
        store.complete_auto_mode_review(
            "review-pass",
            idempotency_key="review-pass:complete",
            status="completed",
            verdict="pass",
            assessment={"public_summary": "Independent review passed."},
            findings=[],
        )
    else:
        _pending_permission(
            store, root, decision_id="decision-missing", expires_at=10**15
        )
        store.start_permission_review_assessment(
            "run-1",
            assessment_id="assessment-missing",
            audit_id="permission-audit-missing",
            decision_id="decision-missing",
            action_digest=store.permission_request_action_digest("decision-missing"),
            policy_version="guardian-v1",
            idempotency_key="permission-missing:start",
        )
        store.complete_permission_review_assessment(
            "assessment-missing",
            idempotency_key="permission-missing:complete",
            status="completed",
            outcome="allow_once",
            risk="low",
            assessment={"public_summary": "Claimed allow-once decision."},
        )

    store._conn.execute(f"DELETE FROM {owner_table}")
    store._conn.commit()

    projected = store.project_auto_mode_run(root, root)
    assert projected["run"]["status"] == "failed"
    assert projected["run"]["terminal_reason"] == "safety_boundary"
    assert projected["run"]["source_claimed_status"] in {"candidate", "running"}

    audits = store.list_auto_mode_audits(root, root, subject_kind=subject_kind)
    assert len(audits) == 1
    assert audits[0]["status"] == "failed"
    assert audits[0]["risk"] == "unknown"
    assert audits[0]["error_kind"] == "integrity_failure"
    assert unsafe_claim not in audits[0]
    store.close()


def test_permission_completion_event_tamper_cannot_publish_allow(tmp_path):
    store, root = _rooted_store(tmp_path)
    _start(store, root)
    _pending_permission(store, root, decision_id="decision-event", expires_at=10**15)
    store.start_permission_review_assessment(
        "run-1",
        assessment_id="assessment-event",
        audit_id="permission-audit-event",
        decision_id="decision-event",
        action_digest=store.permission_request_action_digest("decision-event"),
        policy_version="guardian-v1",
        idempotency_key="permission-event:start",
    )
    store.complete_permission_review_assessment(
        "assessment-event",
        idempotency_key="permission-event:complete",
        status="completed",
        outcome="denied",
        risk="high",
        assessment={"public_summary": "Denied by exact-action policy."},
    )
    event = store._conn.execute(
        "SELECT event_id,payload_json FROM auto_mode_events "
        "WHERE run_id='run-1' AND type='auto_audit_completed'"
    ).fetchone()
    payload = json.loads(event["payload_json"])
    payload.update({"outcome": "allow_once", "risk": "low"})
    store._conn.execute(
        "UPDATE auto_mode_events SET payload_json=?,payload_sha256=? "
        "WHERE event_id=?",
        (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            _sha(payload),
            event["event_id"],
        ),
    )
    store._conn.commit()

    audits = store.list_auto_mode_audits(root, root)
    assert len(audits) == 1
    assert audits[0]["status"] == "failed"
    assert audits[0]["risk"] == "unknown"
    assert audits[0]["error_kind"] == "integrity_failure"
    assert "outcome" not in audits[0]
    store.close()


@pytest.mark.parametrize(
    ("status", "severity", "message"),
    [
        ("unavailable", None, "non-completed review cannot pass"),
        ("failed", None, "non-completed review cannot pass"),
        ("completed", "material", "pass review cannot contain a material finding"),
        ("completed", "high", "pass review cannot contain a material finding"),
    ],
)
def test_contradictory_result_review_completion_is_rejected(
    tmp_path, status: str, severity: str | None, message: str
):
    store, root = _rooted_store(tmp_path)
    _start(store, root)
    _start_result_review(store)
    findings = []
    if severity is not None:
        findings.append(
            {
                "finding_id": f"finding-{severity}",
                "fingerprint": f"finding-{severity}",
                "severity": severity,
                "category": "evidence",
                "claim": "A material result defect cannot coexist with pass.",
                "evidence_refs": ["cell-1"],
                "artifact_ids": [],
                "version_ids": ["version-before"],
                "cell_ids": ["cell-1"],
            }
        )

    with pytest.raises(ValueError, match=message):
        store.complete_auto_mode_review(
            "review-pass",
            idempotency_key="review-pass:complete",
            status=status,
            verdict="pass",
            assessment={"public_summary": "Contradictory pass claim."},
            findings=findings,
        )

    owner = store._conn.execute(
        "SELECT status,completion_idempotency_key FROM review_runs "
        "WHERE review_run_id='review-pass'"
    ).fetchone()
    assert dict(owner) == {"status": "started", "completion_idempotency_key": None}
    assert (
        store._conn.execute(
            "SELECT COUNT(*) FROM auto_mode_events WHERE type='auto_audit_completed'"
        ).fetchone()[0]
        == 0
    )
    assert (
        store._conn.execute("SELECT COUNT(*) FROM review_findings").fetchone()[0] == 0
    )
    store.close()


def test_audit_page_validation_work_is_bounded_by_requested_page(tmp_path, monkeypatch):
    def measure(audit_count: int) -> tuple[int, int]:
        store, root = _rooted_store(tmp_path, f"audits-{audit_count}.db")
        _start(store, root)
        for index in range(audit_count):
            decision_id = f"decision-{index}"
            _pending_permission(store, root, decision_id=decision_id, expires_at=10**15)
            store.start_permission_review_assessment(
                "run-1",
                assessment_id=f"assessment-{index}",
                audit_id=f"permission-audit-{index}",
                decision_id=decision_id,
                action_digest=store.permission_request_action_digest(decision_id),
                policy_version="guardian-v1",
                idempotency_key=f"permission:{index}:start",
            )
            store.complete_permission_review_assessment(
                f"assessment-{index}",
                idempotency_key=f"permission:{index}:complete",
                status="completed",
                outcome="denied",
                risk="high",
                assessment={"public_summary": f"Denied action {index}."},
            )

        proof_calls = 0
        repository = store._auto_mode
        original = repository._assert_permission_assessment_proof_locked

        def counted_proof(*args, **kwargs):
            nonlocal proof_calls
            proof_calls += 1
            return original(*args, **kwargs)

        statements: list[str] = []
        with monkeypatch.context() as patch:
            patch.setattr(
                repository,
                "_assert_permission_assessment_proof_locked",
                counted_proof,
            )
            store._conn.set_trace_callback(statements.append)
            try:
                audits = store.list_auto_mode_audits(root, root, limit=1)
            finally:
                store._conn.set_trace_callback(None)
        assert len(audits) == 1
        event_selects = sum(
            statement.lstrip().upper().startswith("SELECT")
            and "FROM AUTO_MODE_EVENTS" in statement.upper()
            for statement in statements
        )
        store.close()
        return event_selects, proof_calls

    small = measure(8)
    large = measure(64)
    assert small == large
    assert small[0] <= 2
    assert small[1] == 1


def test_overlapping_audit_pagination_pairs_before_filtering(tmp_path):
    store, root = _rooted_store(tmp_path)
    _start(store, root)
    for suffix in ("a", "b"):
        _pending_permission(
            store,
            root,
            decision_id=f"decision-{suffix}",
            expires_at=10**15,
        )
        store.start_permission_review_assessment(
            "run-1",
            assessment_id=f"assessment-{suffix}",
            audit_id=f"permission-audit-{suffix}",
            decision_id=f"decision-{suffix}",
            action_digest=store.permission_request_action_digest(f"decision-{suffix}"),
            policy_version="guardian-v1",
            idempotency_key=f"permission:{suffix}:start",
        )

    # Complete B first, then A. Their intervals overlap:
    # A(start), B(start), B(complete), A(complete).
    for suffix in ("b", "a"):
        store.complete_permission_review_assessment(
            f"assessment-{suffix}",
            idempotency_key=f"permission:{suffix}:complete",
            status="completed",
            outcome="denied",
            risk="high",
            assessment={"public_summary": f"Denied {suffix}."},
        )

    first = store.list_auto_mode_audits(root, root, limit=1)
    assert [(item["audit_id"], item["status"]) for item in first] == [
        ("permission-audit-a", "completed")
    ]
    second = store.list_auto_mode_audits(
        root,
        root,
        before=str(first[0]["event_ordinal"]),
        limit=1,
    )
    assert [(item["audit_id"], item["status"]) for item in second] == [
        ("permission-audit-b", "completed")
    ]
    assert (
        store.list_auto_mode_audits(
            root,
            root,
            before=str(second[0]["event_ordinal"]),
            limit=1,
        )
        == []
    )
    by_identity = store.list_auto_mode_audits(
        root,
        root,
        before="permission-audit-a",
        limit=1,
    )
    assert by_identity == second
    store.close()


def test_repair_requires_current_restorable_checkpoint_and_fresh_review(tmp_path):
    store, root = _rooted_store(tmp_path)
    _start(store, root)
    review = _issue_review(store)
    assert review["findings"][0]["status"] == "open"

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "result.txt").write_text("candidate v1\n", encoding="utf-8")
    tree = WorkspaceCAS(tmp_path / "workspace-cas").capture(workspace)
    checkpoint = store.create_session_checkpoint(
        checkpoint_id="checkpoint-repair",
        root_frame_id=root,
        branch_id=root,
        reason="pre_repair",
        workspace_tree_id=tree["tree_id"],
        auto_event_cursor=store.auto_mode_event_cursor(root),
    )
    repair = store.start_auto_mode_repair(
        "run-1",
        repair_run_id="repair-1",
        idempotency_key="repair:start",
        finding_ids=["finding-1"],
        before_version_ids=["version-before"],
        checkpoint_id=checkpoint["checkpoint_id"],
    )
    replay = store.start_auto_mode_repair(
        "run-1",
        repair_run_id="ignored-on-replay",
        idempotency_key="repair:start",
        finding_ids=["finding-1"],
        before_version_ids=["version-before"],
        checkpoint_id=checkpoint["checkpoint_id"],
    )
    assert repair["created"] is True
    assert replay["created"] is False
    assert replay["repair_run_id"] == "repair-1"

    wrong_group = store.append_action_group(
        root_frame_id=root,
        branch_id=root,
        turn_id="another-turn",
        kind="native_tools",
        assistant_content="wrong scope",
    )
    with pytest.raises(AutoModeConflictError, match="another action scope"):
        store.bind_auto_mode_repair_execution_group(
            "repair-1",
            action_group_id=wrong_group["group_id"],
            idempotency_key="repair:bind:wrong",
        )
    assert (
        store._conn.execute(
            "SELECT status FROM repair_runs WHERE repair_run_id='repair-1'"
        ).fetchone()[0]
        == "started"
    )

    group = store.append_action_group(
        root_frame_id=root,
        branch_id=root,
        turn_id="turn-1",
        kind="native_tools",
        assistant_content="bounded repair",
    )
    bound = store.bind_auto_mode_repair_execution_group(
        "repair-1",
        action_group_id=group["group_id"],
        idempotency_key="repair:bind:1",
    )
    replayed_binding = store.bind_auto_mode_repair_execution_group(
        "repair-1",
        action_group_id=group["group_id"],
        idempotency_key="repair:bind:1",
    )
    assert bound["created"] is True
    assert replayed_binding["created"] is False
    with pytest.raises(AutoModeConflictError, match="not terminal"):
        store.complete_auto_mode_repair(
            "repair-1",
            idempotency_key="repair:complete:premature",
            status="completed",
            after_version_ids=["version-after"],
            execution_group_ids=[group["group_id"]],
        )
    store.append_action_event(
        group_id=group["group_id"],
        type="proposed",
        action_id="repair-action-1",
        tool_call_id="repair-action-1",
        side_effect_class="workspace_write",
        resource_keys=["workspace:result.txt"],
    )
    store.append_action_event(
        group_id=group["group_id"],
        type="result",
        action_id="repair-action-1",
        tool_call_id="repair-action-1",
        result={"status": "completed"},
        side_effect_class="workspace_write",
        resource_keys=["workspace:result.txt"],
    )
    attempt = store.allocate_execution_attempt(
        group_id=group["group_id"],
        producing_cell_id="repair-cell-1",
    )
    store.mark_execution_attempt_started(attempt["attempt_id"])
    store.mark_execution_attempt_response(attempt["attempt_id"])
    store.mark_execution_attempt_capture(attempt["attempt_id"])
    store.finish_execution_attempt(attempt["attempt_id"], terminal_state="completed")
    database_path = store.db_path
    store.close()

    # A committed action group remains attached after restart. Recovery must
    # finish from this ledger, not create or execute a replacement group.
    store = Store(database_path)
    active_repair = store.export_auto_mode_projection(root)["repair_runs"][0]
    assert active_repair["status"] == "started"
    assert active_repair["execution_group_ids"] == [group["group_id"]]
    completed = store.complete_auto_mode_repair(
        "repair-1",
        idempotency_key="repair:complete",
        status="completed",
        after_version_ids=["version-after"],
        execution_group_ids=[group["group_id"]],
    )
    assert completed["status"] == "completed"
    sealed = store._conn.execute(
        "SELECT ledger_event_count,ledger_sha256,sealed_at "
        "FROM repair_execution_groups WHERE repair_run_id='repair-1'"
    ).fetchone()
    assert sealed["ledger_event_count"] == 2
    assert len(sealed["ledger_sha256"]) == 64
    assert sealed["sealed_at"] is not None
    with pytest.raises(sqlite3.IntegrityError, match="ledger is sealed"):
        store.append_action_event(
            group_id=group["group_id"],
            type="result",
            action_id="repair-action-late",
            tool_call_id="repair-action-late",
            result={"status": "late"},
            side_effect_class="workspace_write",
            resource_keys=["workspace:late.txt"],
        )
    with pytest.raises(sqlite3.IntegrityError, match="ledger is sealed"):
        store.allocate_execution_attempt(
            group_id=group["group_id"],
            producing_cell_id="late-repair-cell",
        )
    with pytest.raises(sqlite3.IntegrityError, match="ledger is sealed"):
        store._conn.execute(
            "DELETE FROM action_events WHERE group_id=? AND type='result'",
            (group["group_id"],),
        )
    with pytest.raises(sqlite3.IntegrityError, match="ledger is sealed"):
        store._conn.execute(
            "DELETE FROM execution_attempts WHERE attempt_id=?",
            (attempt["attempt_id"],),
        )
    store._conn.rollback()
    source_group = store.append_action_group(
        root_frame_id=root,
        branch_id=root,
        turn_id="turn-1",
        kind="native_tools",
        assistant_content="Unbound source group",
    )
    source_event = store.append_action_event(
        group_id=source_group["group_id"],
        type="proposed",
        action_id="source-action",
        tool_call_id="source-action",
        side_effect_class="workspace_write",
        resource_keys=["workspace:source.txt"],
    )
    source_attempt = store.allocate_execution_attempt(
        group_id=source_group["group_id"],
        producing_cell_id="source-cell",
    )
    with pytest.raises(sqlite3.IntegrityError, match="ledger is sealed"):
        store._conn.execute(
            "UPDATE action_events SET group_id=? WHERE event_id=?",
            (group["group_id"], source_event["event_id"]),
        )
    with pytest.raises(sqlite3.IntegrityError, match="ledger is sealed"):
        store._conn.execute(
            "UPDATE execution_attempts SET group_id=? WHERE attempt_id=?",
            (group["group_id"], source_attempt["attempt_id"]),
        )
    store._conn.rollback()
    run = store.export_auto_mode_projection(root)["runs"][0]
    assert run["status"] == "running"
    assert run["candidate_id"] is None
    assert (
        store._conn.execute(
            "SELECT status FROM review_findings WHERE finding_id='finding-1'"
        ).fetchone()[0]
        == "addressed_pending_review"
    )
    with pytest.raises(AutoModeConflictError, match="candidate"):
        store.terminate_auto_mode_run(
            "run-1",
            idempotency_key="terminal:verified",
            status="verified",
            reason="repair_self_attestation",
        )
    replayed = store.complete_auto_mode_repair(
        "repair-1",
        idempotency_key="repair:complete",
        status="completed",
        after_version_ids=["version-after"],
        execution_group_ids=[group["group_id"]],
    )
    assert replayed["created"] is False

    # Simulate an offline database edit that bypassed the runtime trigger. The
    # sealed digest must still make every read/replay boundary fail closed.
    store._conn.execute("DROP TRIGGER trg_repair_event_delete_sealed")
    store._conn.execute(
        "DELETE FROM action_events WHERE group_id=? AND type='result'",
        (group["group_id"],),
    )
    store._conn.commit()
    with pytest.raises(AutoModeConflictError, match="repair ledger proof"):
        store.complete_auto_mode_repair(
            "repair-1",
            idempotency_key="repair:complete",
            status="completed",
            after_version_ids=["version-after"],
            execution_group_ids=[group["group_id"]],
        )
    with pytest.raises(
        AutoModeConflictError, match="repair ledger proof|export projection integrity"
    ):
        store.export_auto_mode_projection(root, branch_id=root)
    store.close()


def test_repair_completion_rejects_pending_permission_before_sealing(tmp_path):
    store, root = _rooted_store(tmp_path)
    group = _bound_repair(store, root, tmp_path)
    store.append_action_event(
        group_id=group["group_id"],
        type="proposed",
        action_id="repair-permission-action",
        tool_call_id="repair-permission-action",
        side_effect_class="workspace_write",
        resource_keys=["workspace:result.txt"],
    )
    store.create_permission_request(
        decision_id="repair-permission-pending",
        root_frame_id=root,
        frame_id=root,
        action_group_id=group["group_id"],
        action_id="repair-permission-action",
        tool_call_id="repair-permission-action",
        tool="write_file",
        target="result.txt",
        side_effect_class="workspace_write",
        resource_keys=["workspace:result.txt"],
        canonical_arguments=[{"path": "result.txt", "content": "repaired"}],
    )

    with pytest.raises(AutoModeConflictError, match="pending permission"):
        store.complete_auto_mode_repair(
            "repair-1",
            idempotency_key="repair:unknown:pending",
            status="outcome_unknown",
            after_version_ids=[],
            execution_group_ids=[group["group_id"]],
        )
    assert store._conn.in_transaction is False
    assert (
        store.get_permission_request("repair-permission-pending")["state"] == "pending"
    )
    assert (
        store._conn.execute(
            "SELECT sealed_at FROM repair_execution_groups WHERE action_group_id=?",
            (group["group_id"],),
        ).fetchone()[0]
        is None
    )

    store.resolve_permission_request("repair-permission-pending", state="denied")
    completed = store.complete_auto_mode_repair(
        "repair-1",
        idempotency_key="repair:failed:denied",
        status="failed",
        after_version_ids=[],
        execution_group_ids=[group["group_id"]],
    )
    assert completed["status"] == "failed"
    assert (
        store.get_permission_request("repair-permission-pending")["state"] == "denied"
    )
    store.close()


def test_outcome_unknown_pauses_and_revokes_restart_continuation(tmp_path):
    store, root = _rooted_store(tmp_path)
    group = _bound_repair(store, root, tmp_path)
    store.append_action_event(
        group_id=group["group_id"],
        type="proposed",
        action_id="repair-unknown-action",
        tool_call_id="repair-unknown-action",
        side_effect_class="workspace_write",
        resource_keys=["workspace:result.txt"],
    )
    store.create_permission_request(
        decision_id="repair-restart-once",
        root_frame_id=root,
        frame_id=root,
        action_group_id=group["group_id"],
        action_id="repair-unknown-action",
        tool_call_id="repair-unknown-action",
        tool="write_file",
        target="result.txt",
        side_effect_class="workspace_write",
        resource_keys=["workspace:result.txt"],
        canonical_arguments=[{"path": "result.txt", "content": "repaired"}],
    )
    store.resolve_permission_request(
        "repair-restart-once",
        state="allowed",
        scope="once",
        resolution_context="after_restart",
    )
    store.activate_restart_permission_continuation(
        "repair-restart-once", expires_at=10**15
    )
    attempt = store.allocate_execution_attempt(
        group_id=group["group_id"],
        producing_cell_id="repair-unknown-cell",
        owner_instance_id="old-daemon",
    )
    store.mark_execution_attempt_started(attempt["attempt_id"])

    completed = store.complete_auto_mode_repair(
        "repair-1",
        idempotency_key="repair:unknown:complete",
        status="outcome_unknown",
        after_version_ids=[],
        execution_group_ids=[group["group_id"]],
    )
    assert completed["status"] == "outcome_unknown"
    projected = store.project_auto_mode_run(root, root)["run"]
    assert projected["status"] == "paused"
    assert projected["terminal_reason"] == "outcome_unknown"
    request = store.get_permission_request("repair-restart-once")
    assert not request["continuation_required"]
    assert (
        store.consume_restart_permission_grant(
            root_frame_id=root,
            tool="write_file",
            target="result.txt",
            side_effect_class="workspace_write",
            resource_keys=["workspace:result.txt"],
            canonical_arguments=[{"path": "result.txt", "content": "repaired"}],
        )
        is None
    )
    attempt_row = store.list_execution_attempts(group_id=group["group_id"])[0]
    assert attempt_row["terminal_state"] == "outcome_unknown"
    assert (
        store.abandon_incomplete_execution_attempts(owner_instance_id="new-daemon") == 0
    )
    replay = store.complete_auto_mode_repair(
        "repair-1",
        idempotency_key="repair:unknown:complete",
        status="outcome_unknown",
        after_version_ids=[],
        execution_group_ids=[group["group_id"]],
    )
    assert replay["created"] is False
    store.close()


def test_failed_repair_uses_aggregate_known_failure_across_groups(tmp_path):
    store, root = _rooted_store(tmp_path)
    successful_read = _bound_repair(store, root, tmp_path)
    failed_write = store.append_action_group(
        root_frame_id=root,
        branch_id=root,
        turn_id="turn-1",
        kind="native_tools",
        assistant_content="second bounded repair group",
    )
    store.bind_auto_mode_repair_execution_group(
        "repair-1",
        action_group_id=failed_write["group_id"],
        idempotency_key="repair:bind:failed-write",
    )
    store.append_action_event(
        group_id=successful_read["group_id"],
        type="proposed",
        action_id="read-action",
        tool_call_id="read-action",
        side_effect_class="read_only",
        resource_keys=["workspace:result.txt"],
    )
    store.append_action_event(
        group_id=successful_read["group_id"],
        type="result",
        action_id="read-action",
        tool_call_id="read-action",
        result={"status": "completed"},
        side_effect_class="read_only",
        resource_keys=["workspace:result.txt"],
    )
    store.append_action_event(
        group_id=failed_write["group_id"],
        type="proposed",
        action_id="write-action",
        tool_call_id="write-action",
        side_effect_class="workspace_write",
        resource_keys=["workspace:result.txt"],
    )
    store.append_action_event(
        group_id=failed_write["group_id"],
        type="result",
        action_id="write-action",
        tool_call_id="write-action",
        result={"status": "failed", "is_error": True, "output_committed": False},
        side_effect_class="workspace_write",
        resource_keys=["workspace:result.txt"],
    )

    completed = store.complete_auto_mode_repair(
        "repair-1",
        idempotency_key="repair:aggregate:failed",
        status="failed",
        after_version_ids=[],
        execution_group_ids=[successful_read["group_id"], failed_write["group_id"]],
    )
    assert completed["status"] == "failed"
    assert store.project_auto_mode_run(root, root)["run"]["status"] == "candidate"
    store.close()


def test_partial_commit_failure_pauses_instead_of_retrying(tmp_path):
    store, root = _rooted_store(tmp_path)
    committed = _bound_repair(store, root, tmp_path)
    failed = store.append_action_group(
        root_frame_id=root,
        branch_id=root,
        turn_id="turn-1",
        kind="native_tools",
    )
    store.bind_auto_mode_repair_execution_group(
        "repair-1",
        action_group_id=failed["group_id"],
        idempotency_key="repair:bind:partial-failure",
    )
    for group, action_id, result in (
        (
            committed,
            "committed-write",
            {"status": "completed", "output_committed": True},
        ),
        (
            failed,
            "failed-write",
            {"status": "failed", "is_error": True, "output_committed": False},
        ),
    ):
        store.append_action_event(
            group_id=group["group_id"],
            type="proposed",
            action_id=action_id,
            tool_call_id=action_id,
            side_effect_class="workspace_write",
            resource_keys=[f"workspace:{action_id}.txt"],
        )
        store.append_action_event(
            group_id=group["group_id"],
            type="result",
            action_id=action_id,
            tool_call_id=action_id,
            result=result,
            side_effect_class="workspace_write",
            resource_keys=[f"workspace:{action_id}.txt"],
        )

    completed = store.complete_auto_mode_repair(
        "repair-1",
        idempotency_key="repair:partial:failed",
        status="failed",
        after_version_ids=["version-partial"],
        execution_group_ids=[committed["group_id"], failed["group_id"]],
    )
    assert completed["status"] == "failed"
    projected = store.project_auto_mode_run(root, root)["run"]
    assert projected["status"] == "paused"
    assert projected["terminal_reason"] == "repair_partial_commit"
    with pytest.raises(AutoModeConflictError, match="terminal"):
        store.start_auto_mode_repair(
            "run-1",
            repair_run_id="repair-retry-forbidden",
            idempotency_key="repair:retry:forbidden",
            finding_ids=["finding-1"],
            before_version_ids=["version-partial"],
            checkpoint_id="checkpoint-repair-1",
        )
    replay = store.complete_auto_mode_repair(
        "repair-1",
        idempotency_key="repair:partial:failed",
        status="failed",
        after_version_ids=["version-partial"],
        execution_group_ids=[committed["group_id"], failed["group_id"]],
    )
    assert replay["created"] is False
    store.close()


def test_unstarted_repair_attempt_cannot_be_laundered_as_unknown(tmp_path):
    store, root = _rooted_store(tmp_path)
    group = _bound_repair(store, root, tmp_path)
    store.append_action_event(
        group_id=group["group_id"],
        type="proposed",
        action_id="never-started",
        tool_call_id="never-started",
        side_effect_class="workspace_write",
        resource_keys=["workspace:never.txt"],
    )
    store.allocate_execution_attempt(
        group_id=group["group_id"], producing_cell_id="never-started-cell"
    )

    with pytest.raises(AutoModeConflictError, match="uncertain side-effect"):
        store.complete_auto_mode_repair(
            "repair-1",
            idempotency_key="repair:unknown:never-started",
            status="outcome_unknown",
            after_version_ids=[],
            execution_group_ids=[group["group_id"]],
        )
    attempt = store.list_execution_attempts(group_id=group["group_id"])[0]
    assert attempt["terminal_state"] is None
    assert (
        store._conn.execute(
            "SELECT sealed_at FROM repair_execution_groups WHERE action_group_id=?",
            (group["group_id"],),
        ).fetchone()[0]
        is None
    )
    store.close()


def test_revert_after_bound_effect_abandons_repair_admission(tmp_path):
    store, root = _rooted_store(tmp_path)
    group = _bound_repair(store, root, tmp_path)
    target = store.create_session_checkpoint(
        checkpoint_id="checkpoint-after-bind",
        root_frame_id=root,
        branch_id=root,
        reason="after_bind",
        workspace_tree_id=None,
        auto_event_cursor=store.auto_mode_event_cursor(root),
    )
    store.append_action_event(
        group_id=group["group_id"],
        type="proposed",
        action_id="effect-before-revert",
        tool_call_id="effect-before-revert",
        side_effect_class="workspace_write",
        resource_keys=["workspace:result.txt"],
    )
    store.append_action_event(
        group_id=group["group_id"],
        type="result",
        action_id="effect-before-revert",
        tool_call_id="effect-before-revert",
        result={"status": "completed"},
        side_effect_class="workspace_write",
        resource_keys=["workspace:result.txt"],
    )
    store.append_action_event(
        group_id=group["group_id"],
        type="proposed",
        action_id="late-result",
        tool_call_id="late-result",
        side_effect_class="workspace_write",
        resource_keys=["workspace:late.txt"],
    )
    attempt = store.allocate_execution_attempt(
        group_id=group["group_id"], producing_cell_id="post-target-cell"
    )
    resume_cursor = store.auto_mode_event_cursor(root)
    undo = store.create_session_checkpoint(
        checkpoint_id="checkpoint-revert-undo",
        root_frame_id=root,
        branch_id=root,
        reason="before_revert",
        workspace_tree_id=None,
        auto_event_cursor=resume_cursor,
    )
    store.create_session_checkpoint(
        checkpoint_id="checkpoint-revert-applied",
        root_frame_id=root,
        branch_id=root,
        reason="revert_continue",
        workspace_tree_id=None,
        auto_event_cursor=target["auto_event_cursor"],
        metadata={
            "reverted_to": target["checkpoint_id"],
            "undo_checkpoint_id": undo["checkpoint_id"],
            "history_projection": {
                "version": 1,
                "base_checkpoint_id": target["checkpoint_id"],
                "resume_cursors": {"auto_event_cursor": resume_cursor},
            },
        },
    )
    owner = store._conn.execute(
        "SELECT abandoned_by_checkpoint_id FROM auto_mode_runs WHERE run_id='run-1'"
    ).fetchone()
    assert owner["abandoned_by_checkpoint_id"] == "checkpoint-revert-applied"

    with pytest.raises(AutoModeConflictError, match="abandoned branch tail"):
        store.append_action_event(
            group_id=group["group_id"],
            type="proposed",
            action_id="new-effect-after-revert",
            tool_call_id="new-effect-after-revert",
            side_effect_class="workspace_write",
            resource_keys=["workspace:new.txt"],
        )
    with pytest.raises(AutoModeConflictError, match="abandoned branch tail"):
        store.allocate_execution_attempt(
            group_id=group["group_id"], producing_cell_id="new-cell-after-revert"
        )
    with pytest.raises(AutoModeConflictError, match="abandoned branch tail"):
        store.mark_execution_attempt_started(attempt["attempt_id"])

    # A late terminal observation is audit evidence, not fresh authority.
    late = store.append_action_event(
        group_id=group["group_id"],
        type="result",
        action_id="late-result",
        tool_call_id="late-result",
        result={"status": "failed", "output_committed": False},
        side_effect_class="workspace_write",
        resource_keys=["workspace:late.txt"],
    )
    assert late["type"] == "result"
    store.close()


def test_cross_connection_start_is_exactly_once(tmp_path):
    path = tmp_path / "concurrent.db"
    seed = Store(path)
    project = seed.create_project(name="Concurrent Auto Mode start")
    root = seed.new_frame(project_id=project["project_id"], kind="turn")
    seed.ensure_session_branch(root_frame_id=root, branch_id=root)
    seed.close()
    barrier = threading.Barrier(2)
    results: list[bool] = []
    errors: list[BaseException] = []

    def worker() -> None:
        store = Store(path)
        try:
            barrier.wait(timeout=10)
            results.append(_start(store, root)["created"])
        except BaseException as error:  # pragma: no cover - asserted below
            errors.append(error)
        finally:
            store.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert sorted(results) == [False, True]

    reopened = Store(path)
    events = reopened.list_auto_mode_events(root)
    assert [(event["event_cursor"], event["type"]) for event in events] == [
        (1, "auto_run_started")
    ]
    assert (
        reopened._conn.execute(
            "SELECT COUNT(*) FROM auto_mode_runs WHERE root_frame_id=?", (root,)
        ).fetchone()[0]
        == 1
    )
    reopened.close()


def test_reopen_cannot_hide_an_older_active_run_on_the_same_branch(tmp_path):
    path = tmp_path / "active-run.db"
    store, root = _rooted_store(tmp_path, path.name)
    first = _start(store, root)
    store.close()

    reopened = Store(path)
    with pytest.raises(AutoModeConflictError, match="recovery-required active run"):
        _start(
            reopened,
            root,
            run_id="run-2",
            turn_id="turn-2",
            execution_id="execution-2",
        )
    projection = reopened.project_auto_mode_run(root, root)
    assert projection["run"]["run_id"] == first["run_id"] == "run-1"
    assert (
        reopened._conn.execute(
            "SELECT COUNT(*) FROM auto_mode_runs WHERE root_frame_id=?",
            (root,),
        ).fetchone()[0]
        == 1
    )

    reopened.terminate_auto_mode_run(
        "run-1",
        idempotency_key="run-1:failed",
        status="failed",
        reason="reconciled_after_restart",
    )
    second = _start(
        reopened,
        root,
        run_id="run-2",
        turn_id="turn-2",
        execution_id="execution-2",
    )
    assert second["created"] is True
    reopened.close()


def test_restart_replays_same_run_with_new_process_owner(tmp_path):
    path = tmp_path / "owner-restart.db"
    store, root = _rooted_store(tmp_path, path.name)
    first = _start(store, root, owner_instance_id="daemon-A")
    assert first["owner_instance_id"] == "daemon-A"
    store.close()

    reopened = Store(path)
    replay = _start(reopened, root, owner_instance_id="daemon-B")
    assert replay["created"] is False
    assert replay["run_id"] == first["run_id"]
    assert replay["owner_instance_id"] == "daemon-A"
    assert (
        reopened._conn.execute(
            "SELECT COUNT(*) FROM auto_mode_events WHERE run_id='run-1'"
        ).fetchone()[0]
        == 1
    )
    reopened.close()
