"""Contract tests for the stdlib-only deterministic scenario harness."""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
import types
from pathlib import Path

import pytest

from harness import auto_mode_contract as auto_mode_contract_mod
from harness.auto_mode_contract import run_auto_mode_contract
from harness.auto_mode_terminal_contract import run_auto_mode_terminal_contract
from harness.cli import main
from harness.evals.retrosynthesis_backends import evaluate_backend_replays
from harness.faults import FakeClock, FakeUUIDFactory, FaultSchedule
from harness.normalize import normalized_trace_bytes
from harness.providers.scripted_llm import ScriptedLLM, ScriptedProviderError
from harness.runner import run_scenario
from harness.schema import (
    FaultSpec,
    ProviderStep,
    Scenario,
    ScenarioValidationError,
    load_scenario,
)
from openai4s.config import get_config

_SCENARIOS = Path(__file__).resolve().parents[1] / "harness" / "scenarios"


def _scenario_paths() -> list[Path]:
    return sorted(_SCENARIOS.rglob("*.json"))


def _skill_module(name: str):
    skills_dir = str(get_config().skills_dir)
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    return importlib.import_module(name)


def test_pr_offline_baseline_has_at_least_three_versioned_scenarios():
    scenarios = [load_scenario(path) for path in _scenario_paths()]
    selected = [
        scenario
        for scenario in scenarios
        if scenario.in_tier("pr") and scenario.is_offline
    ]
    assert len(selected) >= 3
    assert all(scenario.schema_version == 1 for scenario in selected)


def test_schema_rejects_unknown_version_and_unknown_fields():
    raw = {
        "schema_version": 2,
        "id": "bad_version",
        "tags": ["offline", "tier:pr"],
        "surface": "harness",
        "task": "x",
        "provider_script": [{"response": {"content": "x"}}],
        "faults": [],
        "expect": {"terminal_reason": "script_exhausted", "model_attempts": 1},
    }
    with pytest.raises(ScenarioValidationError, match="schema_version"):
        Scenario.from_dict(raw)
    raw["schema_version"] = 1
    raw["surprise"] = True
    with pytest.raises(ScenarioValidationError, match="unsupported field"):
        Scenario.from_dict(raw)


def test_schema_defaults_rules_only_and_rejects_hyphenated_alias():
    raw = {
        "schema_version": 1,
        "id": "permission_mode",
        "tags": ["offline", "tier:pr"],
        "surface": "harness",
        "task": "x",
        "fixtures": {"workspace": "minimal"},
        "provider_script": [{"response": {"content": "x"}}],
        "faults": [],
        "expect": {"terminal_reason": "script_exhausted", "model_attempts": 1},
    }
    scenario = Scenario.from_dict(raw)
    assert scenario.fixtures == {"workspace": "minimal"}
    assert scenario.permissions.noninteractive == "rules_only"
    raw["permissions"] = {"noninteractive": "rules-only"}
    with pytest.raises(ScenarioValidationError, match="rules_only"):
        Scenario.from_dict(raw)


def test_fake_clock_and_uuid_are_deterministic_and_never_sleep():
    clock = FakeClock(start_ms=10)
    clock.sleep(0.25)
    assert clock.monotonic_ms() == 260
    ids = FakeUUIDFactory()
    assert ids() == "00000000-0000-4000-8000-000000000001"
    assert ids() == "00000000-0000-4000-8000-000000000002"


def test_fault_schedule_fires_only_at_exact_occurrence():
    schedule = FaultSchedule(
        [FaultSpec("before_model", 2, "timeout", "boom", retryable=True)]
    )
    assert schedule.check("before_model") is None
    fault = schedule.check("before_model")
    assert fault is not None
    assert (fault.kind, fault.retryable, str(fault)) == ("timeout", True, "boom")
    assert schedule.check("before_model") is None
    assert schedule.unfired == ()


def test_scripted_provider_records_calls_and_exposes_typed_error():
    provider = ScriptedLLM(
        [
            ProviderStep(response={"content": "ok"}),
            ProviderStep(
                error={"kind": "rate_limit", "message": "later", "status": 429}
            ),
        ]
    )
    messages = [{"role": "user", "content": "hello"}]
    assert provider(messages)["content"] == "ok"
    messages[0]["content"] = "mutated after call"
    assert provider.calls[0][0]["content"] == "hello"
    with pytest.raises(ScriptedProviderError) as caught:
        provider(messages)
    assert caught.value.kind == "rate_limit"
    assert caught.value.status == 429


@pytest.mark.parametrize(
    "error,match",
    [
        ({"kind": "x", "message": "x", "status": True}, "status"),
        ({"kind": "x", "message": "x", "headers": {"x": 1}}, "headers"),
        ({"kind": "x", "message": "x", "retryable": "yes"}, "retryable"),
    ],
)
def test_schema_rejects_ill_typed_provider_errors(error, match):
    raw = {
        "schema_version": 1,
        "id": "typed_error",
        "tags": ["offline", "tier:pr"],
        "surface": "harness",
        "task": "x",
        "provider_script": [{"error": error}],
        "expect": {"terminal_reason": "model_error", "model_attempts": 1},
    }
    with pytest.raises(ScenarioValidationError, match=match):
        Scenario.from_dict(raw)


def _run_for_surface(scenario):
    """The same dispatch the CLI does, for the same reason: a scenario's own
    `surface` decides which runner executes it, so a scenario cannot be
    silently run by the wrong one because of where its file sits."""
    if scenario.surface == "orchestration":
        from harness.orchestration import run_orchestration_scenario

        return run_orchestration_scenario(scenario, offline=True)
    if scenario.surface == "auto_mode_contract":
        return run_auto_mode_contract(scenario, offline=True)
    if scenario.surface == "auto_mode_terminal_contract":
        return run_auto_mode_terminal_contract(scenario, offline=True)
    return run_scenario(scenario, offline=True)


@pytest.mark.parametrize("path", _scenario_paths(), ids=lambda path: path.stem)
def test_each_baseline_scenario_passes_and_is_byte_identical(path):
    scenario = load_scenario(path)
    first = _run_for_surface(scenario)
    second = _run_for_surface(scenario)
    assert first.passed, first.errors
    assert second.passed, second.errors
    assert first.normalized == second.normalized
    assert first.trace_sha256 == second.trace_sha256


