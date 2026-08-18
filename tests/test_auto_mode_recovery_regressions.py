"""Regression coverage for Stage-2 Auto Mode proof and branch recovery."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from openai4s.storage.auto_mode import AutoModeConflictError
from openai4s.storage.snapshots import revert_recovery_setting_key
from openai4s.store import Store


def _sha(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _selection() -> dict[str, str]:
    return {
        "preset": "autonomous",
        "result_review_mode": "auto_fix",
        "approvals_reviewer": "auto_review",
        "source": "frame",
    }


@pytest.fixture
def store_root(tmp_path: Path):
    store = Store(tmp_path / "auto-mode-recovery.db")
    project = store.create_project(name="Auto Mode recovery regression")
    root = store.new_frame(project_id=project["project_id"], kind="turn")
    store.ensure_session_branch(root_frame_id=root, branch_id=root)
    try:
        yield store, root
    finally:
        store.close()


def _start_fields(
    root: str,
    *,
    run_id: str = "run-1",
    branch_id: str | None = None,
    turn_id: str = "turn-1",
    execution_id: str = "execution-1",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "idempotency_key": idempotency_key or f"{turn_id}:auto-run",
        "root_frame_id": root,
        "branch_id": branch_id or root,
        "turn_id": turn_id,
        "execution_id": execution_id,
        "mode": "auto_fix",
        "selection": _selection(),
        "budgets": {"max_review_attempts": 2, "max_repair_rounds": 2},
        "owner_instance_id": "daemon-regression",
        "created_at": 100,
    }


def _candidate_fields(
    *,
    idempotency_key: str = "candidate:1",
) -> tuple[dict[str, Any], dict[str, Any]]:
    evidence = {"candidate_id": "candidate-1", "complete": True}
    return evidence, {
        "idempotency_key": idempotency_key,
        "candidate_id": "candidate-1",
        "candidate_snapshot_sha256": "a" * 64,
        "evidence_snapshot_sha256": _sha(evidence),
        "artifact_set_sha256": "b" * 64,
        "candidate_artifact_ids": ["artifact-1"],
        "candidate_version_ids": ["version-1"],
        "created_at": 110,
    }


def _review_fields(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "review_run_id": "review-1",
        "audit_id": "audit-1",
        "idempotency_key": "review:start",
        "candidate_id": "candidate-1",
        "candidate_snapshot_sha256": "a" * 64,
        "evidence_snapshot": evidence,
        "evidence_snapshot_sha256": _sha(evidence),
        "round_index": 0,
        "attempt": 1,
        "reviewer": {
            "profile_id": "scientific-reviewer",
            "profile_revision": 1,
            "model_fingerprint": "reviewer-model-v1",
        },
        "started_at": 120,
    }


def _complete_review(
    store: Store,
    *,
    findings: list[dict[str, Any]] | None = None,
) -> None:
    store.complete_auto_mode_review(
        "review-1",
        idempotency_key="review:complete",
        status="completed",
        verdict="pass",
        assessment={"public_summary": "Independent review passed."},
        findings=findings or [],
        usage={"input_tokens": 10, "output_tokens": 5},
        completed_at=130,
    )


def _build_verified(
    store: Store,
    root: str,
    *,
    findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    start = _start_fields(root)
    store.start_auto_mode_run(**start)
    evidence, candidate = _candidate_fields()
    store.record_auto_mode_candidate("run-1", **candidate)
    review = _review_fields(evidence)
    store.start_auto_mode_review("run-1", **review)
    _complete_review(store, findings=findings)
    terminal = {
        "idempotency_key": "terminal:1",
        "status": "verified",
        "reason": "review_passed",
        "finished_at": 140,
    }
    store.terminate_auto_mode_run("run-1", **terminal)
    return {
        "start": start,
        "candidate": candidate,
        "terminal": terminal,
    }


def _assert_safety_boundary(store: Store, root: str) -> None:
    projection = store.project_auto_mode_run(root, root)
    assert projection["run"]["source_claimed_status"] == "verified"
    assert projection["run"]["status"] == "failed"
    assert projection["run"]["terminal_reason"] == "safety_boundary"


@pytest.mark.parametrize("replay_kind", ["start", "candidate", "terminal"])
def test_verified_exact_replays_fail_closed_after_proof_tamper(
    store_root, replay_kind: str
):
    store, root = store_root
    fields = _build_verified(store, root)
    store._conn.execute(
        "UPDATE review_runs SET assessment_json='{}' WHERE review_run_id='review-1'"
    )
    store._conn.commit()

    with pytest.raises(AutoModeConflictError):
        if replay_kind == "start":
            store.start_auto_mode_run(**fields["start"])
        elif replay_kind == "candidate":
            store.record_auto_mode_candidate("run-1", **fields["candidate"])
        else:
            store.terminate_auto_mode_run("run-1", **fields["terminal"])

    _assert_safety_boundary(store, root)


def test_verified_projection_rejects_finding_bound_to_another_candidate(store_root):
    store, root = store_root
    finding = {
        "finding_id": "finding-1",
        "fingerprint": "minor-finding-1",
        "severity": "minor",
        "category": "clarity",
        "claim": "Clarify one supporting note.",
        "evidence_refs": ["cell-1"],
        "artifact_ids": ["artifact-1"],
        "version_ids": ["version-1"],
        "cell_ids": ["cell-1"],
    }
    _build_verified(store, root, findings=[finding])
    store._conn.execute(
        "UPDATE review_findings SET candidate_id='candidate-other' "
        "WHERE finding_id='finding-1'"
    )
    store._conn.commit()

    _assert_safety_boundary(store, root)


@pytest.mark.parametrize("ordinal", ["sequence", "event_cursor"])
def test_verified_projection_rejects_review_event_order_swap(store_root, ordinal: str):
    store, root = store_root
    _build_verified(store, root)
    start = store._conn.execute(
        f"SELECT {ordinal} FROM auto_mode_events "
        "WHERE run_id='run-1' AND type='auto_audit_started'"
    ).fetchone()[0]
    completed = store._conn.execute(
        f"SELECT {ordinal} FROM auto_mode_events "
        "WHERE run_id='run-1' AND type='auto_audit_completed'"
    ).fetchone()[0]
    store._conn.execute(
        f"UPDATE auto_mode_events SET {ordinal}=9999 "
        "WHERE run_id='run-1' AND type='auto_audit_started'"
    )
    store._conn.execute(
        f"UPDATE auto_mode_events SET {ordinal}=? "
        "WHERE run_id='run-1' AND type='auto_audit_completed'",
        (start,),
    )
    store._conn.execute(
        f"UPDATE auto_mode_events SET {ordinal}=? "
        "WHERE run_id='run-1' AND type='auto_audit_started'",
        (completed,),
    )
    store._conn.commit()

    _assert_safety_boundary(store, root)


@pytest.mark.parametrize("tamper", ["rehashed_downgrade", "event_type"])
def test_terminal_event_tamper_cannot_hide_verified_integrity_failure(
    store_root, tamper: str
):
    store, root = store_root
    _build_verified(store, root)
    if tamper == "rehashed_downgrade":
        row = store._conn.execute(
            "SELECT event_id,payload_json FROM auto_mode_events "
            "WHERE run_id='run-1' AND type='auto_run_terminal'"
        ).fetchone()
        payload = json.loads(row["payload_json"])
        payload["status"] = "review_unavailable"
        payload["terminal_reason"] = "timeout"
        store._conn.execute(
            "UPDATE auto_mode_events SET payload_json=?,payload_sha256=? "
            "WHERE event_id=?",
            (_canonical(payload), _sha(payload), row["event_id"]),
        )
    else:
        store._conn.execute(
            "UPDATE auto_mode_events SET type='repair_completed' "
            "WHERE run_id='run-1' AND type='auto_run_terminal'"
        )
    store._conn.commit()

    _assert_safety_boundary(store, root)


def test_historical_candidate_prefix_and_fork_do_not_inherit_later_terminal(
    store_root,
):
    store, root = store_root
    store.start_auto_mode_run(**_start_fields(root))
    evidence, candidate = _candidate_fields()
    store.record_auto_mode_candidate("run-1", **candidate)
    checkpoint = store.create_session_checkpoint(
        checkpoint_id="checkpoint-candidate",
        root_frame_id=root,
        branch_id=root,
        reason="candidate",
        workspace_tree_id=None,
        auto_event_cursor=store.auto_mode_event_cursor(root),
    )
    store.start_auto_mode_review("run-1", **_review_fields(evidence))
    _complete_review(store)
    store.terminate_auto_mode_run(
        "run-1",
        idempotency_key="terminal:1",
        status="verified",
        reason="review_passed",
        finished_at=140,
    )

    historical = store.project_auto_mode_run(
        root,
        root,
        upto_event_cursor=checkpoint["auto_event_cursor"],
    )
    store.fork_session_branch(
        root_frame_id=root,
        from_checkpoint_id=checkpoint["checkpoint_id"],
        branch_id="branch-candidate",
    )
    forked = store.project_auto_mode_run(root, "branch-candidate")

    assert historical["run"]["status"] == "candidate"
    assert historical["run"].get("source_claimed_status") is None
    assert forked["run"]["status"] == "candidate"
    assert forked["run"].get("source_claimed_status") is None


def test_fork_from_active_run_can_start_independent_child_run(store_root):
    store, root = store_root
    store.start_auto_mode_run(**_start_fields(root, run_id="parent-run"))
    checkpoint = store.create_session_checkpoint(
        checkpoint_id="checkpoint-active-parent",
        root_frame_id=root,
        branch_id=root,
        reason="active_parent",
        workspace_tree_id=None,
        auto_event_cursor=store.auto_mode_event_cursor(root),
    )
    store.fork_session_branch(
        root_frame_id=root,
        from_checkpoint_id=checkpoint["checkpoint_id"],
        branch_id="branch-child",
    )

    store.start_auto_mode_run(
        **_start_fields(
            root,
            run_id="child-run",
            branch_id="branch-child",
            turn_id="turn-child",
            execution_id="execution-child",
        )
    )

    assert store.project_auto_mode_run(root, root)["run"]["run_id"] == "parent-run"
    assert (
        store.project_auto_mode_run(root, "branch-child")["run"]["run_id"]
        == "child-run"
    )


def _append_revert_checkpoint(
    store: Store,
    root: str,
    *,
    target_checkpoint_id: str,
) -> None:
    resume_cursor = store.auto_mode_event_cursor(root)
    undo = store.create_session_checkpoint(
        checkpoint_id="checkpoint-undo",
        root_frame_id=root,
        branch_id=root,
        reason="undo_capture",
        workspace_tree_id=None,
        auto_event_cursor=resume_cursor,
    )
    target = store.get_session_checkpoint(target_checkpoint_id)
    assert target is not None
    store.create_session_checkpoint(
        checkpoint_id="checkpoint-revert",
        root_frame_id=root,
        branch_id=root,
        reason="revert_continue",
        workspace_tree_id=None,
        auto_event_cursor=target["auto_event_cursor"],
        metadata={
            "reverted_to": target_checkpoint_id,
            "undo_checkpoint_id": undo["checkpoint_id"],
            "history_projection": {
                "version": 1,
                "base_checkpoint_id": target_checkpoint_id,
                "resume_cursors": {"auto_event_cursor": resume_cursor},
            },
        },
    )


def test_same_branch_revert_before_terminal_can_start_new_continuation(store_root):
    store, root = store_root
    store.start_auto_mode_run(**_start_fields(root))
    evidence, candidate = _candidate_fields()
    store.record_auto_mode_candidate("run-1", **candidate)
    checkpoint = store.create_session_checkpoint(
        checkpoint_id="checkpoint-before-terminal",
        root_frame_id=root,
        branch_id=root,
        reason="candidate",
        workspace_tree_id=None,
        auto_event_cursor=store.auto_mode_event_cursor(root),
    )
    store.start_auto_mode_review("run-1", **_review_fields(evidence))
    _complete_review(store)
    store.terminate_auto_mode_run(
        "run-1",
        idempotency_key="terminal:1",
        status="verified",
        reason="review_passed",
        finished_at=140,
    )
    _append_revert_checkpoint(
        store,
        root,
        target_checkpoint_id=checkpoint["checkpoint_id"],
    )
    assert store.project_auto_mode_run(root, root)["run"]["status"] == "candidate"

    store.start_auto_mode_run(
        **_start_fields(
            root,
            run_id="continuation-run",
            turn_id="turn-2",
            execution_id="execution-2",
        )
    )

    assert (
        store.project_auto_mode_run(root, root)["run"]["run_id"] == "continuation-run"
    )
    old = store._conn.execute(
        "SELECT status,abandoned_at FROM auto_mode_runs WHERE run_id='run-1'"
    ).fetchone()
    assert old["status"] == "verified"
    assert old["abandoned_at"] is None


def test_same_branch_revert_hiding_started_review_abandons_old_active_run(
    store_root,
):
    store, root = store_root
    old_start = _start_fields(root)
    store.start_auto_mode_run(**old_start)
    evidence, candidate = _candidate_fields()
    store.record_auto_mode_candidate("run-1", **candidate)
    checkpoint = store.create_session_checkpoint(
        checkpoint_id="checkpoint-before-review",
        root_frame_id=root,
        branch_id=root,
        reason="candidate",
        workspace_tree_id=None,
        auto_event_cursor=store.auto_mode_event_cursor(root),
    )
    store.start_auto_mode_review("run-1", **_review_fields(evidence))
    _append_revert_checkpoint(
        store,
        root,
        target_checkpoint_id=checkpoint["checkpoint_id"],
    )

    store.start_auto_mode_run(
        **_start_fields(
            root,
            run_id="continuation-run",
            turn_id="turn-2",
            execution_id="execution-2",
        )
    )

    old = store._conn.execute(
        "SELECT status,abandoned_at FROM auto_mode_runs WHERE run_id='run-1'"
    ).fetchone()
    assert old["status"] == "reviewing"
    assert old["abandoned_at"] is not None
    assert (
        store.project_auto_mode_run(root, root)["run"]["run_id"] == "continuation-run"
    )
    with pytest.raises(AutoModeConflictError, match="abandoned branch tail"):
        _complete_review(store)
    with pytest.raises(AutoModeConflictError, match="abandoned branch tail"):
        store.start_auto_mode_run(**old_start)


def test_revert_hidden_candidate_and_review_cannot_authorize_a_transition(
    store_root,
):
    store, root = store_root
    start = _start_fields(root)
    store.start_auto_mode_run(**start)
    checkpoint = store.create_session_checkpoint(
        checkpoint_id="checkpoint-start-only",
        root_frame_id=root,
        branch_id=root,
        reason="start_only",
        workspace_tree_id=None,
        auto_event_cursor=store.auto_mode_event_cursor(root),
    )
    evidence, candidate = _candidate_fields()
    store.record_auto_mode_candidate("run-1", **candidate)
    store.start_auto_mode_review("run-1", **_review_fields(evidence))
    _complete_review(store)
    _append_revert_checkpoint(
        store,
        root,
        target_checkpoint_id=checkpoint["checkpoint_id"],
    )
    assert store.project_auto_mode_run(root, root)["run"]["status"] == "running"

    second_review = _review_fields(evidence)
    second_review.update(
        {
            "review_run_id": "review-hidden",
            "audit_id": "audit-hidden",
            "idempotency_key": "review:hidden:start",
            "attempt": 2,
        }
    )
    with pytest.raises(AutoModeConflictError, match="branch head"):
        store.start_auto_mode_review("run-1", **second_review)
    with pytest.raises(AutoModeConflictError, match="branch head"):
        store.terminate_auto_mode_run(
            "run-1",
            idempotency_key="terminal:hidden-proof",
            status="verified",
            reason="hidden_review_passed",
        )
    with pytest.raises(AutoModeConflictError, match="branch tail"):
        store.start_auto_mode_run(**start)


def test_newer_failed_review_cannot_be_rehashed_away_to_resurrect_old_pass(
    store_root,
):
    store, root = store_root
    store.start_auto_mode_run(**_start_fields(root))
    evidence, candidate = _candidate_fields()
    store.record_auto_mode_candidate("run-1", **candidate)
    store.start_auto_mode_review("run-1", **_review_fields(evidence))
    _complete_review(store)

    second_review = _review_fields(evidence)
    second_review.update(
        {
            "review_run_id": "review-2",
            "audit_id": "audit-2",
            "idempotency_key": "review:2:start",
            "attempt": 2,
            "started_at": 140,
        }
    )
    store.start_auto_mode_review("run-1", **second_review)
    store.complete_auto_mode_review(
        "review-2",
        idempotency_key="review:2:complete",
        status="completed",
        verdict="failed",
        assessment={"public_summary": "Latest review failed."},
        findings=[],
        completed_at=150,
    )
    row = store._conn.execute(
        "SELECT event_id,payload_json FROM auto_mode_events "
        "WHERE run_id='run-1' AND idempotency_key='review:2:complete'"
    ).fetchone()
    payload = json.loads(row["payload_json"])
    payload["candidate_id"] = "candidate-other"
    payload["subject_entity_id"] = "candidate-other"
    store._conn.execute(
        "UPDATE auto_mode_events SET payload_json=?,payload_sha256=? "
        "WHERE event_id=?",
        (_canonical(payload), _sha(payload), row["event_id"]),
    )
    store._conn.commit()

    with pytest.raises(AutoModeConflictError):
        store.terminate_auto_mode_run(
            "run-1",
            idempotency_key="terminal:no-fallback",
            status="verified",
            reason="must_not_fallback",
        )


def test_current_candidate_event_rehash_tamper_fails_read_and_replay(store_root):
    store, root = store_root
    store.start_auto_mode_run(**_start_fields(root))
    _evidence, candidate = _candidate_fields()
    store.record_auto_mode_candidate("run-1", **candidate)
    row = store._conn.execute(
        "SELECT event_id,payload_json FROM auto_mode_events "
        "WHERE run_id='run-1' AND type='candidate_ready'"
    ).fetchone()
    payload = json.loads(row["payload_json"])
    payload["candidate_id"] = "candidate-tampered"
    store._conn.execute(
        "UPDATE auto_mode_events SET payload_json=?,payload_sha256=? "
        "WHERE event_id=?",
        (_canonical(payload), _sha(payload), row["event_id"]),
    )
    store._conn.commit()

    projection = store.project_auto_mode_run(root, root)
    assert projection["run"]["source_claimed_status"] == "candidate"
    assert projection["run"]["status"] == "failed"
    assert projection["run"]["terminal_reason"] == "safety_boundary"
    with pytest.raises(AutoModeConflictError):
        store.record_auto_mode_candidate("run-1", **candidate)


def test_unresolved_revert_barrier_denies_new_auto_and_action_work_but_keeps_evidence(
    store_root,
):
    store, root = store_root
    store.start_auto_mode_run(**_start_fields(root))
    group = store.append_action_group(
        root_frame_id=root,
        branch_id=root,
        turn_id="turn-1",
        kind="native_tools",
    )
    store.set_setting(
        revert_recovery_setting_key(root),
        _canonical(
            {
                "schema_version": 1,
                "state": "recovery_required",
                "operation_id": "revert-fault",
                "branch_id": root,
            }
        ),
    )

    with pytest.raises(AutoModeConflictError, match="requires recovery"):
        store.record_auto_mode_candidate("run-1", **_candidate_fields()[1])
    with pytest.raises(AutoModeConflictError, match="requires recovery"):
        store.start_auto_mode_run(
            **_start_fields(
                root,
                run_id="run-2",
                turn_id="turn-2",
                execution_id="execution-2",
            )
        )
    with pytest.raises(AutoModeConflictError, match="requires recovery"):
        store.append_action_group(
            root_frame_id=root,
            branch_id=root,
            turn_id="turn-2",
            kind="native_tools",
        )
    with pytest.raises(AutoModeConflictError, match="requires recovery"):
        store.append_action_event(
            group_id=group["group_id"],
            type="proposed",
            action_id="blocked-proposal",
        )

    terminal = store.append_action_event(
        group_id=group["group_id"],
        type="failed",
        action_id="admitted-before-revert",
        result={"error": "late terminal evidence"},
    )
    assert terminal["type"] == "failed"
