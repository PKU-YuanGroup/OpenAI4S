"""Regression coverage for Stage-2 Auto Mode proof and branch recovery."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from openai4s.storage.auto_mode import AutoModeConflictError
from openai4s.storage.snapshots import WorkspaceCAS, revert_recovery_setting_key
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
    owner: str = "daemon-regression",
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
        "owner_instance_id": owner,
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


# --------------------------------------------------------------------------
# Boot-time reconciliation of runs a dead daemon left mid-flight.
#
# `_persist_auto_mode` commits start_run, record_candidate, start_review and
# complete_review as four separate transactions. `kill -9` between the third
# and the fourth leaves `reviewing` -- excluded from `_TERMINAL_STATUSES` --
# with no `finished_at` and no `abandoned_at`, and every later turn on that
# branch then dies inside a swallowed `AutoModeConflictError`.
# --------------------------------------------------------------------------


def _crash_mid_review(store: Store, root: str) -> None:
    """Leave exactly the row a `kill -9` during the audit leaves behind."""

    store.start_auto_mode_run(**_start_fields(root, owner="daemon-DEAD"))
    evidence, candidate = _candidate_fields()
    store.record_auto_mode_candidate("run-1", **candidate)
    store.start_auto_mode_review("run-1", **_review_fields(evidence))
    # complete_review never runs: the daemon is gone.


def _next_turn(store: Store, root: str, owner: str) -> str:
    """Start the turn that follows the crash and report the branch's status."""

    store.start_auto_mode_run(
        **_start_fields(
            root,
            run_id="run-2",
            turn_id="turn-2",
            execution_id="execution-2",
            owner=owner,
        )
    )
    projected = store.project_auto_mode_run(root, root)["run"]
    assert projected["run_id"] == "run-2"
    return str(projected["status"])


def test_crash_mid_review_wedges_the_branch_without_reconciliation(store_root):
    """The defect itself: prove the branch is dead before proving the fix."""

    store, root = store_root
    _crash_mid_review(store, root)
    assert store.project_auto_mode_run(root, root)["run"]["status"] == "reviewing"

    with pytest.raises(AutoModeConflictError) as excinfo:
        _next_turn(store, root, "daemon-NEW")
    assert "recovery-required active run" in str(excinfo.value)


def test_reconcile_moves_a_crashed_run_to_review_unavailable(store_root):
    store, root = store_root
    _crash_mid_review(store, root)

    outcomes = store.reconcile_orphaned_auto_mode_runs(
        owner_instance_id="daemon-NEW", now=500
    )
    assert outcomes == [
        {
            "run_id": "run-1",
            "status": "review_unavailable",
            "terminal_reason": "daemon_restart",
        }
    ]

    # A definite terminal, not an abandoned row: the projection agrees, which
    # means the reconciled run still satisfies replay integrity.
    projected = store.project_auto_mode_run(root, root)["run"]
    assert projected["status"] == "review_unavailable"
    assert projected["terminal_reason"] == "daemon_restart"
    assert projected["finished_at"] == 500

    # And the branch accepts work again.
    assert _next_turn(store, root, "daemon-NEW") == "running"


def test_reconcile_closes_the_stranded_review_without_inventing_a_verdict(store_root):
    store, root = store_root
    _crash_mid_review(store, root)
    store.reconcile_orphaned_auto_mode_runs(owner_instance_id="daemon-NEW", now=500)

    review = store._conn.execute(
        "SELECT status,verdict,completed_at FROM review_runs WHERE review_run_id=?",
        ("review-1",),
    ).fetchone()
    assert review["status"] == "unavailable"
    assert review["verdict"] == "review_unavailable"
    assert review["completed_at"] == 500
    # An interrupted audit reports no findings. Manufacturing one would be a
    # claim about code no reviewer ever finished reading.
    assert (
        store._conn.execute(
            "SELECT COUNT(*) FROM review_findings WHERE run_id='run-1'"
        ).fetchone()[0]
        == 0
    )
    # And the surface a reader actually sees says the same thing, in words.
    audits = store.list_auto_mode_audits(root, root)
    assert [(item["status"], item["verdict"]) for item in audits] == [
        ("unavailable", "review_unavailable")
    ]
    assert "exited before" in audits[0]["public_summary"]


