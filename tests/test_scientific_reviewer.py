"""Stage 3 Scientific Reviewer V2 schema and independence helpers."""

from __future__ import annotations

import json

import pytest

from openai4s.review import ReviewError
from openai4s.scientific_reviewer import (
    model_fingerprint,
    normalize_review_v2,
    review_snapshot,
)


def test_incomplete_snapshot_cannot_pass():
    with pytest.raises(ReviewError, match="incomplete"):
        normalize_review_v2(
            {"verdict": "pass", "findings": []}, snapshot_complete=False
        )


def test_material_finding_forces_issues():
    result = normalize_review_v2(
        {
            "verdict": "pass",
            "findings": [
                {
                    "severity": "high",
                    "category": "claim_mismatch",
                    "claim_ref": "n=10",
                    "evidence_refs": ["adapter:v1:table"],
                    "reproduction": "row_count=4",
                    "confidence": 0.9,
                }
            ],
        },
        snapshot_complete=True,
    )
    assert result["verdict"] == "issues"
    assert result["findings"][0]["severity"] == "high"


def test_model_fingerprint_changes_with_endpoint():
    left = model_fingerprint("openai", "https://api.example/v1", "gpt-x")
    right = model_fingerprint("openai", "https://api.other/v1", "gpt-x")
    assert left != right
    assert len(left) == 64


def test_review_snapshot_uses_injected_chat_and_snapshot_only():
    seen = {}

    def chat_call(messages, cfg, **kwargs):
        seen["messages"] = messages
        return {
            "content": json.dumps(
                {
                    "verdict": "pass",
                    "summary": "ok",
                    "findings": [],
                }
            ),
            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        }

    result = review_snapshot(
        {
            "complete": True,
            "user_request": "check the table",
            "candidate_answer": "n=2",
            "hidden_reasoning": "should never be required",
        },
        cfg=type("Cfg", (), {"max_tokens": 2000})(),
        chat_call=chat_call,
    )
    assert result["verdict"] == "pass"
    packet = seen["messages"][1]["content"]
    assert "check the table" in packet
    assert "hidden_reasoning" in packet or "n=2" in packet
    assert result["usage"]["input_tokens"] == 3
