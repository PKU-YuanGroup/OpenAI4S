"""Stage 4 completion gate: review happens before promotion."""

from __future__ import annotations

import json
from types import SimpleNamespace

from openai4s.config import AutoModeConfig, Config, RoadmapFeatureFlags
from openai4s.server.completion_gate import CompletionGateService, terminal_for_review
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
    return _services(store, cfg).gate_after_turn(
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


def test_candidate_event_precedes_verified_terminal(tmp_path):
    store = _store(tmp_path)
    events = []
    cfg = _cfg(stage2=True)
    result = _gate(store, cfg, events)
    assert result is not None
    types = [item.get("type") for item in events]
    assert types.index("candidate_ready") < types.index("auto_run_terminal")
    assert result["terminal"] == "verified"
    assert result["gates_completion"] is True
    loaded = CompletionGateService(
        store=store, config=cfg, scientific_review=None
    ).load("root-1")
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
    result = _gate(store, _cfg(stage2=False), events)
    assert result is not None
    assert result["terminal"] == "review_unavailable"
    assert "not verified" in result["user_truth"]
    stamp = _message_review_gate(store.list_messages("root-1")[-1])
    assert stamp["status"] == "review_unavailable"
    assert stamp["unverified"] is True
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
    persist_at = source.index("for blk in assistant_visible:\n")
    gate_at = source.index("self.completion_gate.gate_after_turn(")
    assert persist_at < gate_at, "the answer must be durable before the review"
    assert "metadata=provisional_metadata," in source
    assert '"review_status": "candidate"' in source