def test_reconcile_preserves_every_committed_side_effect(store_root):
    """Recovery closes the record; it never re-runs what already happened."""

    store, root = store_root
    _crash_mid_review(store, root)
    before = store._conn.execute(
        "SELECT candidate_id,candidate_snapshot_sha256,evidence_snapshot_sha256,"
        "artifact_set_sha256,candidate_artifact_ids_json,candidate_version_ids_json,"
        "created_at FROM auto_mode_runs WHERE run_id='run-1'"
    ).fetchone()
    events_before = [
        event["type"] for event in store.list_auto_mode_events(root, branch_id=root)
    ]
    assert events_before == [
        "auto_run_started",
        "candidate_ready",
        "auto_audit_started",
    ]

    store.reconcile_orphaned_auto_mode_runs(owner_instance_id="daemon-NEW", now=500)

    after = store._conn.execute(
        "SELECT candidate_id,candidate_snapshot_sha256,evidence_snapshot_sha256,"
        "artifact_set_sha256,candidate_artifact_ids_json,candidate_version_ids_json,"
        "created_at FROM auto_mode_runs WHERE run_id='run-1'"
    ).fetchone()
    assert tuple(after) == tuple(before)

    # The audit chain is appended to, never rewritten: the pre-crash events
    # stay byte-identical and reconciliation adds only its own two closures.
    events_after = [
        event["type"] for event in store.list_auto_mode_events(root, branch_id=root)
    ]
    assert events_after == events_before + [
        "auto_audit_completed",
        "auto_run_terminal",
    ]


def test_reconcile_never_touches_a_run_this_instance_owns(store_root):
    """Ownership is the whole discriminator -- a live run must survive boot."""

    store, root = store_root
    store.start_auto_mode_run(**_start_fields(root, owner="daemon-LIVE"))
    evidence, candidate = _candidate_fields()
    store.record_auto_mode_candidate("run-1", **candidate)
    store.start_auto_mode_review("run-1", **_review_fields(evidence))

    assert (
        store.reconcile_orphaned_auto_mode_runs(
            owner_instance_id="daemon-LIVE", now=500
        )
        == []
    )
    assert store.project_auto_mode_run(root, root)["run"]["status"] == "reviewing"
    # Still live, so the in-flight audit can still commit its real verdict.
    _complete_review(store)
    assert store.project_auto_mode_run(root, root)["run"]["status"] == "candidate"


def test_reconcile_is_idempotent_across_repeated_restarts(store_root):
    """A crash loop must not append a second terminal to the same run."""

    store, root = store_root
    _crash_mid_review(store, root)

    assert (
        len(
            store.reconcile_orphaned_auto_mode_runs(
                owner_instance_id="daemon-NEW", now=500
            )
        )
        == 1
    )
    for tick in (600, 700):
        assert (
            store.reconcile_orphaned_auto_mode_runs(
                owner_instance_id="daemon-NEWER", now=tick
            )
            == []
        )
    terminals = [
        event
        for event in store.list_auto_mode_events(root, branch_id=root)
        if event["type"] == "auto_run_terminal"
    ]
    assert len(terminals) == 1
    assert store.project_auto_mode_run(root, root)["run"]["finished_at"] == 500


def test_reconcile_leaves_an_already_terminal_run_alone(store_root):
    """A Verified run from a dead daemon keeps its own terminal truth."""

    store, root = store_root
    _build_verified(store, root)
    assert (
        store.reconcile_orphaned_auto_mode_runs(owner_instance_id="daemon-NEW", now=500)
        == []
    )
    projected = store.project_auto_mode_run(root, root)["run"]
    assert projected["status"] == "verified"
    assert projected["terminal_reason"] == "review_passed"


def test_reconcile_defers_to_an_unresolved_revert_recovery_hold(store_root):
    """A revert barrier is a deliberate hold with its own recovery path."""

    store, root = store_root
    _crash_mid_review(store, root)
    store.set_setting(
        revert_recovery_setting_key(root),
        _canonical({"operation_id": "op-1", "branch_id": root, "state": "reverting"}),
    )

    outcomes = store.reconcile_orphaned_auto_mode_runs(
        owner_instance_id="daemon-NEW", now=500
    )
    assert len(outcomes) == 1
    assert outcomes[0]["deferred"] == "revert_recovery_required"
    # Neither terminalized nor abandoned: the revert owns this row now.
    row = store._conn.execute(
        "SELECT status,finished_at,abandoned_at FROM auto_mode_runs WHERE run_id='run-1'"
    ).fetchone()
    assert row["status"] == "reviewing"
    assert row["finished_at"] is None
    assert row["abandoned_at"] is None