@pytest.mark.parametrize(
    "scenario_id,terminal_reason",
    [
        ("auto_mode_verified", "verified"),
        ("auto_mode_completed_with_issues", "completed_with_issues"),
        (
            "auto_mode_review_only_completed_with_issues",
            "completed_with_issues",
        ),
        ("auto_mode_review_timeout", "review_unavailable"),
        ("auto_mode_review_parse_failure", "review_unavailable"),
        ("auto_mode_review_timeout_then_pass", "verified"),
    ],
)
def test_auto_mode_result_contract_keeps_candidate_nonterminal(
    scenario_id, terminal_reason
):
    scenario = load_scenario(_SCENARIOS / "baseline" / f"{scenario_id}.json")
    result = run_auto_mode_contract(scenario)
    candidate = next(
        event for event in result.events if event.kind == "candidate_ready"
    )
    run_started = result.events[0]
    terminal = result.events[-1]

    assert result.passed, result.errors
    assert result.terminal_reason == terminal_reason
    assert candidate.status == "candidate"
    assert candidate.payload["state"] == "candidate"
    assert candidate.payload["terminal"] is False
    assert candidate.payload["user_visible_completion"] is False
    assert candidate.payload["candidate_digest"]
    assert candidate.payload["evidence_snapshot_digest"]
    assert candidate.payload["artifact_set_digest"]
    assert candidate.payload["snapshot_complete"] is True
    assert candidate.payload["snapshot_frozen"] is True
    assert (
        candidate.payload["evidence_refs"]
        == scenario.fixtures["evidence_snapshot"]["evidence_refs"]
    )
    assert (
        candidate.payload["provenance_version_ids"]
        == scenario.fixtures["evidence_snapshot"]["provenance_version_ids"]
    )
    assert candidate.payload["review_mode"] == "independent_read_only"
    assert candidate.payload["review_request_policy_digest"]
    response_only = {
        "findings",
        "material_findings_digest",
        "termination_basis",
        "verdict",
    }
    assert response_only.isdisjoint(candidate.payload)
    review_starts = [
        event for event in result.events if event.kind == "auto_audit_started"
    ]
    assert review_starts
    assert all(response_only.isdisjoint(event.payload) for event in review_starts)
    assert [event.kind for event in result.events if event.status == "terminal"] == [
        "auto_run_terminal"
    ]
    assert terminal.payload["auto_user_state"] == terminal_reason
    assert run_started.payload["contract_adapter"] is True
    assert run_started.payload["production_state_machine"] is False
    identity = scenario.fixtures["identity"]
    assert all(event.payload["identity"] == identity for event in result.events)
    assert all(
        event.payload["event_names"]["canonical"] == event.kind
        for event in result.events
    )


def _canonical_digest(value):
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_auto_mode_verified_binds_candidate_evidence_artifacts_and_independent_audit():
    scenario = load_scenario(_SCENARIOS / "baseline" / "auto_mode_verified.json")
    result = run_auto_mode_contract(scenario)
    candidate = next(
        event for event in result.events if event.kind == "candidate_ready"
    )
    review = next(
        event
        for event in result.events
        if event.kind == "auto_audit_completed" and event.status == "passed"
    )
    fixtures = scenario.fixtures

    assert result.passed, result.errors
    assert candidate.payload["candidate_digest"] == _canonical_digest(
        fixtures["candidate"]
    )
    assert candidate.payload["evidence_snapshot_digest"] == _canonical_digest(
        fixtures["evidence_snapshot"]
    )
    assert candidate.payload["artifact_set_digest"] == _canonical_digest(
        fixtures["candidate"]["artifact_versions"]
    )
    assert (
        fixtures["evidence_snapshot"]["artifact_versions"]
        == fixtures["candidate"]["artifact_versions"]
    )
    assert review.payload["audit_durable"] is True
    assert review.payload["risk"]["open_material_findings"] == 0
    assert review.payload["reviewer_id"] != review.payload["producer_id"]
    assert review.payload["workspace_writes"] == 0
    assert review.payload["assessment_digest"] == _canonical_digest(
        review.payload["assessment"]
    )
    assert review.payload["audit_request_digest"] != review.payload["assessment_digest"]


def test_auto_mode_review_request_cannot_precommit_the_reviewer_answer():
    verified = run_auto_mode_contract(
        load_scenario(_SCENARIOS / "baseline" / "auto_mode_verified.json")
    )
    issues = run_auto_mode_contract(
        load_scenario(_SCENARIOS / "baseline" / "auto_mode_completed_with_issues.json")
    )
    verified_candidate = next(
        event for event in verified.events if event.kind == "candidate_ready"
    )
    issues_candidate = next(
        event for event in issues.events if event.kind == "candidate_ready"
    )
    verified_start = next(
        event for event in verified.events if event.kind == "auto_audit_started"
    )
    issues_start = next(
        event for event in issues.events if event.kind == "auto_audit_started"
    )
    verified_completion = next(
        event for event in verified.events if event.kind == "auto_audit_completed"
    )
    issues_completion = next(
        event for event in issues.events if event.kind == "auto_audit_completed"
    )

    assert verified.passed, verified.errors
    assert issues.passed, issues.errors
    assert verified_candidate.payload == issues_candidate.payload
    assert verified_start.payload == issues_start.payload
    assert (
        verified_start.payload["audit_request_digest"]
        == issues_start.payload["audit_request_digest"]
    )
    assert (
        verified_completion.payload["assessment_digest"]
        != issues_completion.payload["assessment_digest"]
    )


def test_auto_mode_review_only_issues_never_start_repair():
    scenario = load_scenario(
        _SCENARIOS / "baseline" / "auto_mode_review_only_completed_with_issues.json"
    )
    result = run_auto_mode_contract(scenario)
    candidate = next(
        event for event in result.events if event.kind == "candidate_ready"
    )
    completion = next(
        event for event in result.events if event.kind == "auto_audit_completed"
    )

    assert result.passed, result.errors
    assert result.terminal_reason == "completed_with_issues"
    assert scenario.fixtures["review_policy"] == {
        "review_mode": "independent_read_only",
        "auto_fix_enabled": False,
        "auto_fix_budget_exhausted": False,
        "termination_basis": "review_only_no_repair",
    }
    assert candidate.payload["result_review_mode"] == "review_only"
    assert completion.payload["result_review_mode"] == "review_only"
    assert completion.payload["findings"] == list(
        scenario.fixtures["material_findings"]
    )
    assert not any(
        event.kind in {"repair_started", "repair_completed"} for event in result.events
    )
    assert completion.payload["rationale"] == (
        "review_only_no_repair_with_open_material_findings"
    )
    assert completion.payload["workspace_writes"] == 0
    assert result.events[-1].payload["recoverable"] is True


def test_auto_mode_reviewer_retries_once_then_passes():
    scenario = load_scenario(
        _SCENARIOS / "baseline" / "auto_mode_review_timeout_then_pass.json"
    )
    result = run_auto_mode_contract(scenario)
    completions = [
        event for event in result.events if event.kind == "auto_audit_completed"
    ]

    assert result.passed, result.errors
    assert result.terminal_reason == "verified"
    assert [event.status for event in completions] == ["retrying", "passed"]
    assert completions[0].payload["retry_scheduled"] is True
    assert completions[0].payload["findings"] == []
    assert [event.payload["attempt"] for event in completions] == [1, 2]


