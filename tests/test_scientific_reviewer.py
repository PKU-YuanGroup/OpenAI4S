"""Stage 3 Scientific Reviewer V2 schema and independence helpers."""

from __future__ import annotations

import json

import pytest

from openai4s.scientific_reviewer import (
    model_fingerprint,
    normalize_review_v2,
    review_snapshot,
)
from openai4s.server.completion_gate import terminal_for_review


def test_incomplete_snapshot_forces_incomplete_even_when_model_passes():
    result = normalize_review_v2(
        {"verdict": "pass", "findings": []}, snapshot_complete=False
    )
    assert result["verdict"] == "incomplete"


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


@pytest.mark.parametrize("large_field", ["source_metadata", "structured_completion"])
def test_oversize_reviewer_packet_stays_valid_json_and_cannot_pass(large_field):
    seen = {}

    def chat_call(messages, cfg, **kwargs):
        raw = messages[1]["content"].split("\n", 1)[1]
        packet = json.loads(raw)
        seen["packet"] = packet
        seen["raw"] = raw
        return {
            "content": json.dumps({"verdict": "pass", "summary": "ok", "findings": []}),
            "usage": {},
        }

    snapshot = {
        "schema_version": 1,
        "frozen": True,
        "complete": True,
        "identity": {
            "root_frame_id": "root-1",
            "branch_id": "root-1",
            "turn_id": "turn-1",
            "execution_id": "exec-1",
        },
        "omissions": [],
        "truncation": {},
        large_field: {"payload": "x" * 100_000},
    }

    result = review_snapshot(
        snapshot,
        cfg=type("Cfg", (), {"max_tokens": 2000})(),
        chat_call=chat_call,
    )

    assert len(seen["raw"]) <= 80_000
    assert seen["packet"]["complete"] is False
    assert seen["packet"]["truncation"]["reviewer_packet"] is True
    assert result["verdict"] == "incomplete"
    terminal, user_truth = terminal_for_review(result)
    assert terminal == "review_unavailable"
    assert terminal != "verified"
    assert "not verified" in user_truth
