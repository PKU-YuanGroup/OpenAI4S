"""Stage 4 completion gate: review happens before promotion."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from openai4s.config import AutoModeConfig, Config, RoadmapFeatureFlags
from openai4s.server.completion_gate import (
    CompletionGateService,
    message_review_metadata,
    terminal_for_review,
)
from openai4s.server.gateway import _message_review_gate
from openai4s.server.scientific_review import ScientificReviewService
from openai4s.store import Store


def _cfg(stage4=True, stage3=True, stage2=False):
    return Config(
        roadmap_features=RoadmapFeatureFlags(
            stage2_auto_run_storage=stage2,
            stage3_scientific_review_shadow=stage3,
            stage4_review_completion_gate=stage4,
        ),
        auto_mode=AutoModeConfig(result_review_mode="review_only"),
    )


def _llm(model="reviewer"):
    return SimpleNamespace(
        provider="openai",
        model=model,
        base_url="https://review.example/v1",
        timeout_s=30,
        max_tokens=800,
    )


def _pass_chat(messages, cfg, **kwargs):
    return {
        "content": json.dumps({"verdict": "pass", "summary": "ok", "findings": []}),
        "usage": {},
    }


def _store(tmp_path):
    store = Store(tmp_path / "gate.db")
    store.create_project(name="p", project_id="project-1")
    store._conn.execute(
        "INSERT INTO frames(frame_id,parent_id,project_id,root_frame_id,kind,"
        "status,depth,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        ("root-1", None, "project-1", "root-1", "turn", "processing", 0, 1, 1),
    )
    store._conn.commit()
    store.ensure_session_branch(root_frame_id="root-1", branch_id="root-1")
    store.add_message(
        root_frame_id="root-1",
        branch_id="root-1",
        role="assistant",
        content="qualitative limitation only",
        frame_id="root-1",
        metadata={
            "review_status": "candidate",
            "user_truth": "Candidate · provisional / not verified",
            "gates_completion": True,
            "unverified": True,
            "turn_id": "turn-gate",
            "execution_id": "exec-gate",
            "candidate_content_sha256": hashlib.sha256(
                b"qualitative limitation only"
            ).hexdigest(),
        },
    )
    return store


def _services(store, cfg, chat=_pass_chat):
    auto = SimpleNamespace(
        get=lambda frame_id: {
            "selection": {
                "result_review_mode": "review_only",
                "preset": "off",
                "approvals_reviewer": "user",
            }
        }
    )
    review = ScientificReviewService(
        store=store, config=cfg, auto_mode=auto, chat_call=chat
    )
    gate = CompletionGateService(
        store=store, config=cfg, scientific_review=review, auto_mode=auto
    )
    return gate


def _gate(store, cfg, events):
    service = _services(store, cfg)
    result = service.gate_after_turn(
        root_frame_id="root-1",
        project_id="project-1",
        branch_id="root-1",
        turn_id="turn-gate",
        execution_id="exec-gate",
        user_request="state the limitation",
        candidate_answer="qualitative limitation only",
        agent_cfg=_llm("agent"),
        reviewer_cfg=_llm("reviewer"),
        emit=events.append,
    )
    return service, result


def _finalize(service, result, *, delivered=True, emit=None):
    answer = str(result["final_answer"])
    original = "qualitative limitation only"
    message_id = service.store._conn.execute(
        "SELECT message_id FROM messages WHERE root_frame_id=? "
        "ORDER BY seq DESC LIMIT 1",
        ("root-1",),
    ).fetchone()[0]
    metadata = message_review_metadata(result)
    metadata.update(
        {
            "turn_id": "turn-gate",
            "execution_id": "exec-gate",
            "candidate_content_sha256": hashlib.sha256(
                original.encode("utf-8")
            ).hexdigest(),
            "reviewed_content_sha256": hashlib.sha256(
                answer.encode("utf-8")
            ).hexdigest(),
        }
    )
    return service.finalize_after_delivery(
        "root-1",
        "root-1",
        result,
        delivered,
        emit=emit,
        message_id=str(message_id) if delivered else None,
        expected_message_content=original if delivered else None,
        promoted_message_content=answer if delivered else None,
        message_metadata=metadata if delivered else None,
    )


def test_candidate_event_precedes_verified_terminal(tmp_path):
    store = _store(tmp_path)
    events = []
    cfg = _cfg(stage2=True)
    service, result = _gate(store, cfg, events)
    assert result is not None
    types = [item.get("type") for item in events]
    assert "candidate_ready" in types
    assert "auto_run_terminal" not in types
    assert result["terminal"] == "verified"
    assert result["gates_completion"] is True
    assert result["finalized"] is False
    assert service.load("root-1") is None
    assert _message_review_gate(store.list_messages("root-1")[-1])["status"] == (
        "candidate"
    )

    finalized = _finalize(service, result)
    assert finalized["terminal"] == "verified"
    assert finalized["finalized"] is True
    assert finalized["durable_terminal"] is True
    types = [item.get("type") for item in store.list_auto_mode_events("root-1")]
    assert types.index("candidate_ready") < types.index("auto_run_terminal")
    assert types.count("auto_run_terminal") == 1
    loaded = service.load("root-1")
    assert loaded["terminal"] == "verified"
    messages = store.list_messages("root-1")
    stamp = _message_review_gate(messages[-1])
    assert stamp["status"] == "verified"
    assert stamp["unverified"] is False
    store.close()


def test_verified_is_unreachable_without_durable_review_storage(tmp_path):
    """Stage 4 without Stage 2 must not stamp Verified from an in-memory dict.

    `_assert_verified_locked` is the only check that an independent pass review
    actually exists, in the right event order, with no material findings open --
    and it lives behind Stage 2 storage. The stage flags are independent
    booleans with no cross-validation, so a deployment that enables the gate but
    not the storage used to publish a green badge nothing could substantiate.
    """

    store = _store(tmp_path)
    events = []
    service, result = _gate(store, _cfg(stage2=False), events)
    assert result is not None
    assert result["terminal"] == "review_unavailable"
    assert "not verified" in result["user_truth"]
    stamp = _message_review_gate(store.list_messages("root-1")[-1])
    assert stamp["status"] == "candidate"

    finalized = _finalize(service, result)
    assert finalized["finalized"] is True
    assert finalized["durable_terminal"] is False
    assert finalized["durable_promotion"] is True
    stamp = _message_review_gate(store.list_messages("root-1")[-1])
    assert stamp["status"] == "review_unavailable"
    assert stamp["unverified"] is True
    assert service.load("root-1")["terminal"] == "review_unavailable"
    store.close()


def test_lost_terminal_response_replays_one_atomic_promotion(tmp_path):
    store = _store(tmp_path)
    service, result = _gate(store, _cfg(stage2=True), [])
    terminate = store.terminate_auto_mode_run
    calls = 0

    def _lost_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        transition = terminate(*args, **kwargs)
        if calls == 1:
            raise RuntimeError("response lost after commit")
        return transition

    store.terminate_auto_mode_run = _lost_once
    finalized = _finalize(service, result)

    assert calls == 2
    assert finalized["terminal"] == "verified"
    assert finalized["finalized"] is True
    events = store.list_auto_mode_events("root-1", branch_id="root-1")
    assert [item["type"] for item in events].count("auto_run_terminal") == 1
    assert _message_review_gate(store.list_messages("root-1")[-1])["status"] == (
        "verified"
    )
    store.close()


def test_stage2_off_lost_promotion_response_replays_the_exact_message(tmp_path):
    store = _store(tmp_path)
    service, result = _gate(store, _cfg(stage2=False), [])
    promote = store.promote_candidate_message
    calls = 0

    def _lost_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        receipt = promote(*args, **kwargs)
        if calls == 1:
            raise RuntimeError("response lost after message CAS")
        return receipt

    store.promote_candidate_message = _lost_once
    finalized = _finalize(service, result)

    assert calls == 2
    assert finalized["terminal"] == "review_unavailable"
    assert finalized["finalized"] is True
    assert finalized["durable_promotion"] is True
    messages = store.list_messages("root-1")
    assert len(messages) == 1
    stamp = _message_review_gate(messages[0])
    assert stamp["status"] == "review_unavailable"
    store.close()


def test_stage2_off_never_splits_a_stage1_delivery_across_transactions(tmp_path):
    store = _store(tmp_path)
    service, result = _gate(store, _cfg(stage2=False), [])
    message_id = store._conn.execute(
        "SELECT message_id FROM messages WHERE root_frame_id=? "
        "ORDER BY seq DESC LIMIT 1",
        ("root-1",),
    ).fetchone()[0]
    answer = str(result["final_answer"])
    metadata = message_review_metadata(result)
    metadata.update(
        {
            "turn_id": "turn-gate",
            "execution_id": "exec-gate",
            "candidate_content_sha256": hashlib.sha256(
                b"qualitative limitation only"
            ).hexdigest(),
            "reviewed_content_sha256": hashlib.sha256(
                answer.encode("utf-8")
            ).hexdigest(),
        }
    )

    def _must_not_run(*args, **kwargs):
        raise AssertionError("split transaction attempted")

    store.promote_candidate_delivery = _must_not_run
    store.mark_completion_delivery_published = _must_not_run
    failed = service.finalize_after_delivery(
        "root-1",
        "root-1",
        result,
        True,
        message_id=str(message_id),
        expected_message_content="qualitative limitation only",
        promoted_message_content=answer,
        completion_delivery_id="delivery-stage1",
        message_metadata=metadata,
    )

    assert failed["finalized"] is False
    assert failed["reason"] == "durable_atomic_promotion_unavailable"
    assert _message_review_gate(store.list_messages("root-1")[-1])["status"] == (
        "candidate"
    )
    store.close()


def test_terminal_failure_keeps_the_exact_message_candidate(tmp_path):
    store = _store(tmp_path)
    service, result = _gate(store, _cfg(stage2=True), [])
    calls = 0

    def _broken(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("sqlite unavailable")

    store.terminate_auto_mode_run = _broken
    failed = _finalize(service, result)

    assert calls == 2
    assert failed["terminal"] == "review_unavailable"
    assert failed["finalized"] is False
    assert service.load("root-1") is None
    assert _message_review_gate(store.list_messages("root-1")[-1])["status"] == (
        "candidate"
    )
    events = store.list_auto_mode_events("root-1", branch_id="root-1")
    assert "auto_run_terminal" not in [item["type"] for item in events]
    store.close()


def test_emit_failure_after_terminal_commit_does_not_undo_finalization(tmp_path):
    store = _store(tmp_path)
    service, result = _gate(store, _cfg(stage2=True), [])

    def _broken_emit(_event):
        raise RuntimeError("socket closed")

    finalized = _finalize(service, result, emit=_broken_emit)

    assert finalized["terminal"] == "verified"
    assert finalized["finalized"] is True
    assert finalized["durable_terminal"] is True
    assert service.load("root-1")["terminal"] == "verified"
    store.close()


def test_post_review_cancel_closes_review_then_seals_cancelled(tmp_path):
    store = _store(tmp_path)
    cfg = _cfg(stage2=True)
    service = _services(store, cfg)
    result = service.gate_after_turn(
        root_frame_id="root-1",
        project_id="project-1",
        branch_id="root-1",
        turn_id="turn-cancel",
        execution_id="exec-cancel",
        user_request="state it",
        candidate_answer="a qualitative limitation",
        agent_cfg=_llm("agent"),
        reviewer_cfg=_llm("reviewer"),
        cancel=lambda: True,
    )

    assert result["terminal"] == "cancelled"
    assert result["durable_review"]["closed"] is True
    before = store.project_auto_mode_run("root-1", "root-1")["run"]
    assert before["status"] != "reviewing"

    finalized = service.finalize_after_delivery(
        "root-1", "root-1", result, delivered=False
    )
    assert finalized["terminal"] == "cancelled"
    assert finalized["finalized"] is True
    assert finalized["durable_terminal"] is True
    projected = store.project_auto_mode_run("root-1", "root-1")["run"]
    assert projected["status"] == "cancelled"
    store.close()


def test_reviewer_exception_closes_stage4_audit_without_shadow_stamp(tmp_path):
    store = _store(tmp_path)
    service = _services(store, _cfg(stage2=True))

    def _raises(*args, **kwargs):
        raise RuntimeError("review provider failed")

    service.scientific_review.evaluate = _raises
    result = service.gate_after_turn(
        root_frame_id="root-1",
        project_id="project-1",
        branch_id="root-1",
        turn_id="turn-error",
        execution_id="exec-error",
        user_request="state it",
        candidate_answer="a qualitative limitation",
        agent_cfg=_llm("agent"),
        reviewer_cfg=_llm("reviewer"),
    )

    assert result["terminal"] == "review_unavailable"
    assert result["durable_review"]["closed"] is True
    assert store.project_auto_mode_run("root-1", "root-1")["run"]["status"] != (
        "reviewing"
    )
    audits = store.list_auto_mode_audits(
        "root-1", "root-1", subject_kind="result_review"
    )
    assert audits
    assessment = json.loads(
        store._conn.execute(
            "SELECT assessment_json FROM review_runs WHERE audit_id=?",
            (audits[0]["audit_id"],),
        ).fetchone()[0]
    )
    assert assessment["shadow"] is False
    assert assessment["gates_completion"] is True
    assert assessment["stage"] == 4
    store.close()


def test_issues_are_completed_with_issues_not_verified(tmp_path):
    store = _store(tmp_path)
    result = _services(store, _cfg()).gate_after_turn(
        root_frame_id="root-1",
        project_id="project-1",
        branch_id="root-1",
        turn_id="turn-issues",
        execution_id="exec-issues",
        user_request="report the table",
        candidate_answer="missing-final.csv proves n=99",
        agent_cfg=_llm("agent"),
        reviewer_cfg=_llm("reviewer"),
        emit=lambda event: None,
    )
    assert result["terminal"] == "completed_with_issues"
    assert result["gate"]["unverified"] is True
    assert "Verified" not in result["user_truth"]
    store.close()


def test_flag_off_does_not_gate(tmp_path):
    store = _store(tmp_path)
    result = _services(store, _cfg(stage4=False)).gate_after_turn(
        root_frame_id="root-1",
        project_id="project-1",
        branch_id="root-1",
        turn_id="turn-off",
        execution_id="exec-off",
        user_request="x",
        candidate_answer="y",
        agent_cfg=_llm("agent"),
        reviewer_cfg=_llm("reviewer"),
    )
    assert result is None
    store.close()


def test_terminal_mapping_refuses_to_call_incomplete_verified():
    terminal, truth = terminal_for_review({"verdict": "incomplete", "findings": []})
    assert terminal == "review_unavailable"
    assert "not verified" in truth
    terminal, _ = terminal_for_review(
        {
            "verdict": "issues",
            "findings": [{"severity": "high"}],
        }
    )
    assert terminal == "completed_with_issues"


def test_the_answer_row_is_written_provisional_before_the_review(tmp_path):
    """The row is durable and MARKED before the long part of the turn runs.

    Gating before the write would be worse, not better: the gate is a reviewer
    round-trip plus, under auto_fix, a whole repair loop, so a hard exit during
    it would lose the answer the user is already reading. What the old order got
    wrong was leaving the row durable and UNMARKED -- an answer that looked
    reviewed and never would be. Provisional-at-write is honest at every instant.
    """

    source = __import__("pathlib").Path("openai4s/server/gateway.py").read_text("utf-8")
    candidate_metadata_at = source.index("provisional_metadata: dict[str, object]")
    delivery_at = source.index(
        "message_metadata=provisional_metadata", candidate_metadata_at
    )
    row_at = source.index(
        "candidate_row = self.store.add_message(", candidate_metadata_at
    )
    gate_at = source.index("self.completion_gate.gate_after_turn(")
    finalize_at = source.index("self.completion_gate.finalize_after_delivery(", gate_at)
    assert delivery_at < gate_at
    assert row_at < gate_at, "the answer must be durable before the review"
    assert gate_at < finalize_at, "terminal promotion must follow the review"
    assert "metadata=provisional_metadata," in source
    assert '"review_status": "candidate"' in source
