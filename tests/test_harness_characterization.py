"""Pre-change r5 production characterizations.

Known-bug rows below are migration evidence, not promises to preserve broken
behavior.  A deliberate runtime fix must update the golden explicitly and review
that the corresponding ``desired_contract`` became true; silent drift is what
this test prevents.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness.characterize import characterization_bytes

_GOLDEN = (
    Path(__file__).resolve().parents[1]
    / "harness"
    / "golden_traces"
    / "v1"
    / "r5_prechange.json"
)


def _by_id(document: dict) -> dict[str, dict]:
    return {case["id"]: case for case in document["cases"]}


def test_r5_prechange_trace_is_byte_identical_and_matches_reviewed_golden(tmp_path):
    first = characterization_bytes(tmp_path / "first-data-dir")
    second = characterization_bytes(tmp_path / "second-data-dir")
    assert first == second, "normalization must remove temp paths and volatile ids"
    assert b"characterization-key" not in first
    assert str(tmp_path).encode() not in first
    assert first == _GOLDEN.read_bytes(), (
        "production behavior drifted from the reviewed r5 pre-change trace. "
        "If this is an intentional fix, regenerate the golden explicitly and "
        "review current_behavior, desired_contract, and known_bug together."
    )


def test_r5_prechange_cases_state_current_behavior_and_expected_direction(tmp_path):
    document = json.loads(characterization_bytes(tmp_path / "metadata-data-dir"))
    assert document["schema_version"] == 1
    assert document["kind"] == "r5_prechange_production_characterization"
    assert "not permanent contracts" in document["update_policy"]

    cases = _by_id(document)
    assert set(cases) == {
        "cli_max_turns",
        "rate_limit_single_attempt",
        "partial_sse_hard_failure",
        "compaction_summary_provider_hoist",
        "oversized_observation_unbudgeted",
        "headless_ask_allows_deny_absolute",
        "disabled_mcp_tools_connects",
    }
    for case in cases.values():
        assert case["current_behavior"]
        assert case["desired_contract"]
        assert isinstance(case["known_bug"], bool)
        assert [event["seq"] for event in case["trace"]] == [1, 2, 3]
        assert [event["kind"] for event in case["trace"]] == [
            "characterization_started",
            "production_observed",
            "characterization_finished",
        ]
        assert case["trace"][-1]["payload"] == {"captured": True}

    assert cases["cli_max_turns"]["known_bug"] is False
    assert cases["partial_sse_hard_failure"]["known_bug"] is False
    for case_id in set(cases) - {"cli_max_turns", "partial_sse_hard_failure"}:
        assert cases[case_id]["known_bug"] is True

    observed = {case_id: case["trace"][1]["payload"] for case_id, case in cases.items()}
    assert observed["cli_max_turns"]["stop_reason"] == "max_turns"
    assert observed["rate_limit_single_attempt"]["attempts"] == 1
    assert observed["partial_sse_hard_failure"] == {
        "blocking_fallback_attempts": 0,
        "deltas": ["committed-delta"],
        "error_text": "stream disconnected after committed delta",
        "error_type": "LLMError",
        "sse_attempts": 1,
    }

    hoist = observed["compaction_summary_provider_hoist"]
    assert hoist["anthropic_summary_in_top_level_system"] is True
    assert hoist["anthropic_summary_in_messages"] is False
    assert hoist["gemini_summary_in_system_instruction"] is True
    assert hoist["gemini_summary_in_contents"] is False

    oversized = observed["oversized_observation_unbudgeted"]
    assert oversized["model_view_chars"] > oversized["input_chars"]
    assert oversized["has_content_ref"] is False

    permission = observed["headless_ask_allows_deny_absolute"]
    assert permission["ask_effective_decision"] == "ask"
    assert permission["headless_ask_allowed"] is True
    # Preserve this invariant when fixing headless ask: standing deny is absolute.
    assert permission["deny_effective_decision"] == "deny"
    assert permission["headless_deny_allowed"] is False

    disabled_mcp = observed["disabled_mcp_tools_connects"]
    assert disabled_mcp["connector_enabled"] is False
    assert disabled_mcp["manager_list_tools_calls"] == 1