@pytest.mark.parametrize(
    "scenario_id,error_kind",
    [
        ("auto_mode_review_timeout", "timeout"),
        ("auto_mode_review_parse_failure", "parse_failure"),
    ],
)
def test_auto_mode_reviewer_two_failures_become_unavailable(scenario_id, error_kind):
    scenario = load_scenario(_SCENARIOS / "baseline" / f"{scenario_id}.json")
    result = run_auto_mode_contract(scenario)
    faults = [event for event in result.events if event.kind == "fault_injected"]
    completions = [
        event for event in result.events if event.kind == "auto_audit_completed"
    ]

    assert result.passed, result.errors
    assert result.terminal_reason == "review_unavailable"
    assert [event.payload["error_kind"] for event in faults] == [
        error_kind,
        error_kind,
    ]
    assert [event.status for event in completions] == ["retrying", "unavailable"]
    assert all(event.payload["findings"] == [] for event in completions)


def test_auto_mode_guardian_contract_is_exact_digest_denial_only():
    scenario = load_scenario(_SCENARIOS / "baseline" / "auto_mode_guardian_denied.json")
    result = run_auto_mode_contract(scenario)
    assert "action_digest" not in scenario.fixtures
    digest = _canonical_digest(scenario.fixtures["canonical_action"])
    digest_events = [
        event
        for event in result.events
        if event.kind in {"auto_audit_started", "auto_audit_completed"}
    ]
    decision = digest_events[-1]

    assert result.passed, result.errors
    assert result.terminal_reason == "blocked_by_guardian"
    assert scenario.permissions.noninteractive == "rules_only"
    assert all(event.payload["action_digest"] == digest for event in digest_events)
    assert digest_events[0].payload["policy_resolution"] == "ask"
    assert all(
        event.payload["digest_source"] == "computed_canonical_action"
        for event in (digest_events[0], decision)
    )
    assert decision.payload["decision"] == "deny"
    assert decision.payload["decision_source"] == "permission_guardian"
    assert decision.payload["audit_durable"] is True
    assert decision.payload["fallback_decision"] is False
    assert decision.payload["action_executed"] is False
    assert decision.payload["fail_closed"] is True
    assert decision.payload["rationale"] == "guardian_durable_deny_no_safe_continuation"
    assert decision.payload["risk"]["terminal_basis"] == "no_safe_continuation"
    assert decision.payload["standing_allow_created"] is False
    assert decision.payload["infra_breaker_open"] is False
    assert decision.payload["denial_circuit_open"] is False
    assert result.events[-1].payload["recoverable"] is False
    assert not any(event.kind == "action_authorized" for event in result.events)


def test_auto_mode_guardian_allow_once_consumes_exactly_once_and_rejects_replay():
    scenario = load_scenario(
        _SCENARIOS / "baseline" / "auto_mode_guardian_allow_once_replay.json"
    )
    result = run_auto_mode_contract(scenario)
    audit = next(
        event for event in result.events if event.kind == "auto_audit_completed"
    )
    resolutions = [
        event for event in result.events if event.kind == "permission_resolved"
    ]
    action_digest = _canonical_digest(scenario.fixtures["canonical_action"])

    assert result.passed, result.errors
    assert audit.payload["decision"] == "allow_once"
    assert audit.payload["audit_durable"] is True
    assert audit.payload["action_authorized"] is True
    assert audit.payload["action_executed"] is False
    assert audit.payload["standing_allow_created"] is False
    assert audit.payload["authorization"]["authorization_scope"] == "once"
    assert audit.payload["authorization"]["bound_action_digest"] == action_digest
    assert audit.payload["authorization"]["max_uses"] == 1
    assert audit.seq < resolutions[0].seq
    assert [event.payload["attempt_kind"] for event in resolutions] == [
        "capability_issued",
        "exact_first_use",
        "replay",
    ]
    assert [event.payload["uses_after"] for event in resolutions] == [0, 1, 1]
    assert sum(event.payload["action_executed"] for event in resolutions) == 1
    assert resolutions[1].payload["attempted_action_digest"] == action_digest
    assert resolutions[2].payload["action_executed"] is False
    assert resolutions[2].payload["execution_count"] == 1
    assert resolutions[2].payload["rejection_reason"] == (
        "one_shot_capability_consumed"
    )
    assert all(
        event.payload["standing_allow_created"] is False for event in resolutions
    )
    for event in resolutions:
        assert event.payload["capability_digest"] == _canonical_digest(
            event.payload["one_shot_capability"]
        )
        assert event.payload["authorization_receipt_digest"] == _canonical_digest(
            event.payload["authorization_receipt"]
        )
        assert event.payload["one_shot_capability"]["max_uses"] == 1
        assert event.payload["one_shot_capability"]["action_digest"] == action_digest
        assert event.payload["one_shot_capability"]["run_context"] == (
            scenario.fixtures["identity"]
        )
    assert result.events[-1].payload["boundary"] == "one_shot_capability_reuse"
    assert result.events[-1].payload["safety_terminal"] is True


def test_auto_mode_guardian_allow_once_rejects_and_burns_action_variant():
    scenario = load_scenario(
        _SCENARIOS / "baseline" / "auto_mode_guardian_allow_once_variant.json"
    )
    result = run_auto_mode_contract(scenario)
    resolutions = [
        event for event in result.events if event.kind == "permission_resolved"
    ]
    rejection = resolutions[-1]

    assert result.passed, result.errors
    assert [event.payload["attempt_kind"] for event in resolutions] == [
        "capability_issued",
        "variant_first_use",
    ]
    assert rejection.payload["expected_action_digest"] == _canonical_digest(
        scenario.fixtures["canonical_action"]
    )
    assert rejection.payload["attempted_action_digest"] == _canonical_digest(
        scenario.fixtures["runtime_action"]
    )
    assert (
        rejection.payload["expected_action_digest"]
        != rejection.payload["attempted_action_digest"]
    )
    assert rejection.payload["capability_consumed"] is True
    assert rejection.payload["uses_before"] == 0
    assert rejection.payload["uses_after"] == 1
    assert rejection.payload["action_authorized"] is False
    assert rejection.payload["action_executed"] is False
    assert rejection.payload["execution_count"] == 0
    assert rejection.payload["rejection_reason"] == "exact_action_digest_mismatch"
    assert not any(event.payload["action_executed"] for event in resolutions)
    assert result.events[-1].payload["boundary"] == "exact_action_digest"
    assert result.events[-1].payload["safety_terminal"] is True


