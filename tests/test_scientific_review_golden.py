"""Stage 3 Go/No-Go: 100+ golden/injected cases through the shipped evaluator."""

from __future__ import annotations

import json
from types import SimpleNamespace

from openai4s.config import Config, RoadmapFeatureFlags
from openai4s.server.evidence_adapters import adapt_table
from openai4s.server.evidence_snapshot import freeze_evidence_snapshot
from openai4s.server.scientific_review import ScientificReviewService


def _llm(model: str) -> SimpleNamespace:
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


def _forged_chat(messages, cfg, **kwargs):
    return {
        "content": json.dumps(
            {
                "verdict": "pass",
                "findings": [
                    {
                        "severity": "low",
                        "category": "other",
                        "claim_ref": "unsupported citation",
                        "evidence_refs": ["forged:not-in-snapshot"],
                        "reproduction": "invented",
                        "confidence": 0.4,
                    }
                ],
            }
        ),
        "usage": {},
    }


def _table_adapter(tmp_path, *, name: str, values: list[float]) -> tuple[dict, dict]:
    path = tmp_path / name
    path.write_text(
        "value\n" + "\n".join(str(item) for item in values) + "\n", encoding="utf-8"
    )
    adapter = adapt_table(path, version_id=f"ver-{name}", artifact_id=f"art-{name}")
    artifact = {
        "artifact_id": f"art-{name}",
        "filename": name,
        "content_type": "text/csv",
        "version_id": f"ver-{name}",
        "checksum": "ab" * 32,
        "exists": True,
    }
    return artifact, adapter


def _base(tmp_path, *, index: int, answer: str, values: list[float] | None = None):
    values = [2.0, 2.0] if values is None else values
    artifact, adapter = _table_adapter(
        tmp_path, name=f"resid-{index}.csv", values=values
    )
    return freeze_evidence_snapshot(
        {
            "identity": {
                "root_frame_id": f"root-{index}",
                "branch_id": f"root-{index}",
                "turn_id": f"turn-{index}",
                "execution_id": f"exec-{index}",
            },
            "user_request": f"report n and mean of {artifact['filename']}",
            "plan": {"title": "residuals", "index": index},
            "candidate_answer": answer,
            "artifacts": [artifact],
            "adapters": [adapter],
            "cells": [{"cell_id": f"cell-{index}"}],
            "truncation": {},
        }
    )