def test_reconcile_startup_wires_auto_mode_recovery_at_boot(store_root):
    """The gap was wiring, not mechanism -- assert the boot path calls it."""

    from openai4s.server.session_recovery import SessionRecoveryService

    store, root = store_root
    _crash_mid_review(store, root)

    recovery = SessionRecoveryService(
        store=store,
        sessions=lambda: [],
        turn_active=lambda _root: False,
        approval_pending=lambda _root: False,
        background_active=lambda _session: False,
        release_idle=lambda _session, _reason: False,
        owner_instance_id="daemon-NEW",
    )
    recovery.reconcile_startup()

    assert [entry["run_id"] for entry in recovery.reconciled_auto_mode_runs] == [
        "run-1"
    ]
    assert (
        store.project_auto_mode_run(root, root)["run"]["status"] == "review_unavailable"
    )
    assert _next_turn(store, root, "daemon-NEW") == "running"


def _crash_mid_repair(
    store: Store, root: str, tmp_path: Path, *, bind_ledger: bool
) -> None:
    """Leave the row a `kill -9` during an auto-fix repair leaves behind."""

    store.start_auto_mode_run(**_start_fields(root, owner="daemon-DEAD"))
    evidence, candidate = _candidate_fields()
    store.record_auto_mode_candidate("run-1", **candidate)
    store.start_auto_mode_review("run-1", **_review_fields(evidence))
    store.complete_auto_mode_review(
        "review-1",
        idempotency_key="review:complete",
        status="completed",
        verdict="issues",
        assessment={"public_summary": "One material issue."},
        findings=[
            {
                "finding_id": "finding-1",
                "fingerprint": "stable-finding-1",
                "severity": "major",
                "category": "evidence",
                "claim": "The claim outruns its evidence.",
                "evidence_refs": ["artifact-1"],
                "artifact_ids": ["artifact-1"],
                "version_ids": ["version-1"],
                "cell_ids": [],
            }
        ],
        usage={},
        completed_at=130,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / "result.txt").write_text("candidate\n", encoding="utf-8")
    tree = WorkspaceCAS(store.db_path.parent / "workspace-cas").capture(workspace)
    checkpoint = store.create_session_checkpoint(
        checkpoint_id="checkpoint-repair",
        root_frame_id=root,
        branch_id=root,
        reason="pre_repair",
        workspace_tree_id=tree["tree_id"],
        auto_event_cursor=store.auto_mode_event_cursor(root),
    )
    store.start_auto_mode_repair(
        "run-1",
        repair_run_id="repair-1",
        idempotency_key="repair-1:start",
        finding_ids=["finding-1"],
        before_version_ids=["version-1"],
        checkpoint_id=checkpoint["checkpoint_id"],
    )
    if not bind_ledger:
        return
    group = store.append_action_group(
        root_frame_id=root,
        branch_id=root,
        turn_id="turn-1",
        kind="native_tools",
        assistant_content="bounded repair",
    )
    store.bind_auto_mode_repair_execution_group(
        "repair-1",
        action_group_id=group["group_id"],
        idempotency_key=f"repair-1:bind:{group['group_id']}",
    )
    # Proposed, never resolved: the exact ledger a `kill -9` mid-write leaves.
    store.append_action_event(
        group_id=group["group_id"],
        type="proposed",
        action_id="repair-write",
        tool_call_id="repair-write",
        canonical_arguments={"path": "result.txt", "content": "repaired\n"},
        side_effect_class="workspace_write",
        resource_keys=["workspace:result.txt"],
    )


def test_reconcile_seals_an_interrupted_repair_as_outcome_unknown(store_root, tmp_path):
    """A half-applied repair is terminal truth, never a retryable candidate."""

    store, root = store_root
    _crash_mid_repair(store, root, tmp_path, bind_ledger=True)
    assert store.project_auto_mode_run(root, root)["run"]["status"] == "repairing"

    outcomes = store.reconcile_orphaned_auto_mode_runs(
        owner_instance_id="daemon-NEW", now=500
    )
    assert outcomes == [
        {"run_id": "run-1", "status": "paused", "terminal_reason": "outcome_unknown"}
    ]

    # `complete_repair` sealed its own terminal, and reconciliation left it
    # alone rather than relabelling it `review_unavailable`: the daemon dying
    # is why we stopped looking, not what we found.
    repair = store._conn.execute(
        "SELECT status,after_version_ids_json FROM repair_runs WHERE repair_run_id=?",
        ("repair-1",),
    ).fetchone()
    assert repair["status"] == "outcome_unknown"
    assert repair["after_version_ids_json"] == "[]"
    assert _next_turn(store, root, "daemon-NEW") == "running"


