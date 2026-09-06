"""Stage 0 contracts for Auto Mode's non-Guardian terminal reasons."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from harness import auto_mode_terminal_contract as terminal_contract_mod
from harness.auto_mode_terminal_contract import (
    CONTRACT,
    SURFACE,
    TERMINAL_REASONS,
    _project_terminal_reason,
    run_auto_mode_terminal_contract,
)
from harness.cli import main
from harness.schema import Scenario, ScenarioValidationError, load_scenario
from openai4s.server.auto_budget import (
    TERMINAL_USER_TRUTH,
    is_completion_disguise,
)

_ROOT = Path(__file__).resolve().parents[1]
_BASELINE = _ROOT / "harness" / "scenarios" / "baseline"
_GOLDEN = (
    _ROOT
    / "harness"
    / "golden_traces"
    / "v1"
    / "auto_mode_terminal_contract_expected.json"
)
_SCENARIO_IDS = {
    "auto_mode_policy_requires_explicit_setup": "policy_requires_explicit_setup",
    "auto_mode_budget_exhausted": "budget_exhausted",
    "auto_mode_safe_rollback_unavailable": "safe_rollback_unavailable",
    "auto_mode_outcome_unknown": "outcome_unknown",
    "auto_mode_loop_detected": "loop_detected",
}
_USER_TRUTH = {
    "policy_requires_explicit_setup": "Blocked · Policy requires explicit setup",
    "budget_exhausted": "Paused · Budget exhausted",
    "safe_rollback_unavailable": "Blocked · Safe rollback unavailable",
    "outcome_unknown": "Needs review · Outcome unknown",
    "loop_detected": "Paused/Blocked · Loop detected",
}
_RECOVERY_MODE = {
    "policy_requires_explicit_setup": "fresh_policy_continuation",
    "budget_exhausted": "fresh_authorized_budget",
    "safe_rollback_unavailable": "fresh_repair_from_proven_state",
    "outcome_unknown": "reconcile_or_operator_review",
    "loop_detected": "materially_different_continuation",
}


def _raw(scenario_id: str) -> dict[str, Any]:
    return json.loads((_BASELINE / f"{scenario_id}.json").read_text(encoding="utf-8"))


def _run_raw(raw: dict[str, Any]):
    return run_auto_mode_terminal_contract(Scenario.from_dict(raw))


def _digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_terminal_scenarios_are_closed_deterministic_contracts():
    selected = {
        path.stem
        for path in _BASELINE.glob("auto_mode_*.json")
        if load_scenario(path).surface == SURFACE
    }
    assert selected == set(_SCENARIO_IDS)
    assert set(TERMINAL_REASONS) == set(_SCENARIO_IDS.values())

    for scenario_id, reason in _SCENARIO_IDS.items():
        scenario = load_scenario(_BASELINE / f"{scenario_id}.json")
        first = run_auto_mode_terminal_contract(scenario)
        second = run_auto_mode_terminal_contract(scenario)

        assert first.passed, first.errors
        assert second.passed, second.errors
        assert first.normalized == second.normalized
        assert first.trace_sha256 == second.trace_sha256
        assert first.model_attempts == 0
        assert first.terminal_reason == reason
        assert [event.kind for event in first.events] == [
            "auto_run_started",
            "auto_run_terminal",
        ]
        assert [event.seq for event in first.events] == [1, 2]
        assert first.events[1].parent_event_id == first.events[0].event_id

        fixtures = scenario.fixtures
        terminal = first.events[-1].payload
        assert terminal["terminal_reason"] == reason
        assert terminal["auto_user_state"] is None
        assert terminal["safety_terminal"] is False
        assert terminal["production_state_machine"] is False
        assert terminal["guardian_terminal"] is False
        assert terminal["automatic_resume"] is False
        assert terminal["recoverable"] is (reason == "outcome_unknown")
        assert terminal["user_truth"] == _USER_TRUTH[reason]
        assert terminal["recovery_mode"] == _RECOVERY_MODE[reason]
        assert terminal["condition"] == fixtures["condition"]
        assert terminal["condition_digest"] == _digest(fixtures["condition"])
        assert first.events[0].payload["identity_digest"] == _digest(
            fixtures["identity"]
        )
        assert "condition_digest" not in fixtures
        assert "identity_digest" not in fixtures

        for event in first.events:
            assert event.payload["identity"] == fixtures["identity"]
            assert event.payload["event_names"]["canonical"] == event.kind
        assert not {
            "auto_audit_started",
            "auto_audit_completed",
            "action_authorized",
            "repair_started",
        } & {event.kind for event in first.events}
        assert not {
            "audit_id",
            "audit_request_digest",
            "assessment_digest",
            "subject_kind",
            "subject_entity_kind",
        } & set(terminal)


def test_independent_golden_freezes_exact_payloads_and_canonical_digests():
    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    assert set(golden) == {
        "schema_version",
        "contract",
        "production_state_machine",
        "cases",
    }
    assert golden["schema_version"] == 1
    assert golden["contract"] == CONTRACT
    assert golden["production_state_machine"] is False
    assert set(golden["cases"]) == set(_SCENARIO_IDS)

    for scenario_id, reason in _SCENARIO_IDS.items():
        raw = _raw(scenario_id)
        expected = golden["cases"][scenario_id]
        assert set(expected) == {
            "lane",
            "terminal_reason",
            "event_kinds",
            "specific_event_kinds",
            "digests",
            "event_payload_digests",
            "trace_sha256",
            "terminal_payload",
        }
        assert expected["lane"] == "non_guardian_terminal"
        assert expected["terminal_reason"] == reason
        assert expected["event_kinds"] == [
            "auto_run_started",
            "auto_run_terminal",
        ]
        assert expected["specific_event_kinds"] == [
            "run_started",
            "run_finished",
        ]
        assert expected["digests"] == {
            "identity_digest": _digest(raw["fixtures"]["identity"]),
            "condition_digest": _digest(raw["fixtures"]["condition"]),
        }
        result = _run_raw(raw)
        projection = dict(result.events[-1].payload)
        projection.pop("identity")
        projection.pop("event_names")
        assert projection == expected["terminal_payload"]


def test_production_budget_terminals_match_harness_truth_and_are_not_completion():
    assert TERMINAL_USER_TRUTH["budget_exhausted"] == _USER_TRUTH["budget_exhausted"]
    assert TERMINAL_USER_TRUTH["loop_detected"] == _USER_TRUTH["loop_detected"]
    assert (
        TERMINAL_USER_TRUTH["budget_measurement_unavailable"] == "无法验证 token 预算"
    )
    for reason in TERMINAL_USER_TRUTH:
        for status in ("verified", "completed", "completed_with_issues", "pass"):
            assert is_completion_disguise(status, reason) is True
        assert is_completion_disguise("paused", reason) is False
        assert is_completion_disguise("review_unavailable", reason) is False


@pytest.mark.parametrize("permission", ["deny", "allow"])
def test_contract_rejects_any_permission_mode_other_than_rules_only(permission):
    raw = _raw("auto_mode_policy_requires_explicit_setup")
    raw["permissions"]["noninteractive"] = permission
    with pytest.raises(ScenarioValidationError, match="rules_only"):
        _run_raw(raw)


def test_contract_rejects_injected_decision_faults():
    raw = _raw("auto_mode_policy_requires_explicit_setup")
    raw["faults"] = [
        {
            "point": "before_terminal",
            "occurrence": 1,
            "kind": "timeout",
            "message": "must not reinterpret a terminal",
            "retryable": False,
        }
    ]
    with pytest.raises(ScenarioValidationError, match="does not accept"):
        _run_raw(raw)


@pytest.mark.parametrize(
    "scenario_id,key,value,match",
    [
        (
            "auto_mode_policy_requires_explicit_setup",
            "refusal_audit_durable",
            False,
            "refusal_audit_durable",
        ),
        (
            "auto_mode_policy_requires_explicit_setup",
            "action_executed",
            True,
            "action_executed",
        ),
        (
            "auto_mode_policy_requires_explicit_setup",
            "guardian_invoked",
            True,
            "guardian_invoked",
        ),
        (
            "auto_mode_budget_exhausted",
            "checked_before_admission",
            False,
            "checked_before_admission",
        ),
        (
            "auto_mode_budget_exhausted",
            "action_admitted",
            True,
            "action_admitted",
        ),
        (
            "auto_mode_budget_exhausted",
            "same_run_refilled",
            True,
            "same_run_refilled",
        ),
        (
            "auto_mode_safe_rollback_unavailable",
            "admission_proven",
            True,
            "admission_proven",
        ),
        (
            "auto_mode_safe_rollback_unavailable",
            "repair_started",
            True,
            "repair_started",
        ),
        (
            "auto_mode_safe_rollback_unavailable",
            "formal_workspace_mutated",
            True,
            "formal_workspace_mutated",
        ),
        (
            "auto_mode_safe_rollback_unavailable",
            "sandbox_path_escape",
            True,
            "sandbox_path_escape",
        ),
        (
            "auto_mode_outcome_unknown",
            "output_committed",
            True,
            "output_committed",
        ),
        (
            "auto_mode_outcome_unknown",
            "blind_retry",
            True,
            "blind_retry",
        ),
        (
            "auto_mode_outcome_unknown",
            "action_retried",
            True,
            "action_retried",
        ),
        (
            "auto_mode_outcome_unknown",
            "reconciliation_exhausted",
            False,
            "reconciliation_exhausted",
        ),
        (
            "auto_mode_loop_detected",
            "circuit_open",
            False,
            "circuit_open",
        ),
        (
            "auto_mode_loop_detected",
            "new_action_admitted",
            True,
            "new_action_admitted",
        ),
    ],
)
def test_reason_specific_fail_closed_flags(
    scenario_id: str, key: str, value: Any, match: str
):
    raw = _raw(scenario_id)
    raw["fixtures"]["condition"][key] = value
    with pytest.raises(ScenarioValidationError, match=match):
        _run_raw(raw)


@pytest.mark.parametrize(
    "scenario_id,updates,match",
    [
        (
            "auto_mode_budget_exhausted",
            {"observed": 29},
            "reach the budget limit",
        ),
        (
            "auto_mode_outcome_unknown",
            {"readback_attempts": 1},
            "exhaust the bounded limit",
        ),
        (
            "auto_mode_loop_detected",
            {"observed": 4},
            "reach the loop limit",
        ),
    ],
)
def test_bounded_counters_must_reach_their_stop_threshold(
    scenario_id: str, updates: dict[str, Any], match: str
):
    raw = _raw(scenario_id)
    raw["fixtures"]["condition"].update(updates)
    with pytest.raises(ScenarioValidationError, match=match):
        _run_raw(raw)


@pytest.mark.parametrize(
    "reason,owner,findings,boundary,expected",
    [
        ("budget_exhausted", "general", False, False, "budget_exhausted"),
        (
            "budget_exhausted",
            "result_review",
            True,
            False,
            "completed_with_issues",
        ),
        (
            "loop_detected",
            "result_review",
            True,
            False,
            "completed_with_issues",
        ),
        (
            "loop_detected",
            "permission_guardian",
            False,
            False,
            "blocked_by_guardian",
        ),
        ("loop_detected", "general", False, False, "loop_detected"),
        (
            "safe_rollback_unavailable",
            "general",
            False,
            True,
            "safety_boundary",
        ),
    ],
)
def test_cross_routing_precedence(
    reason: str,
    owner: str,
    findings: bool,
    boundary: bool,
    expected: str,
):
    assert (
        _project_terminal_reason(
            reason,
            owner_domain=owner,
            open_material_findings=findings,
            boundary_violation=boundary,
        )
        == expected
    )


@pytest.mark.parametrize(
    "scenario_id,owner,match",
    [
        (
            "auto_mode_budget_exhausted",
            "result_review",
            "result-review budget routing",
        ),
        (
            "auto_mode_loop_detected",
            "permission_guardian",
            "must project as blocked_by_guardian",
        ),
    ],
)
def test_fixture_cannot_claim_a_terminal_owned_by_another_lane(
    scenario_id: str, owner: str, match: str
):
    raw = _raw(scenario_id)
    raw["fixtures"]["condition"]["owner_domain"] = owner
    with pytest.raises(ScenarioValidationError, match=match):
        _run_raw(raw)


def test_outcome_unknown_preserves_uncertainty_and_only_allows_reconciliation():
    result = _run_raw(_raw("auto_mode_outcome_unknown"))
    terminal = result.events[-1].payload
    condition = terminal["condition"]
    assert "action_executed" not in condition
    assert condition["output_committed"] is None
    assert condition["blind_retry"] is False
    assert condition["action_retried"] is False
    assert terminal["recoverable"] is True
    assert terminal["automatic_resume"] is False
    assert terminal["recovery_mode"] == "reconcile_or_operator_review"


def test_schema_valid_fixture_drift_still_fails_the_reviewed_digest():
    raw = _raw("auto_mode_policy_requires_explicit_setup")
    raw["fixtures"]["condition"]["trigger_kind"] = "dangerous"
    with pytest.raises(ScenarioValidationError, match="canonical digests"):
        _run_raw(raw)


def test_started_payload_rejects_unreviewed_guardian_projection(monkeypatch):
    scenario = load_scenario(
        _BASELINE / "auto_mode_policy_requires_explicit_setup.json"
    )
    original = terminal_contract_mod._Recorder.emit

    def drifted_emit(self, kind, **kwargs):
        event = original(self, kind, **kwargs)
        if kind == "auto_run_started":
            event.payload["guardian_invoked"] = True
            event.payload["auto_user_state"] = "Verified"
        return event

    monkeypatch.setattr(terminal_contract_mod._Recorder, "emit", drifted_emit)
    result = run_auto_mode_terminal_contract(scenario)

    assert not result.passed
    assert "event_payload_digests: reviewed golden payloads drifted" in result.errors


def test_terminal_contract_rejects_unreviewed_event_envelope_state(monkeypatch):
    scenario = load_scenario(
        _BASELINE / "auto_mode_policy_requires_explicit_setup.json"
    )
    original = terminal_contract_mod._Recorder.emit

    def drifted_emit(self, kind, **kwargs):
        if kind == "auto_run_started":
            kwargs["phase"] = "permission_guardian"
        return original(self, kind, **kwargs)

    monkeypatch.setattr(terminal_contract_mod._Recorder, "emit", drifted_emit)
    result = run_auto_mode_terminal_contract(scenario)

    assert not result.passed
    assert "trace_sha256: reviewed golden trace drifted" in result.errors


def test_terminal_golden_requires_payload_digests(tmp_path, monkeypatch):
    golden = json.loads(_GOLDEN.read_text("utf-8"))
    del golden["cases"]["auto_mode_policy_requires_explicit_setup"][
        "event_payload_digests"
    ]
    changed = tmp_path / _GOLDEN.name
    changed.write_text(json.dumps(golden), encoding="utf-8")
    monkeypatch.setattr(terminal_contract_mod, "_GOLDEN_PATH", changed)

    with pytest.raises(ScenarioValidationError, match="event_payload_digests"):
        run_auto_mode_terminal_contract(
            load_scenario(_BASELINE / "auto_mode_policy_requires_explicit_setup.json")
        )


@pytest.mark.parametrize("mutation", ["missing", "unknown"])
def test_condition_shape_is_exact(mutation: str):
    raw = _raw("auto_mode_loop_detected")
    condition = raw["fixtures"]["condition"]
    if mutation == "missing":
        condition.pop("fingerprint")
    else:
        condition["surprise"] = True
    with pytest.raises(ScenarioValidationError, match="fixtures.condition"):
        _run_raw(raw)


def test_cli_routes_terminal_contract_surface(capsys):
    rc = main(
        [
            "run",
            "--tier",
            "pr",
            "--offline",
            "--scenario",
            "auto_mode_outcome_unknown",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0, captured.out + captured.err
    assert "CONTRACT_PASS production=false auto_mode_outcome_unknown" in captured.out
    assert '"contract_only":{"failed":0,"passed":1,"selected":1}' in captured.out
