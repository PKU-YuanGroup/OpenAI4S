"""Stage 2 Auto Mode selection, projection, and event-delivery contracts."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openai4s.config import AutoModeConfig
from openai4s.server.auto_mode import AutoModeError, AutoModeService
from openai4s.server.session_package import session_import_quarantine_key


class _Store:
    def __init__(self) -> None:
        self.frames = {
            "root": {
                "frame_id": "root",
                "root_frame_id": "root",
                "project_id": "project",
            },
            "child": {
                "frame_id": "child",
                "root_frame_id": "root",
                "project_id": "project",
            },
        }
        self.settings: dict[str, str] = {}
        self.selections: dict[tuple[str, str], dict] = {}
        self.projection: dict | None = None
        self.audits: list[dict] = []
        self.set_calls: list[tuple[str, str, dict, int]] = []

    def get_frame(self, frame_id: str):
        return self.frames.get(frame_id)

    def active_session_branch(self, root_frame_id: str) -> str:
        assert root_frame_id == "root"
        return "branch"

    def get_setting(self, key: str, default=None):
        return self.settings.get(key, default)

    def get_auto_mode_selection(self, scope_kind: str, scope_id: str):
        value = self.selections.get((scope_kind, scope_id))
        return dict(value) if value is not None else None

    def set_auto_mode_selection(
        self,
        scope_kind: str,
        scope_id: str,
        values: dict,
        expected_revision: int,
    ):
        self.set_calls.append((scope_kind, scope_id, dict(values), expected_revision))
        current = self.selections.get((scope_kind, scope_id))
        revision = int((current or {}).get("revision") or 0)
        if revision != expected_revision:
            raise ValueError("auto mode selection revision conflict")
        if not values:
            row = {"is_set": False, "revision": revision + 1}
            self.selections[(scope_kind, scope_id)] = row
            return dict(row)
        row = {**values, "is_set": True, "revision": revision + 1}
        self.selections[(scope_kind, scope_id)] = row
        return dict(row)

    def project_auto_mode_run(
        self,
        root_frame_id: str,
        branch_id: str,
        upto_event_cursor=None,
    ):
        assert (root_frame_id, branch_id, upto_event_cursor) == (
            "root",
            "branch",
            None,
        )
        return self.projection

    def list_auto_mode_audits(
        self,
        root_frame_id: str,
        branch_id: str,
        subject_kind=None,
        before=None,
        limit: int = 100,
    ):
        assert (root_frame_id, branch_id) == ("root", "branch")
        rows = [
            row
            for row in self.audits
            if subject_kind is None or row.get("subject_kind") == subject_kind
        ]
        return rows[:limit]


def _config(*, feature=True, deployment=None):
    return SimpleNamespace(
        roadmap_features=SimpleNamespace(stage2_auto_run_storage=feature),
        auto_mode=deployment or AutoModeConfig(),
    )


def _service(store: _Store, *, feature=True, deployment=None, emitted=None):
    sink = emitted if emitted is not None else []
    return AutoModeService(
        store=store,
        config=_config(feature=feature, deployment=deployment),
        emit=lambda root, event: sink.append((root, event)),
    )


def test_selection_precedence_is_quarantine_frame_project_deployment_legacy_default():
    store = _Store()
    store.settings["review:auto:root"] = "1"
    deployment = AutoModeConfig(
        enabled=False,
        result_review_mode="auto_fix",
        approvals_reviewer="user",
        deployment_explicit=True,
    )
    service = _service(store, deployment=deployment)

    assert service.get("child")["selection"]["source"] == "deployment_explicit"

    store.selections[("project", "project")] = {
        "preset": "off",
        "result_review_mode": "review_only",
        "approvals_reviewer": "user",
        "revision": 2,
    }
    assert service.get("root")["selection"]["source"] == "project"

    store.selections[("frame", "root")] = {
        "preset": "autonomous",
        "result_review_mode": "auto_fix",
        "approvals_reviewer": "auto_review",
        "revision": 3,
    }
    assert service.get("root")["selection"]["source"] == "frame"

    store.settings[session_import_quarantine_key("root")] = '{"state":"quarantined"}'
    view = service.get("root")
    assert view["selection"] == {
        "preset": "off",
        "result_review_mode": "off",
        "approvals_reviewer": "user",
        "source": "import_quarantine",
        "explicit": True,
        "revision": 3,
        "source_revision": 0,
    }
    assert view["writable"] is False


def test_unset_deployment_does_not_erase_legacy_result_review():
    store = _Store()
    store.settings["review:auto:root"] = "true"
    service = _service(store, deployment=AutoModeConfig())

    selection = service.get("root")["selection"]
    assert selection["source"] == "legacy_result_review"
    assert selection["preset"] == "off"
    assert selection["result_review_mode"] == "review_only"
    assert selection["approvals_reviewer"] == "user"


def test_legacy_false_and_builtin_defaults_cannot_enable_permission_review():
    store = _Store()
    service = _service(store)
    assert service.get("root")["selection"]["source"] == "built_in_defaults"

    store.settings["review:auto:root"] = "0"
    selection = service.get("root")["selection"]
    assert selection["source"] == "legacy_result_review"
    assert selection["result_review_mode"] == "off"
    assert selection["approvals_reviewer"] == "user"


def test_feature_flag_off_keeps_get_inert_and_refuses_patch():
    store = _Store()
    service = _service(store, feature=False)

    view = service.get("root")
    assert view["feature_enabled"] is False
    assert view["writable"] is False
    assert view["disabled_reason"] == "stage2_feature_disabled"
    assert store.set_calls == []

    with pytest.raises(AutoModeError) as refused:
        service.patch("root", {"revision": 0, "result_review_mode": "review_only"})
    assert (refused.value.status, refused.value.code) == (
        409,
        "auto_mode_storage_disabled",
    )
    assert store.set_calls == []


def test_patch_is_closed_normalized_and_revision_checked():
    store = _Store()
    service = _service(store)

    with pytest.raises(AutoModeError, match="unsupported"):
        service.patch("root", {"revision": 0, "standing_allow": True})
    with pytest.raises(AutoModeError, match="unsupported"):
        service.patch("root", {"revision": 0, "budgets": {"wall_time_s": 1}})
    with pytest.raises(AutoModeError, match="revision is required"):
        service.patch("root", {"result_review_mode": "review_only"})

    updated = service.patch("root", {"revision": 0, "preset": "autonomous"})
    assert updated["selection"]["preset"] == "autonomous"
    assert updated["selection"]["result_review_mode"] == "auto_fix"
    assert updated["selection"]["approvals_reviewer"] == "auto_review"
    assert store.set_calls[-1] == (
        "frame",
        "root",
        {
            "preset": "autonomous",
            "result_review_mode": "auto_fix",
            "approvals_reviewer": "auto_review",
        },
        0,
    )

    with pytest.raises(AutoModeError) as conflict:
        service.patch("root", {"revision": 0, "preset": "off"})
    assert (conflict.value.status, conflict.value.code) == (
        409,
        "auto_mode_revision_conflict",
    )


def test_patch_all_null_clears_frame_override_but_mixed_null_is_rejected():
    store = _Store()
    store.selections[("frame", "root")] = {
        "preset": "off",
        "result_review_mode": "review_only",
        "approvals_reviewer": "user",
        "revision": 4,
    }
    service = _service(store)

    with pytest.raises(AutoModeError, match="all be null"):
        service.patch(
            "root",
            {"revision": 4, "preset": None, "result_review_mode": "off"},
        )

    cleared = service.patch(
        "root",
        {
            "revision": 4,
            "preset": None,
            "result_review_mode": None,
            "approvals_reviewer": None,
        },
    )
    assert store.set_calls[-1] == ("frame", "root", {}, 4)
    assert cleared["selection"]["source"] == "built_in_defaults"
    assert cleared["selection"]["revision"] == 5

    with pytest.raises(AutoModeError) as stale:
        service.patch("root", {"revision": 4, "result_review_mode": "review_only"})
    assert stale.value.code == "auto_mode_revision_conflict"


@pytest.mark.parametrize("marker", ["1", ""])
def test_quarantine_refuses_patch_even_when_feature_is_enabled(marker):
    store = _Store()
    store.settings[session_import_quarantine_key("root")] = marker

    with pytest.raises(AutoModeError) as refused:
        _service(store).patch("root", {"revision": 0, "preset": "off"})
    assert (refused.value.status, refused.value.code) == (
        423,
        "session_import_quarantined",
    )


def test_projection_and_audits_are_allowlisted_not_raw_database_rows():
    store = _Store()
    store.projection = {
        "runs": [
            {
                "run_id": "run-1",
                "root_frame_id": "root",
                "branch_id": "branch",
                "turn_id": "turn-1",
                "execution_id": "execution-1",
                "status": {"authorization": "not public"},
                "candidate_digest": "a" * 64,
                "candidate_snapshot_sha256": "b" * 64,
                "evidence_snapshot_sha256": "c" * 64,
                "artifact_set_sha256": "d" * 64,
                "recovery_required": True,
                "budgets": {
                    "wall_time_s": 30,
                    "private_numeric_setting": 1234,
                },
                "secret_prompt": "do not expose",
            }
        ],
        "events": [{"event_id": "event-2", "event_cursor": 2}],
    }
    store.audits = [
        {
            "audit_id": "audit-1",
            "run_id": "run-1",
            "root_frame_id": "root",
            "branch_id": "branch",
            "turn_id": "turn-1",
            "execution_id": "execution-1",
            "subject_kind": "result_review",
            "subject_entity_kind": "candidate_evidence_snapshot",
            "subject_entity_id": "candidate-1",
            "status": "completed",
            "audit_request_digest": "b" * 64,
            "assessment_digest": "c" * 64,
            "risk": {"authorization": "nested reusable capability"},
            "rationale": "hidden chain of thought",
            "authorization": "Bearer reusable-capability-token",
            "public_summary": "public summary",
            "provider_api_key": "secret",
            "assessment": {
                "verdict": "pass",
                "risk": {"token": "hidden"},
                "authorization": "hidden",
            },
            "findings": [
                {
                    "finding_id": "finding-1",
                    "fingerprint": "fingerprint-1",
                    "evidence_refs": ["cell-1", {"secret": "hidden"}],
                    "reproduction": {"secret": "hidden"},
                    "suggested_fix": "recompute",
                }
            ],
        },
        {
            "audit_id": "audit-malformed",
            "subject_kind": "result_review",
            "subject_entity_kind": "approval_action",
            "audit_request_digest": "not-a-digest",
            "public_summary": "must not appear",
        },
    ]
    service = _service(store)

    view = service.get("root")
    assert view["run"]["run_id"] == "run-1"
    assert view["run"]["candidate_snapshot_sha256"] == "b" * 64
    assert view["run"]["evidence_snapshot_sha256"] == "c" * 64
    assert view["run"]["artifact_set_sha256"] == "d" * 64
    assert view["run"]["recovery_required"] is True
    assert "status" not in view["run"]
    assert view["run"]["budgets"] == {"wall_time_s": 30}
    assert "secret_prompt" not in view["run"]
    assert view["last_event_id"] == "event-2"
    assert view["last_event_ordinal"] == 2
    page = service.list_audits("root", subject_kind="result_review", limit=10)
    assert page["audits"][0]["public_summary"] == "public summary"
    assert "provider_api_key" not in page["audits"][0]
    assert "rationale" not in page["audits"][0]
    assert "authorization" not in page["audits"][0]
    assert "risk" not in page["audits"][0]
    assert page["audits"][0]["assessment"] == {"verdict": "pass"}
    assert page["audits"][0]["findings"] == [
        {
            "finding_id": "finding-1",
            "fingerprint": "fingerprint-1",
            "evidence_refs": ["cell-1"],
            "suggested_fix": "recompute",
        }
    ]
    json.dumps(page, allow_nan=False)
    assert [row["audit_id"] for row in page["audits"]] == ["audit-1"]

    with pytest.raises(AutoModeError, match="subject_kind"):
        service.list_audits("root", subject_kind="guardian", limit=10)


def test_only_newly_created_canonical_committed_event_is_broadcast():
    store = _Store()
    emitted: list[tuple[str, dict]] = []
    service = _service(store, emitted=emitted)
    event = {
        "type": "candidate_ready",
        "event_id": "event-1",
        "event_cursor": 1,
        "run_id": "run-1",
        "root_frame_id": "root",
        "branch_id": "branch",
        "turn_id": "turn",
        "execution_id": "execution",
        "created_at": 123,
        "status": "candidate",
        "secret": "not public",
    }

    service.publish_committed({"event": event, "created": False})
    service.publish_committed(
        {"event": {**event, "type": "review_started"}, "created": True}
    )
    service.publish_committed({"event": event, "created": True})

    assert emitted == [
        (
            "root",
            {
                "schema_version": 1,
                "type": "candidate_ready",
                "event_id": "event-1",
                "event_ordinal": 1,
                "run_id": "run-1",
                "root_frame_id": "root",
                "branch_id": "branch",
                "turn_id": "turn",
                "execution_id": "execution",
                "occurred_at": 123,
                "status": "candidate",
            },
        )
    ]


def test_broadcast_failure_cannot_roll_back_committed_rest_truth():
    store = _Store()
    store.projection = {
        "run": {
            "run_id": "run-1",
            "root_frame_id": "root",
            "branch_id": "branch",
            "turn_id": "turn",
            "execution_id": "execution",
            "status": "candidate",
        },
        "last_event_id": "event-1",
        "last_event_ordinal": 1,
    }
    service = AutoModeService(
        store=store,
        config=_config(),
        emit=lambda _root, _event: (_ for _ in ()).throw(OSError("socket gone")),
    )

    service.publish_committed(
        {
            "created": True,
            "event": {
                "type": "candidate_ready",
                "event_id": "event-1",
                "event_ordinal": 1,
                "run_id": "run-1",
                "root_frame_id": "root",
                "branch_id": "branch",
                "turn_id": "turn",
                "execution_id": "execution",
            },
        }
    )

    assert service.last_delivery_error == "OSError: socket gone"
    assert service.get("root")["last_event_id"] == "event-1"


def test_audit_live_event_extracts_only_valid_closed_payload_fields():
    store = _Store()
    emitted: list[tuple[str, dict]] = []
    service = _service(store, emitted=emitted)
    base = {
        "type": "auto_audit_started",
        "event_id": "event-audit",
        "event_cursor": 7,
        "run_id": "run-1",
        "root_frame_id": "root",
        "branch_id": "branch",
        "turn_id": "turn",
        "execution_id": "execution",
        "created_at": 321,
        "phase": {"authorization": "hidden"},
        "payload": {
            "audit_id": "audit-1",
            "subject_kind": "result_review",
            "subject_entity_kind": "candidate_evidence_snapshot",
            "subject_entity_id": "candidate-1",
            "audit_request_digest": "a" * 64,
            "error_kind": {"token": "hidden"},
            "authorization": "reusable capability",
            "rationale": "hidden reasoning",
        },
    }

    service.publish_committed({"created": True, "event": base})
    assert emitted[0][1]["event_ordinal"] == 7
    assert emitted[0][1]["occurred_at"] == 321
    assert emitted[0][1]["audit_id"] == "audit-1"
    assert emitted[0][1]["subject_kind"] == "result_review"
    assert "phase" not in emitted[0][1]
    assert "error_kind" not in emitted[0][1]
    assert "authorization" not in emitted[0][1]
    assert "rationale" not in emitted[0][1]

    malformed = {
        **base,
        "event_id": "event-bad",
        "payload": {
            **base["payload"],
            "subject_entity_kind": "approval_action",
        },
    }
    service.publish_committed({"created": True, "event": malformed})
    assert len(emitted) == 1

    unbound_permission = {
        **base,
        "event_id": "event-permission",
        "payload": {
            **base["payload"],
            "subject_kind": "permission_review",
            "subject_entity_kind": "approval_action",
            "subject_entity_id": "decision-1",
        },
    }
    service.publish_committed({"created": True, "event": unbound_permission})
    assert len(emitted) == 1


def test_unknown_frame_fails_without_reading_or_writing_selection():
    store = _Store()
    service = _service(store)

    with pytest.raises(AutoModeError) as missing:
        service.get("missing")
    assert (missing.value.status, missing.value.code) == (404, "frame_not_found")
    assert store.set_calls == []