@pytest.mark.parametrize(
    "scenario_id,trigger,prior_value,after_value",
    [
        (
            "auto_mode_guardian_denial_circuit_consecutive",
            "consecutive",
            2,
            3,
        ),
        ("auto_mode_guardian_denial_circuit_window", "window", 9, 10),
    ],
)
def test_auto_mode_guardian_denial_circuit_opens_only_at_exact_threshold(
    scenario_id, trigger, prior_value, after_value
):
    scenario = load_scenario(_SCENARIOS / "baseline" / f"{scenario_id}.json")
    result = run_auto_mode_contract(scenario)
    decision = next(
        event for event in result.events if event.kind == "auto_audit_completed"
    )
    risk = decision.payload["risk"]
    prior_key = (
        "prior_consecutive_denials"
        if trigger == "consecutive"
        else "prior_window_denials"
    )
    after_key = "consecutive_denials" if trigger == "consecutive" else "window_denials"

    assert result.passed, result.errors
    assert decision.payload["decision"] == "deny"
    assert decision.payload["action_executed"] is False
    assert decision.payload["infra_breaker_open"] is False
    assert decision.payload["denial_circuit_open"] is True
    assert decision.payload["standing_allow_created"] is False
    assert risk["terminal_basis"] == "loop_detected"
    assert risk["trigger"] == trigger
    assert risk[prior_key] == prior_value
    assert risk[after_key] == after_value
    assert risk["thresholds"] == {
        "consecutive_denials": 3,
        "window_size": 50,
        "window_denials": 10,
    }
    assert decision.payload["rationale"] == "guardian_denial_circuit_opened"
    assert result.events[-1].payload["stop_reason"] == "loop_detected"
    assert result.events[-1].payload["denial_circuit_trigger"] == trigger


@pytest.mark.parametrize(
    "scenario_id,error_kind",
    [
        ("auto_mode_guardian_timeout", "timeout"),
        ("auto_mode_guardian_parse_failure", "parse_failure"),
    ],
)
def test_auto_mode_guardian_decision_faults_fail_closed(scenario_id, error_kind):
    scenario = load_scenario(_SCENARIOS / "baseline" / f"{scenario_id}.json")
    result = run_auto_mode_contract(scenario)
    faults = [event for event in result.events if event.kind == "fault_injected"]
    decisions = [
        event for event in result.events if event.kind == "auto_audit_completed"
    ]

    assert result.passed, result.errors
    assert result.terminal_reason == "blocked_by_guardian"
    assert [event.payload["error_kind"] for event in faults] == [
        error_kind,
        error_kind,
    ]
    assert decisions[0].payload["decision"] == "retry_scheduled"
    assert decisions[0].payload["infra_breaker_open"] is False
    assert decisions[0].payload["denial_circuit_open"] is False
    assert decisions[1].payload["decision"] == "unavailable"
    assert decisions[1].payload["infra_breaker_open"] is True
    assert decisions[1].payload["denial_circuit_open"] is False
    assert decisions[1].payload["action_executed"] is False
    assert decisions[1].payload["fail_closed"] is True
    assert decisions[1].payload["standing_allow_created"] is False
    assert result.events[-1].payload["recoverable"] is True


def test_auto_mode_guardian_single_timeout_does_not_open_circuit():
    scenario = load_scenario(
        _SCENARIOS / "baseline" / "auto_mode_guardian_timeout_then_deny.json"
    )
    result = run_auto_mode_contract(scenario)
    decisions = [
        event for event in result.events if event.kind == "auto_audit_completed"
    ]

    assert result.passed, result.errors
    assert [event.payload["decision"] for event in decisions] == [
        "retry_scheduled",
        "deny",
    ]
    assert decisions[0].payload["infra_breaker_open"] is False
    assert decisions[0].payload["denial_circuit_open"] is False
    assert decisions[1].payload["fallback_decision"] is False
    assert decisions[1].payload["infra_breaker_open"] is False
    assert decisions[1].payload["denial_circuit_open"] is False
    assert decisions[1].payload["risk"]["terminal_basis"] == "no_safe_continuation"
    assert (
        decisions[1].payload["rationale"]
        == "guardian_durable_deny_no_safe_continuation"
    )
    assert decisions[1].payload["action_executed"] is False
    assert result.events[-1].payload["recoverable"] is False


@pytest.mark.parametrize(
    "scenario_id,lane,error_kind",
    [
        ("auto_mode_review_audit_failure", "result_review", "audit_failure"),
        (
            "auto_mode_review_evidence_hash_mismatch",
            "result_review",
            "hash_mismatch",
        ),
        (
            "auto_mode_guardian_audit_failure",
            "permission_guardian",
            "audit_failure",
        ),
        (
            "auto_mode_guardian_action_hash_mismatch",
            "permission_guardian",
            "hash_mismatch",
        ),
    ],
)
def test_auto_mode_integrity_faults_are_independent_safety_failures(
    scenario_id, lane, error_kind
):
    scenario = load_scenario(_SCENARIOS / "baseline" / f"{scenario_id}.json")
    result = run_auto_mode_contract(scenario)
    fault = next(event for event in result.events if event.kind == "fault_injected")
    boundary = next(
        event
        for event in result.events
        if event.kind == "auto_audit_completed"
        and event.payload["event_names"]["specific"] == "safety_boundary_reached"
    )
    terminal = result.events[-1]

    assert result.passed, result.errors
    assert scenario.fixtures["lane"] == lane
    assert result.terminal_reason == "safety_boundary"
    assert fault.payload["error_kind"] == error_kind
    assert fault.payload["fail_closed"] is True
    assert boundary.payload["fail_closed"] is True
    assert terminal.kind == "auto_run_terminal"
    assert terminal.payload["auto_user_state"] is None
    assert terminal.payload["safety_terminal"] is True
    assert not any(event.kind == "action_authorized" for event in result.events)
    assert not any(
        event.kind == "auto_audit_completed" and event.payload.get("decision") == "deny"
        for event in result.events
    )
    if lane == "result_review":
        assert boundary.payload["findings"] == []
    if error_kind == "hash_mismatch":
        assert fault.payload["digest_binding"] in {
            "immutable_evidence",
            "exact_action",
        }
        assert fault.payload["expected_digest"] != fault.payload["observed_digest"]
        assert len(fault.payload["expected_digest"]) == 64
        assert len(fault.payload["observed_digest"]) == 64


def test_auto_mode_contract_rejects_noncanonical_guardian_action():
    path = _SCENARIOS / "baseline" / "auto_mode_guardian_denied.json"
    raw = json.loads(path.read_text("utf-8"))
    raw["fixtures"]["canonical_action"]["parameters"]["weight"] = float("nan")
    with pytest.raises(ScenarioValidationError, match="cannot be canonicalized"):
        run_auto_mode_contract(Scenario.from_dict(raw))


def test_auto_mode_contract_rejects_reviewer_self_review():
    path = _SCENARIOS / "baseline" / "auto_mode_verified.json"
    raw = json.loads(path.read_text("utf-8"))
    raw["fixtures"]["reviewer_identity"]["reviewer_id"] = "primary-agent"
    with pytest.raises(ScenarioValidationError, match="must be independent"):
        run_auto_mode_contract(Scenario.from_dict(raw))


