"""Portable, untrusted-safe Auto Mode history projection.

This module is the single reducer shared by Session packages and read-only
shares. It exports only a closed audit DTO, validates scope/reference closure,
and downgrades claims that portable evidence cannot independently prove. It
never restores execution authority or calls a model.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

_RECORD_LIMITS = {
    "auto_mode_runs": 25_000,
    "auto_mode_events": 100_000,
    "auto_mode_review_runs": 50_000,
    "auto_mode_findings": 100_000,
    "auto_mode_repair_runs": 25_000,
    "auto_mode_permission_assessments": 100_000,
}
_MAX_PORTABLE_BYTES = 32 << 20
_MAX_VALUE_ITEMS = 4096
_MAX_STRING_LENGTH = 1 << 20


class AutoModePortabilityError(ValueError):
    """The portable Auto Mode graph is malformed or incomplete."""


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise AutoModePortabilityError(
            "Auto Mode portable value must be canonical JSON"
        ) from error


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sanitize(value: Any, *, depth: int = 0) -> Any:
    """Keep JSON values bounded; field allowlists remove private material."""

    if depth > 32:
        raise AutoModePortabilityError("Auto Mode portable value is nested too deeply")
    if isinstance(value, Mapping):
        if len(value) > _MAX_VALUE_ITEMS:
            raise AutoModePortabilityError("Auto Mode portable object is too large")
        if any(not isinstance(key, str) for key in value):
            raise AutoModePortabilityError("Auto Mode portable object key is invalid")
        return {key: _sanitize(item, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        if len(value) > _MAX_VALUE_ITEMS:
            raise AutoModePortabilityError("Auto Mode portable list is too large")
        return [_sanitize(item, depth=depth + 1) for item in value]
    if isinstance(value, str):
        if len(value) > _MAX_STRING_LENGTH:
            raise AutoModePortabilityError("Auto Mode portable string is too large")
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise AutoModePortabilityError("Auto Mode portable value has an invalid type")


EFFECTIVE_AUTO_MODE_OFF = {
    "preset": "off",
    "result_review_mode": "off",
    "approvals_reviewer": "user",
}
_AUTO_EVENT_TYPES = frozenset(
    {
        "auto_run_started",
        "candidate_ready",
        "auto_audit_started",
        "auto_audit_completed",
        "repair_started",
        "repair_completed",
        "auto_run_terminal",
    }
)
_NONTERMINAL_RUN_STATUSES = frozenset(
    {
        "pending",
        "started",
        "running",
        "candidate",
        "candidate_ready",
        "reviewing",
        "repairing",
        "repair_pending",
        "repair_running",
        "recovery_required",
    }
)
_AUTO_RUN_MODES = frozenset({"off", "review_only", "auto_fix"})
_AUTO_TRUST_STATES = frozenset({"local", "quarantined_import"})
_AUTO_RUN_STATUSES = frozenset(
    {
        "running",
        "candidate",
        "reviewing",
        "repairing",
        "verified",
        "completed_with_issues",
        "review_unavailable",
        "blocked_by_guardian",
        "cancelled",
        "failed",
        "paused",
        "unverified",
        "unverified_import",
    }
)
_AUTO_TERMINAL_STATUSES = _AUTO_RUN_STATUSES - {
    "running",
    "candidate",
    "reviewing",
    "repairing",
}
_REVIEW_STATUSES = frozenset(
    {"started", "completed", "unavailable", "failed", "unverified_import"}
)
_REVIEW_VERDICTS = frozenset(
    {
        "pass",
        "completed_with_issues",
        "issues",
        "fail",
        "failed",
        "incomplete",
        "needs_repair",
        "review_unavailable",
        "unavailable",
    }
)
_REPAIR_STATUSES = frozenset(
    {"started", "completed", "failed", "outcome_unknown", "unverified_import"}
)
_PERMISSION_STATUSES = frozenset(
    {"started", "completed", "unavailable", "failed", "unverified_import"}
)
_PERMISSION_OUTCOMES = frozenset(
    {
        "allow",
        "allow_once",
        "allowed",
        "deny",
        "denied",
        "ask",
        "unavailable",
        "failed",
        "shadow_allow",
        "shadow_deny",
    }
)
_RISK_LEVELS = frozenset({"low", "medium", "high", "critical", "unknown"})
_AUTO_SCOPE_FIELDS = (
    "root_frame_id",
    "branch_id",
    "turn_id",
    "execution_id",
)
_AUTO_RUN_FIELDS = frozenset(
    {
        "run_id",
        *_AUTO_SCOPE_FIELDS,
        "trust_state",
        "mode",
        "status",
        "candidate_id",
        "candidate_snapshot_sha256",
        "evidence_snapshot_sha256",
        "artifact_set_sha256",
        "candidate_artifact_ids",
        "candidate_version_ids",
        "terminal_reason",
        "stop_reason",
        "source_claimed_status",
        "source_terminal_reason",
        "created_at",
        "finished_at",
    }
)
_AUTO_REVIEW_FIELDS = frozenset(
    {
        "review_run_id",
        "audit_id",
        "run_id",
        *_AUTO_SCOPE_FIELDS,
        "candidate_id",
        "candidate_snapshot_sha256",
        "evidence_snapshot_sha256",
        "audit_request_digest",
        "assessment_digest",
        "status",
        "verdict",
        "failure_reason",
        "round",
        "attempt",
        "model_profile_id",
        "model_profile_revision",
        "model_fingerprint",
        "public_summary",
        "created_at",
        "finished_at",
    }
)
_AUTO_FINDING_FIELDS = frozenset(
    {
        "finding_id",
        "review_run_id",
        "run_id",
        *_AUTO_SCOPE_FIELDS,
        "candidate_id",
        "fingerprint",
        "severity",
        "category",
        "claim",
        "evidence_refs",
        "artifact_ids",
        "version_ids",
        "cell_ids",
        # Read-only compatibility for an early singular draft. Output is
        # normalized to the plural list fields below.
        "artifact_id",
        "version_id",
        "producing_cell_id",
        "status",
        "created_at",
        "updated_at",
    }
)
_AUTO_REPAIR_FIELDS = frozenset(
    {
        "repair_run_id",
        "run_id",
        *_AUTO_SCOPE_FIELDS,
        "finding_ids",
        "before_version_ids",
        "after_version_ids",
        "execution_group_ids",
        "verification_review_run_id",
        "status",
        "created_at",
        "finished_at",
    }
)
_AUTO_PERMISSION_FIELDS = frozenset(
    {
        "assessment_id",
        "audit_id",
        "run_id",
        *_AUTO_SCOPE_FIELDS,
        "decision_id",
        "action_digest",
        "audit_request_digest",
        "assessment_digest",
        "policy_version",
        "status",
        "outcome",
        "risk",
        "public_summary",
        "created_at",
        "finished_at",
    }
)
_AUTO_EVENT_PAYLOAD_FIELDS = frozenset(
    {
        "mode",
        "status",
        "terminal_reason",
        "stop_reason",
        "reason_code",
        "candidate_id",
        "candidate_snapshot_sha256",
        "evidence_snapshot_sha256",
        "artifact_set_sha256",
        "artifact_ids",
        "version_ids",
        "candidate_artifact_ids",
        "candidate_version_ids",
        "audit_id",
        "subject_kind",
        "subject_entity_kind",
        "subject_entity_id",
        "audit_request_digest",
        "assessment_digest",
        "action_digest",
        "review_run_id",
        "repair_run_id",
        "assessment_id",
        "decision_id",
        "policy_version",
        "model_profile_id",
        "model_profile_revision",
        "model_fingerprint",
        "finding_id",
        "finding_ids",
        "finding_count",
        "before_version_ids",
        "after_version_ids",
        "execution_group_ids",
        "action_group_id",
        "phase",
        "verification_review_run_id",
        "verdict",
        "decision",
        "outcome",
        "risk",
        "failure_kind",
        "retryable",
        "round",
        "attempt",
        "counts",
        "public_summary",
    }
)


def _auto_record(
    value: Any,
    fields: frozenset[str],
    *,
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AutoModePortabilityError(f"{label} is invalid")
    return {
        key: _sanitize(value.get(key))
        for key in fields
        if key in value and value.get(key) is not None
    }


def _auto_identity_set(
    records: list[dict[str, Any]], field: str, label: str
) -> set[str]:
    values: set[str] = set()
    for record in records:
        value = record.get(field)
        if not isinstance(value, str) or not value or value in values:
            raise AutoModePortabilityError(f"duplicate or invalid {label} identity")
        values.add(value)
    return values


def _auto_required_reference(value: Any, allowed: set[str], label: str) -> None:
    if not isinstance(value, str) or not value or value not in allowed:
        raise AutoModePortabilityError(f"{label} references an unknown identity")


def _auto_reference_list(record: Mapping[str, Any], key: str, label: str) -> list[str]:
    values = record.get(key)
    if values is None:
        return []
    if not isinstance(values, list) or any(
        not isinstance(item, str) or not item for item in values
    ):
        raise AutoModePortabilityError(f"{label} references are invalid")
    return values


def _auto_sha256(value: Any, label: str, *, required: bool = False) -> bool:
    if value in (None, "") and not required:
        return False
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise AutoModePortabilityError(f"{label} digest is invalid")
    return True


def _closed_text(record: Mapping[str, Any], key: str, label: str) -> None:
    value = record.get(key)
    if value is not None and (not isinstance(value, str) or not value):
        raise AutoModePortabilityError(f"{label} {key} is invalid")


def _closed_integer(record: Mapping[str, Any], key: str, label: str) -> None:
    value = record.get(key)
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or value < 0
    ):
        raise AutoModePortabilityError(f"{label} {key} is invalid")


def _assert_no_private_keys(value: Any, *, path: str = "auto_mode") -> None:
    forbidden = {
        "authorization",
        "authorizations",
        "credential",
        "credentials",
        "hidden_rationale",
        "permission_payload",
        "prompt",
        "raw_payload",
        "rationale",
        "secret",
        "secrets",
        "system_prompt",
    }
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key.lower() in forbidden:
                raise AutoModePortabilityError(
                    f"private Auto Mode material is not portable at {path}.{key}"
                )
            _assert_no_private_keys(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_private_keys(item, path=f"{path}[{index}]")


def portable_auto_mode_projection(
    value: Any,
    *,
    trust_state: str,
    root_frame_id: str | None = None,
    branch_ids: set[str] | None = None,
    artifact_ids: set[str] | None = None,
    version_ids: set[str] | None = None,
    cell_ids: set[str] | None = None,
    action_group_ids: set[str] | None = None,
    turn_ids: set[str] | None = None,
    action_group_scopes: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the bounded, portable Auto Mode audit DTO.

    This is deliberately not a database-row serializer.  Hidden model context,
    free-form Reviewer/Guardian rationale, permission payloads, and reusable
    authorization material have no fields in this DTO and therefore cannot
    cross package/share boundaries.  Durable event payloads are reduced to the
    closed public vocabulary and re-hashed after that reduction.
    """

    if not isinstance(value, Mapping):
        raise AutoModePortabilityError("Auto Mode projection must be an object")
    if trust_state not in _AUTO_TRUST_STATES:
        raise AutoModePortabilityError("Auto Mode trust state is invalid")
    source = value
    if source and source.get("schema_version", 1) != 1:
        raise AutoModePortabilityError("Auto Mode projection schema version is invalid")

    selection_source = source.get("historical_selection")
    historical_selection: dict[str, Any] | None = None
    if isinstance(selection_source, Mapping):
        allowed = {
            "preset",
            "result_review_mode",
            "approvals_reviewer",
            "source",
        }
        historical_selection = {
            key: _sanitize(selection_source.get(key))
            for key in allowed
            if key in selection_source and selection_source.get(key) is not None
        }
        for key, choices in (
            ("preset", {"off", "autonomous"}),
            ("result_review_mode", {"off", "review_only", "auto_fix"}),
            ("approvals_reviewer", {"user", "auto_review"}),
        ):
            if key in historical_selection and historical_selection[key] not in choices:
                raise AutoModePortabilityError(
                    f"Auto Mode historical selection {key} is invalid"
                )
        _closed_text(historical_selection, "source", "Auto Mode selection")

    specs = (
        ("runs", "auto_mode_runs", _AUTO_RUN_FIELDS),
        ("review_runs", "auto_mode_review_runs", _AUTO_REVIEW_FIELDS),
        ("findings", "auto_mode_findings", _AUTO_FINDING_FIELDS),
        ("repair_runs", "auto_mode_repair_runs", _AUTO_REPAIR_FIELDS),
        (
            "permission_assessments",
            "auto_mode_permission_assessments",
            _AUTO_PERMISSION_FIELDS,
        ),
    )
    projected: dict[str, list[dict[str, Any]]] = {}
    for key, limit_name, fields in specs:
        records = source.get(key, [])
        if not isinstance(records, list):
            raise AutoModePortabilityError(f"Auto Mode {key} must be a list")
        if len(records) > _RECORD_LIMITS[limit_name]:
            raise AutoModePortabilityError(f"session has too many Auto Mode {key}")
        projected[key] = [
            _auto_record(item, fields, label=f"Auto Mode {key} record")
            for item in records
        ]
        if key == "runs":
            for source_run, run in zip(records, projected[key], strict=True):
                run_trust_state = source_run.get("trust_state", trust_state)
                if run_trust_state not in _AUTO_TRUST_STATES:
                    raise AutoModePortabilityError(
                        "Auto Mode run trust state is invalid"
                    )
                # New projections carry trust at the durable owner boundary.
                # Packages created before that field existed inherit their
                # projection-wide trust state for backwards compatibility.
                run["trust_state"] = run_trust_state
        if key == "findings":
            for record in projected[key]:
                for plural, singular in (
                    ("artifact_ids", "artifact_id"),
                    ("version_ids", "version_id"),
                    ("cell_ids", "producing_cell_id"),
                ):
                    values = record.get(plural)
                    if values is None and record.get(singular) is not None:
                        values = [record[singular]]
                    if values is None:
                        values = []
                    if not isinstance(values, list) or any(
                        not isinstance(item, str) or not item for item in values
                    ):
                        raise AutoModePortabilityError(
                            f"Auto Mode finding {plural} is invalid"
                        )
                    record[plural] = list(values)
                    record.pop(singular, None)

    record_contracts = (
        (
            projected["runs"],
            "Auto Mode run",
            _AUTO_RUN_FIELDS
            - {
                "candidate_artifact_ids",
                "candidate_version_ids",
                "created_at",
                "finished_at",
            },
            {"created_at", "finished_at"},
        ),
        (
            projected["review_runs"],
            "Auto Mode review",
            _AUTO_REVIEW_FIELDS
            - {
                "round",
                "attempt",
                "model_profile_revision",
                "created_at",
                "finished_at",
            },
            {
                "round",
                "attempt",
                "model_profile_revision",
                "created_at",
                "finished_at",
            },
        ),
        (
            projected["findings"],
            "Auto Mode finding",
            _AUTO_FINDING_FIELDS
            - {
                "evidence_refs",
                "artifact_ids",
                "version_ids",
                "cell_ids",
                "artifact_id",
                "version_id",
                "producing_cell_id",
                "created_at",
                "updated_at",
            },
            {"created_at", "updated_at"},
        ),
        (
            projected["repair_runs"],
            "Auto Mode repair",
            _AUTO_REPAIR_FIELDS
            - {
                "finding_ids",
                "before_version_ids",
                "after_version_ids",
                "execution_group_ids",
                "created_at",
                "finished_at",
            },
            {"created_at", "finished_at"},
        ),
        (
            projected["permission_assessments"],
            "Auto Mode permission assessment",
            _AUTO_PERMISSION_FIELDS - {"created_at", "finished_at"},
            {"created_at", "finished_at"},
        ),
    )
    for records, label, text_fields, integer_fields in record_contracts:
        for record in records:
            for key in text_fields:
                _closed_text(record, key, label)
            for key in integer_fields:
                _closed_integer(record, key, label)

    raw_events = source.get("events", [])
    if not isinstance(raw_events, list):
        raise AutoModePortabilityError("Auto Mode events must be a list")
    if len(raw_events) > _RECORD_LIMITS["auto_mode_events"]:
        raise AutoModePortabilityError("session has too many Auto Mode events")
    events: list[dict[str, Any]] = []
    previous_cursor: int | None = None
    for raw in raw_events:
        if not isinstance(raw, Mapping):
            raise AutoModePortabilityError("Auto Mode event is invalid")
        event_type = raw.get("type")
        if event_type not in _AUTO_EVENT_TYPES:
            raise AutoModePortabilityError("Auto Mode event type is invalid")
        cursor = raw.get("event_cursor", raw.get("event_ordinal"))
        if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 1:
            raise AutoModePortabilityError("Auto Mode event cursor is invalid")
        if previous_cursor is not None and cursor <= previous_cursor:
            raise AutoModePortabilityError("Auto Mode event cursors are not ordered")
        previous_cursor = cursor
        payload_source = raw.get("payload")
        if payload_source is None:
            payload_source = {}
        if not isinstance(payload_source, Mapping):
            raise AutoModePortabilityError("Auto Mode event payload is invalid")
        source_payload = _sanitize(payload_source)
        source_payload_sha256 = raw.get("payload_sha256")
        _auto_sha256(source_payload_sha256, "Auto Mode event payload", required=True)
        if source_payload_sha256 != _sha256(_canonical_json(source_payload)):
            raise AutoModePortabilityError("Auto Mode event payload digest mismatch")
        payload = {
            key: source_payload.get(key)
            for key in _AUTO_EVENT_PAYLOAD_FIELDS
            if key in source_payload and source_payload.get(key) is not None
        }
        list_payload_fields = {
            "artifact_ids",
            "version_ids",
            "candidate_artifact_ids",
            "candidate_version_ids",
            "finding_ids",
            "before_version_ids",
            "after_version_ids",
            "execution_group_ids",
        }
        integer_payload_fields = {
            "round",
            "attempt",
            "finding_count",
            "model_profile_revision",
        }
        for key, item in payload.items():
            if key in list_payload_fields:
                if not isinstance(item, list) or any(
                    not isinstance(reference, str) or not reference
                    for reference in item
                ):
                    raise AutoModePortabilityError(
                        f"Auto Mode event {key} references are invalid"
                    )
            elif key in integer_payload_fields:
                if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                    raise AutoModePortabilityError(f"Auto Mode event {key} is invalid")
            elif key == "retryable":
                if not isinstance(item, bool):
                    raise AutoModePortabilityError(
                        "Auto Mode event retryable is invalid"
                    )
            elif key == "counts":
                if not isinstance(item, Mapping) or any(
                    not isinstance(name, str)
                    or not name
                    or isinstance(count, bool)
                    or not isinstance(count, int)
                    or count < 0
                    for name, count in item.items()
                ):
                    raise AutoModePortabilityError("Auto Mode event counts are invalid")
            elif not isinstance(item, str) or not item:
                raise AutoModePortabilityError(f"Auto Mode event {key} is invalid")
        for key in (
            "candidate_snapshot_sha256",
            "evidence_snapshot_sha256",
            "artifact_set_sha256",
            "action_digest",
            "audit_request_digest",
            "assessment_digest",
        ):
            if key in payload:
                _auto_sha256(payload[key], f"Auto Mode event {key}", required=True)
        payload_sha256 = _sha256(_canonical_json(payload))
        event = _auto_record(
            raw,
            frozenset(
                {
                    "event_id",
                    "run_id",
                    *_AUTO_SCOPE_FIELDS,
                    "created_at",
                }
            ),
            label="Auto Mode event",
        )
        event.update(
            {
                "event_cursor": cursor,
                "type": event_type,
                "payload": payload,
                "payload_sha256": payload_sha256,
            }
        )
        events.append(event)
    projected["events"] = events

    runs = projected["runs"]
    run_ids = _auto_identity_set(runs, "run_id", "Auto Mode run")
    event_ids = _auto_identity_set(events, "event_id", "Auto Mode event")
    del event_ids
    review_ids = _auto_identity_set(
        projected["review_runs"], "review_run_id", "Auto Mode review"
    )
    finding_ids = _auto_identity_set(
        projected["findings"], "finding_id", "Auto Mode finding"
    )
    repair_ids = _auto_identity_set(
        projected["repair_runs"], "repair_run_id", "Auto Mode repair"
    )
    assessment_ids = _auto_identity_set(
        projected["permission_assessments"],
        "assessment_id",
        "Auto Mode permission assessment",
    )
    decision_ids = _auto_identity_set(
        projected["permission_assessments"],
        "decision_id",
        "Auto Mode permission decision",
    )

    audit_owners: dict[str, tuple[str, dict[str, Any]]] = {}
    for subject_kind, collection in (
        ("result_review", projected["review_runs"]),
        ("permission_review", projected["permission_assessments"]),
    ):
        for record in collection:
            audit_id = record.get("audit_id")
            if (
                not isinstance(audit_id, str)
                or not audit_id
                or audit_id in audit_owners
            ):
                raise AutoModePortabilityError(
                    "duplicate or invalid Auto Mode audit identity"
                )
            audit_owners[audit_id] = (subject_kind, record)

    allowed_branches = branch_ids
    run_by_id = {str(run["run_id"]): run for run in runs}
    run_trust_by_id = {
        run_id: str(run["trust_state"]) for run_id, run in run_by_id.items()
    }
    for collection, requires_run in (
        (runs, False),
        (events, True),
        (projected["review_runs"], True),
        (projected["findings"], True),
        (projected["repair_runs"], True),
        (projected["permission_assessments"], True),
    ):
        for record in collection:
            for field in _AUTO_SCOPE_FIELDS:
                scoped = record.get(field)
                if not isinstance(scoped, str) or not scoped:
                    raise AutoModePortabilityError(
                        f"Auto Mode record {field} is missing or invalid"
                    )
            if (
                root_frame_id is not None
                and record.get("root_frame_id") != root_frame_id
            ):
                raise AutoModePortabilityError(
                    "Auto Mode record belongs to another Session"
                )
            if allowed_branches is not None:
                _auto_required_reference(
                    record.get("branch_id"), allowed_branches, "Auto Mode branch"
                )
            if requires_run:
                _auto_required_reference(
                    record.get("run_id"), run_ids, "Auto Mode record"
                )
                parent = run_by_id[str(record["run_id"])]
                if any(
                    record.get(field) != parent.get(field)
                    for field in _AUTO_SCOPE_FIELDS
                ):
                    raise AutoModePortabilityError(
                        "Auto Mode record scope does not match its run"
                    )
            if turn_ids is not None:
                _auto_required_reference(
                    record.get("turn_id"), turn_ids, "Auto Mode turn"
                )

    for run in runs:
        if run.get("mode") not in _AUTO_RUN_MODES:
            raise AutoModePortabilityError("Auto Mode run mode is invalid")
        if run.get("status") not in _AUTO_RUN_STATUSES:
            raise AutoModePortabilityError("Auto Mode run status is invalid")
        if (
            run.get("source_claimed_status") is not None
            and run.get("source_claimed_status") not in _AUTO_RUN_STATUSES
        ):
            raise AutoModePortabilityError("Auto Mode source status is invalid")
        if run.get("source_terminal_reason") is not None and (
            not isinstance(run.get("source_terminal_reason"), str)
            or not run.get("source_terminal_reason")
        ):
            raise AutoModePortabilityError(
                "Auto Mode source terminal reason is invalid"
            )
        _auto_sha256(run.get("candidate_snapshot_sha256"), "candidate snapshot")
        _auto_sha256(run.get("evidence_snapshot_sha256"), "candidate evidence")
        artifact_references = _auto_reference_list(
            run, "candidate_artifact_ids", "Auto Mode candidate Artifact"
        )
        version_references = _auto_reference_list(
            run, "candidate_version_ids", "Auto Mode candidate version"
        )
        if artifact_ids is not None:
            for artifact_id in artifact_references:
                _auto_required_reference(
                    artifact_id, artifact_ids, "Auto Mode candidate Artifact"
                )
        if version_ids is not None:
            for version_id in version_references:
                _auto_required_reference(
                    version_id, version_ids, "Auto Mode candidate version"
                )

    for review in projected["review_runs"]:
        if review.get("status") not in _REVIEW_STATUSES:
            raise AutoModePortabilityError("Auto Mode review status is invalid")
        if (
            review.get("verdict") is not None
            and review.get("verdict") not in _REVIEW_VERDICTS
        ):
            raise AutoModePortabilityError("Auto Mode review verdict is invalid")
        _auto_sha256(review.get("candidate_snapshot_sha256"), "candidate snapshot")
        _auto_sha256(review.get("evidence_snapshot_sha256"), "evidence snapshot")
        _auto_sha256(
            review.get("audit_request_digest"), "review request", required=True
        )
        _auto_sha256(review.get("assessment_digest"), "review assessment")
        for name in ("model_profile_id", "model_fingerprint"):
            if not isinstance(review.get(name), str) or not review.get(name):
                raise AutoModePortabilityError(
                    f"Auto Mode review {name} is missing or invalid"
                )
        if (
            type(review.get("model_profile_revision")) is not int
            or int(review["model_profile_revision"]) < 1
            or type(review.get("round")) is not int
            or int(review["round"]) < 0
            or type(review.get("attempt")) is not int
            or int(review["attempt"]) < 1
        ):
            raise AutoModePortabilityError(
                "Auto Mode review identity or attempt is missing or invalid"
            )
        inert_import = run_trust_by_id[
            str(review["run_id"])
        ] == "quarantined_import" and review.get("status") in {
            "started",
            "unverified_import",
        }
        if (
            review.get("status") != "started"
            and not inert_import
            and (
                review.get("verdict") is None
                or not _auto_sha256(
                    review.get("assessment_digest"),
                    "review assessment",
                    required=True,
                )
            )
        ):
            raise AutoModePortabilityError(
                "Auto Mode completed review proof is incomplete"
            )
    for assessment in projected["permission_assessments"]:
        if assessment.get("status") not in _PERMISSION_STATUSES:
            raise AutoModePortabilityError(
                "Auto Mode permission assessment status is invalid"
            )
        if (
            assessment.get("outcome") is not None
            and assessment.get("outcome") not in _PERMISSION_OUTCOMES
        ):
            raise AutoModePortabilityError("Auto Mode permission outcome is invalid")
        if (
            assessment.get("risk") is not None
            and assessment.get("risk") not in _RISK_LEVELS
        ):
            raise AutoModePortabilityError("Auto Mode permission risk is invalid")
        policy_version = assessment.get("policy_version")
        if (
            not isinstance(policy_version, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", policy_version)
            is None
        ):
            raise AutoModePortabilityError(
                "Auto Mode permission policy version is invalid"
            )
        _auto_sha256(
            assessment.get("action_digest"), "permission action", required=True
        )
        _auto_sha256(
            assessment.get("audit_request_digest"),
            "permission request",
            required=True,
        )
        _auto_sha256(assessment.get("assessment_digest"), "permission assessment")
        inert_import = run_trust_by_id[
            str(assessment["run_id"])
        ] == "quarantined_import" and assessment.get("status") in {
            "started",
            "unverified_import",
        }
        if (
            assessment.get("status") != "started"
            and not inert_import
            and (
                assessment.get("outcome") is None
                or assessment.get("risk") is None
                or not _auto_sha256(
                    assessment.get("assessment_digest"),
                    "permission assessment",
                    required=True,
                )
            )
        ):
            raise AutoModePortabilityError(
                "Auto Mode completed permission proof is incomplete"
            )
    review_by_id = {
        str(review["review_run_id"]): review for review in projected["review_runs"]
    }
    finding_by_id = {
        str(finding["finding_id"]): finding for finding in projected["findings"]
    }
    for finding in projected["findings"]:
        _auto_required_reference(
            finding.get("review_run_id"), review_ids, "Auto Mode finding"
        )
        owner = review_by_id[str(finding["review_run_id"])]
        for key in ("candidate_id", "fingerprint", "category", "claim"):
            if not isinstance(finding.get(key), str) or not finding.get(key):
                raise AutoModePortabilityError(
                    f"Auto Mode finding {key} is missing or invalid"
                )
        severity = finding.get("severity")
        if severity not in {"info", "minor", "major", "material", "high", "critical"}:
            raise AutoModePortabilityError("Auto Mode finding severity is invalid")
        if finding.get("status") not in {
            "open",
            "claimed",
            "unaddressed",
            "resolved",
            "accepted",
            "addressed_pending_review",
            "unverified_import",
        }:
            raise AutoModePortabilityError("Auto Mode finding status is invalid")
        _auto_reference_list(finding, "evidence_refs", "Auto Mode finding evidence")
        if any(
            finding.get(field) != owner.get(field)
            for field in ("run_id", *_AUTO_SCOPE_FIELDS, "candidate_id")
        ):
            raise AutoModePortabilityError(
                "Auto Mode finding does not match its owning review"
            )
        for key in ("created_at", "updated_at"):
            value = finding.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise AutoModePortabilityError(
                    f"Auto Mode finding {key} is missing or invalid"
                )
        if int(finding["updated_at"]) < int(finding["created_at"]):
            raise AutoModePortabilityError(
                "Auto Mode finding update precedes its creation"
            )
        for allowed, key, label in (
            (artifact_ids, "artifact_ids", "Auto Mode finding Artifact"),
            (version_ids, "version_ids", "Auto Mode finding version"),
            (cell_ids, "cell_ids", "Auto Mode finding Cell"),
        ):
            if allowed is not None:
                for reference in finding.get(key) or []:
                    _auto_required_reference(reference, allowed, label)
    for repair in projected["repair_runs"]:
        if repair.get("status") not in _REPAIR_STATUSES:
            raise AutoModePortabilityError("Auto Mode repair status is invalid")
        if repair.get("verification_review_run_id") is not None:
            raise AutoModePortabilityError(
                "Stage 2 repair cannot carry a self-asserted verification review"
            )
        repair_finding_ids = _auto_reference_list(
            repair, "finding_ids", "Auto Mode repair finding"
        )
        if not repair_finding_ids:
            raise AutoModePortabilityError(
                "Auto Mode repair must bind at least one finding"
            )
        for finding_id in repair_finding_ids:
            _auto_required_reference(
                finding_id, finding_ids, "Auto Mode repair finding"
            )
            finding = finding_by_id[finding_id]
            if finding.get("run_id") != repair.get("run_id"):
                raise AutoModePortabilityError(
                    "Auto Mode repair references another run's finding"
                )
        for review_id in (
            [repair.get("verification_review_run_id")]
            if repair.get("verification_review_run_id") is not None
            else []
        ):
            _auto_required_reference(
                review_id, review_ids, "Auto Mode repair verification"
            )
        if version_ids is not None:
            for version_id in _auto_reference_list(
                repair, "before_version_ids", "Auto Mode repair before version"
            ) + _auto_reference_list(
                repair, "after_version_ids", "Auto Mode repair after version"
            ):
                _auto_required_reference(
                    version_id, version_ids, "Auto Mode repair version"
                )
        else:
            _auto_reference_list(
                repair, "before_version_ids", "Auto Mode repair before version"
            )
            _auto_reference_list(
                repair, "after_version_ids", "Auto Mode repair after version"
            )
        execution_groups = _auto_reference_list(
            repair, "execution_group_ids", "Auto Mode repair execution group"
        )
        if action_group_ids is not None:
            for group_id in execution_groups:
                _auto_required_reference(
                    group_id,
                    action_group_ids,
                    "Auto Mode repair execution group",
                )
        if action_group_scopes is not None:
            for group_id in execution_groups:
                group = action_group_scopes.get(group_id)
                if group is None or any(
                    group.get(field) != repair.get(field)
                    for field in ("root_frame_id", "branch_id", "turn_id")
                ):
                    raise AutoModePortabilityError(
                        "Auto Mode repair execution group belongs to another scope"
                    )

    candidate_fields = (
        "candidate_id",
        "candidate_snapshot_sha256",
        "evidence_snapshot_sha256",
        "artifact_set_sha256",
        "candidate_artifact_ids",
        "candidate_version_ids",
    )
    current_candidate_by_run: dict[str, dict[str, Any] | None] = {
        run_id: None for run_id in run_ids
    }
    candidate_bindings_by_run: dict[str, dict[str, dict[str, Any]]] = {
        run_id: {} for run_id in run_ids
    }
    phase_status_by_run: dict[str, str | None] = {run_id: None for run_id in run_ids}
    terminal_by_run: dict[str, dict[str, Any]] = {}
    for event in events:
        payload = event.get("payload", {})
        for allowed, key, label in (
            (artifact_ids, "artifact_ids", "Auto Mode event Artifact"),
            (
                artifact_ids,
                "candidate_artifact_ids",
                "Auto Mode event candidate Artifact",
            ),
            (version_ids, "version_ids", "Auto Mode event version"),
            (
                version_ids,
                "candidate_version_ids",
                "Auto Mode event candidate version",
            ),
            (
                version_ids,
                "before_version_ids",
                "Auto Mode event repair before version",
            ),
            (
                version_ids,
                "after_version_ids",
                "Auto Mode event repair after version",
            ),
        ):
            references = _auto_reference_list(payload, key, label)
            if allowed is not None:
                for reference in references:
                    _auto_required_reference(reference, allowed, label)
        for key, allowed, label in (
            ("review_run_id", review_ids, "Auto Mode event review"),
            ("repair_run_id", repair_ids, "Auto Mode event repair"),
            ("assessment_id", assessment_ids, "Auto Mode event assessment"),
            ("decision_id", decision_ids, "Auto Mode event decision"),
            ("finding_id", finding_ids, "Auto Mode event finding"),
        ):
            if payload.get(key) is not None:
                _auto_required_reference(payload.get(key), allowed, label)
        for finding_id in _auto_reference_list(
            payload, "finding_ids", "Auto Mode event finding"
        ):
            _auto_required_reference(finding_id, finding_ids, "Auto Mode event finding")
        for group_id in _auto_reference_list(
            payload,
            "execution_group_ids",
            "Auto Mode event execution group",
        ):
            if action_group_ids is not None:
                _auto_required_reference(
                    group_id,
                    action_group_ids,
                    "Auto Mode event execution group",
                )
        action_group_id = payload.get("action_group_id")
        if action_group_id is not None:
            if action_group_ids is not None:
                _auto_required_reference(
                    action_group_id,
                    action_group_ids,
                    "Auto Mode event execution group",
                )
            if action_group_scopes is not None:
                group = action_group_scopes.get(str(action_group_id))
                if group is None or any(
                    group.get(field) != event.get(field)
                    for field in ("root_frame_id", "branch_id", "turn_id")
                ):
                    raise AutoModePortabilityError(
                        "Auto Mode event execution group belongs to another scope"
                    )
        run_id = str(event["run_id"])
        event_type = str(event.get("type"))
        repair_binding = (
            event_type == "repair_started"
            and payload.get("phase") == "execution_group_bound"
        )
        if payload.get("phase") is not None and not repair_binding:
            raise AutoModePortabilityError("Auto Mode event phase is invalid")
        if (action_group_id is not None) != repair_binding:
            raise AutoModePortabilityError(
                "Auto Mode repair execution binding is incomplete"
            )
        if repair_binding and set(payload) != {
            "repair_run_id",
            "phase",
            "action_group_id",
            "status",
        }:
            raise AutoModePortabilityError(
                "Auto Mode repair execution binding payload is invalid"
            )
        if run_id in terminal_by_run:
            raise AutoModePortabilityError(
                "Auto Mode run has events after its terminal event"
            )
        if phase_status_by_run[run_id] is None:
            if event_type != "auto_run_started":
                raise AutoModePortabilityError(
                    "Auto Mode run does not begin with its start event"
                )
            if payload.get("mode") != run_by_id[run_id].get("mode"):
                raise AutoModePortabilityError(
                    "Auto Mode run mode disagrees with its start event"
                )
            if payload.get("status") not in {None, "running"}:
                raise AutoModePortabilityError("Auto Mode run start status is invalid")
            if run_by_id[run_id].get("created_at") != event.get("created_at"):
                raise AutoModePortabilityError(
                    "Auto Mode run timestamp disagrees with its start event"
                )
            phase_status_by_run[run_id] = "running"
        elif event_type == "auto_run_started":
            raise AutoModePortabilityError("Auto Mode run has duplicate start events")

        candidate_id = payload.get("candidate_id")
        if event_type == "candidate_ready":
            if phase_status_by_run[run_id] not in {"running", "candidate"}:
                raise AutoModePortabilityError(
                    "Auto Mode candidate event is out of phase"
                )
            if not isinstance(candidate_id, str) or not candidate_id:
                raise AutoModePortabilityError(
                    "Auto Mode candidate event has no candidate identity"
                )
            if payload.get("status") not in {None, "candidate"}:
                raise AutoModePortabilityError(
                    "Auto Mode candidate event status is invalid"
                )
            _auto_sha256(
                payload.get("candidate_snapshot_sha256"),
                "candidate snapshot",
            )
            _auto_sha256(
                payload.get("evidence_snapshot_sha256"),
                "candidate evidence",
            )
            _auto_sha256(
                payload.get("artifact_set_sha256"),
                "candidate artifact set",
            )
            binding = {
                "candidate_id": candidate_id,
                "candidate_snapshot_sha256": payload.get("candidate_snapshot_sha256"),
                "evidence_snapshot_sha256": payload.get("evidence_snapshot_sha256"),
                "artifact_set_sha256": payload.get("artifact_set_sha256"),
                "candidate_artifact_ids": list(
                    payload.get("candidate_artifact_ids") or []
                ),
                "candidate_version_ids": list(
                    payload.get("candidate_version_ids") or []
                ),
            }
            previous_binding = candidate_bindings_by_run[run_id].get(candidate_id)
            if previous_binding is not None and previous_binding != binding:
                raise AutoModePortabilityError(
                    "Auto Mode candidate identity changes its immutable binding"
                )
            candidate_bindings_by_run[run_id][candidate_id] = binding
            current_candidate_by_run[run_id] = binding
            phase_status_by_run[run_id] = "candidate"
        elif (
            event_type == "auto_audit_started"
            and payload.get("subject_kind") == "result_review"
        ):
            if phase_status_by_run[run_id] != "candidate":
                raise AutoModePortabilityError(
                    "Auto Mode result review starts out of phase"
                )
            phase_status_by_run[run_id] = "reviewing"
        elif (
            event_type == "auto_audit_completed"
            and payload.get("subject_kind") == "result_review"
        ):
            if phase_status_by_run[run_id] != "reviewing":
                raise AutoModePortabilityError(
                    "Auto Mode result review completes out of phase"
                )
            phase_status_by_run[run_id] = "candidate"
        elif event_type == "repair_started" and repair_binding:
            if phase_status_by_run[run_id] != "repairing":
                raise AutoModePortabilityError(
                    "Auto Mode repair execution group binds out of phase"
                )
            if payload.get("status") != "started":
                raise AutoModePortabilityError(
                    "Auto Mode repair execution binding status is invalid"
                )
        elif event_type == "repair_started":
            if phase_status_by_run[run_id] != "candidate":
                raise AutoModePortabilityError("Auto Mode repair starts out of phase")
            current_candidate = current_candidate_by_run[run_id]
            if current_candidate is None or list(
                payload.get("before_version_ids") or []
            ) != list(current_candidate.get("candidate_version_ids") or []):
                raise AutoModePortabilityError(
                    "Auto Mode repair does not bind the current candidate versions"
                )
            phase_status_by_run[run_id] = "repairing"
        elif event_type == "repair_completed":
            if phase_status_by_run[run_id] != "repairing":
                raise AutoModePortabilityError(
                    "Auto Mode repair completes out of phase"
                )
            if payload.get("status") == "completed":
                current_candidate_by_run[run_id] = None
                phase_status_by_run[run_id] = "running"
            else:
                phase_status_by_run[run_id] = "candidate"
        elif event_type == "auto_run_terminal":
            if phase_status_by_run[run_id] in {"reviewing", "repairing"}:
                raise AutoModePortabilityError(
                    "Auto Mode terminal event strands an active phase"
                )
            terminal_status = payload.get("status")
            if terminal_status not in _AUTO_TERMINAL_STATUSES:
                raise AutoModePortabilityError(
                    "Auto Mode terminal event status is invalid"
                )
            terminal_by_run[run_id] = event
            phase_status_by_run[run_id] = terminal_status

        current_candidate = current_candidate_by_run[run_id]
        if candidate_id is not None and (
            current_candidate is None
            or candidate_id != current_candidate.get("candidate_id")
        ):
            raise AutoModePortabilityError(
                "Auto Mode event candidate is not current at this event boundary"
            )

    for run_id, run in run_by_id.items():
        if phase_status_by_run[run_id] is None:
            raise AutoModePortabilityError("Auto Mode run has no start event")
        current_candidate = current_candidate_by_run[run_id]
        expected_candidate = current_candidate or {
            "candidate_id": None,
            "candidate_snapshot_sha256": None,
            "evidence_snapshot_sha256": None,
            "artifact_set_sha256": None,
            "candidate_artifact_ids": [],
            "candidate_version_ids": [],
        }
        for field in candidate_fields:
            actual = run.get(field)
            if field in {"candidate_artifact_ids", "candidate_version_ids"}:
                actual = actual or []
            if actual != expected_candidate[field]:
                raise AutoModePortabilityError(
                    "Auto Mode run candidate disagrees with its event history"
                )
        run_trust_state = run_trust_by_id[run_id]
        if (
            run_trust_state == "local"
            and run.get("status") != phase_status_by_run[run_id]
        ):
            already_reduced = (
                run.get("status") == "unverified"
                and run.get("source_claimed_status") == phase_status_by_run[run_id]
                and str(run_id) not in terminal_by_run
            )
            if not already_reduced:
                raise AutoModePortabilityError(
                    "Auto Mode run status disagrees with its event history"
                )
        if (
            run_trust_state == "quarantined_import"
            and run.get("status") != "unverified_import"
        ):
            raise AutoModePortabilityError(
                "imported Auto Mode run is not visibly inert"
            )
        terminal = terminal_by_run.get(run_id)
        if run_trust_state == "local":
            if terminal is not None and run.get("finished_at") != terminal.get(
                "created_at"
            ):
                raise AutoModePortabilityError(
                    "Auto Mode run completion timestamp disagrees with its event history"
                )
            if terminal is None and run.get("finished_at") is not None:
                raise AutoModePortabilityError(
                    "Auto Mode nonterminal run has a completion timestamp"
                )
        if terminal is not None:
            terminal_reason = terminal.get("payload", {}).get("terminal_reason")
            if not isinstance(terminal_reason, str) or not terminal_reason:
                raise AutoModePortabilityError(
                    "Auto Mode terminal reason is missing or invalid"
                )
            if terminal_reason != run.get("terminal_reason") or terminal.get(
                "payload", {}
            ).get("stop_reason") != run.get("stop_reason"):
                raise AutoModePortabilityError(
                    "Auto Mode terminal reason disagrees with its run"
                )
        elif run_trust_state == "local":
            portable_inert = (
                run.get("status") == "unverified"
                and run.get("source_claimed_status") == phase_status_by_run[run_id]
                and run.get("terminal_reason") == "portable_execution_inert"
                and run.get("stop_reason") is None
            )
            if not portable_inert and (
                run.get("terminal_reason") is not None
                or run.get("stop_reason") is not None
            ):
                raise AutoModePortabilityError(
                    "Auto Mode nonterminal run carries a terminal reason"
                )

    for review in projected["review_runs"]:
        run_id = str(review["run_id"])
        candidate_id = str(review.get("candidate_id") or "")
        binding = candidate_bindings_by_run[run_id].get(candidate_id)
        if binding is None or any(
            review.get(field) != binding.get(field)
            for field in (
                "candidate_id",
                "candidate_snapshot_sha256",
                "evidence_snapshot_sha256",
            )
        ):
            raise AutoModePortabilityError(
                "Auto Mode review does not bind an immutable candidate snapshot"
            )

    repair_starts: dict[str, dict[str, Any]] = {}
    repair_completions: dict[str, dict[str, Any]] = {}
    repair_binding_groups: dict[str, list[str]] = {}
    repair_by_id = {
        str(repair["repair_run_id"]): repair for repair in projected["repair_runs"]
    }
    for event in events:
        if event.get("type") not in {"repair_started", "repair_completed"}:
            continue
        payload = event.get("payload", {})
        repair_id = payload.get("repair_run_id")
        if not isinstance(repair_id, str) or repair_id not in repair_by_id:
            raise AutoModePortabilityError(
                "Auto Mode repair event references an unknown repair"
            )
        if repair_by_id[repair_id].get("run_id") != event.get("run_id"):
            raise AutoModePortabilityError(
                "Auto Mode repair event belongs to another run"
            )
        if (
            event.get("type") == "repair_started"
            and payload.get("phase") == "execution_group_bound"
        ):
            action_group_id = str(payload["action_group_id"])
            owner_groups = list(
                repair_by_id[repair_id].get("execution_group_ids") or []
            )
            if action_group_id not in owner_groups:
                raise AutoModePortabilityError(
                    "Auto Mode repair binding is absent from its durable owner"
                )
            bound = repair_binding_groups.setdefault(repair_id, [])
            if action_group_id in bound:
                raise AutoModePortabilityError(
                    "duplicate Auto Mode repair execution binding"
                )
            bound.append(action_group_id)
            continue
        target = (
            repair_starts
            if event.get("type") == "repair_started"
            else repair_completions
        )
        if repair_id in target:
            raise AutoModePortabilityError("duplicate Auto Mode repair event")
        target[repair_id] = event

    for repair_id, completion in repair_completions.items():
        start = repair_starts.get(repair_id)
        if start is None or int(start["event_cursor"]) >= int(
            completion["event_cursor"]
        ):
            raise AutoModePortabilityError(
                "Auto Mode repair completion does not follow one start"
            )
    for repair_id, repair in repair_by_id.items():
        start = repair_starts.get(repair_id)
        if start is None:
            raise AutoModePortabilityError(
                "Auto Mode repair record has no unique started event"
            )
        start_payload = start.get("payload", {})
        if (
            repair.get("created_at") != start.get("created_at")
            or start_payload.get("status") != "started"
            or list(repair.get("finding_ids") or [])
            != list(start_payload.get("finding_ids") or [])
            or list(repair.get("before_version_ids") or [])
            != list(start_payload.get("before_version_ids") or [])
        ):
            raise AutoModePortabilityError(
                "Auto Mode repair owner disagrees with its start event"
            )
        if repair_binding_groups.get(repair_id, []) != list(
            repair.get("execution_group_ids") or []
        ):
            raise AutoModePortabilityError(
                "Auto Mode repair execution bindings disagree with their durable owner"
            )
        completion = repair_completions.get(repair_id)
        if completion is None:
            imported_owner = (
                run_trust_by_id[str(repair["run_id"])] == "quarantined_import"
            )
            valid_statuses = (
                {"started", "unverified_import"} if imported_owner else {"started"}
            )
            if (
                repair.get("status") not in valid_statuses
                or repair.get("finished_at") is not None
                or list(repair.get("after_version_ids") or [])
            ):
                raise AutoModePortabilityError(
                    "Auto Mode started repair owner claims a completion"
                )
            if imported_owner:
                repair["status"] = "unverified_import"
            continue
        completion_payload = completion.get("payload", {})
        if completion_payload.get("status") not in {
            "completed",
            "failed",
            "outcome_unknown",
        }:
            raise AutoModePortabilityError(
                "Auto Mode repair completion status is invalid"
            )
        if completion_payload.get("status") == "completed" and not list(
            repair.get("execution_group_ids") or []
        ):
            raise AutoModePortabilityError(
                "completed Auto Mode repair has no execution ledger"
            )
        if repair.get("finished_at") != completion.get("created_at"):
            raise AutoModePortabilityError(
                "Auto Mode repair completion timestamp disagrees with its owner"
            )
        if run_trust_by_id[str(repair["run_id"])] == "quarantined_import":
            if repair.get("status") != "unverified_import":
                raise AutoModePortabilityError(
                    "imported Auto Mode repair owner is not inert"
                )
        elif repair.get("status") != completion_payload.get("status"):
            raise AutoModePortabilityError(
                "Auto Mode repair status disagrees with its durable event"
            )
        if (
            list(repair.get("after_version_ids") or [])
            != list(completion_payload.get("after_version_ids") or [])
            or list(repair.get("execution_group_ids") or [])
            != list(completion_payload.get("execution_group_ids") or [])
            or completion_payload.get("verification_review_run_id") is not None
        ):
            raise AutoModePortabilityError(
                "Auto Mode repair outcome disagrees with its durable event"
            )

    started_audits: dict[tuple[str, str, str], dict[str, Any]] = {}
    completed_audits: dict[tuple[str, str, str], dict[str, Any]] = {}
    audit_key_by_owner: dict[tuple[str, str], tuple[str, str, str]] = {}

    expected_subject_entities = {
        "result_review": "candidate_evidence_snapshot",
        "permission_review": "approval_action",
    }
    for event in events:
        if event.get("type") not in {"auto_audit_started", "auto_audit_completed"}:
            continue
        payload = event.get("payload", {})
        subject_kind = payload.get("subject_kind")
        if expected_subject_entities.get(subject_kind) != payload.get(
            "subject_entity_kind"
        ):
            raise AutoModePortabilityError("Auto Mode audit subject pair is invalid")
        if not isinstance(payload.get("audit_id"), str) or not payload.get("audit_id"):
            raise AutoModePortabilityError("Auto Mode audit identity is invalid")
        audit_id = str(payload["audit_id"])
        owner = audit_owners.get(audit_id)
        if owner is None or owner[0] != subject_kind:
            raise AutoModePortabilityError(
                "Auto Mode audit event references an unknown subject"
            )
        if owner[1].get("run_id") != event.get("run_id"):
            raise AutoModePortabilityError(
                "Auto Mode audit event belongs to another run"
            )
        expected_entity_id = (
            owner[1].get("candidate_id")
            if subject_kind == "result_review"
            else owner[1].get("decision_id")
        )
        if (
            not isinstance(expected_entity_id, str)
            or not expected_entity_id
            or payload.get("subject_entity_id") != expected_entity_id
        ):
            raise AutoModePortabilityError(
                "Auto Mode audit event subject identity does not match its owner"
            )
        if subject_kind == "result_review":
            if payload.get("review_run_id") != owner[1].get(
                "review_run_id"
            ) or payload.get("candidate_id") != owner[1].get("candidate_id"):
                raise AutoModePortabilityError(
                    "Auto Mode result audit owner binding is invalid"
                )
        else:
            if (
                payload.get("assessment_id") != owner[1].get("assessment_id")
                or payload.get("decision_id") != owner[1].get("decision_id")
                or payload.get("action_digest") != owner[1].get("action_digest")
            ):
                raise AutoModePortabilityError(
                    "Auto Mode permission audit owner binding is invalid"
                )
        request_digest = payload.get("audit_request_digest")
        _auto_sha256(request_digest, "audit request", required=True)
        key = (str(event["run_id"]), audit_id, str(request_digest))
        owner_key = (str(event["run_id"]), audit_id)
        prior_key = audit_key_by_owner.setdefault(owner_key, key)
        if prior_key != key:
            raise AutoModePortabilityError(
                "Auto Mode audit request digest changes across its events"
            )
        target = (
            started_audits
            if event.get("type") == "auto_audit_started"
            else completed_audits
        )
        if key in target:
            raise AutoModePortabilityError("duplicate Auto Mode audit event")
        target[key] = event
        if event.get("type") == "auto_audit_completed":
            _auto_sha256(
                payload.get("assessment_digest"),
                "audit assessment",
                required=True,
            )

    for key, completed in completed_audits.items():
        if key not in started_audits:
            raise AutoModePortabilityError(
                "Auto Mode completed audit has no matching started event"
            )
        started = started_audits[key]
        if int(started["event_cursor"]) >= int(completed["event_cursor"]):
            raise AutoModePortabilityError(
                "Auto Mode audit completion does not follow its start"
            )
        for field in (
            "subject_kind",
            "subject_entity_kind",
            "subject_entity_id",
        ):
            started_value = started.get("payload", {}).get(field)
            completed_value = completed.get("payload", {}).get(field)
            if started_value != completed_value:
                raise AutoModePortabilityError(
                    "Auto Mode audit subject changed between started and completed"
                )

    findings_by_review: dict[str, list[dict[str, Any]]] = {}
    for finding in projected["findings"]:
        findings_by_review.setdefault(str(finding["review_run_id"]), []).append(finding)
    for audit_id, (subject_kind, owner) in audit_owners.items():
        owner_key = (str(owner.get("run_id")), audit_id)
        audit_key = audit_key_by_owner.get(owner_key)
        if audit_key is None or audit_key not in started_audits:
            raise AutoModePortabilityError(
                "Auto Mode audit record has no unique started event"
            )
        start_event = started_audits[audit_key]
        start_payload = start_event.get("payload", {})
        if owner.get("created_at") != start_event.get("created_at"):
            raise AutoModePortabilityError(
                "Auto Mode audit start timestamp disagrees with its owner"
            )
        if start_payload.get("status") != "started":
            raise AutoModePortabilityError("Auto Mode audit start status is invalid")
        if owner.get("audit_request_digest") != start_payload.get(
            "audit_request_digest"
        ):
            raise AutoModePortabilityError(
                "Auto Mode audit request digest disagrees with its owner"
            )
        if subject_kind == "result_review":
            for owner_field, event_field in (
                ("candidate_snapshot_sha256", "candidate_snapshot_sha256"),
                ("evidence_snapshot_sha256", "evidence_snapshot_sha256"),
                ("round", "round"),
                ("attempt", "attempt"),
                ("model_profile_id", "model_profile_id"),
                ("model_profile_revision", "model_profile_revision"),
                ("model_fingerprint", "model_fingerprint"),
            ):
                if owner.get(owner_field) != start_payload.get(event_field):
                    raise AutoModePortabilityError(
                        "Auto Mode result review owner disagrees with its start event"
                    )
        elif owner.get("policy_version") != start_payload.get("policy_version"):
            raise AutoModePortabilityError(
                "Auto Mode permission owner disagrees with its start event"
            )

        completion = completed_audits.get(audit_key)
        if completion is None:
            if str(owner.get("run_id")) in terminal_by_run:
                raise AutoModePortabilityError(
                    "Auto Mode terminal run strands an active audit"
                )
            imported_owner = (
                run_trust_by_id[str(owner["run_id"])] == "quarantined_import"
            )
            valid_statuses = (
                {"started", "unverified_import"} if imported_owner else {"started"}
            )
            if (
                owner.get("status") not in valid_statuses
                or owner.get("finished_at") is not None
                or owner.get("assessment_digest") is not None
                or owner.get("verdict") is not None
                or owner.get("outcome") is not None
                or owner.get("risk") is not None
            ):
                raise AutoModePortabilityError(
                    "Auto Mode started audit owner claims a completion"
                )
            if imported_owner:
                owner["status"] = "unverified_import"
            continue
        completed_payload = completion.get("payload", {})
        if owner.get("finished_at") != completion.get("created_at"):
            raise AutoModePortabilityError(
                "Auto Mode audit completion timestamp disagrees with its owner"
            )
        if owner.get("assessment_digest") != completed_payload.get("assessment_digest"):
            raise AutoModePortabilityError(
                "Auto Mode audit assessment digest disagrees with its owner"
            )
        if owner.get("public_summary") != completed_payload.get("public_summary"):
            raise AutoModePortabilityError(
                "Auto Mode audit public summary disagrees with its owner"
            )
        if run_trust_by_id[str(owner["run_id"])] == "quarantined_import":
            if owner.get("status") != "unverified_import":
                raise AutoModePortabilityError(
                    "imported Auto Mode audit owner is not inert"
                )
        elif owner.get("status") != completed_payload.get("status"):
            raise AutoModePortabilityError(
                "Auto Mode audit status disagrees with its durable event"
            )
        if subject_kind == "result_review":
            if completed_payload.get("status") not in {
                "completed",
                "unavailable",
                "failed",
            }:
                raise AutoModePortabilityError(
                    "Auto Mode review completion status is invalid"
                )
            if owner.get("attempt") != completed_payload.get("attempt") or owner.get(
                "verdict"
            ) != completed_payload.get("verdict"):
                raise AutoModePortabilityError(
                    "Auto Mode review verdict disagrees with its durable event"
                )
            finding_count = len(findings_by_review.get(str(owner["review_run_id"]), []))
            if completed_payload.get("finding_count") != finding_count:
                raise AutoModePortabilityError(
                    "Auto Mode review finding count disagrees with its event"
                )
        elif (
            completed_payload.get("status")
            not in {"completed", "unavailable", "failed"}
            or owner.get("outcome") != completed_payload.get("outcome")
            or owner.get("risk") != completed_payload.get("risk")
        ):
            raise AutoModePortabilityError(
                "Auto Mode permission outcome disagrees with its durable event"
            )
        if (
            subject_kind == "permission_review"
            and completed_payload.get("status") != "completed"
            and completed_payload.get("outcome")
            in {
                "allow",
                "allow_once",
                "allowed",
                "shadow_allow",
            }
        ):
            raise AutoModePortabilityError(
                "failed Auto Mode permission assessment cannot allow"
            )

    for run in runs:
        source_status = run.get("status")
        if not isinstance(source_status, str) or not source_status:
            raise AutoModePortabilityError("Auto Mode run status is invalid")
        terminal = terminal_by_run.get(str(run["run_id"]))
        if terminal is not None:
            terminal_status = terminal.get("payload", {}).get("status")
            if terminal_status is not None and terminal_status != source_status:
                raise AutoModePortabilityError(
                    "Auto Mode terminal event disagrees with its run"
                )
        if source_status not in _NONTERMINAL_RUN_STATUSES | {"verified"}:
            continue
        # Stage 2 carries digests and sanitized references, not the complete
        # canonical Evidence Snapshot/assessment objects needed to rehash those
        # digests independently. Any 64-hex string is forgeable package input;
        # portable views therefore never preserve a Verified claim yet. Active
        # source states are also historical only: a package/share cannot resume
        # the model or any side effect.
        run.setdefault("source_claimed_status", source_status)
        if terminal is not None and run.get("source_terminal_reason") is None:
            run["source_terminal_reason"] = run.get("terminal_reason")
        run["status"] = "unverified"
        run["terminal_reason"] = (
            "portable_proof_incomplete"
            if source_status == "verified"
            else "portable_execution_inert"
        )
        if terminal is not None:
            terminal["payload"] = {
                **dict(terminal.get("payload") or {}),
                "status": "unverified",
                "terminal_reason": run["terminal_reason"],
            }
            terminal["payload_sha256"] = _sha256(_canonical_json(terminal["payload"]))

    result = {
        "schema_version": 1,
        "trust_state": trust_state,
        "historical_selection": historical_selection,
        "effective_selection": dict(EFFECTIVE_AUTO_MODE_OFF),
        "runs": runs,
        "events": events,
        "review_runs": projected["review_runs"],
        "findings": projected["findings"],
        "repair_runs": projected["repair_runs"],
        "permission_assessments": projected["permission_assessments"],
    }
    _assert_no_private_keys(result)
    if len(_canonical_json(result)) > _MAX_PORTABLE_BYTES:
        raise AutoModePortabilityError("Auto Mode portable projection is too large")
    return result


__all__ = [
    "AutoModePortabilityError",
    "EFFECTIVE_AUTO_MODE_OFF",
    "portable_auto_mode_projection",
]
