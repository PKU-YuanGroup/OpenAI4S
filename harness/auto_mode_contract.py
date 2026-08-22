"""Deterministic Stage 0 contract replay for proposed Auto Mode states.

This is deliberately a contract adapter, not a production-state-machine
adapter. It fixes identities, canonical digests, retry bounds, event names and
fail-closed meaning that the integrated production stages must continue to
satisfy. Every trace says
``production_state_machine: false``; the CLI labels it ``CONTRACT_PASS``.

The Guardian lane begins only after deterministic policy returned ``ask``.
A hard policy/sandbox/secret/path boundary is not a Guardian verdict. Audit or
digest-integrity failures use the distinct safety-boundary terminal, which is
not one of the five Auto user states.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .faults import FakeClock, FakeUUIDFactory, FaultSchedule, InjectedFault
from .normalize import normalized_trace_bytes
from .runner import ScenarioResult
from .schema import SCHEMA_VERSION, EventEnvelope, Scenario, ScenarioValidationError

SURFACE = "auto_mode_contract"
CONTRACT = "stage0_auto_mode_v3"

_RESULT_REVIEW = "result_review"
_PERMISSION_GUARDIAN = "permission_guardian"
_LANES = {_RESULT_REVIEW, _PERMISSION_GUARDIAN}
_SUBJECT_KIND = {
    _RESULT_REVIEW: "result_review",
    _PERMISSION_GUARDIAN: "permission_review",
}
_SUBJECT_ENTITY_KIND = {
    _RESULT_REVIEW: "candidate_evidence_snapshot",
    _PERMISSION_GUARDIAN: "approval_action",
}
_OUTCOMES = {
    _RESULT_REVIEW: {"pass", "issues"},
    _PERMISSION_GUARDIAN: {
        "allow_once_replay",
        "allow_once_variant",
        "deny",
        "deny_circuit",
    },
}
_FAULT_POINT = {
    _RESULT_REVIEW: "scientific_review.before_decision",
    _PERMISSION_GUARDIAN: "permission_guardian.before_decision",
}
_RETRYABLE_DECISION_FAULTS = {"timeout", "parse_failure"}
_SAFETY_FAULTS = {"audit_failure", "hash_mismatch"}
_FAIL_CLOSED_FAULTS = _RETRYABLE_DECISION_FAULTS | _SAFETY_FAULTS
_AUTO_USER_STATES = (
    "candidate",
    "verified",
    "completed_with_issues",
    "review_unavailable",
    "blocked_by_guardian",
)
_AUTO_TERMINALS = set(_AUTO_USER_STATES[1:])
_ALL_TERMINALS = _AUTO_TERMINALS | {"safety_boundary"}
_DENIAL_CONSECUTIVE_LIMIT = 3
_DENIAL_WINDOW_SIZE = 50
_DENIAL_WINDOW_LIMIT = 10
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_GOLDEN_PATH = (
    Path(__file__).resolve().parent
    / "golden_traces"
    / "v1"
    / "auto_mode_contract_expected.json"
)


@dataclass(frozen=True)
class _ContractCase:
    lane: str
    outcome: str
    identity: Mapping[str, str]
    audit_id: str
    producer_id: str | None
    reviewer_id: str | None
    candidate: Mapping[str, Any] | None
    evidence_snapshot: Mapping[str, Any] | None
    runtime_evidence_snapshot: Mapping[str, Any] | None
    review_policy: Mapping[str, Any] | None
    material_findings: tuple[Mapping[str, Any], ...]
    canonical_action: Mapping[str, Any] | None
    runtime_action: Mapping[str, Any] | None
    one_shot_capability: Mapping[str, Any] | None
    denial_history: tuple[str, ...]
    denial_circuit: Mapping[str, Any] | None
    identity_digest: str
    candidate_digest: str | None
    evidence_digest: str | None
    runtime_evidence_digest: str | None
    artifact_set_digest: str | None
    review_request_policy_digest: str | None
    action_digest: str | None
    runtime_action_digest: str | None
    capability_digest: str | None
    denial_history_digest: str | None
    audit_request_digest: str

    @property
    def subject_kind(self) -> str:
        return _SUBJECT_KIND[self.lane]

    @property
    def subject_entity_kind(self) -> str:
        return _SUBJECT_ENTITY_KIND[self.lane]


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
        specific_kind: str | None = None,
        phase: str,
        status: str,
        payload: Mapping[str, Any] | None = None,
    ) -> EventEnvelope:
        previous = self.events[-1].event_id if self.events else None
        body = dict(payload or {})
        body["identity"] = dict(self.identity)
        body["event_names"] = {
            "canonical": kind,
            "specific": specific_kind or kind,
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
        self.clock.advance_ms(1)
        return event


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ScenarioValidationError(f"{where} must be a JSON object")
    return value


def _only_keys(value: Mapping[str, Any], allowed: set[str], where: str) -> None:
    extras = sorted(set(value) - allowed)
    missing = sorted(allowed - set(value))
    if extras or missing:
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extras:
            details.append("unsupported " + ", ".join(extras))
        raise ScenarioValidationError(f"{where}: {'; '.join(details)}")


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScenarioValidationError(f"{where} must be a non-empty string")
    return value.strip()


def _identifier(value: Any, where: str) -> str:
    result = _text(value, where)
    if not _IDENT.fullmatch(result):
        raise ScenarioValidationError(f"{where} is not a canonical identifier")
    return result


def _canonical_bytes(value: Any, where: str) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ScenarioValidationError(
            f"{where} cannot be canonicalized as strict JSON: {exc}"
        ) from exc
    return encoded.encode("utf-8")


def _digest(value: Any, where: str) -> str:
    return hashlib.sha256(_canonical_bytes(value, where)).hexdigest()


def _identity(fixtures: Mapping[str, Any]) -> dict[str, str]:
    identity = _mapping(fixtures.get("identity"), "fixtures.identity")
    keys = {
        "auto_run_id",
        "root_frame_id",
        "branch_id",
        "turn_id",
        "execution_id",
    }
    _only_keys(identity, keys, "fixtures.identity")
    return {
        key: _identifier(identity.get(key), f"fixtures.identity.{key}")
        for key in sorted(keys)
    }


def _artifact_versions(candidate: Mapping[str, Any]) -> list[dict[str, str]]:
    raw = candidate.get("artifact_versions")
    if not isinstance(raw, list) or not raw:
        raise ScenarioValidationError(
            "fixtures.candidate.artifact_versions must be a non-empty array"
        )
    versions: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        version = _mapping(item, f"fixtures.candidate.artifact_versions[{index}]")
        _only_keys(
            version,
            {"version_id", "sha256"},
            f"fixtures.candidate.artifact_versions[{index}]",
        )
        version_id = _identifier(
            version.get("version_id"),
            f"fixtures.candidate.artifact_versions[{index}].version_id",
        )
        sha256 = _text(
            version.get("sha256"),
            f"fixtures.candidate.artifact_versions[{index}].sha256",
        )
        if not _DIGEST.fullmatch(sha256):
            raise ScenarioValidationError(
                f"fixtures.candidate.artifact_versions[{index}].sha256 must be "
                "a lowercase SHA-256 digest"
            )
        if version_id in seen:
            raise ScenarioValidationError(
                "fixtures.candidate.artifact_versions contains a duplicate version_id"
            )
        seen.add(version_id)
        versions.append({"version_id": version_id, "sha256": sha256})
    return versions


def _string_list(value: Any, where: str, *, nonempty: bool = True) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        suffix = "a non-empty" if nonempty else "an"
        raise ScenarioValidationError(f"{where} must be {suffix} array")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_identifier(item, f"{where}[{index}]"))
    if len(result) != len(set(result)):
        raise ScenarioValidationError(f"{where} must not contain duplicates")
    return result


def _validate_evidence_snapshot(
    raw: Any,
    *,
    candidate_id: str,
    versions: list[dict[str, str]],
) -> tuple[Mapping[str, Any], set[str]]:
    evidence = _mapping(raw, "fixtures.evidence_snapshot")
    _only_keys(
        evidence,
        {
            "snapshot_id",
            "candidate_id",
            "artifact_versions",
            "provenance_version_ids",
            "evidence_refs",
            "complete",
            "frozen",
        },
        "fixtures.evidence_snapshot",
    )
    _identifier(evidence.get("snapshot_id"), "fixtures.evidence_snapshot.snapshot_id")
    if evidence.get("candidate_id") != candidate_id:
        raise ScenarioValidationError(
            "evidence snapshot must bind the canonical candidate_id"
        )
    if evidence.get("artifact_versions") != versions:
        raise ScenarioValidationError(
            "evidence snapshot must bind the exact candidate Artifact versions"
        )
    provenance = _string_list(
        evidence.get("provenance_version_ids"),
        "fixtures.evidence_snapshot.provenance_version_ids",
    )
    if evidence.get("complete") is not True:
        raise ScenarioValidationError("evidence snapshot must be complete")
    if evidence.get("frozen") is not True:
        raise ScenarioValidationError("evidence snapshot must be frozen")

    refs = evidence.get("evidence_refs")
    if not isinstance(refs, list) or not refs:
        raise ScenarioValidationError(
            "fixtures.evidence_snapshot.evidence_refs must be a non-empty array"
        )
    version_hashes = {row["version_id"]: row["sha256"] for row in versions}
    if set(version_hashes) & set(provenance):
        raise ScenarioValidationError(
            "evidence snapshot Artifact and provenance version IDs must be disjoint"
        )
    ref_ids: set[str] = set()
    resolved_source_ids: list[str] = []
    for index, item in enumerate(refs):
        where = f"fixtures.evidence_snapshot.evidence_refs[{index}]"
        ref = _mapping(item, where)
        _only_keys(ref, {"ref_id", "source_kind", "source_id", "sha256"}, where)
        ref_id = _identifier(ref.get("ref_id"), f"{where}.ref_id")
        if ref_id in ref_ids:
            raise ScenarioValidationError(
                "fixtures.evidence_snapshot.evidence_refs contains duplicate ref_id"
            )
        ref_ids.add(ref_id)
        source_kind = ref.get("source_kind")
        source_id = _identifier(ref.get("source_id"), f"{where}.source_id")
        resolved_source_ids.append(source_id)
        if source_kind == "artifact_version":
            expected_hash = version_hashes.get(source_id)
            if expected_hash is None or ref.get("sha256") != expected_hash:
                raise ScenarioValidationError(
                    f"{where} must resolve to an exact candidate Artifact version"
                )
        elif source_kind == "provenance_version":
            if source_id not in provenance or ref.get("sha256") is not None:
                raise ScenarioValidationError(
                    f"{where} must resolve to a declared provenance version"
                )
        else:
            raise ScenarioValidationError(
                f"{where}.source_kind must be artifact_version or provenance_version"
            )
    declared_source_ids = set(version_hashes) | set(provenance)
    if set(resolved_source_ids) != declared_source_ids or len(
        resolved_source_ids
    ) != len(declared_source_ids):
        raise ScenarioValidationError(
            "complete evidence snapshot must reference every Artifact and provenance "
            "version exactly once"
        )
    return evidence, ref_ids


def _validate_review_policy(raw: Any, *, outcome: str) -> Mapping[str, Any]:
    policy = _mapping(raw, "fixtures.review_policy")
    _only_keys(
        policy,
        {
            "review_mode",
            "auto_fix_enabled",
            "auto_fix_budget_exhausted",
            "termination_basis",
        },
        "fixtures.review_policy",
    )
    if policy.get("review_mode") != "independent_read_only":
        raise ScenarioValidationError(
            "fixtures.review_policy.review_mode must be independent_read_only"
        )
    auto_fix_enabled = policy.get("auto_fix_enabled")
    if not isinstance(auto_fix_enabled, bool):
        raise ScenarioValidationError(
            "fixtures.review_policy.auto_fix_enabled must be a boolean"
        )
    exhausted = policy.get("auto_fix_budget_exhausted")
    if not isinstance(exhausted, bool):
        raise ScenarioValidationError(
            "fixtures.review_policy.auto_fix_budget_exhausted must be a boolean"
        )
    basis = policy.get("termination_basis")
    if outcome == "pass":
        if exhausted or basis != "no_open_material_findings":
            raise ScenarioValidationError(
                "pass requires no_open_material_findings with an unexhausted budget"
            )
    elif auto_fix_enabled:
        if not exhausted or basis != "auto_fix_budget_exhausted":
            raise ScenarioValidationError(
                "Auto Fix issues cannot terminate before its budget is exhausted"
            )
    elif exhausted or basis != "review_only_no_repair":
        raise ScenarioValidationError(
            "review_only issues require review_only_no_repair without budget exhaustion"
        )
    return policy


def _result_review_mode(policy: Mapping[str, Any]) -> str:
    return "auto_fix" if policy["auto_fix_enabled"] else "review_only"


def _review_request_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    """Return only policy facts known before an independent review runs.

    Outcome-derived facts such as budget exhaustion and termination basis are
    response-side evidence. Binding them into the request would let a candidate
    pre-commit the Reviewer's answer and turn the audit into circular proof.
    """

    return {
        "schema_version": 1,
        "review_mode": policy["review_mode"],
        "result_review_mode": _result_review_mode(policy),
        "auto_fix_enabled": policy["auto_fix_enabled"],
    }


def _validate_material_findings(
    raw: Any,
    *,
    outcome: str,
    evidence_ref_ids: set[str],
) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(raw, list):
        raise ScenarioValidationError("fixtures.material_findings must be an array")
    findings: list[Mapping[str, Any]] = []
    fingerprints: set[str] = set()
    for index, item in enumerate(raw):
        where = f"fixtures.material_findings[{index}]"
        finding = _mapping(item, where)
        _only_keys(
            finding,
            {"fingerprint", "status", "severity", "evidence_refs", "summary"},
            where,
        )
        fingerprint = _identifier(finding.get("fingerprint"), f"{where}.fingerprint")
        if fingerprint in fingerprints:
            raise ScenarioValidationError(
                "fixtures.material_findings contains duplicate fingerprint"
            )
        fingerprints.add(fingerprint)
        if finding.get("status") != "open" or finding.get("severity") != "material":
            raise ScenarioValidationError(
                f"{where} must describe an open material finding"
            )
        refs = _string_list(finding.get("evidence_refs"), f"{where}.evidence_refs")
        dangling = sorted(set(refs) - evidence_ref_ids)
        if dangling:
            raise ScenarioValidationError(
                f"{where}.evidence_refs contains unresolved ref(s): {', '.join(dangling)}"
            )
        _text(finding.get("summary"), f"{where}.summary")
        findings.append(finding)
    if outcome == "pass" and findings:
        raise ScenarioValidationError("pass cannot contain an open material finding")
    if outcome == "issues" and not findings:
        raise ScenarioValidationError(
            "issues requires at least one structured open material finding"
        )
    return tuple(findings)


def _validate_fault_contract(scenario: Scenario, lane: str) -> bool:
    if len(scenario.faults) > 2:
        raise ScenarioValidationError(
            "auto_mode_contract permits at most one retry (two decision attempts)"
        )
    allowed_point = _FAULT_POINT[lane]
    occurrences: set[int] = set()
    safety_faults = 0
    for fault in scenario.faults:
        if fault.point != allowed_point:
            raise ScenarioValidationError(
                f"auto_mode_contract fault point for {lane} must be {allowed_point!r}"
            )
        if fault.kind not in _FAIL_CLOSED_FAULTS:
            raise ScenarioValidationError(
                "auto_mode_contract fault kind must be timeout, parse_failure, "
                "audit_failure, or hash_mismatch"
            )
        expected_retryable = fault.kind in _RETRYABLE_DECISION_FAULTS
        if fault.retryable is not expected_retryable:
            required = "true" if expected_retryable else "false"
            raise ScenarioValidationError(
                f"auto_mode_contract fault {fault.kind!r} retryable must be {required}"
            )
        if fault.occurrence > 2 or fault.occurrence in occurrences:
            raise ScenarioValidationError(
                "decision faults must uniquely target attempt one or two"
            )
        occurrences.add(fault.occurrence)
        if fault.kind in _SAFETY_FAULTS:
            safety_faults += 1
            if fault.occurrence != 1:
                raise ScenarioValidationError(
                    "audit/hash integrity faults must fail on the first attempt"
                )
    if safety_faults and len(scenario.faults) != 1:
        raise ScenarioValidationError(
            "audit/hash integrity failure cannot be combined with a retry fault"
        )
    if len(scenario.faults) == 2 and occurrences != {1, 2}:
        raise ScenarioValidationError(
            "two decision faults must target attempts one and two"
        )
    return any(fault.kind == "hash_mismatch" for fault in scenario.faults)


def _assessment(
    case: _ContractCase,
    *,
    attempt: int,
    verdict: str | None,
    decision: str | None,
    findings: list[Mapping[str, Any]],
    risk: Mapping[str, Any],
    authorization: Mapping[str, Any],
    outcome: str,
    rationale: str,
    failure_kind: str | None,
    audit_durable: bool,
    retry_scheduled: bool,
) -> tuple[dict[str, Any], str]:
    record = {
        "schema_version": 1,
        "audit_request_digest": case.audit_request_digest,
        "subject_kind": case.subject_kind,
        "subject_entity_kind": case.subject_entity_kind,
        "attempt": attempt,
        "verdict": verdict,
        "decision": decision,
        "findings": findings,
        "risk": dict(risk),
        "authorization": dict(authorization),
        "outcome": outcome,
        "rationale": rationale,
        "failure_kind": failure_kind,
        "audit_durable": audit_durable,
        "retry_scheduled": retry_scheduled,
    }
    return record, _digest(record, "audit completion assessment")


def _completion_payload(
    case: _ContractCase,
    assessment: Mapping[str, Any],
    assessment_digest: str,
) -> dict[str, Any]:
    authorization = _mapping(
        assessment.get("authorization"), "audit completion authorization"
    )
    return {
        "attempt": assessment["attempt"],
        "audit_id": case.audit_id,
        "audit_request_digest": case.audit_request_digest,
        "assessment": dict(assessment),
        "assessment_digest": assessment_digest,
        "subject_kind": case.subject_kind,
        "subject_entity_kind": case.subject_entity_kind,
        "verdict": assessment["verdict"],
        "decision": assessment["decision"],
        "findings": assessment["findings"],
        "risk": assessment["risk"],
        "authorization": dict(authorization),
        "outcome": assessment["outcome"],
        "rationale": assessment["rationale"],
        "failure_kind": assessment["failure_kind"],
        "audit_durable": assessment["audit_durable"],
        "retry_scheduled": assessment["retry_scheduled"],
        "action_authorized": authorization["action_authorized"],
        "action_executed": authorization["action_executed"],
        "standing_allow_created": authorization["standing_allow_created"],
        "infra_breaker_open": authorization["infra_breaker_open"],
        "denial_circuit_open": authorization["denial_circuit_open"],
        "fail_closed": True,
    }


def _authorization(
    *,
    action_authorized: bool = False,
    action_executed: bool = False,
    infra_breaker_open: bool = False,
    denial_circuit_open: bool = False,
) -> dict[str, Any]:
    return {
        "action_authorized": action_authorized,
        "action_executed": action_executed,
        "standing_allow_created": False,
        "infra_breaker_open": infra_breaker_open,
        "denial_circuit_open": denial_circuit_open,
    }


def _validate_action(raw: Any, where: str) -> Mapping[str, Any]:
    action = _mapping(raw, where)
    action_keys = {"schema_version", "action_kind", "target", "parameters", "risk"}
    _only_keys(action, action_keys, where)
    if action.get("schema_version") != 1:
        raise ScenarioValidationError(f"{where}.schema_version must be 1")
    action_kind = _text(action.get("action_kind"), f"{where}.action_kind")
    if action.get("action_kind") != action_kind:
        raise ScenarioValidationError(f"{where}.action_kind must be canonical")
    _mapping(action.get("target"), f"{where}.target")
    _mapping(action.get("parameters"), f"{where}.parameters")
    _mapping(action.get("risk"), f"{where}.risk")
    _canonical_bytes(action, where)
    return action


def _validate_guardian_allowlist(action: Mapping[str, Any]) -> None:
    """Validate the Stage 0 low-risk action class independently of its label."""

    where = "fixtures.canonical_action"
    if action.get("action_kind") != "file_write":
        raise ScenarioValidationError(
            "Guardian allow_once contract permits only allowlisted file_write"
        )
    target = _mapping(action.get("target"), f"{where}.target")
    _only_keys(target, {"scope", "relative_path"}, f"{where}.target")
    relative_path = _text(target.get("relative_path"), f"{where}.target.relative_path")
    normalized = PurePosixPath(relative_path)
    if (
        target.get("scope") != "workspace"
        or normalized.is_absolute()
        or ".." in normalized.parts
        or len(normalized.parts) < 2
        or normalized.parts[0] != "results"
        or str(normalized) != relative_path
    ):
        raise ScenarioValidationError(
            "Guardian allow_once file_write must target a canonical results/ path "
            "inside the workspace"
        )
    parameters = _mapping(action.get("parameters"), f"{where}.parameters")
    _only_keys(
        parameters,
        {"mode", "content_sha256"},
        f"{where}.parameters",
    )
    if parameters.get("mode") != "replace" or not _DIGEST.fullmatch(
        str(parameters.get("content_sha256", ""))
    ):
        raise ScenarioValidationError(
            "Guardian allow_once file_write requires replace mode and exact content hash"
        )
    risk = _mapping(action.get("risk"), f"{where}.risk")
    _only_keys(
        risk,
        {
            "risk_level",
            "dangerous",
            "explicit_allowlist",
            "irreversible",
            "external_write",
        },
        f"{where}.risk",
    )
    if (
        risk.get("risk_level") != "low"
        or risk.get("dangerous") is not False
        or risk.get("explicit_allowlist") is not True
        or risk.get("irreversible") is not False
        or risk.get("external_write") is not False
    ):
        raise ScenarioValidationError(
            "Guardian allow_once requires a deterministic low-risk, reversible, "
            "internal action from the sealed allowlist"
        )


def _validate_one_shot_capability(raw: Any) -> Mapping[str, Any]:
    where = "fixtures.one_shot_capability"
    capability = _mapping(raw, where)
    _only_keys(
        capability,
        {"capability_id", "context_generation", "expires_at", "max_uses"},
        where,
    )
    capability_id = _identifier(
        capability.get("capability_id"), f"{where}.capability_id"
    )
    context_generation = _identifier(
        capability.get("context_generation"), f"{where}.context_generation"
    )
    expires_at = _text(capability.get("expires_at"), f"{where}.expires_at")
    try:
        valid_timestamp = bool(_UTC_TIMESTAMP.fullmatch(expires_at))
        if valid_timestamp:
            datetime.strptime(expires_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        valid_timestamp = False
    if not valid_timestamp or capability.get("expires_at") != expires_at:
        raise ScenarioValidationError(
            "fixtures.one_shot_capability.expires_at must be a canonical UTC timestamp"
        )
    max_uses = capability.get("max_uses")
    if max_uses != 1 or isinstance(max_uses, bool):
        raise ScenarioValidationError(
            "fixtures.one_shot_capability.max_uses must be exactly 1"
        )
    return {
        "capability_id": capability_id,
        "context_generation": context_generation,
        "expires_at": expires_at,
        "max_uses": 1,
    }


def _trailing_denials(decisions: list[str] | tuple[str, ...]) -> int:
    count = 0
    for decision in reversed(decisions):
        if decision != "deny":
            break
        count += 1
    return count


def _validate_denial_history(
    raw: Any,
) -> tuple[tuple[str, ...], Mapping[str, Any], str]:
    where = "fixtures.denial_history"
    history = _mapping(raw, where)
    _only_keys(history, {"prior_decisions"}, where)
    decisions = history.get("prior_decisions")
    if not isinstance(decisions, list) or not decisions:
        raise ScenarioValidationError(
            "fixtures.denial_history.prior_decisions must be a non-empty array"
        )
    if len(decisions) >= _DENIAL_WINDOW_SIZE:
        raise ScenarioValidationError(
            "fixtures.denial_history must contain at most 49 prior decisions"
        )
    if any(decision not in {"allow", "deny"} for decision in decisions):
        raise ScenarioValidationError(
            "fixtures.denial_history decisions must be allow or deny"
        )

    prior = tuple(decisions)
    prior_consecutive = _trailing_denials(prior)
    prior_window = prior[-_DENIAL_WINDOW_SIZE:]
    after = prior + ("deny",)
    after_window = after[-_DENIAL_WINDOW_SIZE:]
    consecutive = _trailing_denials(after)
    prior_window_denials = prior_window.count("deny")
    window_denials = after_window.count("deny")
    if (
        prior_consecutive >= _DENIAL_CONSECUTIVE_LIMIT
        or prior_window_denials >= _DENIAL_WINDOW_LIMIT
    ):
        raise ScenarioValidationError(
            "fixtures.denial_history describes a circuit that was already open"
        )
    consecutive_crossed = consecutive >= _DENIAL_CONSECUTIVE_LIMIT
    window_crossed = window_denials >= _DENIAL_WINDOW_LIMIT
    if consecutive_crossed == window_crossed:
        raise ScenarioValidationError(
            "current denial must cross exactly one denial-circuit threshold"
        )
    trigger = "consecutive" if consecutive_crossed else "window"
    history_record = {
        "schema_version": 1,
        "prior_decisions": list(prior),
        "current_decision": "deny",
    }
    metrics = {
        "trigger": trigger,
        "prior_consecutive_denials": prior_consecutive,
        "consecutive_denials": consecutive,
        "prior_window_size": len(prior_window),
        "window_size": len(after_window),
        "prior_window_denials": prior_window_denials,
        "window_denials": window_denials,
        "thresholds": {
            "consecutive_denials": _DENIAL_CONSECUTIVE_LIMIT,
            "window_size": _DENIAL_WINDOW_SIZE,
            "window_denials": _DENIAL_WINDOW_LIMIT,
        },
    }
    return prior, metrics, _digest(history_record, where)


def _load_expected(scenario_id: str) -> Mapping[str, Any]:
    try:
        raw = json.loads(_GOLDEN_PATH.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScenarioValidationError(
            f"cannot load Auto Mode contract golden {_GOLDEN_PATH}: {exc}"
        ) from exc
    root = _mapping(raw, "auto_mode_contract golden")
    _only_keys(
        root,
        {"schema_version", "contract", "production_state_machine", "cases"},
        "auto_mode_contract golden",
    )
    if root.get("schema_version") != 1 or root.get("contract") != CONTRACT:
        raise ScenarioValidationError("Auto Mode contract golden version mismatch")
    if root.get("production_state_machine") is not False:
        raise ScenarioValidationError(
            "Auto Mode contract golden must declare production_state_machine=false"
        )
    cases = _mapping(root.get("cases"), "auto_mode_contract golden.cases")
    expected = cases.get(scenario_id)
    if not isinstance(expected, Mapping):
        raise ScenarioValidationError(
            f"Auto Mode contract golden has no case for {scenario_id!r}"
        )
    normalized = dict(expected)
    _only_keys(
        normalized,
        {
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
        },
        f"auto_mode_contract golden.cases.{scenario_id}",
    )
    trace_sha256 = normalized.get("trace_sha256")
    if not isinstance(trace_sha256, str) or not _DIGEST.fullmatch(trace_sha256):
        raise ScenarioValidationError(
            f"auto_mode_contract golden.cases.{scenario_id}.trace_sha256 "
            "must be a canonical SHA-256 digest"
        )
    terminal_payload = _mapping(
        normalized.get("terminal_payload"),
        f"auto_mode_contract golden.cases.{scenario_id}.terminal_payload",
    )
    terminal_keys = {
        "terminal_reason",
        "auto_user_state",
        "safety_terminal",
        "production_state_machine",
        "recoverable",
    }
    if scenario_id in {
        "auto_mode_guardian_allow_once_replay",
        "auto_mode_guardian_allow_once_variant",
    }:
        terminal_keys.update({"boundary", "stop_reason"})
    elif scenario_id in {
        "auto_mode_guardian_denial_circuit_consecutive",
        "auto_mode_guardian_denial_circuit_window",
    }:
        terminal_keys.update({"denial_circuit_trigger", "stop_reason"})
    _only_keys(
        terminal_payload,
        terminal_keys,
        f"auto_mode_contract golden.cases.{scenario_id}.terminal_payload",
    )
    return normalized


def _validate_expected_digests(
    scenario: Scenario,
    expected: Mapping[str, Any],
    computed: Mapping[str, str],
) -> None:
    golden = _mapping(expected.get("digests"), f"golden {scenario.id}.digests")
    if dict(golden) != dict(computed):
        raise ScenarioValidationError(
            f"scenario {scenario.id!r} canonical digests do not match the reviewed golden"
        )


def _case(scenario: Scenario, expected: Mapping[str, Any]) -> _ContractCase:
    if scenario.permissions.noninteractive != "rules_only":
        raise ScenarioValidationError(
            "auto_mode_contract permissions.noninteractive must be rules_only"
        )
    fixtures = scenario.fixtures
    lane = fixtures.get("lane")
    if not isinstance(lane, str) or lane not in _LANES:
        raise ScenarioValidationError(
            "auto_mode_contract fixtures.lane must be result_review or "
            "permission_guardian"
        )
    outcome = fixtures.get("outcome")
    if not isinstance(outcome, str) or outcome not in _OUTCOMES[lane]:
        choices = ", ".join(sorted(_OUTCOMES[lane]))
        raise ScenarioValidationError(
            f"auto_mode_contract outcome for {lane} must be one of: {choices}"
        )
    if fixtures.get("contract") != CONTRACT:
        raise ScenarioValidationError(
            f"auto_mode_contract fixtures.contract must be {CONTRACT!r}"
        )
    if fixtures.get("digest_contract") != "canonical-json-sha256-v1":
        raise ScenarioValidationError(
            "fixtures.digest_contract must be 'canonical-json-sha256-v1'"
        )
    has_hash_fault = _validate_fault_contract(scenario, lane)
    if lane == _PERMISSION_GUARDIAN and outcome != "deny" and scenario.faults:
        raise ScenarioValidationError(
            "Guardian allow/circuit contract scenarios cannot combine decision faults"
        )
    identity = _identity(fixtures)
    identity_digest = _digest(identity, "fixtures.identity")
    audit_id = _identifier(fixtures.get("audit_id"), "fixtures.audit_id")
    common = {
        "contract",
        "digest_contract",
        "lane",
        "outcome",
        "identity",
        "audit_id",
    }

    candidate: Mapping[str, Any] | None = None
    evidence: Mapping[str, Any] | None = None
    runtime_evidence: Mapping[str, Any] | None = None
    review_policy: Mapping[str, Any] | None = None
    material_findings: tuple[Mapping[str, Any], ...] = ()
    canonical_action: Mapping[str, Any] | None = None
    runtime_action: Mapping[str, Any] | None = None
    one_shot_capability: Mapping[str, Any] | None = None
    denial_history: tuple[str, ...] = ()
    denial_circuit: Mapping[str, Any] | None = None
    producer_id: str | None = None
    reviewer_id: str | None = None
    candidate_digest: str | None = None
    evidence_digest: str | None = None
    runtime_evidence_digest: str | None = None
    artifact_set_digest: str | None = None
    review_request_policy_digest: str | None = None
    action_digest: str | None = None
    runtime_action_digest: str | None = None
    capability_digest: str | None = None
    denial_history_digest: str | None = None

    if lane == _RESULT_REVIEW:
        result_keys = common | {
            "candidate",
            "evidence_snapshot",
            "reviewer_identity",
            "review_policy",
            "material_findings",
        }
        if has_hash_fault:
            result_keys.add("runtime_evidence_snapshot")
        _only_keys(
            fixtures,
            result_keys,
            "auto_mode_contract result_review fixtures",
        )
        candidate = _mapping(fixtures.get("candidate"), "fixtures.candidate")
        _only_keys(
            candidate,
            {"candidate_id", "producer_id", "summary", "artifact_versions"},
            "fixtures.candidate",
        )
        candidate_id = _identifier(
            candidate.get("candidate_id"), "fixtures.candidate.candidate_id"
        )
        producer_id = _identifier(
            candidate.get("producer_id"), "fixtures.candidate.producer_id"
        )
        _text(candidate.get("summary"), "fixtures.candidate.summary")
        versions = _artifact_versions(candidate)
        if list(candidate.get("artifact_versions") or []) != versions:
            raise ScenarioValidationError(
                "fixtures.candidate.artifact_versions must already be canonical"
            )
        evidence, evidence_ref_ids = _validate_evidence_snapshot(
            fixtures.get("evidence_snapshot"),
            candidate_id=candidate_id,
            versions=versions,
        )
        review_policy = _validate_review_policy(
            fixtures.get("review_policy"), outcome=outcome
        )
        material_findings = _validate_material_findings(
            fixtures.get("material_findings"),
            outcome=outcome,
            evidence_ref_ids=evidence_ref_ids,
        )

        reviewer = _mapping(
            fixtures.get("reviewer_identity"), "fixtures.reviewer_identity"
        )
        _only_keys(
            reviewer,
            {
                "reviewer_id",
                "candidate_producer_id",
                "workspace_access",
                "artifact_write_access",
            },
            "fixtures.reviewer_identity",
        )
        reviewer_id = _identifier(
            reviewer.get("reviewer_id"), "fixtures.reviewer_identity.reviewer_id"
        )
        if reviewer.get("candidate_producer_id") != producer_id:
            raise ScenarioValidationError(
                "reviewer identity must name the candidate producer"
            )
        if reviewer_id == producer_id:
            raise ScenarioValidationError(
                "Scientific Reviewer must be independent from the candidate producer"
            )
        if (
            reviewer.get("workspace_access") != "read_only"
            or reviewer.get("artifact_write_access") is not False
        ):
            raise ScenarioValidationError(
                "Scientific Reviewer fixture must be read-only with no Artifact writes"
            )

        candidate_digest = _digest(candidate, "fixtures.candidate")
        evidence_digest = _digest(evidence, "fixtures.evidence_snapshot")
        artifact_set_digest = _digest(versions, "candidate Artifact set")
        review_request_policy_digest = _digest(
            _review_request_policy(review_policy),
            "Scientific Reviewer request policy",
        )
        if has_hash_fault:
            runtime_evidence, _ = _validate_evidence_snapshot(
                fixtures.get("runtime_evidence_snapshot"),
                candidate_id=candidate_id,
                versions=versions,
            )
            runtime_evidence_digest = _digest(
                runtime_evidence, "fixtures.runtime_evidence_snapshot"
            )
            if runtime_evidence_digest == evidence_digest:
                raise ScenarioValidationError(
                    "runtime evidence mutation must change the canonical digest"
                )
        request_record = {
            "schema_version": 1,
            "audit_id": audit_id,
            "subject_kind": "result_review",
            "subject_entity_kind": "candidate_evidence_snapshot",
            "identity_digest": identity_digest,
            "candidate_digest": candidate_digest,
            "evidence_snapshot_digest": evidence_digest,
            "artifact_set_digest": artifact_set_digest,
            "review_request_policy_digest": review_request_policy_digest,
            "reviewer_id": reviewer_id,
            "producer_id": producer_id,
            "review_mode": review_policy["review_mode"],
            "workspace_access": "read_only",
        }
        audit_request_digest = _digest(
            request_record, "Scientific Reviewer audit request"
        )
        computed = {
            "identity_digest": identity_digest,
            "candidate_digest": candidate_digest,
            "evidence_snapshot_digest": evidence_digest,
            "artifact_set_digest": artifact_set_digest,
            "review_request_policy_digest": review_request_policy_digest,
            "audit_request_digest": audit_request_digest,
        }
        if runtime_evidence_digest is not None:
            computed["runtime_evidence_snapshot_digest"] = runtime_evidence_digest
    else:
        guardian_keys = common | {
            "canonical_action",
            "policy_resolution",
            "guardian_identity",
        }
        if has_hash_fault or outcome == "allow_once_variant":
            guardian_keys.add("runtime_action")
        if outcome in {"allow_once_replay", "allow_once_variant"}:
            guardian_keys.add("one_shot_capability")
        if outcome == "deny_circuit":
            guardian_keys.add("denial_history")
        _only_keys(
            fixtures,
            guardian_keys,
            "auto_mode_contract permission_guardian fixtures",
        )
        if fixtures.get("policy_resolution") != "ask":
            raise ScenarioValidationError(
                "Guardian contract begins only after policy_resolution='ask'"
            )
        canonical_action = _validate_action(
            fixtures.get("canonical_action"), "fixtures.canonical_action"
        )
        guardian = _mapping(
            fixtures.get("guardian_identity"), "fixtures.guardian_identity"
        )
        _only_keys(
            guardian,
            {"guardian_id", "standing_allow_policy"},
            "fixtures.guardian_identity",
        )
        reviewer_id = _identifier(
            guardian.get("guardian_id"), "fixtures.guardian_identity.guardian_id"
        )
        if guardian.get("standing_allow_policy") != "never":
            raise ScenarioValidationError(
                "Permission Guardian cannot create standing allow"
            )
        action_digest = _digest(canonical_action, "fixtures.canonical_action")
        if outcome in {"allow_once_replay", "allow_once_variant"}:
            _validate_guardian_allowlist(canonical_action)
        if has_hash_fault or outcome == "allow_once_variant":
            runtime_action = _validate_action(
                fixtures.get("runtime_action"), "fixtures.runtime_action"
            )
            runtime_action_digest = _digest(runtime_action, "fixtures.runtime_action")
            if runtime_action_digest == action_digest:
                raise ScenarioValidationError(
                    "runtime action mutation must change the canonical digest"
                )
        request_record = {
            "schema_version": 1,
            "audit_id": audit_id,
            "subject_kind": "permission_review",
            "subject_entity_kind": "approval_action",
            "identity_digest": identity_digest,
            "action_digest": action_digest,
            "guardian_id": reviewer_id,
            "policy_resolution": "ask",
            "standing_allow_policy": "never",
        }
        audit_request_digest = _digest(
            request_record, "Permission Guardian audit request"
        )
        computed = {
            "identity_digest": identity_digest,
            "action_digest": action_digest,
            "audit_request_digest": audit_request_digest,
        }
        if runtime_action_digest is not None:
            computed["runtime_action_digest"] = runtime_action_digest
        if outcome in {"allow_once_replay", "allow_once_variant"}:
            capability_template = _validate_one_shot_capability(
                fixtures.get("one_shot_capability")
            )
            one_shot_capability = {
                "schema_version": 1,
                "capability_id": capability_template["capability_id"],
                "scope": "once",
                "action_digest": action_digest,
                "audit_id": audit_id,
                "audit_request_digest": audit_request_digest,
                "identity_digest": identity_digest,
                "run_context": dict(identity),
                "context_generation": capability_template["context_generation"],
                "expires_at": capability_template["expires_at"],
                "max_uses": 1,
            }
            capability_digest = _digest(
                one_shot_capability, "bound one-shot capability"
            )
            computed["capability_digest"] = capability_digest
        if outcome == "deny_circuit":
            (
                denial_history,
                denial_circuit,
                denial_history_digest,
            ) = _validate_denial_history(fixtures.get("denial_history"))
            computed["denial_history_digest"] = denial_history_digest

    if expected.get("lane") != lane:
        raise ScenarioValidationError(
            f"scenario {scenario.id!r} lane does not match its reviewed golden"
        )
    _validate_expected_digests(scenario, expected, computed)
    return _ContractCase(
        lane=lane,
        outcome=outcome,
        identity=identity,
        audit_id=audit_id,
        producer_id=producer_id,
        reviewer_id=reviewer_id,
        candidate=candidate,
        evidence_snapshot=evidence,
        runtime_evidence_snapshot=runtime_evidence,
        review_policy=review_policy,
        material_findings=material_findings,
        canonical_action=canonical_action,
        runtime_action=runtime_action,
        one_shot_capability=one_shot_capability,
        denial_history=denial_history,
        denial_circuit=denial_circuit,
        identity_digest=identity_digest,
        candidate_digest=candidate_digest,
        evidence_digest=evidence_digest,
        runtime_evidence_digest=runtime_evidence_digest,
        artifact_set_digest=artifact_set_digest,
        review_request_policy_digest=review_request_policy_digest,
        action_digest=action_digest,
        runtime_action_digest=runtime_action_digest,
        capability_digest=capability_digest,
        denial_history_digest=denial_history_digest,
        audit_request_digest=audit_request_digest,
    )


def _fault_payload(fault: InjectedFault, case: _ContractCase) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "point": fault.point,
        "error_kind": fault.kind,
        "message": fault.message,
        "retryable": fault.kind in _RETRYABLE_DECISION_FAULTS,
        "fail_closed": True,
    }
    if fault.kind == "hash_mismatch":
        if case.lane == _PERMISSION_GUARDIAN:
            expected = case.action_digest
            observed = case.runtime_action_digest
        else:
            expected = case.evidence_digest
            observed = case.runtime_evidence_digest
        assert expected is not None and observed is not None
        payload.update(
            {
                "digest_binding": (
                    "exact_action"
                    if case.lane == _PERMISSION_GUARDIAN
                    else "immutable_evidence"
                ),
                "expected_digest": expected,
                "observed_digest": observed,
            }
        )
    return payload


def _authorization_receipt(
    case: _ContractCase,
    *,
    attempt_kind: str,
    attempted_action_digest: str,
    uses_before: int,
    uses_after: int,
    capability_consumed: bool,
    action_authorized: bool,
    action_executed: bool,
    execution_count: int,
    rejection_reason: str | None,
) -> tuple[dict[str, Any], str]:
    assert case.action_digest is not None
    assert case.capability_digest is not None
    receipt = {
        "schema_version": 1,
        "audit_id": case.audit_id,
        "capability_digest": case.capability_digest,
        "attempt_kind": attempt_kind,
        "expected_action_digest": case.action_digest,
        "attempted_action_digest": attempted_action_digest,
        "max_uses": 1,
        "uses_before": uses_before,
        "uses_after": uses_after,
        "capability_consumed": capability_consumed,
        "action_authorized": action_authorized,
        "action_executed": action_executed,
        "execution_count": execution_count,
        "standing_allow_created": False,
        "rejection_reason": rejection_reason,
    }
    return receipt, _digest(receipt, "one-shot authorization receipt")


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
    case: _ContractCase,
    expected: Mapping[str, Any],
    terminal_reason: str,
    events: tuple[EventEnvelope, ...],
    faults: FaultSchedule,
) -> list[str]:
    errors: list[str] = []
    expect = scenario.expect
    if expect.terminal_reason != terminal_reason:
        errors.append(
            f"terminal_reason: scenario expected {expect.terminal_reason!r}, "
            f"got {terminal_reason!r}"
        )
    if expected.get("terminal_reason") != terminal_reason:
        errors.append(
            f"golden terminal_reason: expected {expected.get('terminal_reason')!r}, "
            f"got {terminal_reason!r}"
        )
    if expect.model_attempts != 0:
        errors.append(
            "model_attempts: contract adapter never calls a model; expected must be 0"
        )
    actual_kinds = tuple(event.kind for event in events)
    if expect.event_kinds and actual_kinds != expect.event_kinds:
        errors.append(
            f"event_kinds: scenario expected {expect.event_kinds!r}, "
            f"got {actual_kinds!r}"
        )
    golden_kinds = expected.get("event_kinds")
    if not isinstance(golden_kinds, list) or actual_kinds != tuple(golden_kinds):
        errors.append(
            f"event_kinds: reviewed golden expected {golden_kinds!r}, "
            f"got {actual_kinds!r}"
        )
    actual_specific_kinds = tuple(
        event.payload.get("event_names", {}).get("specific") for event in events
    )
    golden_specific_kinds = expected.get("specific_event_kinds")
    if not isinstance(golden_specific_kinds, list) or actual_specific_kinds != tuple(
        golden_specific_kinds
    ):
        errors.append(
            "specific_event_kinds: reviewed golden expected "
            f"{golden_specific_kinds!r}, got {actual_specific_kinds!r}"
        )

    actual_payload_digests = [
        _digest(event.payload, f"emitted {event.kind} payload") for event in events
    ]
    golden_payload_digests = expected.get("event_payload_digests")
    if (
        not isinstance(golden_payload_digests, list)
        or any(
            not isinstance(value, str) or not _DIGEST.fullmatch(value)
            for value in golden_payload_digests
        )
        or actual_payload_digests != golden_payload_digests
    ):
        errors.append(
            "event_payload_digests: reviewed golden expected "
            f"{golden_payload_digests!r}, got {actual_payload_digests!r}"
        )
    actual_trace_sha256 = hashlib.sha256(normalized_trace_bytes(events)).hexdigest()
    if actual_trace_sha256 != expected.get("trace_sha256"):
        errors.append(
            "trace_sha256: reviewed golden expected "
            f"{expected.get('trace_sha256')!r}, got {actual_trace_sha256!r}"
        )

    supported = {
        "ordered_events",
        "one_run_terminal",
        "candidate_nonterminal",
        "terminal_state_unique",
        "fail_closed",
        "binding_mismatch",
        "identity_complete",
        "reviewer_independent",
        "retry_bounded",
        "golden_payloads",
    }
    unknown = set(expect.invariants) - supported
    if unknown:
        errors.append(f"unknown invariant(s): {', '.join(sorted(unknown))}")
    if "ordered_events" in expect.invariants:
        errors.extend(_ordered_events(events))

    for event in events:
        if event.run_id != case.identity["auto_run_id"]:
            errors.append("identity_complete: run_id drifted")
        if event.root_frame_id != case.identity["root_frame_id"]:
            errors.append("identity_complete: root_frame_id drifted")
        if event.turn_id != case.identity["turn_id"]:
            errors.append("identity_complete: turn_id drifted")
        if event.payload.get("identity") != dict(case.identity):
            errors.append(
                "identity_complete: payload identity is incomplete or drifted"
            )
        names = event.payload.get("event_names")
        if not isinstance(names, Mapping) or names.get("canonical") != event.kind:
            errors.append(
                "event_names: canonical/specific names must share one event_id"
            )

    audit_events = [
        event
        for event in events
        if event.kind in {"auto_audit_started", "auto_audit_completed"}
    ]
    for event in audit_events:
        if event.payload.get("audit_id") != case.audit_id:
            errors.append("audit_id: started/completed audit correlation drifted")
        if (
            event.payload.get("subject_kind") != case.subject_kind
            or event.payload.get("subject_entity_kind") != case.subject_entity_kind
        ):
            errors.append("audit subject_kind/entity_kind drifted")
        if event.payload.get("audit_request_digest") != case.audit_request_digest:
            errors.append("audit request digest drifted")

    audit_starts = [
        event for event in audit_events if event.kind == "auto_audit_started"
    ]
    completions = [event for event in events if event.kind == "auto_audit_completed"]
    if case.lane == _RESULT_REVIEW:
        for start in audit_starts:
            payload = start.payload
            if payload.get("review_request_policy_digest") != (
                case.review_request_policy_digest
            ) or any(
                key in payload
                for key in (
                    "findings",
                    "material_findings_digest",
                    "termination_basis",
                    "verdict",
                )
            ):
                errors.append(
                    "review_request_truth: audit start leaked response-side facts"
                )
    attempts = {event.payload.get("attempt") for event in (*audit_starts, *completions)}
    for attempt in attempts:
        starts = [
            event for event in audit_starts if event.payload.get("attempt") == attempt
        ]
        finishes = [
            event for event in completions if event.payload.get("attempt") == attempt
        ]
        if len(starts) != 1 or len(finishes) != 1:
            errors.append(
                "audit_pair: each attempt requires exactly one started/completed pair"
            )
            continue
        for key in (
            "audit_id",
            "audit_request_digest",
            "subject_kind",
            "subject_entity_kind",
        ):
            if starts[0].payload.get(key) != finishes[0].payload.get(key):
                errors.append(f"audit_pair: {key} drifted within attempt {attempt!r}")

    actual_assessment_digests: list[str] = []
    mirror_keys = {
        "attempt",
        "verdict",
        "decision",
        "findings",
        "risk",
        "authorization",
        "outcome",
        "rationale",
        "failure_kind",
        "audit_durable",
        "retry_scheduled",
    }
    for event in completions:
        assessment = event.payload.get("assessment")
        if not isinstance(assessment, Mapping):
            errors.append("assessment_digest: completion assessment is missing")
            continue
        observed = event.payload.get("assessment_digest")
        computed = _digest(assessment, "emitted completion assessment")
        if observed != computed:
            errors.append("assessment_digest: completion assessment digest drifted")
        if observed == case.audit_request_digest:
            errors.append(
                "assessment_digest: request and completion digests must be distinct"
            )
        actual_assessment_digests.append(str(observed))
        if assessment.get("audit_request_digest") != case.audit_request_digest:
            errors.append("assessment_digest: request binding drifted")
        for key in mirror_keys:
            if event.payload.get(key) != assessment.get(key):
                errors.append(f"assessment_digest: completion mirror {key!r} drifted")
        if (
            case.lane == _RESULT_REVIEW
            and event.payload.get("failure_kind") is not None
            and event.payload.get("findings") != []
        ):
            errors.append(
                "review_request_truth: failed review cannot publish scripted findings"
            )
    golden_assessments = expected.get("assessment_digests")
    if (
        not isinstance(golden_assessments, list)
        or actual_assessment_digests != golden_assessments
    ):
        errors.append(
            "assessment_digests: reviewed golden expected "
            f"{golden_assessments!r}, got {actual_assessment_digests!r}"
        )

    permission_resolutions = [
        event for event in events if event.kind == "permission_resolved"
    ]
    actual_receipt_digests: list[str] = []
    receipt_mirror_keys = {
        "audit_id",
        "capability_digest",
        "attempt_kind",
        "expected_action_digest",
        "attempted_action_digest",
        "max_uses",
        "uses_before",
        "uses_after",
        "capability_consumed",
        "action_authorized",
        "action_executed",
        "execution_count",
        "standing_allow_created",
        "rejection_reason",
    }
    for event in permission_resolutions:
        receipt = event.payload.get("authorization_receipt")
        if not isinstance(receipt, Mapping):
            errors.append("authorization_receipt: permission resolution is unbound")
            continue
        observed = event.payload.get("authorization_receipt_digest")
        computed = _digest(receipt, "emitted one-shot authorization receipt")
        if observed != computed:
            errors.append("authorization_receipt: receipt digest drifted")
        actual_receipt_digests.append(str(observed))
        for key in receipt_mirror_keys:
            if event.payload.get(key) != receipt.get(key):
                errors.append(f"authorization_receipt: mirror {key!r} drifted")
        if (
            receipt.get("audit_id") != case.audit_id
            or receipt.get("expected_action_digest") != case.action_digest
            or receipt.get("capability_digest") != case.capability_digest
            or receipt.get("max_uses") != 1
            or receipt.get("standing_allow_created") is not False
        ):
            errors.append(
                "authorization_receipt: exact-action/audit/one-shot binding drifted"
            )
        capability = event.payload.get("one_shot_capability")
        if (
            not isinstance(capability, Mapping)
            or event.payload.get("capability_digest")
            != _digest(capability, "emitted one-shot capability")
            or dict(capability) != dict(case.one_shot_capability or {})
        ):
            errors.append("authorization_receipt: capability binding drifted")
    golden_receipts = expected.get("authorization_receipt_digests")
    if (
        not isinstance(golden_receipts, list)
        or actual_receipt_digests != golden_receipts
    ):
        errors.append(
            "authorization_receipt_digests: reviewed golden expected "
            f"{golden_receipts!r}, got {actual_receipt_digests!r}"
        )

    terminal_events = tuple(event for event in events if event.status == "terminal")
    if "one_run_terminal" in expect.invariants and (
        len(terminal_events) != 1 or terminal_events[0].kind != "auto_run_terminal"
    ):
        errors.append(
            "one_run_terminal: expected exactly one terminal auto_run_terminal event"
        )
    candidates = tuple(event for event in events if event.kind == "candidate_ready")
    if "candidate_nonterminal" in expect.invariants:
        if not candidates:
            errors.append("candidate_nonterminal: no candidate_ready event")
        elif any(
            event.status == "terminal" or event.payload.get("terminal") is not False
            for event in candidates
        ):
            errors.append("candidate_nonterminal: candidate was projected as terminal")
        elif case.evidence_snapshot is None or case.review_policy is None:
            errors.append("candidate_nonterminal: result-review fixture is missing")
        else:
            payload = candidates[0].payload
            expected_refs = [
                dict(row) for row in case.evidence_snapshot["evidence_refs"]
            ]
            if (
                payload.get("snapshot_complete") is not True
                or payload.get("snapshot_frozen") is not True
                or payload.get("user_visible_completion") is not False
                or payload.get("evidence_refs") != expected_refs
                or payload.get("provenance_version_ids")
                != case.evidence_snapshot["provenance_version_ids"]
                or payload.get("review_mode") != case.review_policy["review_mode"]
                or payload.get("result_review_mode")
                != _result_review_mode(case.review_policy)
                or payload.get("review_request_policy_digest")
                != case.review_request_policy_digest
                or any(
                    key in payload
                    for key in (
                        "findings",
                        "material_findings_digest",
                        "termination_basis",
                        "verdict",
                    )
                )
            ):
                errors.append(
                    "candidate_nonterminal: request facts or provisional truth drifted"
                )
    if "terminal_state_unique" in expect.invariants:
        terminal_states = [
            event.payload.get("terminal_reason")
            for event in events
            if event.kind == "auto_run_terminal"
        ]
        if (
            terminal_states != [terminal_reason]
            or terminal_reason not in _ALL_TERMINALS
        ):
            errors.append(
                "terminal_state_unique: terminal is absent, duplicated, or unknown"
            )
        terminal = terminal_events[0] if len(terminal_events) == 1 else None
        if terminal is not None:
            auto_state = terminal.payload.get("auto_user_state")
            if terminal_reason == "safety_boundary":
                if (
                    auto_state is not None
                    or terminal.payload.get("safety_terminal") is not True
                ):
                    errors.append(
                        "terminal_state_unique: safety boundary must not count as an Auto user state"
                    )
            elif auto_state != terminal_reason:
                errors.append(
                    "terminal_state_unique: Auto user state projection drifted"
                )

    if len(audit_starts) > 2:
        errors.append("retry_bounded: more than one decision retry occurred")
    if terminal_reason == "review_unavailable":
        review_faults = [
            event
            for event in events
            if event.kind == "fault_injected"
            and event.phase == "scientific_review"
            and event.payload.get("error_kind") in _RETRYABLE_DECISION_FAULTS
        ]
        if len(review_faults) != 2:
            errors.append(
                "retry_bounded: review_unavailable requires two failed attempts"
            )
    if "reviewer_independent" in expect.invariants and (
        case.lane != _RESULT_REVIEW
        or not case.reviewer_id
        or case.reviewer_id == case.producer_id
    ):
        errors.append("reviewer_independent: Reviewer and producer are not independent")
    if (
        case.lane == _RESULT_REVIEW
        and case.outcome == "issues"
        and case.review_policy is not None
        and not case.review_policy["auto_fix_enabled"]
    ):
        completion = completions[-1] if completions else None
        if (
            completion is None
            or completion.payload.get("findings")
            != [dict(finding) for finding in case.material_findings]
            or completion.payload.get("result_review_mode") != "review_only"
            or completion.payload.get("workspace_writes") != 0
            or any(
                event.kind in {"repair_started", "repair_completed"} for event in events
            )
            or completion.payload.get("rationale")
            != "review_only_no_repair_with_open_material_findings"
        ):
            errors.append("review_only issues must terminate without starting Repair")

    if "fail_closed" in expect.invariants:
        injected = [event for event in events if event.kind == "fault_injected"]
        if not injected:
            errors.append("fail_closed: expected a decision fault")
        if terminal_reason == "verified" and len(injected) >= 2:
            errors.append("fail_closed: two failed reviews produced verified")
        if any(event.payload.get("action_executed") is True for event in events):
            errors.append("fail_closed: a faulted Guardian action executed")
    if "binding_mismatch" in expect.invariants:
        mismatches = [
            event
            for event in events
            if event.kind == "fault_injected"
            and event.payload.get("error_kind") == "hash_mismatch"
        ]
        if len(mismatches) != 1:
            errors.append("binding_mismatch: expected one runtime hash mismatch")
        else:
            payload = mismatches[0].payload
            expected_digest = payload.get("expected_digest")
            observed_digest = payload.get("observed_digest")
            canonical_expected = (
                case.action_digest
                if case.lane == _PERMISSION_GUARDIAN
                else case.evidence_digest
            )
            canonical_observed = (
                case.runtime_action_digest
                if case.lane == _PERMISSION_GUARDIAN
                else case.runtime_evidence_digest
            )
            if (
                expected_digest != canonical_expected
                or observed_digest != canonical_observed
                or expected_digest == observed_digest
            ):
                errors.append(
                    "binding_mismatch: trace must use independently canonicalized runtime fixtures"
                )

    if case.lane == _PERMISSION_GUARDIAN and completions:
        final_assessment = completions[-1].payload
        if any(
            event.payload.get("standing_allow_created") is not False
            for event in (*completions, *permission_resolutions)
        ):
            errors.append("Guardian must never create a standing allow")
        if len([e for e in events if e.kind == "fault_injected"]) == 2:
            if (
                final_assessment.get("decision") != "unavailable"
                or final_assessment.get("infra_breaker_open") is not True
                or final_assessment.get("denial_circuit_open") is not False
            ):
                errors.append("Guardian infrastructure breaker semantics drifted")
        elif case.outcome == "deny" and final_assessment.get("decision") == "deny":
            if (
                final_assessment.get("infra_breaker_open") is not False
                or final_assessment.get("denial_circuit_open") is not False
                or final_assessment.get("risk", {}).get("terminal_basis")
                != "no_safe_continuation"
                or final_assessment.get("rationale")
                != "guardian_durable_deny_no_safe_continuation"
            ):
                errors.append("Guardian non-circuit durable-denial semantics drifted")
        elif case.outcome == "deny_circuit":
            risk = final_assessment.get("risk", {})
            if (
                final_assessment.get("decision") != "deny"
                or final_assessment.get("infra_breaker_open") is not False
                or final_assessment.get("denial_circuit_open") is not True
                or risk.get("terminal_basis") != "loop_detected"
                or risk.get("denial_history_digest") != case.denial_history_digest
                or any(
                    risk.get(key) != value
                    for key, value in dict(case.denial_circuit or {}).items()
                )
                or final_assessment.get("rationale") != "guardian_denial_circuit_opened"
            ):
                errors.append("Guardian denial-circuit threshold semantics drifted")
        elif case.outcome in {"allow_once_replay", "allow_once_variant"}:
            authorization = final_assessment.get("authorization", {})
            if (
                final_assessment.get("decision") != "allow_once"
                or final_assessment.get("audit_durable") is not True
                or final_assessment.get("action_authorized") is not True
                or final_assessment.get("action_executed") is not False
                or authorization.get("authorization_scope") != "once"
                or authorization.get("bound_action_digest") != case.action_digest
                or authorization.get("capability_digest") != case.capability_digest
                or authorization.get("max_uses") != 1
            ):
                errors.append("Guardian one-shot allow decision semantics drifted")
            if (
                not permission_resolutions
                or permission_resolutions[0].seq <= completions[-1].seq
            ):
                errors.append(
                    "one-shot capability issued before its durable allow audit"
                )
            issuance = {
                "status": "allowed",
                "resolution_actor": "permission_guardian",
                "allow": True,
                "attempt_kind": "capability_issued",
                "attempted_action_digest": case.action_digest,
                "uses_before": 0,
                "uses_after": 0,
                "capability_consumed": False,
                "action_authorized": True,
                "action_executed": False,
                "execution_count": 0,
                "rejection_reason": None,
                "boundary": None,
            }
            if case.outcome == "allow_once_replay":
                expected_transitions = [
                    issuance,
                    {
                        "status": "executed",
                        "resolution_actor": "one_shot_capability_gate",
                        "allow": True,
                        "attempt_kind": "exact_first_use",
                        "attempted_action_digest": case.action_digest,
                        "uses_before": 0,
                        "uses_after": 1,
                        "capability_consumed": True,
                        "action_authorized": True,
                        "action_executed": True,
                        "execution_count": 1,
                        "rejection_reason": None,
                        "boundary": None,
                    },
                    {
                        "status": "denied",
                        "resolution_actor": "one_shot_capability_gate",
                        "allow": False,
                        "attempt_kind": "replay",
                        "attempted_action_digest": case.action_digest,
                        "uses_before": 1,
                        "uses_after": 1,
                        "capability_consumed": True,
                        "action_authorized": False,
                        "action_executed": False,
                        "execution_count": 1,
                        "rejection_reason": "one_shot_capability_consumed",
                        "boundary": "one_shot_capability_reuse",
                    },
                ]
                error = "one-shot exact consume/replay semantics drifted"
            else:
                expected_transitions = [
                    issuance,
                    {
                        "status": "denied",
                        "resolution_actor": "one_shot_capability_gate",
                        "allow": False,
                        "attempt_kind": "variant_first_use",
                        "attempted_action_digest": case.runtime_action_digest,
                        "uses_before": 0,
                        "uses_after": 1,
                        "capability_consumed": True,
                        "action_authorized": False,
                        "action_executed": False,
                        "execution_count": 0,
                        "rejection_reason": "exact_action_digest_mismatch",
                        "boundary": "exact_action_digest",
                    },
                ]
                error = "one-shot variant refusal semantics drifted"
            actual_transitions = []
            for event in permission_resolutions:
                transition = {
                    key: event.status if key == "status" else event.payload.get(key)
                    for key in expected_transitions[0]
                }
                actual_transitions.append(transition)
            if actual_transitions != expected_transitions:
                errors.append(error)

    terminal_payload = _mapping(
        expected.get("terminal_payload"), f"golden {scenario.id}.terminal_payload"
    )
    actual_terminal = terminal_events[0].payload if len(terminal_events) == 1 else {}
    terminal_projection = {
        key: value
        for key, value in actual_terminal.items()
        if key not in {"identity", "event_names"}
    }
    if terminal_projection != dict(terminal_payload):
        errors.append(
            "golden_payloads: exact terminal payload expected "
            f"{dict(terminal_payload)!r}, got {terminal_projection!r}"
        )
    start = events[0] if events else None
    if (
        start is None
        or start.kind != "auto_run_started"
        or start.payload.get("production_state_machine") is not False
    ):
        errors.append("golden_payloads: contract-only provenance is missing")

    for point, occurrence in faults.unfired:
        errors.append(
            f"faults: declared fault at ({point!r}, occurrence {occurrence}) never fired"
        )
    return errors


def _emit_result_review(
    recorder: _Recorder,
    case: _ContractCase,
    faults: FaultSchedule,
) -> str:
    assert case.candidate is not None
    assert case.evidence_snapshot is not None
    assert case.candidate_digest is not None
    assert case.evidence_digest is not None
    assert case.artifact_set_digest is not None
    assert case.review_policy is not None
    assert case.review_request_policy_digest is not None
    recorder.emit(
        "candidate_ready",
        specific_kind="candidate_recorded",
        phase="completion",
        status="candidate",
        payload={
            "state": "candidate",
            "terminal": False,
            "user_visible_completion": False,
            "candidate_id": case.candidate["candidate_id"],
            "candidate_digest": case.candidate_digest,
            "evidence_snapshot_id": case.evidence_snapshot["snapshot_id"],
            "evidence_snapshot_digest": case.evidence_digest,
            "artifact_set_digest": case.artifact_set_digest,
            "snapshot_complete": case.evidence_snapshot["complete"],
            "snapshot_frozen": case.evidence_snapshot["frozen"],
            "evidence_refs": [
                dict(row) for row in case.evidence_snapshot["evidence_refs"]
            ],
            "evidence_ref_ids": [
                row["ref_id"] for row in case.evidence_snapshot["evidence_refs"]
            ],
            "provenance_version_ids": list(
                case.evidence_snapshot["provenance_version_ids"]
            ),
            "artifact_version_ids": [
                row["version_id"] for row in case.candidate["artifact_versions"]
            ],
            "producer_id": case.producer_id,
            "review_mode": case.review_policy["review_mode"],
            "result_review_mode": _result_review_mode(case.review_policy),
            "review_request_policy_digest": case.review_request_policy_digest,
        },
    )
    for attempt in (1, 2):
        recorder.emit(
            "auto_audit_started",
            specific_kind="scientific_review_started",
            phase="scientific_review",
            status="running",
            payload={
                "attempt": attempt,
                "max_attempts": 2,
                "audit_id": case.audit_id,
                "audit_request_digest": case.audit_request_digest,
                "subject_kind": case.subject_kind,
                "subject_entity_kind": case.subject_entity_kind,
                "candidate_digest": case.candidate_digest,
                "evidence_snapshot_digest": case.evidence_digest,
                "artifact_set_digest": case.artifact_set_digest,
                "reviewer_id": case.reviewer_id,
                "producer_id": case.producer_id,
                "review_mode": case.review_policy["review_mode"],
                "result_review_mode": _result_review_mode(case.review_policy),
                "review_request_policy_digest": case.review_request_policy_digest,
                "read_only": True,
                "workspace_writes": 0,
            },
        )
        injected = faults.check(_FAULT_POINT[case.lane])
        if injected is not None:
            recorder.emit(
                "fault_injected",
                phase="scientific_review",
                status="error",
                payload={
                    "attempt": attempt,
                    "subject_kind": case.subject_kind,
                    "subject_entity_kind": case.subject_entity_kind,
                    **_fault_payload(injected, case),
                },
            )
            if injected.kind in _SAFETY_FAULTS:
                fault_payload = _fault_payload(injected, case)
                boundary = (
                    "review_audit"
                    if injected.kind == "audit_failure"
                    else "immutable_evidence_digest"
                )
                risk: dict[str, Any] = {
                    "risk_level": "safety_boundary",
                    "boundary": boundary,
                    "evidence_complete": True,
                }
                if injected.kind == "hash_mismatch":
                    risk.update(
                        {
                            "expected_digest": fault_payload["expected_digest"],
                            "observed_digest": fault_payload["observed_digest"],
                        }
                    )
                assessment, assessment_digest = _assessment(
                    case,
                    attempt=attempt,
                    verdict="none",
                    decision=None,
                    findings=[],
                    risk=risk,
                    authorization=_authorization(),
                    outcome="safety_boundary",
                    rationale=(
                        "review_audit_persistence_failed"
                        if injected.kind == "audit_failure"
                        else "runtime_evidence_digest_mismatch"
                    ),
                    failure_kind=injected.kind,
                    audit_durable=injected.kind != "audit_failure",
                    retry_scheduled=False,
                )
                recorder.emit(
                    "auto_audit_completed",
                    specific_kind="safety_boundary_reached",
                    phase="scientific_review",
                    status="failed",
                    payload={
                        **_completion_payload(case, assessment, assessment_digest),
                        "boundary": boundary,
                        "workspace_writes": 0,
                    },
                )
                return "safety_boundary"
            if attempt == 1:
                assessment, assessment_digest = _assessment(
                    case,
                    attempt=attempt,
                    verdict="retry_scheduled",
                    decision=None,
                    findings=[],
                    risk={
                        "risk_level": "unknown",
                        "evidence_complete": True,
                        "open_material_findings": None,
                    },
                    authorization=_authorization(),
                    outcome="retrying",
                    rationale="reviewer_infrastructure_retry_scheduled",
                    failure_kind=injected.kind,
                    audit_durable=True,
                    retry_scheduled=True,
                )
                recorder.emit(
                    "auto_audit_completed",
                    specific_kind="scientific_review_finished",
                    phase="scientific_review",
                    status="retrying",
                    payload={
                        **_completion_payload(case, assessment, assessment_digest),
                        "workspace_writes": 0,
                    },
                )
                continue
            assessment, assessment_digest = _assessment(
                case,
                attempt=attempt,
                verdict="unavailable",
                decision=None,
                findings=[],
                risk={
                    "risk_level": "unknown",
                    "evidence_complete": True,
                    "open_material_findings": None,
                },
                authorization=_authorization(),
                outcome="review_unavailable",
                rationale="reviewer_infrastructure_retry_budget_exhausted",
                failure_kind=injected.kind,
                audit_durable=True,
                retry_scheduled=False,
            )
            recorder.emit(
                "auto_audit_completed",
                specific_kind="scientific_review_finished",
                phase="scientific_review",
                status="unavailable",
                payload={
                    **_completion_payload(case, assessment, assessment_digest),
                    "workspace_writes": 0,
                },
            )
            return "review_unavailable"

        if case.outcome == "pass":
            assessment, assessment_digest = _assessment(
                case,
                attempt=attempt,
                verdict="pass",
                decision=None,
                findings=[],
                risk={
                    "risk_level": "none",
                    "evidence_complete": True,
                    "open_material_findings": 0,
                },
                authorization=_authorization(),
                outcome="verified",
                rationale="no_open_material_findings",
                failure_kind=None,
                audit_durable=True,
                retry_scheduled=False,
            )
            recorder.emit(
                "auto_audit_completed",
                specific_kind="scientific_review_finished",
                phase="scientific_review",
                status="passed",
                payload={
                    **_completion_payload(case, assessment, assessment_digest),
                    "candidate_digest": case.candidate_digest,
                    "evidence_snapshot_digest": case.evidence_digest,
                    "artifact_set_digest": case.artifact_set_digest,
                    "reviewer_id": case.reviewer_id,
                    "producer_id": case.producer_id,
                    "result_review_mode": _result_review_mode(case.review_policy),
                    "termination_basis": case.review_policy["termination_basis"],
                    "workspace_writes": 0,
                },
            )
            return "verified"
        findings = [dict(finding) for finding in case.material_findings]
        assert case.outcome == "issues"
        assessment, assessment_digest = _assessment(
            case,
            attempt=attempt,
            verdict="issues",
            decision=None,
            findings=findings,
            risk={
                "risk_level": "material",
                "evidence_complete": True,
                "open_material_findings": len(findings),
            },
            authorization=_authorization(),
            outcome="completed_with_issues",
            rationale=(
                "auto_fix_budget_exhausted_with_open_material_findings"
                if case.review_policy["auto_fix_enabled"]
                else "review_only_no_repair_with_open_material_findings"
            ),
            failure_kind=None,
            audit_durable=True,
            retry_scheduled=False,
        )
        recorder.emit(
            "auto_audit_completed",
            specific_kind="scientific_review_finished",
            phase="scientific_review",
            status="issues",
            payload={
                **_completion_payload(case, assessment, assessment_digest),
                "result_review_mode": _result_review_mode(case.review_policy),
                "termination_basis": case.review_policy["termination_basis"],
                "workspace_writes": 0,
            },
        )
        return "completed_with_issues"
    raise AssertionError("bounded review loop did not terminate")


def _emit_permission_resolution(
    recorder: _Recorder,
    case: _ContractCase,
    *,
    status: str,
    resolution_actor: str,
    allow: bool,
    attempt_kind: str,
    attempted_action_digest: str,
    uses_before: int,
    uses_after: int,
    capability_consumed: bool,
    action_authorized: bool,
    action_executed: bool,
    execution_count: int,
    rejection_reason: str | None,
    boundary: str | None = None,
) -> None:
    assert case.action_digest is not None
    assert case.one_shot_capability is not None
    assert case.capability_digest is not None
    receipt, receipt_digest = _authorization_receipt(
        case,
        attempt_kind=attempt_kind,
        attempted_action_digest=attempted_action_digest,
        uses_before=uses_before,
        uses_after=uses_after,
        capability_consumed=capability_consumed,
        action_authorized=action_authorized,
        action_executed=action_executed,
        execution_count=execution_count,
        rejection_reason=rejection_reason,
    )
    payload: dict[str, Any] = {
        **receipt,
        "allow": allow,
        "scope": "once",
        "resolution_actor": resolution_actor,
        "action_digest": case.action_digest,
        "one_shot_capability": dict(case.one_shot_capability),
        "authorization_receipt": receipt,
        "authorization_receipt_digest": receipt_digest,
    }
    if boundary is not None:
        payload["boundary"] = boundary
    recorder.emit(
        "permission_resolved",
        phase="permission",
        status=status,
        payload=payload,
    )


def _emit_one_shot_exercise(recorder: _Recorder, case: _ContractCase) -> str:
    assert case.action_digest is not None
    assert case.capability_digest is not None
    _emit_permission_resolution(
        recorder,
        case,
        status="allowed",
        resolution_actor="permission_guardian",
        allow=True,
        attempt_kind="capability_issued",
        attempted_action_digest=case.action_digest,
        uses_before=0,
        uses_after=0,
        capability_consumed=False,
        action_authorized=True,
        action_executed=False,
        execution_count=0,
        rejection_reason=None,
    )
    if case.outcome == "allow_once_replay":
        _emit_permission_resolution(
            recorder,
            case,
            status="executed",
            resolution_actor="one_shot_capability_gate",
            allow=True,
            attempt_kind="exact_first_use",
            attempted_action_digest=case.action_digest,
            uses_before=0,
            uses_after=1,
            capability_consumed=True,
            action_authorized=True,
            action_executed=True,
            execution_count=1,
            rejection_reason=None,
        )
        _emit_permission_resolution(
            recorder,
            case,
            status="denied",
            resolution_actor="one_shot_capability_gate",
            allow=False,
            attempt_kind="replay",
            attempted_action_digest=case.action_digest,
            uses_before=1,
            uses_after=1,
            capability_consumed=True,
            action_authorized=False,
            action_executed=False,
            execution_count=1,
            rejection_reason="one_shot_capability_consumed",
            boundary="one_shot_capability_reuse",
        )
        return "safety_boundary"

    assert case.outcome == "allow_once_variant"
    assert case.runtime_action_digest is not None
    _emit_permission_resolution(
        recorder,
        case,
        status="denied",
        resolution_actor="one_shot_capability_gate",
        allow=False,
        attempt_kind="variant_first_use",
        attempted_action_digest=case.runtime_action_digest,
        uses_before=0,
        uses_after=1,
        capability_consumed=True,
        action_authorized=False,
        action_executed=False,
        execution_count=0,
        rejection_reason="exact_action_digest_mismatch",
        boundary="exact_action_digest",
    )
    return "safety_boundary"


def _emit_guardian(
    recorder: _Recorder,
    case: _ContractCase,
    faults: FaultSchedule,
) -> str:
    assert case.canonical_action is not None
    assert case.action_digest is not None
    for attempt in (1, 2):
        recorder.emit(
            "auto_audit_started",
            specific_kind="guardian_review_started",
            phase="permission",
            status="running",
            payload={
                "attempt": attempt,
                "max_attempts": 2,
                "audit_id": case.audit_id,
                "audit_request_digest": case.audit_request_digest,
                "subject_kind": case.subject_kind,
                "subject_entity_kind": case.subject_entity_kind,
                "action_digest": case.action_digest,
                "digest_source": "computed_canonical_action",
                "policy_resolution": "ask",
                "standing_allow_policy": "never",
            },
        )
        injected = faults.check(_FAULT_POINT[case.lane])
        if injected is not None:
            recorder.emit(
                "fault_injected",
                phase="permission",
                status="error",
                payload={
                    "attempt": attempt,
                    "subject_kind": case.subject_kind,
                    "subject_entity_kind": case.subject_entity_kind,
                    **_fault_payload(injected, case),
                },
            )
            if injected.kind in _SAFETY_FAULTS:
                fault_payload = _fault_payload(injected, case)
                boundary = (
                    "guardian_audit"
                    if injected.kind == "audit_failure"
                    else "exact_action_digest"
                )
                risk: dict[str, Any] = {
                    "risk_level": "safety_boundary",
                    "boundary": boundary,
                    "policy_resolution": "ask",
                }
                if injected.kind == "hash_mismatch":
                    risk.update(
                        {
                            "expected_digest": fault_payload["expected_digest"],
                            "observed_digest": fault_payload["observed_digest"],
                        }
                    )
                assessment, assessment_digest = _assessment(
                    case,
                    attempt=attempt,
                    verdict=None,
                    decision="none",
                    findings=[],
                    risk=risk,
                    authorization=_authorization(),
                    outcome="safety_boundary",
                    rationale=(
                        "guardian_audit_persistence_failed"
                        if injected.kind == "audit_failure"
                        else "runtime_action_digest_mismatch"
                    ),
                    failure_kind=injected.kind,
                    audit_durable=injected.kind != "audit_failure",
                    retry_scheduled=False,
                )
                recorder.emit(
                    "auto_audit_completed",
                    specific_kind="safety_boundary_reached",
                    phase="permission",
                    status="failed",
                    payload={
                        **_completion_payload(case, assessment, assessment_digest),
                        "boundary": boundary,
                        "action_digest": case.action_digest,
                    },
                )
                return "safety_boundary"
            if attempt == 1:
                assessment, assessment_digest = _assessment(
                    case,
                    attempt=attempt,
                    verdict=None,
                    decision="retry_scheduled",
                    findings=[],
                    risk={
                        "risk_level": "unknown",
                        "policy_resolution": "ask",
                        "exact_action_bound": True,
                    },
                    authorization=_authorization(),
                    outcome="retrying",
                    rationale="guardian_infrastructure_retry_scheduled",
                    failure_kind=injected.kind,
                    audit_durable=True,
                    retry_scheduled=True,
                )
                recorder.emit(
                    "auto_audit_completed",
                    specific_kind="guardian_review_finished",
                    phase="permission",
                    status="retrying",
                    payload={
                        **_completion_payload(case, assessment, assessment_digest),
                        "action_digest": case.action_digest,
                    },
                )
                continue
            assessment, assessment_digest = _assessment(
                case,
                attempt=attempt,
                verdict=None,
                decision="unavailable",
                findings=[],
                risk={
                    "risk_level": "unknown",
                    "policy_resolution": "ask",
                    "exact_action_bound": True,
                },
                authorization=_authorization(infra_breaker_open=True),
                outcome="blocked_by_guardian",
                rationale="guardian_infrastructure_retry_budget_exhausted",
                failure_kind=injected.kind,
                audit_durable=True,
                retry_scheduled=False,
            )
            recorder.emit(
                "auto_audit_completed",
                specific_kind="guardian_review_finished",
                phase="permission",
                status="blocked",
                payload={
                    **_completion_payload(case, assessment, assessment_digest),
                    "action_digest": case.action_digest,
                },
            )
            return "blocked_by_guardian"

        if case.outcome in {"allow_once_replay", "allow_once_variant"}:
            assert case.capability_digest is not None
            allow_authorization = _authorization(action_authorized=True)
            allow_authorization.update(
                {
                    "authorization_scope": "once",
                    "bound_action_digest": case.action_digest,
                    "capability_digest": case.capability_digest,
                    "max_uses": 1,
                }
            )
            assessment, assessment_digest = _assessment(
                case,
                attempt=attempt,
                verdict=None,
                decision="allow_once",
                findings=[],
                risk={
                    "risk_level": "low",
                    "policy_resolution": "ask",
                    "exact_action_bound": True,
                    "fallback_decision": False,
                },
                authorization=allow_authorization,
                outcome="authorized_once",
                rationale="guardian_durable_allow_once_exact_action",
                failure_kind=None,
                audit_durable=True,
                retry_scheduled=False,
            )
            recorder.emit(
                "auto_audit_completed",
                specific_kind="guardian_review_finished",
                phase="permission",
                status="allowed",
                payload={
                    **_completion_payload(case, assessment, assessment_digest),
                    "decision_source": "permission_guardian",
                    "fallback_decision": False,
                    "action_digest": case.action_digest,
                    "digest_source": "computed_canonical_action",
                    "capability_digest": case.capability_digest,
                    "max_uses": 1,
                },
            )
            return _emit_one_shot_exercise(recorder, case)

        if case.outcome == "deny_circuit":
            assert case.denial_circuit is not None
            assert case.denial_history_digest is not None
            circuit_risk = {
                "risk_level": "denied",
                "policy_resolution": "ask",
                "exact_action_bound": True,
                "fallback_decision": False,
                "terminal_basis": "loop_detected",
                "denial_history_digest": case.denial_history_digest,
                **dict(case.denial_circuit),
            }
            assessment, assessment_digest = _assessment(
                case,
                attempt=attempt,
                verdict=None,
                decision="deny",
                findings=[],
                risk=circuit_risk,
                authorization=_authorization(denial_circuit_open=True),
                outcome="blocked_by_guardian",
                rationale="guardian_denial_circuit_opened",
                failure_kind=None,
                audit_durable=True,
                retry_scheduled=False,
            )
            recorder.emit(
                "auto_audit_completed",
                specific_kind="guardian_review_finished",
                phase="permission",
                status="denied",
                payload={
                    **_completion_payload(case, assessment, assessment_digest),
                    "decision_source": "permission_guardian",
                    "fallback_decision": False,
                    "action_digest": case.action_digest,
                    "digest_source": "computed_canonical_action",
                },
            )
            return "blocked_by_guardian"

        assert case.outcome == "deny"
        assessment, assessment_digest = _assessment(
            case,
            attempt=attempt,
            verdict=None,
            decision="deny",
            findings=[],
            risk={
                "risk_level": "denied",
                "policy_resolution": "ask",
                "exact_action_bound": True,
                "fallback_decision": False,
                "terminal_basis": "no_safe_continuation",
            },
            authorization=_authorization(),
            outcome="blocked_by_guardian",
            rationale="guardian_durable_deny_no_safe_continuation",
            failure_kind=None,
            audit_durable=True,
            retry_scheduled=False,
        )
        recorder.emit(
            "auto_audit_completed",
            specific_kind="guardian_review_finished",
            phase="permission",
            status="denied",
            payload={
                **_completion_payload(case, assessment, assessment_digest),
                "decision_source": "permission_guardian",
                "fallback_decision": False,
                "action_digest": case.action_digest,
                "digest_source": "computed_canonical_action",
            },
        )
        return "blocked_by_guardian"
    raise AssertionError("bounded Guardian loop did not terminate")


def run_auto_mode_contract(
    scenario: Scenario,
    *,
    offline: bool = True,
    clock: FakeClock | None = None,
    uuid_factory: FakeUUIDFactory | None = None,
) -> ScenarioResult:
    """Replay one proposed Stage 0 Auto Mode contract deterministically.

    No production state machine, model, network, permission service, reviewer,
    or workspace is invoked. The required ``provider_script`` is schema ballast
    and is intentionally not consumed.
    """

    if scenario.surface != SURFACE:
        raise ScenarioValidationError(
            f"run_auto_mode_contract requires surface {SURFACE!r}"
        )
    if offline and not scenario.is_offline:
        raise ValueError(
            f"scenario {scenario.id!r} is not eligible for the offline tier"
        )
    expected = _load_expected(scenario.id)
    case = _case(scenario, expected)
    clock = clock or FakeClock()
    uuid_factory = uuid_factory or FakeUUIDFactory()
    recorder = _Recorder(
        identity=case.identity,
        clock=clock,
        uuid_factory=uuid_factory,
    )
    faults = FaultSchedule(scenario.faults)
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
            "auto_user_states": list(_AUTO_USER_STATES),
            "identity_digest": case.identity_digest,
        },
    )

    if case.lane == _RESULT_REVIEW:
        terminal_reason = _emit_result_review(recorder, case, faults)
    else:
        terminal_reason = _emit_guardian(recorder, case, faults)

    is_auto_state = terminal_reason in _AUTO_TERMINALS
    guardian_unavailable = any(
        event.kind == "auto_audit_completed"
        and event.payload.get("decision") == "unavailable"
        for event in recorder.events
    )
    recoverable = terminal_reason in {
        "completed_with_issues",
        "review_unavailable",
    } or (terminal_reason == "blocked_by_guardian" and guardian_unavailable)
    terminal_details: dict[str, Any] = {}
    if case.outcome == "allow_once_replay":
        terminal_details = {
            "boundary": "one_shot_capability_reuse",
            "stop_reason": "one_shot_capability_consumed",
        }
    elif case.outcome == "allow_once_variant":
        terminal_details = {
            "boundary": "exact_action_digest",
            "stop_reason": "exact_action_digest_mismatch",
        }
    elif case.outcome == "deny_circuit":
        terminal_details = {
            "stop_reason": "loop_detected",
            "denial_circuit_trigger": (
                case.denial_circuit["trigger"]
                if case.denial_circuit is not None
                else None
            ),
        }
    recorder.emit(
        "auto_run_terminal",
        specific_kind="run_finished",
        phase="lifecycle",
        status="terminal",
        payload={
            "terminal_reason": terminal_reason,
            "auto_user_state": terminal_reason if is_auto_state else None,
            "safety_terminal": terminal_reason == "safety_boundary",
            "production_state_machine": False,
            "recoverable": recoverable,
            **terminal_details,
        },
    )
    events = tuple(recorder.events)
    errors = tuple(
        _evaluate(
            scenario,
            case=case,
            expected=expected,
            terminal_reason=terminal_reason,
            events=events,
            faults=faults,
        )
    )
    normalized = normalized_trace_bytes(events)
    return ScenarioResult(
        scenario_id=scenario.id,
        passed=not errors,
        terminal_reason=terminal_reason,
        model_attempts=0,
        events=events,
        errors=errors,
        normalized=normalized,
    )


__all__ = ["CONTRACT", "SURFACE", "run_auto_mode_contract"]
