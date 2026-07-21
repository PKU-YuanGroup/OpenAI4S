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
        "headless_ask_fails_closed_deny_absolute",
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
    assert cases["headless_ask_fails_closed_deny_absolute"]["known_bug"] is False
    # Fixed: the transport now raises a typed TransportError carrying the
    # status and Retry-After, and retries a 429 within a bounded, cancellable
    # budget. This row is an ordinary regression from here on.
    assert cases["rate_limit_single_attempt"]["known_bug"] is False
    # Fixed: `enabled` now gates the spawn, not just the invocation, so a
    # disabled connector never reaches the manager.
    assert cases["disabled_mcp_tools_connects"]["known_bug"] is False
    # Fixed: only *leading* system messages are initial policy.
    assert cases["compaction_summary_provider_hoist"]["known_bug"] is False
    # Fixed: sections are budgeted and the full bytes spill to a
    # workspace-relative content reference the agent can open.
    assert cases["oversized_observation_unbudgeted"]["known_bug"] is False
    # All four known bugs are now fixed. This asserts the set is empty rather
    # than deleting the loop: a future characterization added as a known bug
    # must state so deliberately, not inherit silence.
    assert not [c for c in cases.values() if c["known_bug"]]

    observed = {case_id: case["trace"][1]["payload"] for case_id, case in cases.items()}
    assert observed["cli_max_turns"]["stop_reason"] == "max_turns"

    # A 429 carrying Retry-After is retried and the call recovers. This used to
    # be attempts == 1: the status was flattened into an f-string, so no caller
    # could tell a rate limit from an auth failure and nothing retried.
    rate_limit = observed["rate_limit_single_attempt"]
    assert rate_limit["attempts"] == 2
    assert rate_limit["content"] == "recovered"
    assert rate_limit["error_type"] is None
    assert rate_limit["retry_after_was_available"] is True

    # The other half of the same rule, and the reason the fix is narrow: once a
    # stream has committed output, a retry would duplicate what the user has
    # already seen. Still exactly one attempt.
    assert observed["partial_sse_hard_failure"] == {
        "blocking_fallback_attempts": 0,
        "deltas": ["committed-delta"],
        "error_text": "stream disconnected after committed delta",
        "error_type": "LLMError",
        "sse_attempts": 1,
    }

    # The compaction note keeps its timeline position. It used to be hoisted
    # into the initial system field, which on Anthropic is also the cache
    # prefix — so every compaction both reframed a transient summary as durable
    # policy and invalidated the prompt cache.
    hoist = observed["compaction_summary_provider_hoist"]
    assert hoist["anthropic_summary_in_top_level_system"] is False
    assert hoist["anthropic_summary_in_messages"] is True
    assert hoist["gemini_summary_in_system_instruction"] is False
    assert hoist["gemini_summary_in_contents"] is True

    # A 2M-char observation is previewed, not forwarded. Forwarding it whole was
    # not a large observation so much as a destroyed turn: it evicts the task
    # from the context window and bills for the privilege. The full bytes are
    # reachable at a workspace-relative ref, which is more useful than a tail
    # the model cannot search.
    oversized = observed["oversized_observation_unbudgeted"]
    assert oversized["model_view_chars"] < oversized["input_chars"]
    assert oversized["has_content_ref"] is True
    assert oversized["has_omission_marker"] is True
    assert oversized["full_tail_preserved"] is False

    permission = observed["headless_ask_fails_closed_deny_absolute"]
    assert permission["ask_effective_decision"] == "ask"
    assert permission["headless_ask_allowed"] is False
    # Standing deny remains absolute as well.
    assert permission["deny_effective_decision"] == "deny"
    assert permission["headless_deny_allowed"] is False

    # A disabled connector is zero-spawn. This used to be
    # manager_list_tools_calls == 1: `call` refused a disabled row, but
    # discovery — which is what actually launches the process — did not, so an
    # agent could make the host run a command the user had turned off.
    disabled_mcp = observed["disabled_mcp_tools_connects"]
    assert disabled_mcp["connector_enabled"] is False
    assert disabled_mcp["manager_list_tools_calls"] == 0
    assert "disabled" in disabled_mcp["result"]["error"]
