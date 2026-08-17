"""Deterministic Stage 0 contract for non-Guardian Auto Mode terminals.

The five reasons in this module sit outside the five Auto Mode user states.
They are deterministic control-plane outcomes, not Reviewer verdicts or
Guardian decisions.  This adapter is intentionally production-independent;
every trace declares ``production_state_machine: false`` and exists only to
freeze the contract that later stages must implement.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .faults import FakeClock, FakeUUIDFactory
from .normalize import normalized_trace_bytes
from .runner import ScenarioResult
from .schema import SCHEMA_VERSION, EventEnvelope, Scenario, ScenarioValidationError

SURFACE = "auto_mode_terminal_contract"
CONTRACT = "stage0_auto_mode_terminal_v1"
LANE = "non_guardian_terminal"

TERMINAL_REASONS = (
    "policy_requires_explicit_setup",
    "budget_exhausted",
    "safe_rollback_unavailable",
    "outcome_unknown",
    "loop_detected",
)
_SCENARIO_REASONS = {f"auto_mode_{reason}": reason for reason in TERMINAL_REASONS}

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
_RECOVERABLE = {reason: reason == "outcome_unknown" for reason in TERMINAL_REASONS}

_POLICY_TRIGGERS = {
    "dangerous",
    "unknown_tool",
    "noncanonical_target",
    "policy_conflict",
}
_BUDGET_KINDS = {
    "token",
    "cost",
    "time",
    "turn",
    "cell",
    "extra_cells",
    "review_round",
    "repair_round",
}
_ROLLBACK_FAILURES = {
    "checkpoint_commit_failed",
    "branch_conflict",
    "mutation_set_unproven",
}
_SIDE_EFFECT_KINDS = {
    "external_write",
    "mcp_write",
    "network_mutation",
    "remote_compute",
}
_LOOP_KINDS = {"repeated_finding", "same_action_no_delta", "no_progress"}
_OWNER_DOMAINS = {"general", "result_review", "permission_guardian"}

_IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_GOLDEN_PATH = (
    Path(__file__).resolve().parent
    / "golden_traces"
    / "v1"
    / "auto_mode_terminal_contract_expected.json"
)


@dataclass(frozen=True)
class _TerminalCase:
    reason: str
    identity: Mapping[str, str]
    condition: Mapping[str, Any]
    identity_digest: str
    condition_digest: str

    @property
    def user_truth(self) -> str:
        return _USER_TRUTH[self.reason]

    @property
    def recovery_mode(self) -> str:
        return _RECOVERY_MODE[self.reason]

    @property
    def recoverable(self) -> bool:
        return _RECOVERABLE[self.reason]


class _Recorder:
    def __init__(
        self,
        *,
        identity: Mapping[str, str],
        clock: FakeClock,
        uuid_factory: FakeUUIDFactory,
    ) -> None:
        self.identity = dict(identity)
        self.clock = clock
        self.uuid_factory = uuid_factory
        self.events: list[EventEnvelope] = []

    def emit(
        self,
        kind: str,
        *,
        specific_kind: str,
        phase: str,
        status: str,
        payload: Mapping[str, Any],
    ) -> EventEnvelope:
        previous = self.events[-1].event_id if self.events else None
        body = dict(payload)
        body["identity"] = dict(self.identity)
        body["event_names"] = {
            "canonical": kind,
            "specific": specific_kind,
        }
        event = EventEnvelope(
            schema_version=SCHEMA_VERSION,
            event_id=self.uuid_factory(),
            seq=len(self.events) + 1,
            run_id=self.identity["auto_run_id"],
            root_frame_id=self.identity["root_frame_id"],
            turn_id=self.identity["turn_id"],
            parent_event_id=previous,
            kind=kind,
            phase=phase,
            status=status,
            monotonic_ms=self.clock.monotonic_ms(),
            payload=body,
        )
        self.events.append(event)
        return event


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ScenarioValidationError(f"{where} must be an object")
    return value


def _only_keys(
    value: Mapping[str, Any], allowed: set[str], where: str
) -> Mapping[str, Any]:
    unknown = sorted(set(value) - allowed)
    missing = sorted(allowed - set(value))
    if missing:
        raise ScenarioValidationError(
            f"{where} is missing fields: {', '.join(missing)}"
        )
    if unknown:
        raise ScenarioValidationError(
            f"{where} has unknown fields: {', '.join(unknown)}"
        )
    return value


def _identifier(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _IDENT.fullmatch(value):
        raise ScenarioValidationError(f"{where} must be a stable identifier")
    return value


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScenarioValidationError(f"{where} must be non-empty text")
    return value


def _bool(value: Any, expected: bool, where: str) -> bool:
    if value is not expected:
        raise ScenarioValidationError(f"{where} must be {str(expected).lower()}")
    return expected


def _integer(value: Any, where: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ScenarioValidationError(
            f"{where} must be an integer greater than or equal to {minimum}"
        )
    return value


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _identity(fixtures: Mapping[str, Any]) -> Mapping[str, str]:
    value = _mapping(fixtures.get("identity"), "fixtures.identity")
    keys = (
        "auto_run_id",
        "root_frame_id",
        "branch_id",
        "turn_id",
        "execution_id",
    )
    _only_keys(value, set(keys), "fixtures.identity")
    return {
        key: _identifier(value.get(key), f"fixtures.identity.{key}") for key in keys
    }


def _choice(value: Any, choices: set[str], where: str) -> str:
    text = _text(value, where)
    if text not in choices:
        raise ScenarioValidationError(
            f"{where} must be one of: {', '.join(sorted(choices))}"
        )
    return text


def _validate_condition(reason: str, raw: Any) -> Mapping[str, Any]:
    condition = _mapping(raw, "fixtures.condition")
    if reason == "policy_requires_explicit_setup":
        _only_keys(
            condition,
            {
                "trigger_kind",
                "refusal_audit_durable",
                "action_executed",
                "guardian_invoked",
            },
            "fixtures.condition",
        )
        _choice(
            condition.get("trigger_kind"),
            _POLICY_TRIGGERS,
            "fixtures.condition.trigger_kind",
        )
        _bool(
            condition.get("refusal_audit_durable"),
            True,
            "fixtures.condition.refusal_audit_durable",
        )
        _bool(
            condition.get("action_executed"),
            False,
            "fixtures.condition.action_executed",
        )
        _bool(
            condition.get("guardian_invoked"),
            False,
            "fixtures.condition.guardian_invoked",
        )
    elif reason == "budget_exhausted":
        _only_keys(
            condition,
            {
                "budget_kind",
                "limit",
                "observed",
                "checked_before_admission",
                "action_admitted",
                "same_run_refilled",
                "owner_domain",
            },
            "fixtures.condition",
        )
        _choice(
            condition.get("budget_kind"),
            _BUDGET_KINDS,
            "fixtures.condition.budget_kind",
        )
        limit = _integer(condition.get("limit"), "fixtures.condition.limit", minimum=1)
        observed = _integer(
            condition.get("observed"), "fixtures.condition.observed", minimum=0
        )
        if observed < limit:
            raise ScenarioValidationError(
                "fixtures.condition.observed must reach the budget limit"
            )
        _bool(
            condition.get("checked_before_admission"),
            True,
            "fixtures.condition.checked_before_admission",
        )
        _bool(
            condition.get("action_admitted"),
            False,
            "fixtures.condition.action_admitted",
        )
        _bool(
            condition.get("same_run_refilled"),
            False,
            "fixtures.condition.same_run_refilled",
        )
        _choice(
            condition.get("owner_domain"),
            _OWNER_DOMAINS,
            "fixtures.condition.owner_domain",
        )
    elif reason == "safe_rollback_unavailable":
        _only_keys(
            condition,
            {
                "failure_kind",
                "admission_proven",
                "repair_started",
                "formal_workspace_mutated",
                "sandbox_path_escape",
            },
            "fixtures.condition",
        )
        _choice(
            condition.get("failure_kind"),
            _ROLLBACK_FAILURES,
            "fixtures.condition.failure_kind",
        )
        _bool(
            condition.get("admission_proven"),
            False,
            "fixtures.condition.admission_proven",
        )
        _bool(
            condition.get("repair_started"),
            False,
            "fixtures.condition.repair_started",
        )
        _bool(
            condition.get("formal_workspace_mutated"),
            False,
            "fixtures.condition.formal_workspace_mutated",
        )
        _bool(
            condition.get("sandbox_path_escape"),
            False,
            "fixtures.condition.sandbox_path_escape",
        )
    elif reason == "outcome_unknown":
        _only_keys(
            condition,
            {
                "side_effect_kind",
                "side_effect_dispatched",
                "output_committed",
                "readback_attempts",
                "readback_limit",
                "reconciliation_exhausted",
                "blind_retry",
                "action_retried",
            },
            "fixtures.condition",
        )
        _choice(
            condition.get("side_effect_kind"),
            _SIDE_EFFECT_KINDS,
            "fixtures.condition.side_effect_kind",
        )
        _bool(
            condition.get("side_effect_dispatched"),
            True,
            "fixtures.condition.side_effect_dispatched",
        )
        if condition.get("output_committed") is not None:
            raise ScenarioValidationError(
                "fixtures.condition.output_committed must remain null while unknown"
            )
        attempts = _integer(
            condition.get("readback_attempts"),
            "fixtures.condition.readback_attempts",
            minimum=1,
        )
        limit = _integer(
            condition.get("readback_limit"),
            "fixtures.condition.readback_limit",
            minimum=1,
        )
        if attempts != limit:
            raise ScenarioValidationError(
                "fixtures.condition readback attempts must exhaust the bounded limit"
            )
        _bool(
            condition.get("reconciliation_exhausted"),
            True,
            "fixtures.condition.reconciliation_exhausted",
        )
        _bool(
            condition.get("blind_retry"),
            False,
            "fixtures.condition.blind_retry",
        )
        _bool(
            condition.get("action_retried"),
            False,
            "fixtures.condition.action_retried",
        )
    elif reason == "loop_detected":
        _only_keys(
            condition,
            {
                "loop_kind",
                "fingerprint",
                "observed",
                "limit",
                "circuit_open",
                "owner_domain",
                "new_action_admitted",
            },
            "fixtures.condition",
        )
        _choice(
            condition.get("loop_kind"),
            _LOOP_KINDS,
            "fixtures.condition.loop_kind",
        )
        _identifier(condition.get("fingerprint"), "fixtures.condition.fingerprint")
        observed = _integer(
            condition.get("observed"), "fixtures.condition.observed", minimum=1
        )
        limit = _integer(condition.get("limit"), "fixtures.condition.limit", minimum=1)
        if observed < limit:
            raise ScenarioValidationError(
                "fixtures.condition.observed must reach the loop limit"
            )
        _bool(
            condition.get("circuit_open"),
            True,
            "fixtures.condition.circuit_open",
        )
        _choice(
            condition.get("owner_domain"),
            _OWNER_DOMAINS,
            "fixtures.condition.owner_domain",
        )
        _bool(
            condition.get("new_action_admitted"),
            False,
            "fixtures.condition.new_action_admitted",
        )
    else:  # pragma: no cover - caller checks the closed vocabulary first
        raise ScenarioValidationError(f"unknown non-Guardian terminal {reason!r}")
    return dict(condition)


def _project_terminal_reason(
    stop_reason: str,
    *,
    owner_domain: str = "general",
    open_material_findings: bool = False,
    boundary_violation: bool = False,
) -> str:
    """Project state-specific stops before falling back to a general terminal."""

    if stop_reason not in TERMINAL_REASONS:
        raise ScenarioValidationError(f"unknown stop reason {stop_reason!r}")
    if owner_domain not in _OWNER_DOMAINS:
        raise ScenarioValidationError(f"unknown stop owner {owner_domain!r}")
    if boundary_violation:
        return "safety_boundary"
    if stop_reason == "budget_exhausted" and owner_domain == "result_review":
        if not open_material_findings:
            raise ScenarioValidationError(
                "result-review budget routing requires open material findings"
            )
        return "completed_with_issues"
    if stop_reason == "loop_detected":
        if owner_domain == "result_review":
            if not open_material_findings:
                raise ScenarioValidationError(
                    "result-review loop routing requires open material findings"
                )
            return "completed_with_issues"
        if owner_domain == "permission_guardian":
            return "blocked_by_guardian"
    if owner_domain != "general":
        raise ScenarioValidationError(
            f"{stop_reason} has no non-general terminal routing for {owner_domain}"
        )
    return stop_reason


def _load_expected(scenario_id: str) -> Mapping[str, Any]:
    try:
        raw = json.loads(_GOLDEN_PATH.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScenarioValidationError(
            f"cannot load non-Guardian terminal golden {_GOLDEN_PATH}: {exc}"
        ) from exc
    root = _mapping(raw, "auto_mode_terminal_contract golden")
    _only_keys(
        root,
        {"schema_version", "contract", "production_state_machine", "cases"},
        "auto_mode_terminal_contract golden",
    )
    if root.get("schema_version") != 1 or root.get("contract") != CONTRACT:
        raise ScenarioValidationError("non-Guardian terminal golden version mismatch")
    if root.get("production_state_machine") is not False:
        raise ScenarioValidationError(
            "non-Guardian terminal golden must declare production_state_machine=false"
        )
    cases = _mapping(root.get("cases"), "auto_mode_terminal_contract golden.cases")
    if set(cases) != set(_SCENARIO_REASONS):
        raise ScenarioValidationError(
            "non-Guardian terminal golden must contain exactly the closed scenario set"
        )
    expected = cases.get(scenario_id)
    if not isinstance(expected, Mapping):
        raise ScenarioValidationError(
            f"non-Guardian terminal golden has no case for {scenario_id!r}"
        )
    _only_keys(
        expected,
        {
            "lane",
            "terminal_reason",
            "event_kinds",
            "specific_event_kinds",
            "digests",
            "event_payload_digests",
            "trace_sha256",
            "terminal_payload",
        },
        f"auto_mode_terminal_contract golden.cases.{scenario_id}",
    )
    trace_sha256 = expected.get("trace_sha256")
    if not isinstance(trace_sha256, str) or not _DIGEST.fullmatch(trace_sha256):
        raise ScenarioValidationError(
            f"auto_mode_terminal_contract golden.cases.{scenario_id}.trace_sha256 "
            "must be a canonical SHA-256 digest"
        )
    return expected


def _case(scenario: Scenario, expected: Mapping[str, Any]) -> _TerminalCase:
    fixtures = _mapping(scenario.fixtures, "fixtures")
    _only_keys(
        fixtures,
        {"contract", "digest_contract", "lane", "outcome", "identity", "condition"},
        "fixtures",
    )
    if fixtures.get("contract") != CONTRACT:
        raise ScenarioValidationError(f"fixtures.contract must be {CONTRACT!r}")
    if fixtures.get("digest_contract") != "canonical-json-sha256-v1":
        raise ScenarioValidationError(
            "fixtures.digest_contract must be 'canonical-json-sha256-v1'"
        )
    if fixtures.get("lane") != LANE:
        raise ScenarioValidationError(f"fixtures.lane must be {LANE!r}")
    reason = _choice(fixtures.get("outcome"), set(TERMINAL_REASONS), "fixtures.outcome")
    if scenario.permissions.noninteractive != "rules_only":
        raise ScenarioValidationError(
            "non-Guardian terminal contract requires permissions.noninteractive='rules_only'"
        )
    if scenario.faults:
        raise ScenarioValidationError(
            "non-Guardian terminal contract does not accept injected decision faults"
        )
    identity = _identity(fixtures)
    condition = _validate_condition(reason, fixtures.get("condition"))
    owner_domain = str(condition.get("owner_domain") or "general")
    projected = _project_terminal_reason(reason, owner_domain=owner_domain)
    if projected != reason:
        raise ScenarioValidationError(
            f"{reason} with owner_domain={owner_domain!r} must project as {projected}"
        )
    case = _TerminalCase(
        reason=reason,
        identity=identity,
        condition=condition,
        identity_digest=_digest(identity),
        condition_digest=_digest(condition),
    )
    if expected.get("lane") != LANE or expected.get("terminal_reason") != reason:
        raise ScenarioValidationError(
            f"scenario {scenario.id!r} lane/terminal does not match its reviewed golden"
        )
    if _SCENARIO_REASONS.get(scenario.id) != reason:
        raise ScenarioValidationError(
            f"scenario {scenario.id!r} does not name its terminal reason"
        )
    computed = {
        "identity_digest": case.identity_digest,
        "condition_digest": case.condition_digest,
    }
    golden_digests = _mapping(expected.get("digests"), "golden digests")
    if dict(golden_digests) != computed:
        raise ScenarioValidationError(
            f"scenario {scenario.id!r} canonical digests do not match the reviewed golden"
        )
    return case


def _terminal_payload(case: _TerminalCase) -> dict[str, Any]:
    return {
        "terminal_reason": case.reason,
        "auto_user_state": None,
        "safety_terminal": False,
        "production_state_machine": False,
        "recoverable": case.recoverable,
        "automatic_resume": False,
        "guardian_terminal": False,
        "user_truth": case.user_truth,
        "recovery_mode": case.recovery_mode,
        "condition": dict(case.condition),
        "condition_digest": case.condition_digest,
    }


def _ordered_events(events: tuple[EventEnvelope, ...]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    previous_ms: int | None = None
    for expected_seq, event in enumerate(events, start=1):
        if event.seq != expected_seq:
            errors.append(
                f"ordered_events: expected seq {expected_seq}, got {event.seq}"
            )
        if event.event_id in seen:
            errors.append(f"ordered_events: duplicate event_id {event.event_id}")
        if event.parent_event_id is not None and event.parent_event_id not in seen:
            errors.append(
                "ordered_events: parent_event_id does not refer to an earlier event"
            )
        if previous_ms is not None and event.monotonic_ms < previous_ms:
            errors.append("ordered_events: monotonic_ms moved backwards")
        seen.add(event.event_id)
        previous_ms = event.monotonic_ms
    return errors


def _evaluate(
    scenario: Scenario,
    *,
    case: _TerminalCase,
    expected: Mapping[str, Any],
    events: tuple[EventEnvelope, ...],
) -> list[str]:
    errors: list[str] = []
    supported = {
        "ordered_events",
        "one_run_terminal",
        "terminal_state_unique",
        "identity_complete",
        "golden_payloads",
        "non_guardian_terminal",
        "recovery_contract",
    }
    unknown = set(scenario.expect.invariants) - supported
    if unknown:
        errors.append(f"unknown invariant(s): {', '.join(sorted(unknown))}")
    if scenario.expect.terminal_reason != case.reason:
        errors.append("terminal_reason: scenario expectation drifted")
    if scenario.expect.model_attempts != 0:
        errors.append("model_attempts: terminal contract never calls a model")

    kinds = tuple(event.kind for event in events)
    specific = tuple(
        event.payload.get("event_names", {}).get("specific") for event in events
    )
    if scenario.expect.event_kinds and kinds != scenario.expect.event_kinds:
        errors.append("event_kinds: scenario expectation drifted")
    if kinds != tuple(expected.get("event_kinds") or ()):
        errors.append("event_kinds: reviewed golden drifted")
    if specific != tuple(expected.get("specific_event_kinds") or ()):
        errors.append("specific_event_kinds: reviewed golden drifted")
    actual_payload_digests = [_digest(event.payload) for event in events]
    golden_payload_digests = expected.get("event_payload_digests")
    if (
        not isinstance(golden_payload_digests, list)
        or any(
            not isinstance(value, str) or not _DIGEST.fullmatch(value)
            for value in golden_payload_digests
        )
        or actual_payload_digests != golden_payload_digests
    ):
        errors.append("event_payload_digests: reviewed golden payloads drifted")
    actual_trace_sha256 = hashlib.sha256(normalized_trace_bytes(events)).hexdigest()
    if actual_trace_sha256 != expected.get("trace_sha256"):
        errors.append("trace_sha256: reviewed golden trace drifted")
    if "ordered_events" in scenario.expect.invariants:
        errors.extend(_ordered_events(events))

    for event in events:
        if (
            event.run_id != case.identity["auto_run_id"]
            or event.root_frame_id != case.identity["root_frame_id"]
            or event.turn_id != case.identity["turn_id"]
            or event.payload.get("identity") != dict(case.identity)
        ):
            errors.append("identity_complete: event identity drifted")
        names = event.payload.get("event_names")
        if not isinstance(names, Mapping) or names.get("canonical") != event.kind:
            errors.append("event_names: canonical event identity drifted")

    terminals = [event for event in events if event.status == "terminal"]
    if len(terminals) != 1 or terminals[0].kind != "auto_run_terminal":
        errors.append("one_run_terminal: expected one auto_run_terminal")
    if case.reason not in TERMINAL_REASONS:
        errors.append(
            "terminal_state_unique: terminal reason is outside the vocabulary"
        )
    if len(terminals) == 1:
        terminal = terminals[0]
        projection = dict(terminal.payload)
        projection.pop("identity", None)
        projection.pop("event_names", None)
        golden_payload = _mapping(expected.get("terminal_payload"), "golden payload")
        if projection != dict(golden_payload):
            errors.append("golden_payloads: exact terminal projection drifted")
        if projection != _terminal_payload(case):
            errors.append("terminal_state_unique: computed terminal projection drifted")
        if projection.get("condition_digest") != _digest(case.condition):
            errors.append("golden_payloads: condition digest drifted")
        forbidden = {
            "audit_id",
            "audit_request_digest",
            "assessment_digest",
            "subject_kind",
            "subject_entity_kind",
        }
        if forbidden & set(projection):
            errors.append("non_guardian_terminal: audit fields leaked into projection")

    forbidden_events = {
        "auto_audit_started",
        "auto_audit_completed",
        "action_authorized",
        "repair_started",
    }
    if forbidden_events & set(kinds):
        errors.append("non_guardian_terminal: Reviewer/Guardian/Repair event leaked")
    if not events or events[0].payload.get("production_state_machine") is not False:
        errors.append("golden_payloads: contract-only provenance is missing")
    return errors


def run_auto_mode_terminal_contract(
    scenario: Scenario,
    *,
    offline: bool = True,
    clock: FakeClock | None = None,
    uuid_factory: FakeUUIDFactory | None = None,
) -> ScenarioResult:
    """Replay one proposed non-Guardian terminal contract deterministically."""

    if scenario.surface != SURFACE:
        raise ScenarioValidationError(
            f"run_auto_mode_terminal_contract requires surface {SURFACE!r}"
        )
    if offline and not scenario.is_offline:
        raise ValueError(
            f"scenario {scenario.id!r} is not eligible for the offline tier"
        )
    expected = _load_expected(scenario.id)
    case = _case(scenario, expected)
    recorder = _Recorder(
        identity=case.identity,
        clock=clock or FakeClock(),
        uuid_factory=uuid_factory or FakeUUIDFactory(),
    )
    recorder.emit(
        "auto_run_started",
        specific_kind="run_started",
        phase="lifecycle",
        status="running",
        payload={
            "scenario_id": scenario.id,
            "surface": SURFACE,
            "contract": CONTRACT,
            "contract_adapter": True,
            "production_state_machine": False,
            "identity_digest": case.identity_digest,
        },
    )
    recorder.emit(
        "auto_run_terminal",
        specific_kind="run_finished",
        phase="lifecycle",
        status="terminal",
        payload=_terminal_payload(case),
    )
    events = tuple(recorder.events)
    errors = tuple(_evaluate(scenario, case=case, expected=expected, events=events))
    return ScenarioResult(
        scenario_id=scenario.id,
        passed=not errors,
        terminal_reason=case.reason,
        model_attempts=0,
        events=events,
        errors=errors,
        normalized=normalized_trace_bytes(events),
    )


__all__ = [
    "CONTRACT",
    "SURFACE",
    "TERMINAL_REASONS",
    "run_auto_mode_terminal_contract",
]
