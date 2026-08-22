"""Cross-boundary regressions for Stage-2 Auto Mode portability."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server.auto_mode_portability import (
    AutoModePortabilityError,
    portable_auto_mode_projection,
)
from openai4s.server.session_domain import SessionDomainService
from openai4s.storage.auto_mode import AutoModeConflictError
from openai4s.store import Store
from tests.test_session_package import _portable_auto_mode_projection, _unpack


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _quarantine_value() -> str:
    return _canonical({"reason": "portability_regression", "state": "quarantined"})


def _domain(tmp_path: Path, name: str) -> tuple[Store, SessionDomainService, str]:
    data_dir = tmp_path / name
    data_dir.mkdir()
    store = Store(data_dir / "openai4s.db")
    project = store.create_project(name="Auto Mode portability")
    root = store.new_frame(project_id=project["project_id"], kind="turn", status="done")
    store.ensure_session_branch(root_frame_id=root, branch_id=root)
    store.append_action_group(
        root_frame_id=root,
        branch_id=root,
        turn_id="turn-auto",
        kind="user",
        assistant_message={"role": "user", "content": "Run Auto Mode"},
    )

    def workspace(root_frame_id: str, branch_id: str) -> Path:
        path = data_dir / "workspaces" / root_frame_id / branch_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    return (
        store,
        SessionDomainService(store, data_dir=data_dir, workspace=workspace),
        root,
    )


def _record_verified_run(
    store: Store,
    root: str,
    *,
    run_id: str,
    candidate_id: str,
    marker: str,
    created_at: int,
) -> None:
    candidate_sha256 = _digest({"candidate": marker})
    evidence = {"candidate_id": candidate_id, "marker": marker, "complete": True}
    evidence_sha256 = _digest(evidence)
    store.start_auto_mode_run(
        run_id=run_id,
        idempotency_key=f"{run_id}:start",
        root_frame_id=root,
        branch_id=root,
        turn_id="turn-auto",
        execution_id=f"execution-{run_id}",
        mode="auto_fix",
        selection={
            "preset": "autonomous",
            "result_review_mode": "auto_fix",
            "approvals_reviewer": "auto_review",
            "source": "frame",
        },
        budgets={"max_review_attempts": 2, "max_repair_rounds": 2},
        owner_instance_id="daemon-portability-test",
        created_at=created_at,
    )
    store.record_auto_mode_candidate(
        run_id,
        idempotency_key=f"{run_id}:candidate",
        candidate_id=candidate_id,
        candidate_snapshot_sha256=candidate_sha256,
        evidence_snapshot_sha256=evidence_sha256,
        candidate_version_ids=[],
        created_at=created_at + 1,
    )
    review_id = f"review-{run_id}"
    store.start_auto_mode_review(
        run_id,
        review_run_id=review_id,
        audit_id=f"audit-{run_id}",
        idempotency_key=f"{review_id}:start",
        candidate_id=candidate_id,
        candidate_snapshot_sha256=candidate_sha256,
        evidence_snapshot=evidence,
        evidence_snapshot_sha256=evidence_sha256,
        round_index=0,
        attempt=1,
        reviewer={
            "profile_id": "scientific-reviewer",
            "profile_revision": 7,
            "model_fingerprint": "reviewer-fingerprint-v7",
        },
        started_at=created_at + 2,
    )
    store.complete_auto_mode_review(
        review_id,
        idempotency_key=f"{review_id}:complete",
        status="completed",
        verdict="pass",
        assessment={"public_summary": f"review-{marker}"},
        findings=[],
        completed_at=created_at + 3,
    )
    store.terminate_auto_mode_run(
        run_id,
        idempotency_key=f"{run_id}:terminal",
        status="verified",
        reason="review_passed",
        stop_reason="scientific_review_complete",
        finished_at=created_at + 4,
    )


def _package_auto_mode(package: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        review = json.loads(archive.read("review.json"))
    return dict(review["auto_mode"])


def test_session_domain_round_trip_scopes_reused_candidate_ids_per_run(tmp_path):
    store, domain, root = _domain(tmp_path, "candidate-remap")
    try:
        _record_verified_run(
            store,
            root,
            run_id="run-first",
            candidate_id="candidate-reused",
            marker="FIRST",
            created_at=100,
        )
        _record_verified_run(
            store,
            root,
            run_id="run-second",
            candidate_id="candidate-reused",
            marker="SECOND",
            created_at=200,
        )

        package = domain.session_export(root)["data"]
        imported = domain.session_import(package)
        projected = store.export_auto_mode_projection(imported["root_frame_id"])

        assert len(projected["runs"]) == len(projected["review_runs"]) == 2
        run_by_id = {run["run_id"]: run for run in projected["runs"]}
        assert len({run["candidate_id"] for run in projected["runs"]}) == 2
        assert {review["public_summary"] for review in projected["review_runs"]} == {
            "review-FIRST",
            "review-SECOND",
        }
        for review in projected["review_runs"]:
            assert review["candidate_id"] == run_by_id[review["run_id"]]["candidate_id"]
    finally:
        store.close()


def _event(
    events: list[dict[str, Any]],
    event_type: str,
    scope: dict[str, str],
    payload: dict[str, Any],
) -> dict[str, Any]:
    cursor = len(events) + 1
    event = {
        "event_cursor": cursor,
        "event_id": f"event-{cursor}",
        "type": event_type,
        **scope,
        "payload": payload,
        "payload_sha256": _digest(payload),
        "created_at": cursor,
    }
    events.append(event)
    return event


def _ordered_owner_projection() -> tuple[dict[str, Any], dict[str, str]]:
    root = "source-root"
    events: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    permissions: list[dict[str, Any]] = []
    version_map: dict[str, str] = {}

    for label in ("FIRST", "SECOND"):
        run_id = f"repair-run-{label}"
        candidate_id = f"candidate-{label}"
        version_id = f"version-{label}"
        version_map[version_id] = f"mapped-{version_id}"
        scope = {
            "run_id": run_id,
            "root_frame_id": root,
            "branch_id": root,
            "turn_id": f"turn-{run_id}",
            "execution_id": f"execution-{run_id}",
        }
        candidate_sha256 = _digest({"candidate": label})
        evidence_sha256 = _digest({"evidence": label})
        started = _event(
            events,
            "auto_run_started",
            scope,
            {"mode": "auto_fix", "status": "running"},
        )
        _event(
            events,
            "candidate_ready",
            scope,
            {
                "candidate_id": candidate_id,
                "candidate_snapshot_sha256": candidate_sha256,
                "evidence_snapshot_sha256": evidence_sha256,
                "candidate_artifact_ids": [],
                "candidate_version_ids": [version_id],
                "status": "candidate",
            },
        )
        review_id = f"review-{label}"
        audit_id = f"review-audit-{label}"
        request_digest = _digest({"review": label})
        review_started = _event(
            events,
            "auto_audit_started",
            scope,
            {
                "audit_id": audit_id,
                "review_run_id": review_id,
                "subject_kind": "result_review",
                "subject_entity_kind": "candidate_evidence_snapshot",
                "subject_entity_id": candidate_id,
                "candidate_id": candidate_id,
                "candidate_snapshot_sha256": candidate_sha256,
                "evidence_snapshot_sha256": evidence_sha256,
                "round": 0,
                "attempt": 1,
                "model_profile_id": "scientific-reviewer",
                "model_profile_revision": 1,
                "model_fingerprint": "reviewer-v1",
                "audit_request_digest": request_digest,
                "status": "started",
            },
        )
        finding_ids = [f"finding-{label}-1", f"finding-{label}-2"]
        assessment_digest = _digest({"assessment": label})
        review_completed = _event(
            events,
            "auto_audit_completed",
            scope,
            {
                "audit_id": audit_id,
                "review_run_id": review_id,
                "subject_kind": "result_review",
                "subject_entity_kind": "candidate_evidence_snapshot",
                "subject_entity_id": candidate_id,
                "candidate_id": candidate_id,
                "audit_request_digest": request_digest,
                "assessment_digest": assessment_digest,
                "verdict": "needs_repair",
                "finding_count": 2,
                "status": "completed",
                "public_summary": f"review-{label}",
                "attempt": 1,
            },
        )
        repair_id = f"repair-{label}"
        repair_started = _event(
            events,
            "repair_started",
            scope,
            {
                "repair_run_id": repair_id,
                "finding_ids": finding_ids,
                "before_version_ids": [version_id],
                "status": "started",
            },
        )
        runs.append(
            {
                **scope,
                "mode": "auto_fix",
                "status": "repairing",
                "candidate_id": candidate_id,
                "candidate_snapshot_sha256": candidate_sha256,
                "evidence_snapshot_sha256": evidence_sha256,
                "candidate_artifact_ids": [],
                "candidate_version_ids": [version_id],
                "created_at": started["created_at"],
            }
        )
        reviews.append(
            {
                "review_run_id": review_id,
                "audit_id": audit_id,
                **scope,
                "candidate_id": candidate_id,
                "candidate_snapshot_sha256": candidate_sha256,
                "evidence_snapshot_sha256": evidence_sha256,
                "round": 0,
                "attempt": 1,
                "model_profile_id": "scientific-reviewer",
                "model_profile_revision": 1,
                "model_fingerprint": "reviewer-v1",
                "audit_request_digest": request_digest,
                "assessment_digest": assessment_digest,
                "status": "completed",
                "verdict": "needs_repair",
                "public_summary": f"review-{label}",
                "created_at": review_started["created_at"],
                "finished_at": review_completed["created_at"],
            }
        )
        for ordinal, finding_id in enumerate(finding_ids, start=1):
            findings.append(
                {
                    "finding_id": finding_id,
                    "review_run_id": review_id,
                    **scope,
                    "candidate_id": candidate_id,
                    "fingerprint": f"fingerprint-{label}-{ordinal}",
                    "severity": "minor",
                    "category": "evidence",
                    "claim": f"claim-{label}-{ordinal}",
                    "evidence_refs": [],
                    "artifact_ids": [],
                    "version_ids": [],
                    "cell_ids": [],
                    "status": "open",
                    "created_at": review_completed["created_at"],
                    "updated_at": review_completed["created_at"],
                }
            )
        repairs.append(
            {
                "repair_run_id": repair_id,
                **scope,
                "finding_ids": finding_ids,
                "before_version_ids": [version_id],
                "after_version_ids": [],
                "execution_group_ids": [],
                "status": "started",
                "created_at": repair_started["created_at"],
            }
        )

        permission_run_id = f"permission-run-{label}"
        permission_scope = {
            "run_id": permission_run_id,
            "root_frame_id": root,
            "branch_id": root,
            "turn_id": f"turn-{permission_run_id}",
            "execution_id": f"execution-{permission_run_id}",
        }
        permission_started = _event(
            events,
            "auto_run_started",
            permission_scope,
            {"mode": "auto_fix", "status": "running"},
        )
        assessment_id = f"assessment-{label}"
        permission_audit_id = f"permission-audit-{label}"
        decision_id = f"decision-{label}"
        action_digest = _digest({"action": label})
        permission_request_digest = _digest({"permission": label})
        audit_started = _event(
            events,
            "auto_audit_started",
            permission_scope,
            {
                "audit_id": permission_audit_id,
                "assessment_id": assessment_id,
                "decision_id": decision_id,
                "action_digest": action_digest,
                "subject_kind": "permission_review",
                "subject_entity_kind": "approval_action",
                "subject_entity_id": decision_id,
                "policy_version": "policy-v1",
                "audit_request_digest": permission_request_digest,
                "status": "started",
            },
        )
        permission_assessment_digest = _digest({"permission-assessment": label})
        audit_completed = _event(
            events,
            "auto_audit_completed",
            permission_scope,
            {
                "audit_id": permission_audit_id,
                "assessment_id": assessment_id,
                "decision_id": decision_id,
                "action_digest": action_digest,
                "subject_kind": "permission_review",
                "subject_entity_kind": "approval_action",
                "subject_entity_id": decision_id,
                "audit_request_digest": permission_request_digest,
                "assessment_digest": permission_assessment_digest,
                "outcome": "denied",
                "risk": "high",
                "status": "completed",
                "public_summary": f"permission-{label}",
            },
        )
        terminal = _event(
            events,
            "auto_run_terminal",
            permission_scope,
            {"status": "cancelled", "terminal_reason": "test_complete"},
        )
        runs.append(
            {
                **permission_scope,
                "mode": "auto_fix",
                "status": "cancelled",
                "terminal_reason": "test_complete",
                "created_at": permission_started["created_at"],
                "finished_at": terminal["created_at"],
            }
        )
        permissions.append(
            {
                "assessment_id": assessment_id,
                "audit_id": permission_audit_id,
                **permission_scope,
                "decision_id": decision_id,
                "action_digest": action_digest,
                "policy_version": "policy-v1",
                "audit_request_digest": permission_request_digest,
                "assessment_digest": permission_assessment_digest,
                "status": "completed",
                "outcome": "denied",
                "risk": "high",
                "public_summary": f"permission-{label}",
                "created_at": audit_started["created_at"],
                "finished_at": audit_completed["created_at"],
            }
        )

    # Imported owner IDs are randomized. Deliberately reverse owner groups so
    # the exporter must recover event order instead of retaining input order.
    return (
        {
            "schema_version": 1,
            "trust_state": "local",
            "historical_selection": {
                "preset": "autonomous",
                "result_review_mode": "auto_fix",
                "approvals_reviewer": "auto_review",
                "source": "frame",
            },
            "runs": runs,
            "events": events,
            "review_runs": list(reversed(reviews)),
            "findings": findings[2:] + findings[:2],
            "repair_runs": list(reversed(repairs)),
            "permission_assessments": list(reversed(permissions)),
        },
        version_map,
    )


def _completed_owner_projection() -> dict[str, Any]:
    """Return completed review, permission, and repair owner histories."""

    projection, _version_map = _ordered_owner_projection()
    events = projection["events"]
    runs_by_id = {str(run["run_id"]): run for run in projection["runs"]}
    findings_by_id = {
        str(finding["finding_id"]): finding for finding in projection["findings"]
    }

    # Keep the fixture self-contained for a real share bundle: no Artifact
    # version is needed to establish the owner/trust invariant under test.
    for run in projection["runs"]:
        run["candidate_version_ids"] = []
    for event in events:
        payload = event["payload"]
        for key in ("candidate_version_ids", "before_version_ids"):
            if key in payload:
                payload[key] = []
        event["payload_sha256"] = _digest(payload)

    for repair in projection["repair_runs"]:
        run_id = str(repair["run_id"])
        scope = {
            "run_id": run_id,
            "root_frame_id": str(repair["root_frame_id"]),
            "branch_id": str(repair["branch_id"]),
            "turn_id": str(repair["turn_id"]),
            "execution_id": str(repair["execution_id"]),
        }
        repair["before_version_ids"] = []
        completed = _event(
            events,
            "repair_completed",
            scope,
            {
                "repair_run_id": repair["repair_run_id"],
                "after_version_ids": [],
                "execution_group_ids": [],
                "status": "failed",
            },
        )
        repair.update(status="failed", finished_at=completed["created_at"])
        for finding_id in repair["finding_ids"]:
            findings_by_id[str(finding_id)].update(
                status="unaddressed", updated_at=completed["created_at"]
            )
        terminal = _event(
            events,
            "auto_run_terminal",
            scope,
            {"status": "failed", "terminal_reason": "repair_failed"},
        )
        runs_by_id[run_id].update(
            status="failed",
            terminal_reason="repair_failed",
            finished_at=terminal["created_at"],
        )
    return projection


def _repair_binding_projection() -> tuple[dict[str, Any], str]:
    projection = _completed_owner_projection()
    repair = projection["repair_runs"][0]
    repair_id = str(repair["repair_run_id"])
    group_id = "repair-execution-group"
    repair["execution_group_ids"] = [group_id]
    completion = next(
        event
        for event in projection["events"]
        if event["type"] == "repair_completed"
        and event["payload"]["repair_run_id"] == repair_id
    )
    completion["payload"]["execution_group_ids"] = [group_id]
    completion["payload_sha256"] = _digest(completion["payload"])

    primary_index = next(
        index
        for index, event in enumerate(projection["events"])
        if event["type"] == "repair_started"
        and event["payload"]["repair_run_id"] == repair_id
        and event["payload"].get("phase") is None
    )
    primary = projection["events"][primary_index]
    binding_payload = {
        "repair_run_id": repair_id,
        "phase": "execution_group_bound",
        "action_group_id": group_id,
        "status": "started",
    }
    projection["events"].insert(
        primary_index + 1,
        {
            "event_cursor": 0,
            "event_id": f"binding-{repair_id}",
            "type": "repair_started",
            "run_id": primary["run_id"],
            "root_frame_id": primary["root_frame_id"],
            "branch_id": primary["branch_id"],
            "turn_id": primary["turn_id"],
            "execution_id": primary["execution_id"],
            "payload": binding_payload,
            "payload_sha256": _digest(binding_payload),
            "created_at": primary["created_at"],
        },
    )
    for cursor, event in enumerate(projection["events"], start=1):
        event["event_cursor"] = cursor
    return projection, group_id


def _direct_import(
    store: Store,
    projection: dict[str, Any],
    *,
    project_id: str,
    version_id_map: dict[str, str] | None = None,
) -> tuple[dict[str, Any], str]:
    created = store.create_quarantined_import_session(
        project_id=project_id, quarantine_value=_quarantine_value()
    )
    root = created["root_frame_id"]
    imported = store.import_quarantined_auto_mode_projection(
        projection,
        root_frame_id=root,
        project_id=created["project_id"],
        branch_id=root,
        version_id_map=version_id_map,
        imported_at=500,
    )
    return imported, root


def test_imported_owner_and_finding_order_follows_start_cursor(tmp_path):
    projection, version_map = _ordered_owner_projection()
    observed: set[tuple[tuple[str, ...], ...]] = set()

    for index in range(16):
        store = Store(tmp_path / f"ordering-{index}.db")
        try:
            imported, _root = _direct_import(
                store,
                projection,
                project_id=f"ordering-project-{index}",
                version_id_map=version_map,
            )
            observed.add(
                (
                    tuple(row["public_summary"] for row in imported["review_runs"]),
                    tuple(
                        row["public_summary"]
                        for row in imported["permission_assessments"]
                    ),
                    tuple(
                        row["before_version_ids"][0] for row in imported["repair_runs"]
                    ),
                    tuple(row["claim"] for row in imported["findings"]),
                )
            )
        finally:
            store.close()

    assert observed == {
        (
            ("review-FIRST", "review-SECOND"),
            ("permission-FIRST", "permission-SECOND"),
            ("mapped-version-FIRST", "mapped-version-SECOND"),
            (
                "claim-FIRST-1",
                "claim-FIRST-2",
                "claim-SECOND-1",
                "claim-SECOND-2",
            ),
        )
    }


@pytest.mark.parametrize(
    ("collection", "status"),
    [
        ("review_runs", "unverified_import"),
        ("permission_assessments", "unverified_import"),
        ("repair_runs", "unverified_import"),
    ],
)
def test_local_owner_status_cannot_use_imported_inert_state(collection, status):
    projection = _completed_owner_projection()
    projection[collection][0]["status"] = status

    with pytest.raises(AutoModePortabilityError):
        portable_auto_mode_projection(
            projection,
            trust_state="local",
            root_frame_id="source-root",
            branch_ids={"source-root"},
        )


def test_run_trust_falls_back_for_legacy_packages_and_rejects_invalid_override():
    projection = _completed_owner_projection()
    portable = portable_auto_mode_projection(
        copy.deepcopy(projection),
        trust_state="local",
        root_frame_id="source-root",
        branch_ids={"source-root"},
    )
    assert {run["trust_state"] for run in portable["runs"]} == {"local"}

    projection["runs"][0]["trust_state"] = "trusted"
    with pytest.raises(AutoModePortabilityError, match="run trust state"):
        portable_auto_mode_projection(
            projection,
            trust_state="local",
            root_frame_id="source-root",
            branch_ids={"source-root"},
        )


def test_imported_started_owners_remain_inert_without_completion_proof(tmp_path):
    review_source = _parity_projection()
    review_source["events"] = review_source["events"][:3]
    review_source["permission_assessments"] = []
    review_source["runs"][0].update(status="reviewing")
    review_source["runs"][0].pop("terminal_reason", None)
    review_source["runs"][0].pop("finished_at", None)
    review_source["review_runs"][0].update(status="started")
    for key in ("assessment_digest", "verdict", "public_summary", "finished_at"):
        review_source["review_runs"][0].pop(key, None)

    permission_source = _permission_audit_projection(1)
    permission_source["events"] = permission_source["events"][:2]
    permission_source["runs"][0].update(status="running")
    permission_source["runs"][0].pop("terminal_reason", None)
    permission_source["runs"][0].pop("finished_at", None)
    permission_source["permission_assessments"][0].update(status="started")
    for key in (
        "assessment_digest",
        "outcome",
        "risk",
        "public_summary",
        "finished_at",
    ):
        permission_source["permission_assessments"][0].pop(key, None)

    repair_source, repair_version_map = _ordered_owner_projection()
    cases = (
        ("review", review_source, {"version-1": "mapped-version-1"}, "review_runs"),
        ("permission", permission_source, {}, "permission_assessments"),
        ("repair", repair_source, repair_version_map, "repair_runs"),
    )
    for name, source, version_map, collection in cases:
        store = Store(tmp_path / f"started-{name}.db")
        try:
            imported, root = _direct_import(
                store,
                source,
                project_id=f"started-{name}-project",
                version_id_map=version_map,
            )
            exported = store.export_auto_mode_projection(root)
            reduced = portable_auto_mode_projection(
                exported,
                trust_state="quarantined_import",
                root_frame_id=root,
                branch_ids={root},
                version_ids=set(version_map.values()),
            )
            assert imported[collection]
            assert {owner["status"] for owner in reduced[collection]} == {
                "unverified_import"
            }
        finally:
            store.close()


def test_repair_execution_binding_is_not_a_second_primary_start():
    projection, group_id = _repair_binding_projection()
    portable = portable_auto_mode_projection(
        projection,
        trust_state="local",
        root_frame_id="source-root",
        branch_ids={"source-root"},
        action_group_ids={group_id},
        action_group_scopes={
            group_id: {
                "root_frame_id": "source-root",
                "branch_id": "source-root",
                "turn_id": projection["repair_runs"][0]["turn_id"],
            }
        },
    )
    assert portable["repair_runs"][0]["execution_group_ids"] == [group_id]
    assert (
        sum(
            event["type"] == "repair_started"
            and event["payload"].get("repair_run_id")
            == portable["repair_runs"][0]["repair_run_id"]
            for event in portable["events"]
        )
        == 2
    )


def test_repair_execution_binding_must_exist_on_its_owner():
    projection, group_id = _repair_binding_projection()
    projection["repair_runs"][0]["execution_group_ids"] = []
    completion = next(
        event
        for event in projection["events"]
        if event["type"] == "repair_completed"
        and event["payload"]["repair_run_id"]
        == projection["repair_runs"][0]["repair_run_id"]
    )
    completion["payload"]["execution_group_ids"] = []
    completion["payload_sha256"] = _digest(completion["payload"])

    with pytest.raises(AutoModePortabilityError, match="absent from its durable owner"):
        portable_auto_mode_projection(
            projection,
            trust_state="local",
            root_frame_id="source-root",
            branch_ids={"source-root"},
            action_group_ids={group_id},
        )


def test_repair_owner_group_must_have_one_ordered_binding_event():
    projection, group_id = _repair_binding_projection()
    projection["events"] = [
        event
        for event in projection["events"]
        if event["payload"].get("phase") != "execution_group_bound"
    ]
    for cursor, event in enumerate(projection["events"], start=1):
        event["event_cursor"] = cursor

    with pytest.raises(
        AutoModePortabilityError,
        match="execution bindings disagree with their durable owner",
    ):
        portable_auto_mode_projection(
            projection,
            trust_state="local",
            root_frame_id="source-root",
            branch_ids={"source-root"},
            action_group_ids={group_id},
        )


def test_repair_binding_event_order_must_match_owner_order():
    projection, first_group = _repair_binding_projection()
    second_group = "repair-execution-group-second"
    repair = projection["repair_runs"][0]
    repair["execution_group_ids"] = [first_group, second_group]
    completion = next(
        event
        for event in projection["events"]
        if event["type"] == "repair_completed"
        and event["payload"]["repair_run_id"] == repair["repair_run_id"]
    )
    completion["payload"]["execution_group_ids"] = [first_group, second_group]
    completion["payload_sha256"] = _digest(completion["payload"])
    binding_index = next(
        index
        for index, event in enumerate(projection["events"])
        if event["payload"].get("phase") == "execution_group_bound"
    )
    first_binding = projection["events"][binding_index]
    second_binding = copy.deepcopy(first_binding)
    first_binding["payload"]["action_group_id"] = second_group
    first_binding["payload_sha256"] = _digest(first_binding["payload"])
    second_binding["event_id"] = "binding-second-swapped"
    second_binding["payload"]["action_group_id"] = first_group
    second_binding["payload_sha256"] = _digest(second_binding["payload"])
    projection["events"].insert(binding_index + 1, second_binding)
    for cursor, event in enumerate(projection["events"], start=1):
        event["event_cursor"] = cursor
    scope = {
        "root_frame_id": "source-root",
        "branch_id": "source-root",
        "turn_id": repair["turn_id"],
    }

    with pytest.raises(
        AutoModePortabilityError,
        match="execution bindings disagree with their durable owner",
    ):
        portable_auto_mode_projection(
            projection,
            trust_state="local",
            root_frame_id="source-root",
            branch_ids={"source-root"},
            action_group_ids={first_group, second_group},
            action_group_scopes={first_group: scope, second_group: scope},
        )


def test_session_package_round_trip_preserves_two_repair_bindings_in_order(
    tmp_path,
):
    from openai4s.storage.snapshots import WorkspaceCAS
    from tests.test_auto_mode_faults import _rooted_store, _start

    source_dir = tmp_path / "repair-package"
    source_dir.mkdir()
    store, root = _rooted_store(source_dir)
    project_id = store.get_frame(root)["project_id"]
    source_workspace = source_dir / "workspace"
    source_workspace.mkdir()
    artifact_path = source_workspace / "result.txt"
    artifact_path.write_text("candidate\n", encoding="utf-8")
    cell_id = store.log_cell(
        frame_id=root,
        root_frame_id=root,
        project_id=project_id,
        code="produce_candidate()",
        result={"id": "repair-package-cell", "stdout": "", "stderr": ""},
        cell_index=1,
        state_revision=1,
    )
    artifact = store.save_artifact(
        path=str(artifact_path),
        snapshot_path=str(artifact_path),
        filename="result.txt",
        content_type="text/plain",
        size_bytes=artifact_path.stat().st_size,
        checksum=hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
        producing_cell_id=cell_id,
        frame_id=root,
        root_frame_id=root,
        project_id=project_id,
    )
    version_id = artifact["version_id"]
    _start(store, root)
    evidence = {"candidate_id": "candidate-1", "complete": True}
    evidence_digest = _digest(evidence)
    store.record_auto_mode_candidate(
        "run-1",
        idempotency_key="repair-package:candidate",
        candidate_id="candidate-1",
        candidate_snapshot_sha256="a" * 64,
        evidence_snapshot_sha256=evidence_digest,
        artifact_set_sha256="b" * 64,
        candidate_version_ids=[version_id],
    )
    store.start_auto_mode_review(
        "run-1",
        review_run_id="repair-package-review",
        audit_id="repair-package-audit",
        idempotency_key="repair-package:review:start",
        candidate_id="candidate-1",
        candidate_snapshot_sha256="a" * 64,
        evidence_snapshot=evidence,
        evidence_snapshot_sha256=evidence_digest,
        round_index=0,
        attempt=1,
        reviewer={
            "profile_id": "scientific-reviewer",
            "profile_revision": 1,
            "model_fingerprint": "reviewer-v1",
        },
    )
    store.complete_auto_mode_review(
        "repair-package-review",
        idempotency_key="repair-package:review:complete",
        status="completed",
        verdict="completed_with_issues",
        assessment={"public_summary": "One bounded repair is required."},
        findings=[
            {
                "finding_id": "repair-package-finding",
                "fingerprint": "repair-package-fingerprint",
                "severity": "major",
                "category": "evidence",
                "claim": "Recompute one result.",
                "evidence_refs": [cell_id],
                "artifact_ids": [artifact["artifact_id"]],
                "version_ids": [version_id],
                "cell_ids": [cell_id],
            }
        ],
    )
    tree = WorkspaceCAS(source_dir / "workspace-cas").capture(source_workspace)
    checkpoint = store.create_session_checkpoint(
        checkpoint_id="repair-package-checkpoint",
        root_frame_id=root,
        branch_id=root,
        reason="pre_repair",
        workspace_tree_id=tree["tree_id"],
        auto_event_cursor=store.auto_mode_event_cursor(root),
    )
    store.start_auto_mode_repair(
        "run-1",
        repair_run_id="repair-package-run",
        idempotency_key="repair-package:start",
        finding_ids=["repair-package-finding"],
        before_version_ids=[version_id],
        checkpoint_id=checkpoint["checkpoint_id"],
    )
    first = store.append_action_group(
        root_frame_id=root,
        branch_id=root,
        turn_id="turn-1",
        kind="native_tools",
        assistant_content="first bounded repair group",
    )
    store.bind_auto_mode_repair_execution_group(
        "repair-package-run",
        action_group_id=first["group_id"],
        idempotency_key="repair-package:bind:first",
    )
    second = store.append_action_group(
        root_frame_id=root,
        branch_id=root,
        turn_id="turn-1",
        kind="python_cell",
        assistant_content="second bounded repair group",
    )
    store.bind_auto_mode_repair_execution_group(
        "repair-package-run",
        action_group_id=second["group_id"],
        idempotency_key="repair-package:bind:second",
    )

    def workspace(root_frame_id: str, branch_id: str) -> Path:
        path = source_dir / "package-workspaces" / root_frame_id / branch_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    domain = SessionDomainService(
        store,
        data_dir=source_dir,
        workspace=workspace,
    )
    try:
        first_package = domain.session_export(root)["data"]
        first_auto = json.loads(_unpack(first_package)["review.json"])["auto_mode"]
        assert first_auto["repair_runs"][0]["execution_group_ids"] == [
            first["group_id"],
            second["group_id"],
        ]

        imported = domain.session_import(first_package)
        imported_root = imported["root_frame_id"]
        local_projection = store.export_auto_mode_projection(imported_root)
        imported_groups = local_projection["repair_runs"][0]["execution_group_ids"]
        assert len(imported_groups) == 2
        assert [
            store.get_action_group(group_id)["kind"] for group_id in imported_groups
        ] == [
            "native_tools",
            "python_cell",
        ]
        binding_rows = store._conn.execute(
            "SELECT action_group_id,binding_ordinal,action_group_kind "
            "FROM repair_execution_groups WHERE repair_run_id=? "
            "ORDER BY binding_ordinal",
            (local_projection["repair_runs"][0]["repair_run_id"],),
        ).fetchall()
        assert [tuple(row) for row in binding_rows] == [
            (imported_groups[0], 0, "native_tools"),
            (imported_groups[1], 1, "python_cell"),
        ]

        second_package = domain.session_export(imported_root)["data"]
        second_auto = json.loads(_unpack(second_package)["review.json"])["auto_mode"]
        assert second_auto["repair_runs"][0]["execution_group_ids"] == imported_groups
    finally:
        store.close()


def test_repair_execution_binding_must_reference_known_repair_and_owner_run():
    projection, _group_id = _repair_binding_projection()
    binding = next(
        event
        for event in projection["events"]
        if event["payload"].get("phase") == "execution_group_bound"
    )
    binding["payload"]["repair_run_id"] = "unknown-repair"
    binding["payload_sha256"] = _digest(binding["payload"])
    with pytest.raises(AutoModePortabilityError, match="unknown identity"):
        portable_auto_mode_projection(
            projection,
            trust_state="local",
            root_frame_id="source-root",
            branch_ids={"source-root"},
        )

    projection, _group_id = _repair_binding_projection()
    binding = next(
        event
        for event in projection["events"]
        if event["payload"].get("phase") == "execution_group_bound"
    )
    other = next(
        repair
        for repair in projection["repair_runs"]
        if repair["run_id"] != binding["run_id"]
    )
    binding.update(
        {
            field: other[field]
            for field in (
                "run_id",
                "root_frame_id",
                "branch_id",
                "turn_id",
                "execution_id",
            )
        }
    )
    with pytest.raises(AutoModePortabilityError, match="belongs to another run"):
        portable_auto_mode_projection(
            projection,
            trust_state="local",
            root_frame_id="source-root",
            branch_ids={"source-root"},
        )

    projection, _group_id = _repair_binding_projection()
    binding = next(
        event
        for event in projection["events"]
        if event["payload"].get("phase") == "execution_group_bound"
    )
    binding["payload"]["finding_ids"] = []
    binding["payload_sha256"] = _digest(binding["payload"])
    with pytest.raises(AutoModePortabilityError, match="binding payload is invalid"):
        portable_auto_mode_projection(
            projection,
            trust_state="local",
            root_frame_id="source-root",
            branch_ids={"source-root"},
        )


def test_confirmed_fresh_restart_share_round_trip_preserves_mixed_run_trust(
    tmp_path, monkeypatch
):
    class SilentHub:
        def emitter(self, _root_frame_id):
            return lambda _event: None

        def broadcast(self, _root_frame_id, _event):
            return None

        def drop_frame(self, _root_frame_id):
            return None

    class FakeRecoveryRuntime:
        def fresh_manifests(self):
            return (SimpleNamespace(language="python"),)

        def run(self, _plan):
            return {"ok": True, "status": "active", "recovery_id": "fresh"}

        def kernel_status_event(self, result, recovery_id):
            return {
                "type": "kernel_status",
                "status": result["status"],
                "recovery_id": recovery_id,
            }

    config = Config(
        data_dir=tmp_path / "mixed-trust-runner",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    runner = gateway_mod.SessionRunner(config, SilentHub(), start_idle_sweeper=False)
    try:
        imported, root = _direct_import(
            runner.store,
            _completed_owner_projection(),
            project_id="mixed-trust-project",
        )
        project_id = "mixed-trust-project"
        imported_run_ids = {str(run["run_id"]) for run in imported["runs"]}
        for run in imported["runs"]:
            runner.store.append_action_group(
                root_frame_id=root,
                branch_id=root,
                turn_id=str(run["turn_id"]),
                kind="auto_mode_import_history",
                assistant_content="Imported inert Auto Mode history",
            )

        monkeypatch.setattr(
            runner,
            "_recovery_runtime",
            lambda _state, _emit: FakeRecoveryRuntime(),
        )
        restarted = runner.execute_recovery_action(
            root,
            project_id,
            "restart_fresh",
            confirmed=True,
        )
        assert restarted["quarantine_cleared"] is True

        local_turn_id = "turn-local-after-fresh-restart"
        runner.store.append_action_group(
            root_frame_id=root,
            branch_id=root,
            turn_id=local_turn_id,
            kind="user",
            assistant_message={"role": "user", "content": "Run locally"},
        )
        runner.store.start_auto_mode_run(
            run_id="run-local-after-fresh-restart",
            idempotency_key="run-local-after-fresh-restart:start",
            root_frame_id=root,
            branch_id=root,
            turn_id=local_turn_id,
            execution_id="execution-local-after-fresh-restart",
            mode="auto_fix",
            selection={
                "preset": "autonomous",
                "result_review_mode": "auto_fix",
                "approvals_reviewer": "auto_review",
                "source": "frame",
            },
            budgets={"max_review_attempts": 2, "max_repair_rounds": 2},
            owner_instance_id="daemon-mixed-trust-test",
            created_at=1_000,
        )

        raw_mixed = runner.store.export_auto_mode_projection(root)
        assert raw_mixed["trust_state"] == "local"
        shared = portable_auto_mode_projection(
            raw_mixed,
            trust_state="local",
            root_frame_id=root,
            branch_ids={root},
        )
        trust_by_run = {
            str(run["run_id"]): run["trust_state"] for run in shared["runs"]
        }
        assert {trust_by_run[run_id] for run_id in imported_run_ids} == {
            "quarantined_import"
        }
        assert trust_by_run["run-local-after-fresh-restart"] == "local"
        assert (
            next(
                run
                for run in shared["runs"]
                if run["run_id"] == "run-local-after-fresh-restart"
            )["status"]
            == "unverified"
        )
        for collection in (
            "review_runs",
            "permission_assessments",
            "repair_runs",
        ):
            assert {owner["status"] for owner in shared[collection]} == {
                "unverified_import"
            }
        for collection, claimed_status in (
            ("review_runs", "completed"),
            ("permission_assessments", "completed"),
            ("repair_runs", "failed"),
        ):
            tampered = copy.deepcopy(shared)
            tampered[collection][0]["status"] = claimed_status
            with pytest.raises(AutoModePortabilityError, match="not inert"):
                portable_auto_mode_projection(
                    tampered,
                    trust_state="local",
                    root_frame_id=root,
                    branch_ids={root},
                )

        # Exercise the same reducer through the real read-only share builder,
        # then feed its import-compatible bundle back through Session import.
        frozen = runner.shares.builder.build(root, root)
        shared_auto = frozen.review["auto_mode"]
        assert {run["trust_state"] for run in shared_auto["runs"]} == {
            "local",
            "quarantined_import",
        }
        bundle = runner.shares.builder.serialize_package(frozen)
        reimported = runner.session_domain.session_import(bundle["data"])

        reimported_root = reimported["root_frame_id"]
        reimported_raw = runner.store.export_auto_mode_projection(reimported_root)
        assert reimported_raw["trust_state"] == "quarantined_import"
        assert {run["trust_state"] for run in reimported_raw["runs"]} == {
            "quarantined_import"
        }
        for collection in (
            "review_runs",
            "permission_assessments",
            "repair_runs",
        ):
            assert {owner["status"] for owner in reimported_raw[collection]} == {
                "unverified_import"
            }
    finally:
        runner.close()


Mutation = Callable[[dict[str, Any]], None]


def _parity_projection() -> dict[str, Any]:
    projection = _portable_auto_mode_projection("root", "version-1")
    projection["events"][3]["payload"]["attempt"] = 1
    for event in projection["events"]:
        event["payload_sha256"] = _digest(event["payload"])
    return projection


def _review_status_bogus(projection: dict[str, Any]) -> None:
    projection["events"][3]["payload"]["status"] = "bogus"


def _permission_status_bogus(projection: dict[str, Any]) -> None:
    projection["events"][5]["payload"]["status"] = "bogus"


def _candidate_status_bogus(projection: dict[str, Any]) -> None:
    projection["events"][1]["payload"]["status"] = "bogus"


def _bad_policy_version(projection: dict[str, Any]) -> None:
    projection["permission_assessments"][0]["policy_version"] = "bad policy"
    projection["events"][4]["payload"]["policy_version"] = "bad policy"


def _missing_review_attempt(projection: dict[str, Any]) -> None:
    projection["events"][3]["payload"].pop("attempt")


def _mismatched_review_attempt(projection: dict[str, Any]) -> None:
    projection["events"][3]["payload"]["attempt"] = 2


def _failed_permission_allows(projection: dict[str, Any]) -> None:
    projection["permission_assessments"][0].update(
        {"status": "failed", "outcome": "allow"}
    )
    projection["events"][5]["payload"].update({"status": "failed", "outcome": "allow"})


@pytest.mark.parametrize(
    ("name", "mutate"),
    [
        ("review_status", _review_status_bogus),
        ("permission_status", _permission_status_bogus),
        ("candidate_status", _candidate_status_bogus),
        ("policy_version", _bad_policy_version),
        ("missing_review_attempt", _missing_review_attempt),
        ("mismatched_review_attempt", _mismatched_review_attempt),
        ("failed_permission_allow", _failed_permission_allows),
    ],
)
def test_shared_and_direct_import_reject_the_same_invalid_graphs(
    tmp_path, name: str, mutate: Mutation
):
    projection = _parity_projection()
    mutate(projection)
    for event in projection["events"]:
        event["payload_sha256"] = _digest(event["payload"])

    with pytest.raises(AutoModePortabilityError):
        portable_auto_mode_projection(
            copy.deepcopy(projection),
            trust_state="local",
            root_frame_id="root",
            branch_ids={"root"},
            version_ids={"version-1"},
        )

    store = Store(tmp_path / f"parity-{name}.db")
    try:
        with pytest.raises((AutoModeConflictError, ValueError)):
            _direct_import(
                store,
                copy.deepcopy(projection),
                project_id=f"parity-project-{name}",
                version_id_map={"version-1": "mapped-version-1"},
            )
    finally:
        store.close()


def _permission_audit_projection(count: int) -> dict[str, Any]:
    root = "root"
    scope = {
        "run_id": "run",
        "root_frame_id": root,
        "branch_id": root,
        "turn_id": "turn",
        "execution_id": "execution",
    }
    events: list[dict[str, Any]] = []
    permissions: list[dict[str, Any]] = []
    started = _event(
        events,
        "auto_run_started",
        scope,
        {"mode": "auto_fix", "status": "running"},
    )
    for index in range(count):
        audit_id = f"audit-{index}"
        assessment_id = f"assessment-{index}"
        decision_id = f"decision-{index}"
        action_digest = _digest({"action": index})
        request_digest = _digest({"request": index})
        assessment_digest = _digest({"assessment": index})
        audit_started = _event(
            events,
            "auto_audit_started",
            scope,
            {
                "audit_id": audit_id,
                "assessment_id": assessment_id,
                "decision_id": decision_id,
                "action_digest": action_digest,
                "subject_kind": "permission_review",
                "subject_entity_kind": "approval_action",
                "subject_entity_id": decision_id,
                "policy_version": "policy-v1",
                "audit_request_digest": request_digest,
                "status": "started",
            },
        )
        audit_completed = _event(
            events,
            "auto_audit_completed",
            scope,
            {
                "audit_id": audit_id,
                "assessment_id": assessment_id,
                "decision_id": decision_id,
                "action_digest": action_digest,
                "subject_kind": "permission_review",
                "subject_entity_kind": "approval_action",
                "subject_entity_id": decision_id,
                "audit_request_digest": request_digest,
                "assessment_digest": assessment_digest,
                "outcome": "denied",
                "risk": "high",
                "status": "completed",
                "public_summary": "Denied.",
            },
        )
        permissions.append(
            {
                "assessment_id": assessment_id,
                "audit_id": audit_id,
                **scope,
                "decision_id": decision_id,
                "action_digest": action_digest,
                "policy_version": "policy-v1",
                "audit_request_digest": request_digest,
                "assessment_digest": assessment_digest,
                "status": "completed",
                "outcome": "denied",
                "risk": "high",
                "public_summary": "Denied.",
                "created_at": audit_started["created_at"],
                "finished_at": audit_completed["created_at"],
            }
        )
    terminal = _event(
        events,
        "auto_run_terminal",
        scope,
        {"status": "cancelled", "terminal_reason": "test_complete"},
    )
    return {
        "schema_version": 1,
        "trust_state": "local",
        "historical_selection": {
            "preset": "autonomous",
            "result_review_mode": "auto_fix",
            "approvals_reviewer": "auto_review",
            "source": "frame",
        },
        "runs": [
            {
                **scope,
                "mode": "auto_fix",
                "status": "cancelled",
                "terminal_reason": "test_complete",
                "created_at": started["created_at"],
                "finished_at": terminal["created_at"],
            }
        ],
        "events": events,
        "review_runs": [],
        "findings": [],
        "repair_runs": [],
        "permission_assessments": permissions,
    }


def _event_select_count(tmp_path: Path, count: int) -> int:
    store = Store(tmp_path / f"trace-{count}.db")
    statements: list[str] = []
    try:
        created = store.create_quarantined_import_session(
            project_id=f"trace-project-{count}",
            quarantine_value=_quarantine_value(),
        )
        store._conn.set_trace_callback(statements.append)
        store.import_quarantined_auto_mode_projection(
            _permission_audit_projection(count),
            root_frame_id=created["root_frame_id"],
            project_id=created["project_id"],
            branch_id=created["root_frame_id"],
            imported_at=500,
        )
    finally:
        store.close()
    return sum(
        statement.lstrip().upper().startswith("SELECT")
        and "AUTO_MODE_EVENTS" in statement.upper()
        for statement in statements
    )


def test_direct_import_audit_event_select_count_is_constant(tmp_path):
    small = _event_select_count(tmp_path, 8)
    large = _event_select_count(tmp_path, 64)
    assert small == large
    assert large <= 3


def test_second_session_round_trip_preserves_source_claim_and_stays_inert(tmp_path):
    store, domain, root = _domain(tmp_path, "second-round-trip")
    try:
        _record_verified_run(
            store,
            root,
            run_id="run-source",
            candidate_id="candidate-source",
            marker="SOURCE",
            created_at=100,
        )

        first_package = domain.session_export(root)["data"]
        first_auto = _package_auto_mode(first_package)
        first_import = domain.session_import(first_package)
        second_package = domain.session_export(first_import["root_frame_id"])["data"]
        second_auto = _package_auto_mode(second_package)
        second_import = domain.session_import(second_package)
        third_package = domain.session_export(second_import["root_frame_id"])["data"]
        third_auto = _package_auto_mode(third_package)

        assert [
            item["runs"][0]["source_claimed_status"]
            for item in (first_auto, second_auto, third_auto)
        ] == ["verified", "verified", "verified"]
        assert [
            item["runs"][0]["source_terminal_reason"]
            for item in (first_auto, second_auto, third_auto)
        ] == ["review_passed", "review_passed", "review_passed"]
        assert [
            item["runs"][0]["status"] for item in (first_auto, second_auto, third_auto)
        ] == ["unverified", "unverified_import", "unverified_import"]
        assert all(
            item["effective_selection"]
            == {
                "preset": "off",
                "result_review_mode": "off",
                "approvals_reviewer": "user",
            }
            for item in (first_auto, second_auto, third_auto)
        )

        final_projection = store.export_auto_mode_projection(
            second_import["root_frame_id"]
        )
        final_run = final_projection["runs"][0]
        assert final_projection["trust_state"] == "quarantined_import"
        assert final_run["status"] == "unverified_import"
        assert final_run["terminal_reason"] == "quarantined_import"
        assert final_run["source_claimed_status"] == "verified"
        assert final_run["source_terminal_reason"] == "review_passed"
        assert final_run["recovery_required"] is False
    finally:
        store.close()
