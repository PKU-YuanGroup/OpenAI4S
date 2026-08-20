"""`openai4s run --auto`: autonomous preset plus a real post-run verdict."""

from __future__ import annotations

import json

from openai4s.agent.loop import (
    AUTO_RUN_ENVIRONMENT,
    enable_auto_run_environment,
    review_cli_result,
)
from openai4s.config import Config, RoadmapFeatureFlags


def test_auto_sets_the_autonomous_environment_without_overruling_an_operator():
    """--auto asks for autonomous; it does not overwrite an explicit choice."""

    environ: dict[str, str] = {}
    applied = enable_auto_run_environment(environ)
    assert applied == dict(AUTO_RUN_ENVIRONMENT)
    assert environ["OPENAI4S_AUTO_MODE"] == "autonomous"

    chosen = {"OPENAI4S_STAGE7_GUARDIAN_ENFORCEMENT": "0"}
    applied = enable_auto_run_environment(chosen)
    assert "OPENAI4S_STAGE7_GUARDIAN_ENFORCEMENT" not in applied
    assert chosen["OPENAI4S_STAGE7_GUARDIAN_ENFORCEMENT"] == "0"


def test_auto_is_not_a_blanket_grant():
    """The flag's whole claim is that it is not full access."""

    assert "OPENAI4S_UNATTENDED_APPROVAL" not in AUTO_RUN_ENVIRONMENT
    assert AUTO_RUN_ENVIRONMENT["OPENAI4S_AUTO_MODE"] == "autonomous"


def _cfg():
    return Config(
        roadmap_features=RoadmapFeatureFlags(stage3_scientific_review_shadow=True)
    )


def _reply(payload):
    def chat(*_args, **_kwargs):
        return {"content": json.dumps(payload), "usage": {}}

    return chat


def test_a_clean_answer_reports_verified():
    review = review_cli_result(
        "what is 6*7",
        {"final_message": "6 multiplied by 7 is 42.", "submitted_output": None},
        cfg=_cfg(),
        chat_call=_reply({"verdict": "pass", "summary": "ok", "findings": []}),
    )
    assert review["terminal"] == "verified"
    assert review["unverified"] is False


def test_findings_report_issues_not_verified():
    review = review_cli_result(
        "count the rows",
        {"final_message": "There are 100 rows.", "submitted_output": None},
        cfg=_cfg(),
        chat_call=_reply(
            {
                "verdict": "issues",
                "summary": "wrong",
                "findings": [
                    {
                        "severity": "high",
                        "category": "claim_mismatch",
                        "claim_ref": "100 rows",
                        "evidence_refs": ["source:candidate_answer"],
                        "reproduction": "the table has 97",
                    }
                ],
            }
        ),
    )
    assert review["terminal"] == "completed_with_issues"
    assert review["unverified"] is True
    assert review["findings"][0]["severity"] == "high"


def test_a_reviewer_that_fails_is_unavailable_not_a_pass():
    def boom(*_args, **_kwargs):
        raise RuntimeError("provider down")

    review = review_cli_result(
        "anything",
        {"final_message": "an answer", "submitted_output": None},
        cfg=_cfg(),
        chat_call=boom,
    )
    assert review["terminal"] == "review_unavailable"
    assert review["unverified"] is True


def test_the_cli_run_carries_an_identity_so_provenance_is_not_flagged():
    """Four blank ids read as missing provenance and produced a finding about
    the harness rather than the answer."""

    seen: dict[str, object] = {}

    def capture(messages, *_args, **_kwargs):
        seen["packet"] = messages[-1]["content"]
        return {"content": json.dumps({"verdict": "pass", "findings": []}), "usage": {}}

    review = review_cli_result(
        "q", {"final_message": "a"}, cfg=_cfg(), chat_call=capture
    )
    assert review["terminal"] == "verified"
    assert '"root_frame_id": ""' not in str(seen["packet"])
