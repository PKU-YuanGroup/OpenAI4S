"""Stage 3 shadow orchestrator on the shipped evaluate/shadow entry points."""

from __future__ import annotations

import json
from types import SimpleNamespace

from openai4s.config import AutoModeConfig, Config, RoadmapFeatureFlags
from openai4s.server.evidence_snapshot import freeze_evidence_snapshot
from openai4s.server.scientific_review import ScientificReviewService
from openai4s.store import Store


def _cfg(**flags):
    return Config(
        roadmap_features=RoadmapFeatureFlags(
            stage2_auto_run_storage=flags.get("stage2", False),
            stage3_scientific_review_shadow=flags.get("stage3", True),
        ),
        auto_mode=AutoModeConfig(result_review_mode="review_only"),
    )


def _llm(
    provider="openai", model="reviewer-model", base_url="https://review.example/v1"
):
    return SimpleNamespace(
        provider=provider,
        model=model,
        base_url=base_url,
        timeout_s=30,
        max_tokens=800,
    )


def _pass_chat(messages, cfg, **kwargs):
    return {
        "content": json.dumps({"verdict": "pass", "summary": "ok", "findings": []}),
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


def _snapshot(**overrides):
    parts = {
        "identity": {
            "root_frame_id": "root-1",
            "branch_id": "root-1",
            "turn_id": "turn-1",
            "execution_id": "exec-1",
        },
        "user_request": "report n and mean of resid.csv",
        "plan": {"title": "residuals"},
        "candidate_answer": "resid.csv has n=2 and mean=2.0",
        "artifacts": [
            {
                "artifact_id": "art-1",
                "filename": "resid.csv",
                "content_type": "text/csv",
                "version_id": "ver-1",
                "checksum": "a" * 64,
                "exists": True,
            }
        ],
        "adapters": [
            {
                "adapter": "table",
                "version_id": "ver-1",
                "artifact_id": "art-1",
                "complete": True,
                "summary": {
                    "row_count": 2,
                    "columns": {"value": {"mean": 2.0}},
                },
            }
        ],
        "cells": [{"cell_id": "cell-1"}],
        "truncation": {},
    }
    parts.update(overrides)
    return freeze_evidence_snapshot(parts)


def test_auto_fix_same_model_is_unavailable_without_inference():
    called = {"n": 0}

    def chat_call(messages, cfg, **kwargs):
        called["n"] += 1
        return _pass_chat(messages, cfg, **kwargs)

    service = ScientificReviewService(store=None, config=_cfg(), chat_call=chat_call)
    same = _llm()
    result = service.evaluate(
        _snapshot(),
        result_review_mode="auto_fix",
        agent_cfg=same,
        reviewer_cfg=same,
        chat_call=chat_call,
    )
    assert result["verdict"] == "review_unavailable"
    assert result["reason"] == "reviewer_independence_unavailable"
    assert result["gates_completion"] is False
    assert called["n"] == 0


def test_review_only_may_use_same_model_independent_session():
    service = ScientificReviewService(store=None, config=_cfg(), chat_call=_pass_chat)
    same = _llm()
    result = service.evaluate(
        _snapshot(),
        result_review_mode="review_only",
        agent_cfg=same,
        reviewer_cfg=same,
        chat_call=_pass_chat,
    )
    assert result["verdict"] == "pass"
    assert result["same_model_independent_session"] is True
    assert result["gates_completion"] is False


def test_numeric_mismatch_is_a_high_finding_even_if_model_passes():
    service = ScientificReviewService(store=None, config=_cfg(), chat_call=_pass_chat)
    snapshot = _snapshot(candidate_answer="resid.csv has n=99 and mean=0.01")
    result = service.evaluate(
        snapshot,
        result_review_mode="review_only",
        agent_cfg=_llm(model="agent"),
        reviewer_cfg=_llm(model="reviewer"),
        chat_call=_pass_chat,
    )
    assert result["verdict"] == "issues"
    categories = {item["category"] for item in result["findings"]}
    assert "claim_mismatch" in categories
    for finding in result["findings"]:
        for ref in finding["evidence_refs"]:
            assert any(
                row["ref_id"] == ref for row in result["snapshot"]["evidence_refs"]
            )


def test_forged_evidence_ref_cannot_pass():
    def forged_chat(messages, cfg, **kwargs):
        return {
            "content": json.dumps(
                {
                    "verdict": "pass",
                    "findings": [
                        {
                            "severity": "low",
                            "category": "other",
                            "claim_ref": "looks fine",
                            "evidence_refs": ["forged:not-in-snapshot"],
                            "reproduction": "invented",
                            "confidence": 0.8,
                        }
                    ],
                }
            ),
            "usage": {},
        }

    service = ScientificReviewService(store=None, config=_cfg(), chat_call=forged_chat)
    result = service.evaluate(
        _snapshot(),
        result_review_mode="review_only",
        agent_cfg=_llm(model="agent"),
        reviewer_cfg=_llm(model="reviewer"),
        chat_call=forged_chat,
    )
    assert result["verdict"] == "issues"
    assert any("forged" in item["claim_ref"] for item in result["findings"])


def test_shadow_after_turn_records_step_and_includes_plan(tmp_path):
    store = Store(tmp_path / "stage3.db")
    store.create_project(name="p", project_id="project-1")
    store._conn.execute(
        "INSERT INTO frames(frame_id,parent_id,project_id,root_frame_id,kind,"
        "status,depth,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        ("root-1", None, "project-1", "root-1", "turn", "processing", 0, 1, 1),
    )
    store._conn.commit()
    store.ensure_session_branch(root_frame_id="root-1", branch_id="root-1")
    store.add_step(
        step_id="plan-step",
        frame_id="root-1",
        kind="plan",
        title="Plan",
        input={"title": "analyze"},
        status="done",
    )
    cfg = _cfg(stage3=True, stage2=False)
    auto = SimpleNamespace(
        get=lambda frame_id: {
            "selection": {
                "result_review_mode": "review_only",
                "preset": "off",
                "approvals_reviewer": "user",
            }
        }
    )
    service = ScientificReviewService(
        store=store, config=cfg, auto_mode=auto, chat_call=_pass_chat
    )
    result = service.shadow_after_turn(
        root_frame_id="root-1",
        project_id="project-1",
        branch_id="root-1",
        turn_id="turn-plan",
        execution_id="exec-plan",
        user_request="make a plan then execute",
        candidate_answer="plan ready",
        structured_completion={"plan": True},
        agent_cfg=_llm(model="agent"),
        reviewer_cfg=_llm(model="reviewer"),
    )
    assert result is not None
    assert result["gates_completion"] is False
    steps = [
        item for item in store.list_steps("root-1") if item.get("kind") == "review"
    ]
    assert steps
    output = steps[-1].get("output") or {}
    if isinstance(output, str):
        output = json.loads(output)
    assert output.get("mode") == "shadow"
    assert output.get("gates_completion") is False
    store.close()


def test_gateway_stage3_hook_does_not_skip_plans():
    from pathlib import Path

    text = Path("openai4s/server/gateway.py").read_text(encoding="utf-8")
    assert "scientific_review.shadow_after_turn" in text
    hook_index = text.index("scientific_review.shadow_after_turn")
    window = text[hook_index - 400 : hook_index]
    assert "not st.plan" not in window
    assert "stage3_scientific_review_shadow" in text


def test_shadow_persists_review_audit_when_stage2_storage_is_on(tmp_path):
    store = Store(tmp_path / "stage3-persist.db")
    store.create_project(name="p", project_id="project-1")
    store._conn.execute(
        "INSERT INTO frames(frame_id,parent_id,project_id,root_frame_id,kind,"
        "status,depth,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        ("root-1", None, "project-1", "root-1", "turn", "processing", 0, 1, 1),
    )
    store._conn.commit()
    store.ensure_session_branch(root_frame_id="root-1", branch_id="root-1")
    cfg = _cfg(stage3=True, stage2=True)
    auto = SimpleNamespace(
        get=lambda frame_id: {
            "selection": {
                "result_review_mode": "review_only",
                "preset": "off",
                "approvals_reviewer": "user",
            }
        }
    )
    service = ScientificReviewService(
        store=store,
        config=cfg,
        auto_mode=auto,
        chat_call=_pass_chat,
        owner_instance_id="test-daemon",
    )
    result = service.shadow_after_turn(
        root_frame_id="root-1",
        project_id="project-1",
        branch_id="root-1",
        turn_id="turn-persist",
        execution_id="exec-persist",
        user_request="summarize",
        candidate_answer="qualitative limitation only",
        agent_cfg=_llm(model="agent"),
        reviewer_cfg=_llm(model="reviewer"),
    )
    assert result is not None
    audits = store.list_auto_mode_audits(
        "root-1", "root-1", subject_kind="result_review"
    )
    rows = audits.get("audits") if isinstance(audits, dict) else audits
    assert rows
    store.close()


def test_flag_off_is_inert(tmp_path):
    store = Store(tmp_path / "off.db")
    service = ScientificReviewService(store=store, config=_cfg(stage3=False))
    assert (
        service.shadow_after_turn(
            root_frame_id="root-1",
            project_id="p",
            branch_id="root-1",
            turn_id="t",
            execution_id="e",
            user_request="x",
            candidate_answer="y",
            agent_cfg=_llm(),
            reviewer_cfg=_llm(),
        )
        is None
    )
    store.close()
