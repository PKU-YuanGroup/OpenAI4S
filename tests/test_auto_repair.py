"""Stage 5 auto-fix / re-review: planted cases become a corrected candidate."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openai4s.config import AutoModeBudgets, AutoModeConfig, Config, RoadmapFeatureFlags
from openai4s.server.auto_repair import (
    AutoRepairService,
    adapt_legacy_repair_fn,
    apply_claim_repair,
)
from openai4s.server.evidence_snapshot import freeze_evidence_snapshot
from openai4s.server.scientific_review import ScientificReviewService


def _cfg(stage5=True):
    return Config(
        roadmap_features=RoadmapFeatureFlags(stage5_auto_repair=stage5),
        auto_mode=AutoModeConfig(
            result_review_mode="auto_fix",
            approvals_reviewer="auto_review",
            budgets=AutoModeBudgets(max_repair_rounds=2),
        ),
    )


def _llm(model):
    return SimpleNamespace(
        provider="openai",
        model=model,
        base_url="https://review.example/v1",
        timeout_s=30,
        max_tokens=400,
    )


def _pass_chat(messages, cfg, **kwargs):
    return {
        "content": json.dumps({"verdict": "pass", "summary": "ok", "findings": []}),
        "usage": {},
    }


def _table_snapshot(answer: str, *, rows=3, mean=2.0, nulls=2):
    return freeze_evidence_snapshot(
        {
            "identity": {
                "root_frame_id": "root-1",
                "branch_id": "root-1",
                "turn_id": "turn-1",
                "execution_id": "exec-1",
            },
            "user_request": "report residuals",
            "candidate_answer": answer,
            "artifacts": [
                {
                    "artifact_id": "art-resid",
                    "filename": "resid.csv",
                    "content_type": "text/csv",
                    "version_id": "ver-1",
                    "checksum": "ab" * 32,
                    "exists": True,
                }
            ],
            "adapters": [
                {
                    "adapter": "table",
                    "version_id": "ver-1",
                    "artifact_id": "art-resid",
                    "complete": True,
                    "summary": {
                        "row_count": rows,
                        "columns": {
                            "value": {"mean": mean, "null_count": nulls},
                        },
                    },
                }
            ],
        }
    )


def test_residual_row_miscount_is_found_and_repaired():
    review = ScientificReviewService(store=None, config=_cfg(), chat_call=_pass_chat)
    snapshot = _table_snapshot("resid.csv has n=99 and mean=2.0")
    first = review.evaluate(
        snapshot,
        result_review_mode="auto_fix",
        agent_cfg=_llm("agent"),
        reviewer_cfg=_llm("reviewer"),
        chat_call=_pass_chat,
    )
    assert first["verdict"] == "issues"
    repaired = AutoRepairService(
        store=None, config=_cfg(), scientific_review=review
    ).run(
        initial=first,
        result_review_mode="auto_fix",
        agent_cfg=_llm("agent"),
        reviewer_cfg=_llm("reviewer"),
    )
    assert repaired["verdict"] == "pass"
    assert "n=3" in repaired["snapshot"]["candidate_answer"]


def test_missing_value_misstatement_is_found_and_repaired():
    review = ScientificReviewService(store=None, config=_cfg(), chat_call=_pass_chat)
    snapshot = _table_snapshot("resid.csv has n=3 and mean=2.0 with no missing values")
    first = review.evaluate(
        snapshot,
        result_review_mode="auto_fix",
        agent_cfg=_llm("agent"),
        reviewer_cfg=_llm("reviewer"),
        chat_call=_pass_chat,
    )
    assert first["verdict"] == "issues"
    repaired = AutoRepairService(
        store=None, config=_cfg(), scientific_review=review
    ).run(
        initial=first,
        result_review_mode="auto_fix",
        agent_cfg=_llm("agent"),
        reviewer_cfg=_llm("reviewer"),
    )
    assert repaired["verdict"] == "pass"
    assert "missing values in value=2" in repaired["snapshot"]["candidate_answer"]


def test_multi_column_means_are_repaired_by_column_without_cross_assignment():
    snapshot = {
        "candidate_answer": "mean of x=10 and mean of y=20",
        "artifacts": [],
        "adapters": [
            {
                "adapter": "table",
                "complete": True,
                "summary": {
                    "row_count": 1,
                    "columns": {
                        "x": {"mean": 1.0},
                        "y": {"mean": 2.0},
                    },
                },
            }
        ],
    }

    repaired = apply_claim_repair(
        snapshot,
        [{"category": "claim_mismatch", "severity": "high"}],
    )

    assert repaired["candidate_answer"] == "mean of x=1.0 and mean of y=2.0"
    assert repaired["changed"] is True


def test_identical_bytes_reuse_the_previous_version():
    before = [
        {
            "artifact_id": "art-resid",
            "version_id": "ver-1",
            "checksum": "ab" * 32,
        }
    ]
    after = [
        {
            "artifact_id": "art-resid",
            "version_id": "ver-2-should-not-stick",
            "checksum": "ab" * 32,
        }
    ]
    service = AutoRepairService(store=None, config=_cfg(), scientific_review=None)
    reused = service._reuse_identical_versions(before, after)
    assert reused[0]["version_id"] == "ver-1"


def test_repair_cannot_self_certify():
    review = ScientificReviewService(store=None, config=_cfg(), chat_call=_pass_chat)

    def bad_repair(snapshot, findings):
        return {"changed": True, "self_certified": True, "candidate_answer": "fixed"}

    service = AutoRepairService(
        store=None, config=_cfg(), scientific_review=review, repair_fn=bad_repair
    )
    first = review.evaluate(
        _table_snapshot("resid.csv has n=99 and mean=2.0"),
        result_review_mode="auto_fix",
        agent_cfg=_llm("agent"),
        reviewer_cfg=_llm("reviewer"),
        chat_call=_pass_chat,
    )
    with pytest.raises(RuntimeError, match="cannot certify"):
        service.run(
            initial=first,
            result_review_mode="auto_fix",
            agent_cfg=_llm("agent"),
            reviewer_cfg=_llm("reviewer"),
        )


def test_review_only_never_repairs():
    review = ScientificReviewService(store=None, config=_cfg(), chat_call=_pass_chat)
    first = review.evaluate(
        _table_snapshot("resid.csv has n=99 and mean=2.0"),
        result_review_mode="review_only",
        agent_cfg=_llm("agent"),
        reviewer_cfg=_llm("reviewer"),
        chat_call=_pass_chat,
    )
    repaired = AutoRepairService(
        store=None, config=_cfg(), scientific_review=review
    ).run(
        initial=first,
        result_review_mode="review_only",
        agent_cfg=_llm("agent"),
        reviewer_cfg=_llm("reviewer"),
    )
    assert repaired["verdict"] == "issues"
    assert (
        repaired["snapshot"]["candidate_answer"]
        == first["snapshot"]["candidate_answer"]
    )


def test_flag_off_is_inert():
    review = ScientificReviewService(
        store=None, config=_cfg(stage5=False), chat_call=_pass_chat
    )
    first = {
        "verdict": "issues",
        "findings": [{"severity": "high", "fingerprint": "x"}],
    }
    repaired = AutoRepairService(
        store=None, config=_cfg(stage5=False), scientific_review=review
    ).run(
        initial=first,
        result_review_mode="auto_fix",
        agent_cfg=_llm("agent"),
        reviewer_cfg=_llm("reviewer"),
    )
    assert repaired is first or repaired["verdict"] == "issues"
    assert repaired.get("snapshot") == first.get("snapshot")


def test_budget_exhausted_repair_is_not_a_pass():
    review = ScientificReviewService(store=None, config=_cfg(), chat_call=_pass_chat)
    first = {
        "verdict": "issues",
        "findings": [{"severity": "high", "fingerprint": "stuck"}],
        "snapshot": _table_snapshot("resid.csv has n=99 and mean=2.0"),
    }

    def noop(snapshot, findings):
        del findings
        return {
            "changed": True,
            "self_certified": False,
            "candidate_answer": snapshot.get("candidate_answer"),
            "after_version_ids": [],
        }

    repaired = AutoRepairService(
        store=None,
        config=_cfg(),
        scientific_review=review,
        repair_fn=noop,
    ).run(
        initial=first,
        result_review_mode="auto_fix",
        agent_cfg=_llm("agent"),
        reviewer_cfg=_llm("reviewer"),
    )
    assert repaired.get("verdict") != "pass"
    assert repaired.get("stop_reason") in {"budget_exhausted", "loop_detected"}


def test_apply_claim_repair_is_declared_deterministic():
    assert getattr(apply_claim_repair, "openai4s_deterministic_repair") is True


def test_legacy_adapter_marks_deterministic_without_changing_bytes():
    def fn(snapshot, findings):
        return apply_claim_repair(snapshot, findings)

    adapted = adapt_legacy_repair_fn(fn, deterministic=True)
    assert getattr(adapted, "openai4s_deterministic_repair") is True
    snapshot = {
        "candidate_answer": "n=99",
        "artifacts": [],
        "adapters": [
            {
                "adapter": "table",
                "complete": True,
                "summary": {"row_count": 3, "columns": {}},
            }
        ],
    }
    assert adapted(snapshot, [])["candidate_answer"] == "n=3"


def test_metered_repair_fn_without_budget_uses_inert_admission():
    calls = {"n": 0}

    def metered(snapshot, findings, admission=None):
        del findings
        admission.run_provider(lambda: calls.__setitem__("n", calls["n"] + 1))
        return {
            "changed": False,
            "self_certified": False,
            "candidate_answer": snapshot.get("candidate_answer"),
        }

    review = ScientificReviewService(store=None, config=_cfg(), chat_call=_pass_chat)
    first = {
        "verdict": "issues",
        "findings": [{"severity": "high", "fingerprint": "stuck"}],
        "snapshot": _table_snapshot("resid.csv has n=99 and mean=2.0"),
    }
    repaired = AutoRepairService(
        store=None,
        config=_cfg(),
        scientific_review=review,
        repair_fn=metered,
    ).run(
        initial=first,
        result_review_mode="auto_fix",
        agent_cfg=_llm("agent"),
        reviewer_cfg=_llm("reviewer"),
    )
    assert calls["n"] == 1
    assert repaired.get("stop_reason") == "loop_detected"


def test_undeclared_legacy_repair_fn_still_runs_without_a_budget_envelope():
    def unmarked(snapshot, findings):
        del findings
        return {
            "changed": True,
            "self_certified": False,
            "candidate_answer": snapshot.get("candidate_answer"),
            "after_version_ids": [],
        }

    review = ScientificReviewService(store=None, config=_cfg(), chat_call=_pass_chat)
    first = {
        "verdict": "issues",
        "findings": [{"severity": "high", "fingerprint": "stuck"}],
        "snapshot": _table_snapshot("resid.csv has n=99 and mean=2.0"),
    }
    repaired = AutoRepairService(
        store=None,
        config=_cfg(),
        scientific_review=review,
        repair_fn=unmarked,
    ).run(
        initial=first,
        result_review_mode="auto_fix",
        agent_cfg=_llm("agent"),
        reviewer_cfg=_llm("reviewer"),
    )
    assert repaired.get("verdict") != "pass"