def iter_cases(tmp_path):
    cases = []
    # Planted high/medium issues.
    for i in range(20):
        cases.append(
            {
                "id": f"omitted-file-{i}",
                "planted": True,
                "mode": "review_only",
                "snapshot": _base(
                    tmp_path,
                    index=i,
                    answer=f"also delivered missing-{i}.csv and n=2",
                ),
                "chat": _pass_chat,
                "agent": "agent",
                "reviewer": "reviewer",
            }
        )
    for i in range(20):
        cases.append(
            {
                "id": f"mean-mismatch-{i}",
                "planted": True,
                "mode": "review_only",
                "snapshot": _base(
                    tmp_path,
                    index=100 + i,
                    answer=f"resid-{100 + i}.csv has n=2 and mean={10 + i}.0",
                    values=[2.0, 2.0],
                ),
                "chat": _pass_chat,
                "agent": "agent",
                "reviewer": "reviewer",
            }
        )
    for i in range(10):
        cases.append(
            {
                "id": f"pdf-incomplete-{i}",
                "planted": True,
                "mode": "review_only",
                "snapshot": freeze_evidence_snapshot(
                    {
                        "identity": {
                            "root_frame_id": f"pdf-{i}",
                            "branch_id": f"pdf-{i}",
                            "turn_id": f"t-pdf-{i}",
                            "execution_id": f"e-pdf-{i}",
                        },
                        "user_request": "review the PDF",
                        "candidate_answer": f"report-{i}.pdf supports the claim",
                        "artifacts": [
                            {
                                "artifact_id": f"art-pdf-{i}",
                                "filename": f"report-{i}.pdf",
                                "content_type": "application/pdf",
                                "version_id": f"ver-pdf-{i}",
                                "checksum": "cd" * 32,
                                "exists": True,
                            }
                        ],
                        "adapters": [
                            {
                                "adapter": "pdf",
                                "version_id": f"ver-pdf-{i}",
                                "complete": False,
                                "omission_reason": "pdf_text_unavailable",
                            }
                        ],
                    }
                ),
                "chat": _pass_chat,
                "agent": "agent",
                "reviewer": "reviewer",
            }
        )
    for i in range(10):
        cases.append(
            {
                "id": f"image-incomplete-{i}",
                "planted": True,
                "mode": "review_only",
                "snapshot": freeze_evidence_snapshot(
                    {
                        "identity": {
                            "root_frame_id": f"img-{i}",
                            "branch_id": f"img-{i}",
                            "turn_id": f"t-img-{i}",
                            "execution_id": f"e-img-{i}",
                        },
                        "user_request": "review the figure",
                        "candidate_answer": f"figure-{i}.png shows the result",
                        "artifacts": [
                            {
                                "artifact_id": f"art-img-{i}",
                                "filename": f"figure-{i}.png",
                                "content_type": "image/png",
                                "version_id": f"ver-img-{i}",
                                "checksum": "ef" * 32,
                                "exists": True,
                            }
                        ],
                        "adapters": [
                            {
                                "adapter": "image",
                                "version_id": f"ver-img-{i}",
                                "complete": False,
                                "omission_reason": "image_dimensions_unavailable",
                            }
                        ],
                    }
                ),
                "chat": _pass_chat,
                "agent": "agent",
                "reviewer": "reviewer",
            }
        )
    for i in range(10):
        cases.append(
            {
                "id": f"structure-mismatch-{i}",
                "planted": True,
                "mode": "review_only",
                "snapshot": freeze_evidence_snapshot(
                    {
                        "identity": {
                            "root_frame_id": f"mol-{i}",
                            "branch_id": f"mol-{i}",
                            "turn_id": f"t-mol-{i}",
                            "execution_id": f"e-mol-{i}",
                        },
                        "user_request": "count atoms",
                        "candidate_answer": "benzene.mol has 5 atoms",
                        "artifacts": [
                            {
                                "artifact_id": f"art-mol-{i}",
                                "filename": "benzene.mol",
                                "version_id": f"ver-mol-{i}",
                                "checksum": "11" * 32,
                                "exists": True,
                            }
                        ],
                        "adapters": [
                            {
                                "adapter": "structure",
                                "version_id": f"ver-mol-{i}",
                                "complete": True,
                                "summary": {"atom_count": 6, "bond_count": 6},
                            }
                        ],
                    }
                ),
                "chat": _pass_chat,
                "agent": "agent",
                "reviewer": "reviewer",
            }
        )
    for i in range(10):
        cases.append(
            {
                "id": f"forged-ref-{i}",
                "planted": True,
                "mode": "review_only",
                "snapshot": _base(
                    tmp_path,
                    index=400 + i,
                    answer=f"resid-{400 + i}.csv has n=2 and mean=2.0",
                ),
                "chat": _forged_chat,
                "agent": "agent",
                "reviewer": "reviewer",
            }
        )
    for i in range(8):
        cases.append(
            {
                "id": f"checksum-mismatch-{i}",
                "planted": True,
                "mode": "review_only",
                "snapshot": freeze_evidence_snapshot(
                    {
                        "identity": {
                            "root_frame_id": f"sum-{i}",
                            "branch_id": f"sum-{i}",
                            "turn_id": f"t-sum-{i}",
                            "execution_id": f"e-sum-{i}",
                        },
                        "user_request": "verify bytes",
                        "candidate_answer": "notes.txt is unchanged",
                        "artifacts": [
                            {
                                "artifact_id": f"art-sum-{i}",
                                "filename": "notes.txt",
                                "version_id": f"ver-sum-{i}",
                                "checksum": "aa" * 32,
                                "observed_checksum": "bb" * 32,
                                "exists": True,
                            }
                        ],
                    }
                ),
                "chat": _pass_chat,
                "agent": "agent",
                "reviewer": "reviewer",
            }
        )
    for i in range(8):
        cases.append(
            {
                "id": f"same-model-auto-fix-{i}",
                "planted": True,
                "mode": "auto_fix",
                "snapshot": _base(
                    tmp_path,
                    index=500 + i,
                    answer=f"resid-{500 + i}.csv has n=2 and mean=2.0",
                ),
                "chat": _pass_chat,
                "agent": "shared-model",
                "reviewer": "shared-model",
                "independence": True,
            }
        )
    # Clean tasks for false-positive measurement.
    for i in range(20):
        cases.append(
            {
                "id": f"clean-table-{i}",
                "planted": False,
                "mode": "review_only",
                "snapshot": _base(
                    tmp_path,
                    index=800 + i,
                    answer=f"resid-{800 + i}.csv has n=2 and mean=2.0",
                ),
                "chat": _pass_chat,
                "agent": "agent",
                "reviewer": "reviewer",
            }
        )
    for i in range(10):
        cases.append(
            {
                "id": f"clean-text-{i}",
                "planted": False,
                "mode": "review_only",
                "snapshot": freeze_evidence_snapshot(
                    {
                        "identity": {
                            "root_frame_id": f"txt-{i}",
                            "branch_id": f"txt-{i}",
                            "turn_id": f"t-txt-{i}",
                            "execution_id": f"e-txt-{i}",
                        },
                        "user_request": "state the limitation",
                        "candidate_answer": "This is a qualitative limitation statement.",
                        "artifacts": [],
                    }
                ),
                "chat": _pass_chat,
                "agent": "agent",
                "reviewer": "reviewer",
            }
        )
    return cases