def test_auto_mode_contract_cannot_self_prove_by_changing_scenario_outcome():
    path = _SCENARIOS / "baseline" / "auto_mode_verified.json"
    raw = json.loads(path.read_text("utf-8"))
    raw["fixtures"]["outcome"] = "issues"
    raw["fixtures"]["review_policy"].update(
        {
            "auto_fix_budget_exhausted": True,
            "termination_basis": "auto_fix_budget_exhausted",
        }
    )
    raw["fixtures"]["material_findings"] = [
        {
            "fingerprint": "finding-mutated-outcome",
            "status": "open",
            "severity": "material",
            "evidence_refs": ["evidence-report"],
            "summary": "A scenario-local outcome cannot replace reviewed golden evidence.",
        }
    ]

    result = run_auto_mode_contract(Scenario.from_dict(raw))

    assert not result.passed
    assert any(
        marker in error
        for error in result.errors
        for marker in (
            "golden terminal_reason",
            "event_payload_digests",
            "trace_sha256",
        )
    )


def test_auto_mode_contract_rejects_incomplete_evidence_snapshot():
    path = _SCENARIOS / "baseline" / "auto_mode_verified.json"
    raw = json.loads(path.read_text("utf-8"))
    raw["fixtures"]["evidence_snapshot"]["complete"] = False

    with pytest.raises(ScenarioValidationError, match="snapshot must be complete"):
        run_auto_mode_contract(Scenario.from_dict(raw))


def test_auto_mode_contract_rejects_complete_snapshot_with_missing_source_ref():
    path = _SCENARIOS / "baseline" / "auto_mode_verified.json"
    raw = json.loads(path.read_text("utf-8"))
    raw["fixtures"]["evidence_snapshot"]["evidence_refs"].pop()

    with pytest.raises(ScenarioValidationError, match="reference every Artifact"):
        run_auto_mode_contract(Scenario.from_dict(raw))


def test_auto_mode_contract_rejects_dangling_evidence_reference():
    path = _SCENARIOS / "baseline" / "auto_mode_verified.json"
    raw = json.loads(path.read_text("utf-8"))
    raw["fixtures"]["evidence_snapshot"]["evidence_refs"][0][
        "source_id"
    ] = "v-stage0-missing"

    with pytest.raises(ScenarioValidationError, match="must resolve to an exact"):
        run_auto_mode_contract(Scenario.from_dict(raw))


def test_auto_mode_contract_rejects_finding_with_dangling_evidence_reference():
    path = _SCENARIOS / "baseline" / "auto_mode_completed_with_issues.json"
    raw = json.loads(path.read_text("utf-8"))
    raw["fixtures"]["material_findings"][0]["evidence_refs"] = ["evidence-missing"]

    with pytest.raises(ScenarioValidationError, match="contains unresolved ref"):
        run_auto_mode_contract(Scenario.from_dict(raw))


def test_auto_mode_contract_rejects_pass_with_open_material_finding():
    path = _SCENARIOS / "baseline" / "auto_mode_verified.json"
    raw = json.loads(path.read_text("utf-8"))
    raw["fixtures"]["material_findings"] = [
        {
            "fingerprint": "finding-open-on-pass",
            "status": "open",
            "severity": "material",
            "evidence_refs": ["evidence-report"],
            "summary": "A pass cannot conceal an open material finding.",
        }
    ]

    with pytest.raises(ScenarioValidationError, match="pass cannot contain"):
        run_auto_mode_contract(Scenario.from_dict(raw))


def test_auto_mode_contract_rejects_issues_before_auto_fix_budget_exhaustion():
    path = _SCENARIOS / "baseline" / "auto_mode_completed_with_issues.json"
    raw = json.loads(path.read_text("utf-8"))
    raw["fixtures"]["review_policy"].update(
        {
            "auto_fix_budget_exhausted": False,
            "termination_basis": "no_open_material_findings",
        }
    )

    with pytest.raises(ScenarioValidationError, match="cannot terminate before"):
        run_auto_mode_contract(Scenario.from_dict(raw))


@pytest.mark.parametrize("mode", ["allow", "deny"])
def test_auto_mode_contract_requires_rules_only_noninteractive_posture(mode):
    path = _SCENARIOS / "baseline" / "auto_mode_guardian_denied.json"
    raw = json.loads(path.read_text("utf-8"))
    raw["permissions"]["noninteractive"] = mode

    with pytest.raises(ScenarioValidationError, match="must be rules_only"):
        run_auto_mode_contract(Scenario.from_dict(raw))


@pytest.mark.parametrize(
    "scenario_id,retryable",
    [
        ("auto_mode_guardian_timeout", False),
        ("auto_mode_guardian_audit_failure", True),
    ],
)
def test_auto_mode_contract_rejects_fault_retryability_kind_mismatch(
    scenario_id, retryable
):
    path = _SCENARIOS / "baseline" / f"{scenario_id}.json"
    raw = json.loads(path.read_text("utf-8"))
    raw["faults"][0]["retryable"] = retryable

    with pytest.raises(ScenarioValidationError, match="retryable must be"):
        run_auto_mode_contract(Scenario.from_dict(raw))


def test_auto_mode_contract_rejects_reusable_guardian_capability():
    path = _SCENARIOS / "baseline" / "auto_mode_guardian_allow_once_replay.json"
    raw = json.loads(path.read_text("utf-8"))
    raw["fixtures"]["one_shot_capability"]["max_uses"] = 2

    with pytest.raises(ScenarioValidationError, match="max_uses must be exactly 1"):
        run_auto_mode_contract(Scenario.from_dict(raw))


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda action: action.__setitem__("action_kind", "shell"),
            "only allowlisted file_write",
        ),
        (
            lambda action: action["target"].__setitem__(
                "relative_path", "../outside.txt"
            ),
            "canonical results/ path",
        ),
        (
            lambda action: action["risk"].__setitem__("external_write", True),
            "deterministic low-risk",
        ),
        (
            lambda action: action["risk"].__setitem__("irreversible", True),
            "deterministic low-risk",
        ),
    ],
)
def test_auto_mode_guardian_allow_once_uses_sealed_action_allowlist(mutate, message):
    path = _SCENARIOS / "baseline" / "auto_mode_guardian_allow_once_replay.json"
    raw = json.loads(path.read_text("utf-8"))
    mutate(raw["fixtures"]["canonical_action"])

    with pytest.raises(ScenarioValidationError, match=message):
        run_auto_mode_contract(Scenario.from_dict(raw))


def test_auto_mode_contract_rejects_circuit_history_that_does_not_cross_threshold():
    path = (
        _SCENARIOS / "baseline" / "auto_mode_guardian_denial_circuit_consecutive.json"
    )
    raw = json.loads(path.read_text("utf-8"))
    raw["fixtures"]["denial_history"]["prior_decisions"] = ["deny"]

    with pytest.raises(ScenarioValidationError, match="cross exactly one"):
        run_auto_mode_contract(Scenario.from_dict(raw))


