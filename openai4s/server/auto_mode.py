"""Stage 2 Auto Mode selection and durable-state projection.

This module is intentionally orchestration-free.  It resolves configuration,
projects committed repository state, and forwards a newly-created durable
event as a best-effort WebSocket hint.  It never invokes a Reviewer, Repair
Agent, Permission Guardian, model provider, or permission broker.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict
from typing import Any, Callable, Mapping, Protocol

from openai4s.config import AutoModeConfig
from openai4s.server.reviews import legacy_auto_mode_selection
from openai4s.server.session_package import session_import_quarantine_key

AUTO_MODE_SCHEMA_VERSION = 1
RESULT_REVIEW_MODES = frozenset({"off", "review_only", "auto_fix"})
APPROVAL_REVIEWERS = frozenset({"user", "auto_review"})
AUTO_MODE_PRESETS = frozenset({"off", "autonomous"})
AUDIT_SUBJECT_KINDS = frozenset({"result_review", "permission_review"})

# Literal addressed envelopes let the contract scanner discover the exact
# outbound vocabulary while also giving publication one canonical spelling.
# These are prototypes only; no empty root id is ever emitted.
_AUTO_EVENT_PROTOTYPES = {
    "auto_run_started": {"type": "auto_run_started", "root_frame_id": ""},
    "candidate_ready": {"type": "candidate_ready", "root_frame_id": ""},
    "auto_audit_started": {"type": "auto_audit_started", "root_frame_id": ""},
    "auto_audit_completed": {
        "type": "auto_audit_completed",
        "root_frame_id": "",
    },
    "repair_started": {"type": "repair_started", "root_frame_id": ""},
    "repair_completed": {"type": "repair_completed", "root_frame_id": ""},
    "auto_run_terminal": {"type": "auto_run_terminal", "root_frame_id": ""},
}
CANONICAL_AUTO_EVENTS = frozenset(_AUTO_EVENT_PROTOTYPES)

_RUN_FIELDS = (
    "run_id",
    "root_frame_id",
    "branch_id",
    "turn_id",
    "execution_id",
    "mode",
    "status",
    "result_review_mode",
    "approvals_reviewer",
    "review_round",
    "repair_round",
    "candidate_id",
    "candidate_digest",
    "candidate_snapshot_sha256",
    "evidence_snapshot_sha256",
    "artifact_set_sha256",
    "candidate_version_ids",
    "terminal_reason",
    "reason",
    "user_truth",
    "recovery_required",
    "started_at",
    "updated_at",
    "terminal_at",
    "finished_at",
    "last_event_id",
    "last_event_ordinal",
)
_AUDIT_FIELDS = (
    "audit_id",
    "run_id",
    "root_frame_id",
    "branch_id",
    "turn_id",
    "execution_id",
    "subject_kind",
    "subject_entity_kind",
    "subject_entity_id",
    "status",
    "verdict",
    "outcome",
    "decision",
    "risk",
    "round",
    "attempt",
    "reviewer_profile_id",
    "profile_revision",
    "reviewer_fingerprint",
    "audit_request_digest",
    "assessment_digest",
    "action_digest",
    "candidate_digest",
    "finding_count",
    "rationale_summary",
    "public_summary",
    "error_kind",
    "created_at",
    "started_at",
    "completed_at",
    "event_ordinal",
)
_FINDING_FIELDS = (
    "finding_id",
    "review_run_id",
    "run_id",
    "candidate_id",
    "fingerprint",
    "severity",
    "category",
    "status",
    "claim",
    "claim_ref",
    "evidence_refs",
    "artifact_ids",
    "version_ids",
    "cell_ids",
    "reproduction",
    "suggested_fix",
    "confidence",
)
_ASSESSMENT_FIELDS = (
    "verdict",
    "decision",
    "risk",
    "outcome",
    "rationale_summary",
    "public_summary",
    "error_kind",
    "retryable",
)
_EVENT_FIELDS = (
    "schema_version",
    "event_id",
    "event_ordinal",
    "run_id",
    "root_frame_id",
    "branch_id",
    "turn_id",
    "execution_id",
    "parent_event_id",
    "occurred_at",
    "phase",
    "mode",
    "status",
    "audit_id",
    "subject_kind",
    "subject_entity_kind",
    "subject_entity_id",
    "audit_request_digest",
    "assessment_digest",
    "action_digest",
    "candidate_id",
    "candidate_digest",
    "candidate_snapshot_sha256",
    "evidence_snapshot_sha256",
    "artifact_set_sha256",
    "candidate_artifact_ids",
    "candidate_version_ids",
    "review_run_id",
    "repair_run_id",
    "assessment_id",
    "decision_id",
    "policy_version",
    "model_profile_id",
    "model_profile_revision",
    "model_fingerprint",
    "finding_ids",
    "before_version_ids",
    "after_version_ids",
    "execution_group_ids",
    "verdict",
    "outcome",
    "risk",
    "review_round",
    "repair_round",
    "finding_count",
    "terminal_reason",
    "user_truth",
    "error_kind",
)
_EVENT_PAYLOAD_FIELDS = (
    "mode",
    "status",
    "audit_id",
    "subject_kind",
    "subject_entity_kind",
    "subject_entity_id",
    "audit_request_digest",
    "assessment_digest",
    "action_digest",
    "candidate_id",
    "candidate_digest",
    "candidate_snapshot_sha256",
    "evidence_snapshot_sha256",
    "artifact_set_sha256",
    "candidate_artifact_ids",
    "candidate_version_ids",
    "review_run_id",
    "repair_run_id",
    "assessment_id",
    "decision_id",
    "policy_version",
    "model_profile_id",
    "model_profile_revision",
    "model_fingerprint",
    "finding_ids",
    "before_version_ids",
    "after_version_ids",
    "execution_group_ids",
    "verdict",
    "outcome",
    "risk",
    "review_round",
    "repair_round",
    "finding_count",
    "terminal_reason",
    "user_truth",
    "error_kind",
)
_AUDIT_SUBJECT_ENTITY = {
    "result_review": "candidate_evidence_snapshot",
    "permission_review": "approval_action",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_PUBLIC_TEXT = 20_000
_MAX_PUBLIC_ITEMS = 1_000
_MAX_IDENTIFIER = 512
_MAX_SAFE_INTEGER = (1 << 63) - 1
_OMIT = object()
_BUDGET_FIELDS = frozenset(
    {
        "max_review_rounds",
        "max_repair_rounds",
        "repair_turns_per_round",
        "max_extra_cells",
        "wall_time_s",
        "extra_token_multiplier",
        "repeated_finding_limit",
        "same_action_no_delta_limit",
        "no_progress_turn_limit",
        "guardian_timeout_s",
        "guardian_consecutive_denial_limit",
        "guardian_window_size",
        "guardian_window_denial_limit",
    }
)


class AutoModeStore(Protocol):
    def get_frame(self, frame_id: str) -> dict | None: ...

    def active_session_branch(self, root_frame_id: str) -> str: ...

    def get_setting(self, key: str, default: str | None = None) -> str | None: ...

    def get_auto_mode_selection(
        self, scope_kind: str, scope_id: str
    ) -> dict | None: ...

    def set_auto_mode_selection(
        self,
        scope_kind: str,
        scope_id: str,
        values: dict,
        expected_revision: int,
    ) -> dict | None: ...

    def project_auto_mode_run(
        self,
        root_frame_id: str,
        branch_id: str,
        upto_event_cursor: int | None = None,
    ) -> dict | None: ...

    def list_auto_mode_audits(
        self,
        root_frame_id: str,
        branch_id: str,
        subject_kind: str | None = None,
        before: str | None = None,
        limit: int = 100,
    ) -> list[dict] | dict: ...


class AutoModeError(ValueError):
    """A safe client-visible Auto Mode refusal."""

    def __init__(self, status: int, message: str, code: str) -> None:
        super().__init__(message)
        self.status = int(status)
        self.code = str(code)


def _revision(row: Mapping[str, Any] | None) -> int:
    value = (row or {}).get("revision", 0)
    return value if type(value) is int and value >= 0 else 0


def _selection_values(raw: Mapping[str, Any]) -> dict[str, str]:
    """Normalize one trusted row, raising when durable state is malformed."""

    nested = raw.get("values")
    source = nested if isinstance(nested, Mapping) else raw
    preset_raw = source.get("preset")
    if preset_raw is None and "enabled" in source:
        preset_raw = "autonomous" if source.get("enabled") is True else "off"
    preset = str(preset_raw or "off").strip().lower()
    result_mode = str(source.get("result_review_mode") or "off").strip().lower()
    approvals = str(source.get("approvals_reviewer") or "user").strip().lower()
    if preset not in AUTO_MODE_PRESETS:
        raise ValueError("invalid stored Auto Mode preset")
    if result_mode not in RESULT_REVIEW_MODES:
        raise ValueError("invalid stored result review mode")
    if approvals not in APPROVAL_REVIEWERS:
        raise ValueError("invalid stored approvals reviewer")
    if preset == "autonomous":
        result_mode = "auto_fix"
        approvals = "auto_review"
    return {
        "preset": preset,
        "result_review_mode": result_mode,
        "approvals_reviewer": approvals,
    }


def _deployment_values(config: AutoModeConfig) -> dict[str, str]:
    return {
        "preset": config.preset,
        "result_review_mode": config.result_review_mode,
        "approvals_reviewer": config.approvals_reviewer,
    }


def _safe_selection() -> dict[str, str]:
    return {
        "preset": "off",
        "result_review_mode": "off",
        "approvals_reviewer": "user",
    }


def _public_value(value: Any, *, list_items: bool = True) -> Any:
    """Return one bounded JSON value, never an arbitrary nested row value."""

    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        return value if abs(value) <= _MAX_SAFE_INTEGER else _OMIT
    if isinstance(value, float):
        return value if math.isfinite(value) else _OMIT
    if isinstance(value, str):
        return value[:_MAX_PUBLIC_TEXT]
    if list_items and isinstance(value, (list, tuple)):
        public: list[Any] = []
        for item in value[:_MAX_PUBLIC_ITEMS]:
            bounded = _public_value(item, list_items=False)
            if bounded is not _OMIT:
                public.append(bounded)
        return public
    # Durable snapshots may originate in imported packages. A field name is
    # not sufficient reason to serialize an arbitrary mapping/object from one.
    return _OMIT


def _public_mapping(raw: Mapping[str, Any], fields: tuple[str, ...]) -> dict:
    public: dict[str, Any] = {}
    for name in fields:
        if name not in raw:
            continue
        value = _public_value(raw[name])
        if value is not _OMIT:
            public[name] = value
    return public


def _identifier(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > _MAX_IDENTIFIER:
        return None
    return value


def _drop_invalid_identifiers(public: dict, fields: tuple[str, ...]) -> None:
    for name in fields:
        if name in public and _identifier(public[name]) is None:
            public.pop(name, None)


def _drop_invalid_text(public: dict, fields: tuple[str, ...]) -> None:
    for name in fields:
        value = public.get(name)
        if name in public and (not isinstance(value, str) or not value):
            public.pop(name, None)


def _drop_invalid_counts(public: dict, fields: tuple[str, ...]) -> None:
    for name in fields:
        value = public.get(name)
        if name in public and (
            type(value) is not int or not 0 <= value <= _MAX_SAFE_INTEGER
        ):
            public.pop(name, None)


def _digest(value: Any) -> str | None:
    return value if isinstance(value, str) and _SHA256.fullmatch(value) else None


def _string_list(value: Any) -> list[str] | None:
    if not isinstance(value, (list, tuple)):
        return None
    return [item for item in value[:_MAX_PUBLIC_ITEMS] if _identifier(item) is not None]


def _public_run(raw: Any) -> dict | None:
    if not isinstance(raw, Mapping):
        return None
    public = _public_mapping(raw, _RUN_FIELDS)
    if any(
        _identifier(public.get(name)) is None
        for name in (
            "run_id",
            "root_frame_id",
            "branch_id",
            "turn_id",
            "execution_id",
        )
    ):
        return None
    _drop_invalid_identifiers(
        public,
        (
            "root_frame_id",
            "branch_id",
            "turn_id",
            "execution_id",
            "candidate_id",
            "last_event_id",
        ),
    )
    _drop_invalid_text(
        public,
        (
            "mode",
            "status",
            "result_review_mode",
            "approvals_reviewer",
            "terminal_reason",
            "reason",
            "user_truth",
        ),
    )
    _drop_invalid_counts(public, ("review_round", "repair_round", "last_event_ordinal"))
    for name in (
        "candidate_digest",
        "candidate_snapshot_sha256",
        "evidence_snapshot_sha256",
        "artifact_set_sha256",
    ):
        if name in public and _digest(public[name]) is None:
            public.pop(name, None)
    versions = _string_list(public.get("candidate_version_ids"))
    if versions is not None:
        public["candidate_version_ids"] = versions
    else:
        public.pop("candidate_version_ids", None)
    if type(public.get("recovery_required")) is not bool:
        public.pop("recovery_required", None)
    budgets = raw.get("budgets")
    if isinstance(budgets, Mapping):
        public["budgets"] = {
            key: value
            for key, value in budgets.items()
            if key in _BUDGET_FIELDS
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and value >= 0
        }
    return public or None


def _public_finding(raw: Any) -> dict | None:
    if not isinstance(raw, Mapping):
        return None
    value = _public_mapping(raw, _FINDING_FIELDS)
    if _identifier(value.get("finding_id")) is None:
        return None
    _drop_invalid_identifiers(
        value,
        ("review_run_id", "run_id", "candidate_id", "fingerprint", "claim_ref"),
    )
    _drop_invalid_text(
        value,
        (
            "severity",
            "category",
            "status",
            "claim",
            "reproduction",
            "suggested_fix",
        ),
    )
    confidence = value.get("confidence")
    if "confidence" in value and (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(float(confidence))
        or not 0 <= float(confidence) <= 1
    ):
        value.pop("confidence", None)
    for name in ("evidence_refs", "artifact_ids", "version_ids", "cell_ids"):
        refs = _string_list(value.get(name))
        if refs is not None:
            value[name] = refs
        else:
            value.pop(name, None)
    return value or None


def _public_audit(raw: Any) -> dict | None:
    if not isinstance(raw, Mapping):
        return None
    public = _public_mapping(raw, _AUDIT_FIELDS)
    subject_kind = public.get("subject_kind")
    if (
        subject_kind not in _AUDIT_SUBJECT_ENTITY
        or public.get("subject_entity_kind") != _AUDIT_SUBJECT_ENTITY[subject_kind]
        or _identifier(public.get("audit_id")) is None
        or _identifier(public.get("run_id")) is None
        or any(
            _identifier(public.get(name)) is None
            for name in (
                "root_frame_id",
                "branch_id",
                "turn_id",
                "execution_id",
                "subject_entity_id",
            )
        )
        or _digest(public.get("audit_request_digest")) is None
    ):
        return None
    assessment_digest = public.get("assessment_digest")
    if assessment_digest is not None and _digest(assessment_digest) is None:
        return None
    for name in ("action_digest", "candidate_digest"):
        if name in public and _digest(public[name]) is None:
            return None
    if (
        subject_kind == "permission_review"
        and _digest(public.get("action_digest")) is None
    ):
        return None
    _drop_invalid_identifiers(
        public,
        (
            "root_frame_id",
            "branch_id",
            "turn_id",
            "execution_id",
            "subject_entity_id",
            "reviewer_profile_id",
            "reviewer_fingerprint",
        ),
    )
    _drop_invalid_text(
        public,
        (
            "status",
            "verdict",
            "outcome",
            "decision",
            "risk",
            "rationale_summary",
            "public_summary",
            "error_kind",
        ),
    )
    _drop_invalid_counts(
        public,
        ("round", "attempt", "profile_revision", "finding_count", "event_ordinal"),
    )
    for summary_name in ("rationale_summary", "public_summary"):
        if isinstance(public.get(summary_name), str):
            public[summary_name] = public[summary_name][:_MAX_PUBLIC_TEXT]
    assessment = raw.get("assessment")
    if isinstance(assessment, Mapping):
        safe_assessment = _public_mapping(assessment, _ASSESSMENT_FIELDS)
        _drop_invalid_text(
            safe_assessment,
            (
                "verdict",
                "decision",
                "risk",
                "outcome",
                "rationale_summary",
                "public_summary",
                "error_kind",
            ),
        )
        if type(safe_assessment.get("retryable")) is not bool:
            safe_assessment.pop("retryable", None)
        for summary_name in ("rationale_summary", "public_summary"):
            if isinstance(safe_assessment.get(summary_name), str):
                safe_assessment[summary_name] = safe_assessment[summary_name][
                    :_MAX_PUBLIC_TEXT
                ]
        public["assessment"] = safe_assessment
    findings = raw.get("findings")
    if isinstance(findings, list):
        public["findings"] = [
            item
            for item in (_public_finding(finding) for finding in findings[:1000])
            if item is not None
        ]
    return public or None


def _event_kind(raw: Mapping[str, Any]) -> str:
    for key in ("type", "event_type", "kind"):
        value = raw.get(key)
        if isinstance(value, str):
            return value
    return ""


def public_auto_event(raw: Any) -> dict | None:
    """Return the bounded live event, or None for an alias/malformed row."""

    if not isinstance(raw, Mapping):
        return None
    event_type = _event_kind(raw)
    prototype = _AUTO_EVENT_PROTOTYPES.get(event_type)
    if prototype is None:
        return None
    public = dict(prototype)
    public.update(_public_mapping(raw, _EVENT_FIELDS))
    payload = raw.get("payload")
    if isinstance(payload, Mapping):
        for key in _EVENT_PAYLOAD_FIELDS:
            if key not in public and key in payload and payload[key] is not None:
                value = _public_value(payload[key])
                if value is not _OMIT:
                    public[key] = value
        if "candidate_digest" not in public and "candidate_snapshot_sha256" in payload:
            value = _public_value(
                payload["candidate_snapshot_sha256"], list_items=False
            )
            if value is not _OMIT:
                public["candidate_digest"] = value
        if "review_round" not in public and "round" in payload:
            value = _public_value(payload["round"], list_items=False)
            if value is not _OMIT:
                public["review_round"] = value
    if "event_ordinal" not in public and type(raw.get("event_cursor")) is int:
        public["event_ordinal"] = raw["event_cursor"]
    if "occurred_at" not in public and "created_at" in raw:
        occurred_at = _public_value(raw["created_at"], list_items=False)
        if occurred_at is not _OMIT:
            public["occurred_at"] = occurred_at
    # This projector defines the wire schema. A corrupted/imported row cannot
    # make the daemon claim that an unknown schema version is understood.
    public["schema_version"] = AUTO_MODE_SCHEMA_VERSION
    public["type"] = event_type
    root = public.get("root_frame_id")
    required = ("event_id", "run_id", "branch_id", "turn_id", "execution_id")
    if (
        type(public.get("event_ordinal")) is not int
        or public["event_ordinal"] < 1
        or public["event_ordinal"] > _MAX_SAFE_INTEGER
        or _identifier(root) is None
        or any(_identifier(public.get(name)) is None for name in required)
    ):
        return None
    _drop_invalid_identifiers(
        public,
        (
            "parent_event_id",
            "audit_id",
            "subject_entity_id",
            "candidate_id",
            "review_run_id",
            "repair_run_id",
            "assessment_id",
            "decision_id",
        ),
    )
    _drop_invalid_text(
        public,
        (
            "phase",
            "mode",
            "status",
            "subject_kind",
            "subject_entity_kind",
            "terminal_reason",
            "user_truth",
            "error_kind",
            "verdict",
            "outcome",
            "risk",
            "policy_version",
            "model_profile_id",
            "model_fingerprint",
        ),
    )
    _drop_invalid_counts(
        public,
        (
            "review_round",
            "repair_round",
            "finding_count",
            "model_profile_revision",
        ),
    )
    for name in (
        "audit_request_digest",
        "assessment_digest",
        "action_digest",
        "candidate_digest",
        "candidate_snapshot_sha256",
        "evidence_snapshot_sha256",
        "artifact_set_sha256",
    ):
        if name in public and _digest(public[name]) is None:
            return None
    for name in (
        "candidate_artifact_ids",
        "candidate_version_ids",
        "finding_ids",
        "before_version_ids",
        "after_version_ids",
        "execution_group_ids",
    ):
        if name in public:
            values = _string_list(public[name])
            if values is None:
                return None
            public[name] = values
    if event_type in {"auto_audit_started", "auto_audit_completed"}:
        subject_kind = public.get("subject_kind")
        if (
            _identifier(public.get("audit_id")) is None
            or _identifier(public.get("subject_entity_id")) is None
            or subject_kind not in _AUDIT_SUBJECT_ENTITY
            or public.get("subject_entity_kind") != _AUDIT_SUBJECT_ENTITY[subject_kind]
            or _digest(public.get("audit_request_digest")) is None
        ):
            return None
        if event_type == "auto_audit_completed" and (
            _digest(public.get("assessment_digest")) is None
        ):
            return None
        if (
            subject_kind == "permission_review"
            and _digest(public.get("action_digest")) is None
        ):
            return None
    return public


class AutoModeService:
    """Resolve effective selection and expose durable Auto Mode truth."""

    def __init__(
        self,
        *,
        store: AutoModeStore,
        config: Any,
        emit: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.store = store
        self.config = config
        self.emit = emit
        self.last_delivery_error: str | None = None

    @property
    def feature_enabled(self) -> bool:
        return bool(self.config.roadmap_features.stage2_auto_run_storage)

    def _scope(self, frame_id: str) -> tuple[dict, str, str, str]:
        frame = self.store.get_frame(str(frame_id))
        if not frame:
            raise AutoModeError(404, "frame not found", "frame_not_found")
        root_frame_id = str(frame.get("root_frame_id") or frame_id)
        root = frame
        if root_frame_id != str(frame_id):
            root = self.store.get_frame(root_frame_id) or frame
        project_id = str(root.get("project_id") or frame.get("project_id") or "")
        branch_id = str(
            self.store.active_session_branch(root_frame_id) or root_frame_id
        )
        return root, root_frame_id, project_id, branch_id

    def _quarantined(self, root_frame_id: str) -> bool:
        # Presence is the durable barrier.  An empty or otherwise corrupt row
        # is not evidence that quarantine ended; only deleting the exact key is.
        return (
            self.store.get_setting(session_import_quarantine_key(root_frame_id))
            is not None
        )

    def _resolve_selection(
        self,
        root_frame_id: str,
        project_id: str,
    ) -> dict:
        frame_row = self.store.get_auto_mode_selection("frame", root_frame_id)
        frame_revision = _revision(frame_row)

        if self._quarantined(root_frame_id):
            values = _safe_selection()
            source = "import_quarantine"
            source_revision = 0
        elif frame_row is not None and frame_row.get("is_set") is not False:
            try:
                values = _selection_values(frame_row)
            except ValueError:
                # A corrupt higher-precedence row must not fall through to a
                # lower setting that enables more autonomy.
                values = _safe_selection()
            source = "frame"
            source_revision = _revision(frame_row)
        else:
            project_row = (
                self.store.get_auto_mode_selection("project", project_id)
                if project_id
                else None
            )
            if project_row is not None and project_row.get("is_set") is not False:
                try:
                    values = _selection_values(project_row)
                except ValueError:
                    values = _safe_selection()
                source = "project"
                source_revision = _revision(project_row)
            elif self.config.auto_mode.deployment_explicit:
                values = _deployment_values(self.config.auto_mode)
                source = "deployment_explicit"
                source_revision = 0
            else:
                legacy = legacy_auto_mode_selection(self.store, root_frame_id)
                if legacy is not None:
                    values = legacy
                    source = "legacy_result_review"
                else:
                    values = _safe_selection()
                    source = "built_in_defaults"
                source_revision = 0

        return {
            **values,
            "source": source,
            "explicit": source != "built_in_defaults",
            # PATCH compares the frame override, not whichever inherited row
            # happened to supply the current effective values.
            "revision": frame_revision,
            "source_revision": source_revision,
        }

    def get(self, frame_id: str) -> dict:
        _root, root_frame_id, project_id, branch_id = self._scope(frame_id)
        selection = self._resolve_selection(root_frame_id, project_id)
        quarantined = selection["source"] == "import_quarantine"
        projection = self.store.project_auto_mode_run(root_frame_id, branch_id)
        projection = projection if isinstance(projection, Mapping) else {}
        raw_run = projection.get("run")
        if raw_run is None:
            runs = projection.get("runs")
            if isinstance(runs, list) and runs:
                raw_run = runs[-1]
        if raw_run is None and projection.get("run_id"):
            raw_run = projection
        last_event_id = projection.get("last_event_id")
        last_event_ordinal = projection.get("last_event_ordinal")
        events = projection.get("events")
        if isinstance(events, list) and events:
            tail = events[-1]
            if isinstance(tail, Mapping):
                last_event_id = last_event_id or tail.get("event_id")
                last_event_ordinal = (
                    last_event_ordinal
                    if last_event_ordinal is not None
                    else tail.get("event_ordinal", tail.get("event_cursor"))
                )
        last_event_id = _identifier(last_event_id)
        if (
            type(last_event_ordinal) is not int
            or not 0 <= last_event_ordinal <= _MAX_SAFE_INTEGER
        ):
            last_event_ordinal = None
        disabled_reason = None
        if quarantined:
            disabled_reason = "import_quarantine"
        elif not self.feature_enabled:
            disabled_reason = "stage2_feature_disabled"
        return {
            "schema_version": AUTO_MODE_SCHEMA_VERSION,
            "feature_enabled": self.feature_enabled,
            "writable": self.feature_enabled and not quarantined,
            "disabled_reason": disabled_reason,
            "root_frame_id": root_frame_id,
            "branch_id": branch_id,
            "selection": selection,
            "deployment": {
                "explicit": bool(self.config.auto_mode.deployment_explicit),
                "explicit_fields": list(
                    self.config.auto_mode.deployment_explicit_fields
                ),
            },
            # Stage 2 exposes deployment hard ceilings read-only.  A later
            # stage may add a complete only-tighten project/frame resolver;
            # accepting half-effective budget PATCHes here would be false.
            "budgets": asdict(self.config.auto_mode.budgets),
            "run": _public_run(raw_run),
            "last_event_id": last_event_id,
            "last_event_ordinal": last_event_ordinal,
        }

    def patch(self, frame_id: str, body: Any) -> dict:
        _root, root_frame_id, project_id, _branch_id = self._scope(frame_id)
        if not self.feature_enabled:
            raise AutoModeError(
                409,
                "Auto Mode storage is disabled by the Stage 2 feature flag",
                "auto_mode_storage_disabled",
            )
        if self._quarantined(root_frame_id):
            raise AutoModeError(
                423,
                "imported Session is quarantined and view-only",
                "session_import_quarantined",
            )
        if not isinstance(body, Mapping):
            raise AutoModeError(400, "JSON object required", "invalid_auto_mode")
        allowed = {
            "revision",
            "preset",
            "result_review_mode",
            "approvals_reviewer",
        }
        unknown = sorted(set(body) - allowed)
        if unknown:
            raise AutoModeError(
                400,
                f"unsupported Auto Mode fields: {', '.join(unknown)}",
                "invalid_auto_mode_fields",
            )
        if "revision" not in body:
            raise AutoModeError(
                400, "revision is required", "auto_mode_revision_required"
            )
        expected_revision = body.get("revision")
        if type(expected_revision) is not int or expected_revision < 0:
            raise AutoModeError(
                400,
                "revision must be a non-negative integer",
                "invalid_auto_mode_revision",
            )
        mutable = [name for name in allowed if name != "revision" and name in body]
        if not mutable:
            raise AutoModeError(
                400, "an Auto Mode selection field is required", "empty_auto_mode_patch"
            )
        null_fields = [name for name in mutable if body.get(name) is None]
        if null_fields:
            all_selection_fields = {
                "preset",
                "result_review_mode",
                "approvals_reviewer",
            }
            if set(mutable) != all_selection_fields or len(null_fields) != 3:
                raise AutoModeError(
                    400,
                    "selection fields must all be null to clear the frame override",
                    "invalid_auto_mode_clear",
                )
            values: dict[str, str] = {}
        else:
            current = self._resolve_selection(root_frame_id, project_id)
            values = {
                "preset": current["preset"],
                "result_review_mode": current["result_review_mode"],
                "approvals_reviewer": current["approvals_reviewer"],
            }
            for name in mutable:
                value = body.get(name)
                if not isinstance(value, str):
                    raise AutoModeError(
                        400, f"{name} must be a string", "invalid_auto_mode"
                    )
                values[name] = value.strip().lower()
            if values["preset"] not in AUTO_MODE_PRESETS:
                raise AutoModeError(400, "invalid preset", "invalid_auto_mode")
            if values["result_review_mode"] not in RESULT_REVIEW_MODES:
                raise AutoModeError(
                    400, "invalid result_review_mode", "invalid_auto_mode"
                )
            if values["approvals_reviewer"] not in APPROVAL_REVIEWERS:
                raise AutoModeError(
                    400, "invalid approvals_reviewer", "invalid_auto_mode"
                )
            if "preset" not in mutable and (
                "result_review_mode" in mutable or "approvals_reviewer" in mutable
            ):
                values["preset"] = "off"
            if values["preset"] == "autonomous":
                values["result_review_mode"] = "auto_fix"
                values["approvals_reviewer"] = "auto_review"
            elif "preset" in mutable and len(mutable) == 1:
                values["result_review_mode"] = "off"
                values["approvals_reviewer"] = "user"

        current_frame = self.store.get_auto_mode_selection("frame", root_frame_id)
        if _revision(current_frame) != expected_revision:
            raise AutoModeError(
                409,
                "Auto Mode selection revision conflict",
                "auto_mode_revision_conflict",
            )
        try:
            self.store.set_auto_mode_selection(
                "frame", root_frame_id, values, expected_revision
            )
        except ValueError as exc:
            raise AutoModeError(
                409,
                "Auto Mode selection revision conflict",
                "auto_mode_revision_conflict",
            ) from exc
        return self.get(root_frame_id)

    def list_audits(
        self,
        frame_id: str,
        *,
        subject_kind: str | None = None,
        before: str | None = None,
        limit: int = 100,
    ) -> dict:
        _root, root_frame_id, _project_id, branch_id = self._scope(frame_id)
        if subject_kind is not None and subject_kind not in AUDIT_SUBJECT_KINDS:
            raise AutoModeError(
                400,
                "subject_kind must be result_review or permission_review",
                "invalid_subject_kind",
            )
        if type(limit) is not int or not 1 <= limit <= 500:
            raise AutoModeError(400, "limit must be in [1, 500]", "invalid_limit")
        if before is not None and (
            not isinstance(before, str) or not before or len(before) > 128
        ):
            raise AutoModeError(400, "invalid before cursor", "invalid_cursor")
        try:
            raw_page = self.store.list_auto_mode_audits(
                root_frame_id,
                branch_id,
                subject_kind=subject_kind,
                before=before,
                limit=limit + 1,
            )
        except ValueError as exc:
            raise AutoModeError(400, "invalid before cursor", "invalid_cursor") from exc
        if isinstance(raw_page, Mapping):
            raw_rows = raw_page.get("audits") or []
            supplied_next = raw_page.get("next_before")
        else:
            raw_rows = raw_page
            supplied_next = None
        rows = list(raw_rows) if isinstance(raw_rows, (list, tuple)) else []
        has_more = len(rows) > limit
        visible = [
            item
            for item in (_public_audit(row) for row in rows[:limit])
            if item is not None
        ]
        next_before: str | None = None
        if isinstance(supplied_next, str) and 0 < len(supplied_next) <= 128:
            next_before = supplied_next
        elif type(supplied_next) is int and 1 <= supplied_next <= _MAX_SAFE_INTEGER:
            next_before = str(supplied_next)
        if has_more and visible and not next_before:
            tail = visible[-1]
            candidate = tail.get("event_ordinal") or tail.get("audit_id")
            if type(candidate) is int and 1 <= candidate <= _MAX_SAFE_INTEGER:
                next_before = str(candidate)
            elif isinstance(candidate, str) and 0 < len(candidate) <= 128:
                next_before = candidate
        return {
            "schema_version": AUTO_MODE_SCHEMA_VERSION,
            "root_frame_id": root_frame_id,
            "branch_id": branch_id,
            "subject_kind": subject_kind,
            "audits": visible,
            "next_before": next_before,
            # Never claim a next page without a bounded cursor the caller can
            # actually send back. Malformed imported rows therefore stop here.
            "has_more": next_before is not None,
        }

    def publish_committed(self, transition: Any) -> Any:
        """Best-effort live forwarding for an already committed transition.

        Repository transactions return ``created=false`` on idempotent replay;
        those must not produce a duplicate live event.  Socket delivery is not
        part of the database transaction and can never turn committed state
        back into failure.  Reopen/REST project from SQLite.
        """

        if not isinstance(transition, Mapping) or transition.get("created") is not True:
            return transition
        event = public_auto_event(transition.get("event"))
        if event is None or self.emit is None:
            return transition
        try:
            self.emit(str(event["root_frame_id"]), event)
            self.last_delivery_error = None
        except Exception as exc:  # noqa: BLE001 - REST remains authoritative
            self.last_delivery_error = f"{type(exc).__name__}: {exc}"[:500]
        return transition


__all__ = [
    "APPROVAL_REVIEWERS",
    "AUDIT_SUBJECT_KINDS",
    "AUTO_MODE_PRESETS",
    "AUTO_MODE_SCHEMA_VERSION",
    "AutoModeError",
    "AutoModeService",
    "AutoModeStore",
    "CANONICAL_AUTO_EVENTS",
    "RESULT_REVIEW_MODES",
    "public_auto_event",
]
