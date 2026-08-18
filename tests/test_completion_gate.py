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


def test_candidate_event_precedes_verified_terminal(tmp_path):
    store = _store(tmp_path)
    events = []
    result = _services(store, _cfg()).gate_after_turn(
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
    assert result is not None
    types = [item.get("type") for item in events]
    assert types.index("candidate_ready") < types.index("auto_run_terminal")
    assert result["terminal"] == "verified"
    assert result["gates_completion"] is True
    loaded = CompletionGateService(
        store=store, config=_cfg(), scientific_review=None
    ).load("root-1")
    assert loaded["terminal"] == "verified"
    messages = store.list_messages("root-1")
    stamp = _message_review_gate(messages[-1])
    assert stamp["status"] == "verified"
    assert stamp["unverified"] is False
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