def test_auto_mode_contract_rehashes_and_rejects_receipt_digest_drift(monkeypatch):
    scenario = load_scenario(
        _SCENARIOS / "baseline" / "auto_mode_guardian_allow_once_replay.json"
    )
    original = auto_mode_contract_mod._authorization_receipt

    def drifted_receipt(case, **kwargs):
        receipt, digest = original(case, **kwargs)
        if kwargs["attempt_kind"] == "replay":
            digest = "0" * 64
        return receipt, digest

    monkeypatch.setattr(
        auto_mode_contract_mod, "_authorization_receipt", drifted_receipt
    )
    result = run_auto_mode_contract(scenario)

    assert not result.passed
    assert any("authorization_receipt" in error for error in result.errors)


@pytest.mark.parametrize(
    "scenario_id,subject_kind,subject_entity_kind",
    [
        (
            "auto_mode_verified",
            "result_review",
            "candidate_evidence_snapshot",
        ),
        ("auto_mode_guardian_denied", "permission_review", "approval_action"),
    ],
)
def test_auto_mode_audit_subject_and_request_assessment_digests_are_distinct(
    scenario_id, subject_kind, subject_entity_kind
):
    scenario = load_scenario(_SCENARIOS / "baseline" / f"{scenario_id}.json")
    result = run_auto_mode_contract(scenario)
    audit_events = [
        event
        for event in result.events
        if event.kind in {"auto_audit_started", "auto_audit_completed"}
    ]

    assert result.passed, result.errors
    assert audit_events
    assert all(event.payload["subject_kind"] == subject_kind for event in audit_events)
    assert all(
        event.payload["subject_entity_kind"] == subject_entity_kind
        for event in audit_events
    )
    assert {event.payload["audit_id"] for event in audit_events} == {
        scenario.fixtures["audit_id"]
    }
    request_digests = {event.payload["audit_request_digest"] for event in audit_events}
    assert len(request_digests) == 1
    for event in audit_events:
        if event.kind == "auto_audit_completed":
            assert event.payload["assessment_digest"] == _canonical_digest(
                event.payload["assessment"]
            )
            assert event.payload["assessment_digest"] not in request_digests


@pytest.mark.parametrize(
    "scenario_id",
    ["auto_mode_verified", "auto_mode_guardian_denied"],
)
def test_auto_mode_contract_rejects_started_completed_audit_id_drift(
    scenario_id, monkeypatch
):
    scenario = load_scenario(_SCENARIOS / "baseline" / f"{scenario_id}.json")
    original = auto_mode_contract_mod._completion_payload

    def drifted_completion(case, assessment, assessment_digest):
        payload = original(case, assessment, assessment_digest)
        payload["audit_id"] = "audit-drifted"
        return payload

    monkeypatch.setattr(
        auto_mode_contract_mod, "_completion_payload", drifted_completion
    )
    result = run_auto_mode_contract(scenario)

    assert not result.passed
    assert any("audit_id" in error for error in result.errors)


@pytest.mark.parametrize(
    "scenario_id,event_kind,extra_field",
    [
        ("auto_mode_verified", "candidate_ready", "verified_badge"),
        ("auto_mode_verified", "auto_audit_started", "standing_allow_created"),
        (
            "auto_mode_guardian_allow_once_replay",
            "permission_resolved",
            "standing_grant_id",
        ),
        (
            "auto_mode_guardian_denied",
            "auto_run_terminal",
            "standing_allow_created",
        ),
    ],
)
def test_auto_mode_contract_rejects_unreviewed_event_payload_fields(
    scenario_id, event_kind, extra_field, monkeypatch
):
    scenario = load_scenario(_SCENARIOS / "baseline" / f"{scenario_id}.json")
    original = auto_mode_contract_mod._Recorder.emit

    def drifted_emit(self, kind, **kwargs):
        event = original(self, kind, **kwargs)
        if kind == event_kind:
            event.payload[extra_field] = True
        return event

    monkeypatch.setattr(auto_mode_contract_mod._Recorder, "emit", drifted_emit)
    result = run_auto_mode_contract(scenario)

    assert not result.passed
    assert any(
        marker in error
        for error in result.errors
        for marker in (
            "event_payload_digests",
            "exact terminal payload",
            "trace_sha256",
        )
    )


def test_auto_mode_contract_rejects_unreviewed_event_envelope_state(monkeypatch):
    scenario = load_scenario(_SCENARIOS / "baseline" / "auto_mode_verified.json")
    original = auto_mode_contract_mod._Recorder.emit

    def drifted_emit(self, kind, **kwargs):
        if kind == "candidate_ready":
            kwargs["phase"] = "permission_guardian"
            kwargs["status"] = "verified"
        return original(self, kind, **kwargs)

    monkeypatch.setattr(auto_mode_contract_mod._Recorder, "emit", drifted_emit)
    result = run_auto_mode_contract(scenario)

    assert not result.passed
    assert any("trace_sha256" in error for error in result.errors)


def test_auto_mode_contract_requires_every_reviewed_golden_field(tmp_path, monkeypatch):
    source = auto_mode_contract_mod._GOLDEN_PATH
    golden = json.loads(source.read_text("utf-8"))
    del golden["cases"]["auto_mode_verified"]["authorization_receipt_digests"]
    changed = tmp_path / source.name
    changed.write_text(json.dumps(golden), encoding="utf-8")
    monkeypatch.setattr(auto_mode_contract_mod, "_GOLDEN_PATH", changed)
    scenario = load_scenario(_SCENARIOS / "baseline" / "auto_mode_verified.json")

    with pytest.raises(
        ScenarioValidationError, match="missing authorization_receipt_digests"
    ):
        run_auto_mode_contract(scenario)


def test_auto_mode_contract_requires_reviewed_trace_digest(tmp_path, monkeypatch):
    source = auto_mode_contract_mod._GOLDEN_PATH
    golden = json.loads(source.read_text("utf-8"))
    del golden["cases"]["auto_mode_verified"]["trace_sha256"]
    changed = tmp_path / source.name
    changed.write_text(json.dumps(golden), encoding="utf-8")
    monkeypatch.setattr(auto_mode_contract_mod, "_GOLDEN_PATH", changed)
    scenario = load_scenario(_SCENARIOS / "baseline" / "auto_mode_verified.json")

    with pytest.raises(ScenarioValidationError, match="missing trace_sha256"):
        run_auto_mode_contract(scenario)