def _detected(result: dict, case: dict) -> bool:
    if case.get("independence"):
        return (
            result.get("verdict") == "review_unavailable"
            and result.get("reason") == "reviewer_independence_unavailable"
        )
    return any(
        item.get("severity") in {"high", "medium"}
        for item in result.get("findings") or []
    )


def test_stage3_golden_recall_and_false_positives(tmp_path):
    cases = iter_cases(tmp_path)
    assert len(cases) >= 100
    service = ScientificReviewService(
        store=None,
        config=Config(
            roadmap_features=RoadmapFeatureFlags(stage3_scientific_review_shadow=True)
        ),
        chat_call=_pass_chat,
    )
    planted_hits = 0
    planted = 0
    clean_false = 0
    clean = 0
    forged_refs = 0
    for case in cases:
        result = service.evaluate(
            case["snapshot"],
            result_review_mode=case["mode"],
            agent_cfg=_llm(case["agent"]),
            reviewer_cfg=_llm(case["reviewer"]),
            chat_call=case["chat"],
        )
        snapshot_refs = {row["ref_id"] for row in result["snapshot"]["evidence_refs"]}
        for finding in result.get("findings") or []:
            for ref in finding.get("evidence_refs") or []:
                if ref not in snapshot_refs:
                    forged_refs += 1
        if case["planted"]:
            planted += 1
            if _detected(result, case):
                planted_hits += 1
        else:
            clean += 1
            if _detected(result, case):
                clean_false += 1
    recall = planted_hits / planted
    false_positive = clean_false / clean
    assert planted >= 80
    assert clean >= 20
    assert recall >= 0.90, f"recall {recall:.3f} ({planted_hits}/{planted})"
    assert false_positive <= 0.10, f"fp {false_positive:.3f} ({clean_false}/{clean})"
    assert forged_refs == 0