def test_reconcile_releases_a_repair_whose_ledger_was_never_bound(store_root, tmp_path):
    """No completion status can represent this, so claim nothing and release."""

    store, root = store_root
    _crash_mid_repair(store, root, tmp_path, bind_ledger=False)

    outcomes = store.reconcile_orphaned_auto_mode_runs(
        owner_instance_id="daemon-NEW", now=500
    )
    assert len(outcomes) == 1
    assert outcomes[0]["run_id"] == "run-1"
    assert outcomes[0]["abandoned_at"] == 500
    # No invented terminal: the status still says what the run was doing, and
    # only `abandoned_at` marks the tail as no longer current.
    row = store._conn.execute(
        "SELECT status,finished_at,abandoned_at,terminal_reason "
        "FROM auto_mode_runs WHERE run_id='run-1'"
    ).fetchone()
    assert row["status"] == "repairing"
    assert row["finished_at"] is None
    assert row["terminal_reason"] is None
    assert row["abandoned_at"] == 500
    # The branch is released either way -- that is the whole point.
    assert _next_turn(store, root, "daemon-NEW") == "running"


def test_one_sweep_reconciles_every_dead_session_and_spares_the_live_one(tmp_path):
    """The real boot condition is a mixed set, not one run in isolation."""

    store = Store(tmp_path / "auto-mode-mixed.db")
    project = store.create_project(name="Auto Mode mixed restart")
    try:
        roots = []
        for index in range(3):
            root = store.new_frame(project_id=project["project_id"], kind="turn")
            store.ensure_session_branch(root_frame_id=root, branch_id=root)
            roots.append(root)
            store.start_auto_mode_run(
                **_start_fields(
                    root,
                    run_id=f"run-{index}",
                    turn_id=f"turn-{index}",
                    execution_id=f"execution-{index}",
                    # The third session belongs to the daemon booting now.
                    owner="daemon-LIVE" if index == 2 else f"daemon-DEAD-{index}",
                )
            )

        outcomes = store.reconcile_orphaned_auto_mode_runs(
            owner_instance_id="daemon-LIVE", now=500
        )
        # Both dead sessions, in a deterministic order; the live one untouched.
        assert [entry["run_id"] for entry in outcomes] == ["run-0", "run-1"]
        assert {entry["status"] for entry in outcomes} == {"review_unavailable"}
        assert store.project_auto_mode_run(roots[2], roots[2])["run"]["status"] == (
            "running"
        )
    finally:
        store.close()


def test_one_unreconcilable_run_does_not_deny_the_others_recovery(tmp_path):
    """Boot-time recovery is all-or-nothing exactly once: per row.

    The per-run method can only guard what happens after control enters it, so
    a failure outside its caught tuple escaped the sweep loop and cost every
    remaining session the recovery this method exists to give it.
    """

    import sqlite3

    from openai4s.store import Store

    store = Store(tmp_path / "sweep.db")
    store.create_project(name="p", project_id="proj-1")
    for fid in ("root-1", "root-2"):
        store._conn.execute(
            "INSERT INTO frames(frame_id,parent_id,project_id,root_frame_id,kind,"
            "status,depth,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (fid, None, "proj-1", fid, "turn", "processing", 0, 1, 1),
        )
    store._conn.commit()
    try:
        for fid in ("root-1", "root-2"):
            store.ensure_session_branch(root_frame_id=fid, branch_id=fid)
            store.start_auto_mode_run(
                run_id=f"auto-{fid}",
                root_frame_id=fid,
                branch_id=fid,
                turn_id="t1",
                execution_id=f"e-{fid}",
                mode="review_only",
                selection={
                    "preset": "off",
                    "result_review_mode": "review_only",
                    "approvals_reviewer": "user",
                },
                budgets={},
                owner_instance_id="daemon-OLD",
                idempotency_key="t1:start",
                created_at=1000,
            )

        repo = store._auto_mode
        original = repo._reconcile_orphaned_run_locked

        def poisoned(run_id, *, now):
            # Not in the per-run method's caught tuple, and raised before its
            # own handlers can see it.
            if run_id == "auto-root-1":
                raise sqlite3.OperationalError("database is locked")
            return original(run_id, now=now)

        repo._reconcile_orphaned_run_locked = poisoned
        outcomes = store.reconcile_orphaned_auto_mode_runs(
            owner_instance_id="daemon-NEW", now=2000
        )
        by_run = {item["run_id"]: item for item in outcomes}
        assert by_run["auto-root-1"]["unreconciled"]
        assert by_run["auto-root-2"]["status"] == "review_unavailable"
    finally:
        store.close()