def test_auto_mode_assessment_digest_binds_every_review_field():
    scenario = load_scenario(_SCENARIOS / "baseline" / "auto_mode_verified.json")
    result = run_auto_mode_contract(scenario)
    completion = next(
        event for event in result.events if event.kind == "auto_audit_completed"
    )
    assessment = completion.payload["assessment"]
    original = completion.payload["assessment_digest"]
    mutations = {
        "attempt": 2,
        "verdict": "issues",
        "decision": "deny",
        "findings": [{"fingerprint": "changed"}],
        "risk": {"risk_level": "material"},
        "authorization": {"action_authorized": True},
        "outcome": "completed_with_issues",
        "rationale": "changed rationale",
        "failure_kind": "parse_failure",
    }

    assert original == _canonical_digest(assessment)
    for field, value in mutations.items():
        changed = json.loads(json.dumps(assessment))
        changed[field] = value
        assert _canonical_digest(changed) != original, field


@pytest.mark.parametrize(
    "scenario_id,runtime_key,canonical_key",
    [
        (
            "auto_mode_review_evidence_hash_mismatch",
            "runtime_evidence_snapshot",
            "evidence_snapshot",
        ),
        (
            "auto_mode_guardian_action_hash_mismatch",
            "runtime_action",
            "canonical_action",
        ),
    ],
)
def test_auto_mode_hash_mismatch_rehashes_runtime_mutation(
    scenario_id, runtime_key, canonical_key
):
    scenario = load_scenario(_SCENARIOS / "baseline" / f"{scenario_id}.json")
    result = run_auto_mode_contract(scenario)
    fault = next(event for event in result.events if event.kind == "fault_injected")

    assert result.passed, result.errors
    assert '"observed_digest"' not in json.dumps(scenario.fixtures)
    assert fault.payload["expected_digest"] == _canonical_digest(
        scenario.fixtures[canonical_key]
    )
    assert fault.payload["observed_digest"] == _canonical_digest(
        scenario.fixtures[runtime_key]
    )
    assert fault.payload["expected_digest"] != fault.payload["observed_digest"]


def test_auto_mode_contract_golden_is_independent_and_contract_only():
    path = (
        Path(__file__).resolve().parents[1]
        / "harness"
        / "golden_traces"
        / "v1"
        / "auto_mode_contract_expected.json"
    )
    golden = json.loads(path.read_text("utf-8"))
    assert golden["contract"] == "stage0_auto_mode_v3"
    assert golden["production_state_machine"] is False
    scenario_ids = {
        scenario_path.stem
        for scenario_path in (_SCENARIOS / "baseline").glob("*.json")
        if load_scenario(scenario_path).surface == "auto_mode_contract"
    }
    assert set(golden["cases"]) == scenario_ids
    required_case_fields = {
        "lane",
        "terminal_reason",
        "event_kinds",
        "specific_event_kinds",
        "digests",
        "assessment_digests",
        "authorization_receipt_digests",
        "event_payload_digests",
        "trace_sha256",
        "terminal_payload",
    }
    for expected in golden["cases"].values():
        assert set(expected) == required_case_fields
        assert len(expected["event_payload_digests"]) == len(expected["event_kinds"])
    assert set(golden["cases"]["auto_mode_verified"]["digests"]) == {
        "identity_digest",
        "candidate_digest",
        "evidence_snapshot_digest",
        "artifact_set_digest",
        "review_request_policy_digest",
        "audit_request_digest",
    }
    assert golden["cases"]["auto_mode_verified"]["assessment_digests"]
    assert golden["cases"]["auto_mode_guardian_allow_once_replay"][
        "authorization_receipt_digests"
    ]


def test_normalizer_preserves_event_order_instead_of_sorting():
    scenario = load_scenario(_SCENARIOS / "baseline" / "two_response_sequence.json")
    result = run_scenario(scenario)
    forward = normalized_trace_bytes(result.events)
    reversed_bytes = normalized_trace_bytes(reversed(result.events))
    assert forward != reversed_bytes
    normalized = json.loads(forward)
    assert [event["seq"] for event in normalized] == [1, 2, 3, 4, 5, 6]
    parent_positions = {
        event["event_id"]: index for index, event in enumerate(normalized)
    }
    for index, event in enumerate(normalized):
        parent = event["parent_event_id"]
        if parent is not None:
            assert parent_positions[parent] < index


def test_normalizer_uses_explicit_path_and_localhost_port_replacements():
    scenario = load_scenario(_SCENARIOS / "baseline" / "single_response_submitted.json")
    result = run_scenario(scenario)
    events = [event.to_dict() for event in result.events]
    events[0]["payload"]["workspace_file"] = "/tmp/run-a/workspace/data.csv"
    events[0]["payload"]["db"] = "/tmp/run-a/data-dir/openai4s.db"
    events[0]["payload"]["endpoint"] = "http://127.0.0.1:54321/api/ws"
    replacements = {
        "/tmp/run-a/workspace": "<workspace>",
        "/tmp/run-a/data-dir": "<data-dir>",
        "127.0.0.1:54321": "127.0.0.1:<port>",
    }
    first = json.loads(normalized_trace_bytes(events, replacements=replacements))
    payload = first[0]["payload"]
    assert payload["workspace_file"] == "<workspace>/data.csv"
    assert payload["db"] == "<data-dir>/openai4s.db"
    assert payload["endpoint"] == "http://127.0.0.1:<port>/api/ws"
    assert [event["seq"] for event in first] == [1, 2, 3, 4]


