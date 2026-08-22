"""Atomic Candidate -> terminal message and Stage 1 delivery promotion."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

import pytest

from openai4s.server.auto_mode import public_auto_event
from openai4s.server.delivery import CompletionDeliveryService
from openai4s.storage.auto_mode import AutoModeConflictError
from openai4s.store import Store

_CANDIDATE = "Candidate result: n=100."
_PROMOTED = "Reviewed result: n=97."


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _review_evidence(candidate_answer: str = _CANDIDATE) -> dict[str, Any]:
    return {
        "candidate_answer": candidate_answer,
        "structured_completion": None,
    }


def _candidate_snapshot_digest(candidate_answer: str = _CANDIDATE) -> str:
    evidence = _review_evidence(candidate_answer)
    return _digest(
        {
            "candidate_answer": evidence["candidate_answer"],
            "structured_completion": evidence["structured_completion"],
        }
    )


def _candidate_metadata() -> dict[str, Any]:
    return {
        "review_status": "candidate",
        "user_truth": "Candidate · provisional / not verified",
        "gates_completion": True,
        "unverified": True,
        "turn_id": "turn-1",
        "execution_id": "execution-1",
        "candidate_content_sha256": _sha_text(_CANDIDATE),
    }


def _verdict_metadata() -> dict[str, Any]:
    return {
        "review_status": "completed_with_issues",
        "user_truth": "Completed · unverified · 1 unresolved issue",
        "gates_completion": True,
        "unverified": True,
        "turn_id": "turn-1",
        "execution_id": "execution-1",
        "candidate_content_sha256": _sha_text(_CANDIDATE),
        "reviewed_content_sha256": _sha_text(_PROMOTED),
        "review_run_id": "review-1",
    }


def _verified_metadata(
    content: str = _CANDIDATE, *, review_run_id: str = "review-pass"
) -> dict[str, Any]:
    return {
        "review_status": "verified",
        "user_truth": "Verified · independently reviewed",
        "gates_completion": True,
        "unverified": False,
        "turn_id": "turn-1",
        "execution_id": "execution-1",
        "candidate_content_sha256": _sha_text(_CANDIDATE),
        "reviewed_content_sha256": _sha_text(content),
        "review_run_id": review_run_id,
    }


def _setup(tmp_path: Path, *, name: str) -> tuple[Store, dict[str, Any]]:
    data_dir = tmp_path / name
    data_dir.mkdir()
    store = Store(data_dir / "openai4s.db")
    project_id = f"project-{name}"
    store.create_project(name="atomic promotion", project_id=project_id)
    root = store.new_frame(project_id=project_id, status="processing")
    store.ensure_session_branch(root_frame_id=root, branch_id=root)
    store.start_auto_mode_run(
        run_id="run-1",
        idempotency_key="turn-1:auto-run",
        root_frame_id=root,
        branch_id=root,
        turn_id="turn-1",
        execution_id="execution-1",
        mode="auto_fix",
        selection={
            "preset": "autonomous",
            "result_review_mode": "auto_fix",
            "approvals_reviewer": "auto_review",
            "source": "frame",
        },
        budgets={"max_review_attempts": 2, "max_repair_rounds": 2},
        owner_instance_id="daemon-1",
        created_at=100,
    )

    snapshot = data_dir / "artifact-versions" / "candidate.bin"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_bytes(b"atomic-candidate-artifact")
    artifact = store.save_artifact(
        path=str(snapshot),
        filename="result.csv",
        content_type="text/csv",
        size_bytes=snapshot.stat().st_size,
        checksum=hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        frame_id=root,
        project_id=project_id,
        snapshot_path=str(snapshot),
    )
    delivery_service = CompletionDeliveryService(store=store, data_dir=data_dir)
    verified = delivery_service.build_manifest(
        root_frame_id=root,
        project_id=project_id,
        versions=[artifact["version_id"]],
    )
    delivery = delivery_service.commit_verified_manifest(
        verified=verified,
        idempotency_key="execution-1:completion",
        root_frame_id=root,
        branch_id=root,
        frame_id=root,
        content=_CANDIDATE,
        message_metadata=_candidate_metadata(),
        created_at=150,
    )
    evidence = _review_evidence()
    store.record_auto_mode_candidate(
        "run-1",
        idempotency_key="turn-1:candidate",
        candidate_id="candidate-1",
        candidate_snapshot_sha256=_candidate_snapshot_digest(),
        evidence_snapshot_sha256=_digest(evidence),
        candidate_version_ids=[artifact["version_id"]],
        candidate_artifact_ids=[artifact["artifact_id"]],
        created_at=200,
    )
    promotion = {
        "message_id": delivery["message_id"],
        "delivery_id": delivery["delivery_id"],
        "root_frame_id": root,
        "branch_id": root,
        "frame_id": root,
        "expected_content": _CANDIDATE,
        "content": _PROMOTED,
        "metadata": _verdict_metadata(),
    }
    return store, {
        "root": root,
        "message_id": delivery["message_id"],
        "delivery_id": delivery["delivery_id"],
        "promotion": promotion,
    }


def _complete_pass_review(
    store: Store,
    *,
    candidate_answer: str = _CANDIDATE,
    candidate_id: str = "candidate-1",
    review_run_id: str = "review-pass",
    round_index: int = 0,
) -> None:
    evidence = _review_evidence(candidate_answer)
    if candidate_id != "candidate-1":
        store.record_auto_mode_candidate(
            "run-1",
            idempotency_key=f"turn-1:{candidate_id}",
            candidate_id=candidate_id,
            candidate_snapshot_sha256=_candidate_snapshot_digest(candidate_answer),
            evidence_snapshot_sha256=_digest(evidence),
            candidate_version_ids=[],
            candidate_artifact_ids=[],
            created_at=205,
        )
    store.start_auto_mode_review(
        "run-1",
        review_run_id=review_run_id,
        audit_id=f"audit-{review_run_id}",
        idempotency_key=f"turn-1:{review_run_id}:start",
        candidate_id=candidate_id,
        candidate_snapshot_sha256=_candidate_snapshot_digest(candidate_answer),
        evidence_snapshot=evidence,
        evidence_snapshot_sha256=_digest(evidence),
        round_index=round_index,
        attempt=1,
        reviewer={
            "profile_id": "scientific-reviewer",
            "profile_revision": 1,
            "model_fingerprint": "reviewer-model-v1",
        },
        started_at=210,
    )
    store.complete_auto_mode_review(
        review_run_id,
        idempotency_key=f"turn-1:{review_run_id}:complete",
        status="completed",
        verdict="pass",
        assessment={"public_summary": "Independent review passed."},
        findings=[],
        usage={},
        completed_at=220,
    )


def _state(store: Store, context: dict[str, Any]) -> dict[str, Any]:
    message = store._conn.execute(  # noqa: SLF001 - transaction boundary assertion
        "SELECT content,metadata FROM messages WHERE message_id=?",
        (context["message_id"],),
    ).fetchone()
    delivery = store._conn.execute(  # noqa: SLF001
        "SELECT content_sha256,status,published_at FROM completion_deliveries "
        "WHERE delivery_id=?",
        (context["delivery_id"],),
    ).fetchone()
    run = store._conn.execute(  # noqa: SLF001
        "SELECT status,state_revision,terminal_reason,finished_at,"
        "terminal_idempotency_key,terminal_request_sha256 FROM auto_mode_runs "
        "WHERE run_id='run-1'"
    ).fetchone()
    terminals = store._conn.execute(  # noqa: SLF001
        "SELECT event_id,payload_json,payload_sha256 FROM auto_mode_events "
        "WHERE run_id='run-1' AND type='auto_run_terminal' ORDER BY sequence"
    ).fetchall()
    return {
        "message": {
            "content": message["content"],
            "metadata": json.loads(message["metadata"]),
        },
        "delivery": dict(delivery),
        "run": dict(run),
        "terminals": [dict(row) for row in terminals],
    }


def _terminate(store: Store, promotion: dict[str, Any]) -> dict[str, Any]:
    return store.terminate_auto_mode_run(
        "run-1",
        idempotency_key="turn-1:terminal",
        status="completed_with_issues",
        reason="review_reported_issues",
        finished_at=500,
        message_promotion=promotion,
    )


def _assert_candidate_state(state: dict[str, Any]) -> None:
    assert state["message"]["content"] == _CANDIDATE
    assert state["message"]["metadata"]["review_status"] == "candidate"
    assert state["delivery"] == {
        "content_sha256": _sha_text(_CANDIDATE),
        "status": "committed",
        "published_at": None,
    }
    assert state["run"]["status"] == "candidate"
    assert state["run"]["terminal_reason"] is None
    assert state["run"]["finished_at"] is None
    assert state["run"]["terminal_idempotency_key"] is None
    assert state["run"]["terminal_request_sha256"] is None
    assert state["terminals"] == []


def test_terminal_promotion_commits_message_delivery_and_event_together(tmp_path):
    store, context = _setup(tmp_path, name="success")
    promotion = context["promotion"]

    terminal = _terminate(store, promotion)
    state = _state(store, context)

    assert terminal["created"] is True
    assert terminal["status"] == "completed_with_issues"
    assert state["message"]["content"] == _PROMOTED
    for key, value in _verdict_metadata().items():
        assert state["message"]["metadata"][key] == value
    envelope = state["message"]["metadata"]["completion_delivery"]
    assert envelope["delivery_id"] == context["delivery_id"]
    assert envelope["status"] == "published"
    assert envelope["published_at"] == 500
    assert state["delivery"] == {
        "content_sha256": _sha_text(_PROMOTED),
        "status": "published",
        "published_at": 500,
    }
    assert state["run"]["status"] == "completed_with_issues"
    assert state["run"]["terminal_reason"] == "review_reported_issues"
    assert state["run"]["finished_at"] == 500
    assert len(state["terminals"]) == 1
    payload = json.loads(state["terminals"][0]["payload_json"])
    assert payload["message_promotion_sha256"] == _digest(promotion)
    assert payload["message_promotion_receipt"]["message_id"] == context["message_id"]
    assert state["terminals"][0]["payload_sha256"] == _digest(payload)
    raw_terminal = store.list_auto_mode_events(
        context["root"], branch_id=context["root"]
    )[-1]
    public_terminal = public_auto_event(raw_terminal)
    assert public_terminal is not None
    assert "message_promotion_sha256" not in public_terminal
    assert "message_promotion_receipt" not in public_terminal
    assert store.get_completion_delivery(context["delivery_id"])["status"] == (
        "published"
    )
    projection = store.project_auto_mode_run(context["root"], context["root"])
    assert projection["run"]["status"] == "completed_with_issues"
    store.close()


def test_verified_terminal_promotes_the_exact_latest_pass_review_bytes(tmp_path):
    store, context = _setup(tmp_path, name="verified-success")
    _complete_pass_review(store)
    promotion = deepcopy(context["promotion"])
    promotion["content"] = _CANDIDATE
    promotion["metadata"] = _verified_metadata()

    terminal = store.terminate_auto_mode_run(
        "run-1",
        idempotency_key="turn-1:terminal",
        status="verified",
        reason="review_passed",
        finished_at=500,
        message_promotion=promotion,
    )
    state = _state(store, context)

    assert terminal["status"] == "verified"
    assert state["message"]["content"] == _CANDIDATE
    assert state["message"]["metadata"]["review_status"] == "verified"
    assert state["message"]["metadata"]["review_run_id"] == "review-pass"
    assert state["delivery"]["status"] == "published"
    assert state["run"]["status"] == "verified"
    assert len(state["terminals"]) == 1
    store.close()


def test_verified_stage5_repair_binds_promoted_bytes_not_original_candidate(tmp_path):
    store, context = _setup(tmp_path, name="verified-repair")
    _complete_pass_review(
        store,
        candidate_answer=_PROMOTED,
        candidate_id="candidate-repaired",
        review_run_id="review-repaired",
        round_index=1,
    )
    promotion = deepcopy(context["promotion"])
    assert promotion["expected_content"] == _CANDIDATE
    promotion["metadata"] = _verified_metadata(
        _PROMOTED, review_run_id="review-repaired"
    )

    terminal = store.terminate_auto_mode_run(
        "run-1",
        idempotency_key="turn-1:terminal",
        status="verified",
        reason="review_passed",
        finished_at=500,
        message_promotion=promotion,
    )
    state = _state(store, context)

    assert terminal["status"] == "verified"
    assert state["message"]["content"] == _PROMOTED
    assert state["message"]["metadata"]["review_run_id"] == "review-repaired"
    assert state["delivery"]["content_sha256"] == _sha_text(_PROMOTED)
    assert state["run"]["status"] == "verified"
    store.close()


@pytest.mark.parametrize("mutation", ["unreviewed-content", "wrong-review"])
def test_verified_terminal_rejects_content_not_bound_to_latest_pass_review(
    tmp_path, mutation: str
):
    store, context = _setup(tmp_path, name=f"verified-binding-{mutation}")
    _complete_pass_review(store)
    promotion = deepcopy(context["promotion"])
    promotion["content"] = _CANDIDATE
    promotion["metadata"] = _verified_metadata()
    if mutation == "unreviewed-content":
        promotion["content"] = "CONTENT NEVER REVIEWED"
        promotion["metadata"]["reviewed_content_sha256"] = _sha_text(
            promotion["content"]
        )
    else:
        promotion["metadata"]["review_run_id"] = "another-pass-review"
    before = _state(store, context)

    with pytest.raises(
        AutoModeConflictError,
        match="verified promotion does not match its durable pass review",
    ):
        store.terminate_auto_mode_run(
            "run-1",
            idempotency_key="turn-1:terminal",
            status="verified",
            reason="review_passed",
            finished_at=500,
            message_promotion=promotion,
        )

    assert _state(store, context) == before
    assert before["message"]["metadata"]["review_status"] == "candidate"
    assert before["delivery"]["status"] == "committed"
    assert before["run"]["status"] == "candidate"
    assert before["terminals"] == []
    store.close()


@pytest.mark.parametrize(
    "name,mutate",
    [
        (
            "message-scope",
            lambda promotion: promotion.update(message_id="missing-message"),
        ),
        (
            "delivery-scope",
            lambda promotion: promotion.update(delivery_id="missing-delivery"),
        ),
        (
            "delivery-omitted",
            lambda promotion: promotion.pop("delivery_id"),
        ),
        (
            "expected-bytes",
            lambda promotion: promotion.update(expected_content="different bytes"),
        ),
        (
            "verdict-status",
            lambda promotion: promotion["metadata"].update(review_status="verified"),
        ),
        (
            "completion-gate",
            lambda promotion: promotion["metadata"].update(gates_completion=False),
        ),
        (
            "unverified-posture",
            lambda promotion: promotion["metadata"].update(unverified=False),
        ),
        (
            "turn-scope",
            lambda promotion: promotion["metadata"].update(turn_id="turn-2"),
        ),
        (
            "execution-scope",
            lambda promotion: promotion["metadata"].update(execution_id="execution-2"),
        ),
        (
            "candidate-digest",
            lambda promotion: promotion["metadata"].update(
                candidate_content_sha256="0" * 64
            ),
        ),
        (
            "reviewed-digest",
            lambda promotion: promotion["metadata"].update(
                reviewed_content_sha256="0" * 64
            ),
        ),
        (
            "reserved-delivery-envelope",
            lambda promotion: promotion["metadata"].update(
                completion_delivery={"status": "published"}
            ),
        ),
        (
            "reserved-verdict-digest",
            lambda promotion: promotion["metadata"].update(
                candidate_verdict_metadata_sha256="0" * 64
            ),
        ),
    ],
)
def test_terminal_promotion_cas_faults_leave_all_three_facts_unchanged(
    tmp_path,
    name: str,
    mutate: Callable[[dict[str, Any]], None],
):
    store, context = _setup(tmp_path, name=name)
    before = _state(store, context)
    _assert_candidate_state(before)
    promotion = deepcopy(context["promotion"])
    mutate(promotion)

    with pytest.raises(AutoModeConflictError):
        _terminate(store, promotion)

    assert _state(store, context) == before
    _assert_candidate_state(_state(store, context))
    store.close()


def test_terminal_promotion_rejects_non_review_terminal_without_mutation(tmp_path):
    store, context = _setup(tmp_path, name="non-review-terminal")
    before = _state(store, context)

    with pytest.raises(AutoModeConflictError):
        store.terminate_auto_mode_run(
            "run-1",
            idempotency_key="turn-1:terminal",
            status="failed",
            reason="execution_failed",
            finished_at=500,
            message_promotion=context["promotion"],
        )

    assert _state(store, context) == before
    _assert_candidate_state(_state(store, context))
    store.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("turn_id", "turn-2"),
        ("execution_id", "execution-2"),
        ("candidate_content_sha256", "0" * 64),
        ("gates_completion", False),
    ],
)
def test_terminal_promotion_rejects_a_candidate_with_drifted_identity(
    tmp_path,
    field: str,
    value: Any,
):
    store, context = _setup(tmp_path, name=f"candidate-{field}")
    row = store._conn.execute(  # noqa: SLF001 - corrupt durable candidate
        "SELECT metadata FROM messages WHERE message_id=?",
        (context["message_id"],),
    ).fetchone()
    metadata = json.loads(row["metadata"])
    metadata[field] = value
    store._conn.execute(  # noqa: SLF001
        "UPDATE messages SET metadata=? WHERE message_id=?",
        (
            json.dumps(metadata, sort_keys=True, separators=(",", ":")),
            context["message_id"],
        ),
    )
    store._conn.commit()  # noqa: SLF001
    before = _state(store, context)

    with pytest.raises(AutoModeConflictError):
        _terminate(store, context["promotion"])

    assert _state(store, context) == before
    assert before["run"]["status"] == "candidate"
    assert before["terminals"] == []
    store.close()


def test_terminal_promotion_rejects_publication_before_delivery_commit(tmp_path):
    store, context = _setup(tmp_path, name="publication-predates-commit")
    before = _state(store, context)

    with pytest.raises(AutoModeConflictError):
        store.terminate_auto_mode_run(
            "run-1",
            idempotency_key="turn-1:terminal",
            status="completed_with_issues",
            reason="review_reported_issues",
            finished_at=149,
            message_promotion=context["promotion"],
        )

    assert _state(store, context) == before
    _assert_candidate_state(_state(store, context))
    store.close()


def test_terminal_event_insert_fault_rolls_back_prior_message_and_delivery_updates(
    tmp_path,
):
    store, context = _setup(tmp_path, name="event-fault")
    before = _state(store, context)
    store._conn.execute(  # noqa: SLF001 - inject after both promotion updates
        "CREATE TRIGGER fail_terminal_event BEFORE INSERT ON auto_mode_events "
        "WHEN NEW.type='auto_run_terminal' BEGIN "
        "SELECT RAISE(ABORT,'injected terminal event failure'); END"
    )
    store._conn.commit()  # noqa: SLF001

    with pytest.raises(sqlite3.IntegrityError, match="terminal event failure"):
        _terminate(store, context["promotion"])

    assert _state(store, context) == before
    _assert_candidate_state(_state(store, context))
    store.close()


def test_exact_terminal_promotion_replay_is_a_read(tmp_path):
    store, context = _setup(tmp_path, name="replay")
    first = _terminate(store, context["promotion"])
    before_replay = _state(store, context)

    replay = _terminate(store, deepcopy(context["promotion"]))

    assert replay["created"] is False
    assert replay["event_id"] == first["event_id"]
    assert _state(store, context) == before_replay
    assert len(_state(store, context)["terminals"]) == 1
    store.close()


@pytest.mark.parametrize(
    "tamper",
    [
        "message-content",
        "message-metadata",
        "delivery-envelope",
        "delivery-hash",
    ],
)
def test_terminal_promotion_projection_fails_safe_after_fact_drift(
    tmp_path,
    tamper: str,
):
    store, context = _setup(tmp_path, name=f"read-tamper-{tamper}")
    _terminate(store, context["promotion"])

    if tamper == "message-content":
        store._conn.execute(  # noqa: SLF001 - corrupt promoted bytes
            "UPDATE messages SET content=? WHERE message_id=?",
            ("Tampered reviewed result.", context["message_id"]),
        )
    elif tamper == "delivery-hash":
        store._conn.execute(  # noqa: SLF001 - corrupt delivery binding
            "UPDATE completion_deliveries SET content_sha256=? " "WHERE delivery_id=?",
            ("0" * 64, context["delivery_id"]),
        )
    else:
        row = store._conn.execute(  # noqa: SLF001
            "SELECT metadata FROM messages WHERE message_id=?",
            (context["message_id"],),
        ).fetchone()
        metadata = json.loads(row["metadata"])
        if tamper == "message-metadata":
            metadata["user_truth"] = "Tampered verdict truth."
        else:
            metadata["completion_delivery"]["published_at"] += 1
        store._conn.execute(  # noqa: SLF001
            "UPDATE messages SET metadata=? WHERE message_id=?",
            (
                json.dumps(metadata, sort_keys=True, separators=(",", ":")),
                context["message_id"],
            ),
        )
    store._conn.commit()  # noqa: SLF001

    projected = store.project_auto_mode_run(context["root"], context["root"])

    assert projected["run"]["source_claimed_status"] == "completed_with_issues"
    assert projected["run"]["status"] == "failed"
    assert projected["run"]["terminal_reason"] == "safety_boundary"
    terminal = next(
        event for event in projected["events"] if event["type"] == "auto_run_terminal"
    )
    assert terminal["payload"]["status"] == "failed"
    assert terminal["payload"]["terminal_reason"] == "safety_boundary"
    store.close()


@pytest.mark.parametrize(
    "mutation",
    [
        "omitted",
        "different-message",
        "different-expected-content",
        "metadata-subset",
    ],
)
def test_terminal_replay_rejects_a_different_message_or_promotion(
    tmp_path, mutation: str
):
    store, context = _setup(tmp_path, name=f"replay-{mutation}")
    _terminate(store, context["promotion"])
    before = _state(store, context)
    changed = deepcopy(context["promotion"])
    if mutation == "different-message":
        changed["message_id"] = "another-message"
    elif mutation == "different-expected-content":
        changed["expected_content"] = "different expected bytes"
    elif mutation == "metadata-subset":
        changed["metadata"] = {
            "review_status": "completed_with_issues",
            "user_truth": _verdict_metadata()["user_truth"],
        }

    fields: dict[str, Any] = {
        "idempotency_key": "turn-1:terminal",
        "status": "completed_with_issues",
        "reason": "review_reported_issues",
        "finished_at": 500,
    }
    if mutation != "omitted":
        fields["message_promotion"] = changed
    with pytest.raises(AutoModeConflictError):
        store.terminate_auto_mode_run("run-1", **fields)

    assert _state(store, context) == before
    assert len(_state(store, context)["terminals"]) == 1
    store.close()