def test_cli_runs_pr_offline_tier(capsys):
    assert main(["run", "--tier", "pr", "--offline"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert (
        sum(line.startswith("CONTRACT_PASS production=false ") for line in lines) >= 3
    )
    assert any(line.startswith("PRODUCTION_PASS production=true ") for line in lines)
    summary = json.loads(
        next(line[8:] for line in lines if line.startswith("SUMMARY "))
    )
    assert summary["schema_version"] == 1
    assert summary["tier"] == "pr"
    assert summary["offline"] is True
    assert summary["failed"] == 0
    assert summary["load_errors"] == 0
    assert summary["passed"] == summary["selected"]
    assert summary["contract_only"]["failed"] == 0
    assert summary["production_backed"]["failed"] == 0
    assert (
        summary["contract_only"]["selected"] + summary["production_backed"]["selected"]
        == summary["selected"]
    )
    assert summary["selected"] >= 3


def test_cli_counts_runner_validation_failure_once(tmp_path, capsys):
    raw = {
        "schema_version": 1,
        "id": "bad_auto_contract",
        "tags": ["offline", "tier:pr"],
        "surface": "auto_mode_contract",
        "task": "one runner validation failure",
        "fixtures": {},
        "provider_script": [{"response": {"content": "unused"}}],
        "faults": [],
        "permissions": {"noninteractive": "rules_only"},
        "expect": {"terminal_reason": "verified", "model_attempts": 0},
    }
    (tmp_path / "bad.json").write_text(json.dumps(raw), "utf-8")

    assert (
        main(
            [
                "run",
                "--tier",
                "pr",
                "--offline",
                "--scenario-dir",
                str(tmp_path),
                "--scenario",
                "bad_auto_contract",
            ]
        )
        == 2
    )
    lines = capsys.readouterr().out.splitlines()
    assert sum(line.startswith("ERROR ") for line in lines) == 1
    assert not any("was not found" in line for line in lines)
    summary = json.loads(
        next(line[8:] for line in lines if line.startswith("SUMMARY "))
    )
    assert summary["failed"] == 1
    assert summary["load_errors"] == 1
    assert summary["selected"] == 1
    assert summary["contract_only"] == {"selected": 1, "passed": 0, "failed": 1}


def test_declared_fault_that_never_fires_fails_the_scenario():
    raw = {
        "schema_version": 1,
        "id": "unfired_fault",
        "tags": ["offline", "tier:pr"],
        "surface": "harness",
        "task": "x",
        "provider_script": [
            {"response": {"content": "x"}, "terminal_reason": "submitted"}
        ],
        "faults": [
            {
                "point": "before_modle",
                "occurrence": 1,
                "kind": "timeout",
                "message": "typo'd point must not pass vacuously",
            }
        ],
        "expect": {"terminal_reason": "submitted", "model_attempts": 1},
    }
    result = run_scenario(Scenario.from_dict(raw), offline=True)
    assert not result.passed
    assert any("never fired" in error for error in result.errors)


def test_explicit_empty_invariants_is_an_opt_out():
    raw = {
        "schema_version": 1,
        "id": "invariant_opt_out",
        "tags": ["offline", "tier:pr"],
        "surface": "harness",
        "task": "x",
        "provider_script": [{"response": {"content": "x"}}],
        "expect": {
            "terminal_reason": "script_exhausted",
            "model_attempts": 1,
            "invariants": [],
        },
    }
    assert Scenario.from_dict(raw).expect.invariants == ()
    del raw["expect"]["invariants"]
    assert Scenario.from_dict(raw).expect.invariants == (
        "ordered_events",
        "one_run_terminal",
    )


def test_offline_runner_rejects_external_scenario():
    raw = {
        "schema_version": 1,
        "id": "external_case",
        "tags": ["tier:pr", "external"],
        "surface": "harness",
        "task": "must not run offline",
        "provider_script": [
            {
                "response": {"content": "x"},
                "terminal_reason": "submitted",
            }
        ],
        "faults": [],
        "expect": {"terminal_reason": "submitted", "model_attempts": 1},
    }
    with pytest.raises(ValueError, match="not eligible"):
        run_scenario(Scenario.from_dict(raw), offline=True)


def test_retrosynthesis_manifest_and_capabilities_are_offline(tmp_path):
    backend_module = _skill_module("retrosynthesis_planning.external_backends")
    manifest = backend_module.ModelManifest(
        provider="Microsoft Research",
        model="RetroChimera",
        model_version="1.2.0",
        checkpoint_id="synthetic-checkpoint",
        checkpoint_sha256="a" * 64,
        training_dataset="synthetic fixture",
        code_license="MIT",
        checkpoint_license="MIT",
        source_url="https://github.com/microsoft/retrochimera",
    )
    assert manifest.provenance_status == "complete"
    assert len(manifest.fingerprint) == 64
    with pytest.raises(backend_module.BackendProtocolError, match="SHA-256"):
        backend_module.ModelManifest(
            provider="Microsoft Research",
            model="RetroChimera",
            model_version="1.2.0",
            checkpoint_id="bad",
            checkpoint_sha256="not-a-digest",
            training_dataset="synthetic fixture",
            code_license="MIT",
            checkpoint_license="MIT",
        )

    backend = backend_module.SyntheseusBackend(
        model="RetroChimera",
        model_dir=tmp_path / "checkpoint",
        manifest=manifest,
    )
    capabilities = backend.capabilities(request_id="capabilities-test")
    assert capabilities["ok"] is True
    assert capabilities["operation"] == "capabilities"
    assert "RetroChimera" in capabilities["capabilities"]["models"]

    no_checkpoint = backend_module.SyntheseusBackend(model="RetroChimera")
    with pytest.raises(ValueError, match="automatic checkpoint downloads"):
        no_checkpoint.single_step("CCO")


def test_retrosynthesis_worker_runs_behind_fake_optional_modules(monkeypatch):
    backend_module = _skill_module("retrosynthesis_planning.external_backends")
    worker = _skill_module("retrosynthesis_planning.syntheseus_worker")

    class FakeMolecule:
        def __init__(self, smiles):
            self.smiles = smiles

    class FakePrediction:
        reactants_str = "CCO.N"
        reaction_smiles = "CCO.N>>CCON"
        metadata = {
            "probability": 0.7,
            "checkpoint_path": "/private/path/must-not-escape",
        }

    class FakeRetroChimeraModel:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __call__(self, molecules, num_results):
            assert molecules[0].smiles == "CCON"
            assert num_results == 3
            return [[FakePrediction()]]

    syntheseus = types.ModuleType("syntheseus")
    syntheseus.Molecule = FakeMolecule
    retrochimera = types.ModuleType("retrochimera")
    retrochimera.RetroChimeraModel = FakeRetroChimeraModel
    monkeypatch.setitem(sys.modules, "syntheseus", syntheseus)
    monkeypatch.setitem(sys.modules, "retrochimera", retrochimera)

    manifest = backend_module.ModelManifest(
        provider="Microsoft Research",
        model="RetroChimera",
        model_version="1.2.0",
        checkpoint_id="synthetic-checkpoint",
        checkpoint_sha256="b" * 64,
        training_dataset="synthetic fixture",
        code_license="MIT",
        checkpoint_license="MIT",
    )
    response = worker.handle_request(
        {
            "schema_version": 1,
            "request_id": "fake-worker-test",
            "operation": "single_step",
            "target_smiles": "CCON",
            "model": "RetroChimera",
            "model_dir": "/synthetic/checkpoint",
            "num_results": 3,
            "allow_model_download": False,
            "model_manifest": manifest.to_dict(),
        }
    )
    normalized = backend_module.normalize_external_backend_response(
        response, expected_request_id="fake-worker-test"
    )
    assert normalized["ok"] is True
    assert normalized["predictions"][0]["reactants_smiles"] == "CCO.N"
    assert normalized["predictions"][0]["score"] == 0.7
    assert "checkpoint_path" not in normalized["predictions"][0]["metadata"]
    assert normalized["provenance_status"] == "complete"
    assert "experimental success probabilities" in normalized["scientific_disclaimer"]


def test_retrosynthesis_backend_replays_are_deterministic():
    first = evaluate_backend_replays()
    second = evaluate_backend_replays()
    assert first["accuracy"] == 1.0
    assert first["complete_provenance_rate"] == 1.0
    assert first["prediction_count"] == 2
    assert first["scored_prediction_rate"] == 1.0
    assert [case["normalized_sha256"] for case in first["cases"]] == [
        case["normalized_sha256"] for case in second["cases"]
    ]
