"""Cross-surface Stage-2 Auto Mode recovery and quarantine regressions.

These tests deliberately drive the real Gateway, durable Store, Session package,
share projection, and WebSocket hub seams together.  A live notification is only
a hint: SQLite remains authoritative after a lost socket or daemon restart.
"""

from __future__ import annotations

import hashlib
import http.client
import json
from pathlib import Path
from typing import Any

import pytest

from openai4s.server import local_auth
from openai4s.server.session_package import SessionPackageError
from tests.test_team_auth_routes import _TeamDaemon

_SAFE_SELECTION = {
    "preset": "off",
    "result_review_mode": "off",
    "approvals_reviewer": "user",
}


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _request_json(
    daemon: _TeamDaemon,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    headers = {
        local_auth.TOKEN_HEADER: daemon.token,
        "Accept": "application/json",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(payload))
    connection = http.client.HTTPConnection("127.0.0.1", daemon.port, timeout=20)
    try:
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        return response.status, json.loads(raw.decode("utf-8"))
    finally:
        connection.close()


def _new_session(daemon: _TeamDaemon) -> tuple[str, str]:
    project = daemon.store.create_project(name="Auto Mode surface regression")
    project_id = str(project["project_id"])
    root = daemon.store.new_frame(project_id=project_id, kind="turn", status="done")
    daemon.store.ensure_session_branch(root_frame_id=root, branch_id=root)
    daemon.runner.workspace_for_branch(root, root).mkdir(parents=True, exist_ok=True)
    daemon.store.add_message(
        root_frame_id=root,
        branch_id=root,
        frame_id=root,
        role="user",
        content="Verify the candidate result.",
    )
    daemon.store.append_action_group(
        root_frame_id=root,
        branch_id=root,
        turn_id="turn-auto-surface",
        kind="user",
        assistant_message={
            "role": "user",
            "content": "Verify the candidate result.",
        },
    )
    return project_id, root


def _start_run(daemon: _TeamDaemon, root: str) -> dict[str, Any]:
    return daemon.store.start_auto_mode_run(
        run_id=f"auto-run-{root}",
        idempotency_key="auto-surface:start",
        root_frame_id=root,
        branch_id=root,
        turn_id="turn-auto-surface",
        execution_id="execution-auto-surface",
        mode="auto_fix",
        selection={
            "preset": "autonomous",
            "result_review_mode": "auto_fix",
            "approvals_reviewer": "auto_review",
            "source": "frame",
        },
        budgets={"max_review_attempts": 2, "max_repair_rounds": 2},
        owner_instance_id="daemon-surface",
    )


def _record_candidate(daemon: _TeamDaemon, root: str) -> dict[str, Any]:
    evidence = {"candidate_id": f"candidate-{root}", "complete": True}
    return daemon.store.record_auto_mode_candidate(
        f"auto-run-{root}",
        idempotency_key="auto-surface:candidate",
        candidate_id=f"candidate-{root}",
        candidate_snapshot_sha256="a" * 64,
        evidence_snapshot_sha256=_digest(evidence),
        candidate_artifact_ids=[],
        candidate_version_ids=[],
    )


def _seed_verified(daemon: _TeamDaemon, root: str) -> dict[str, Any]:
    started = _start_run(daemon, root)
    candidate = _record_candidate(daemon, root)
    evidence = {"candidate_id": f"candidate-{root}", "complete": True}
    review_run_id = f"review-{root}"
    daemon.store.start_auto_mode_review(
        f"auto-run-{root}",
        review_run_id=review_run_id,
        audit_id=f"audit-{root}",
        idempotency_key="auto-surface:review:start",
        candidate_id=f"candidate-{root}",
        candidate_snapshot_sha256="a" * 64,
        evidence_snapshot=evidence,
        evidence_snapshot_sha256=_digest(evidence),
        round_index=0,
        attempt=1,
        reviewer={
            "profile_id": "scientific-reviewer",
            "profile_revision": 1,
            "model_fingerprint": "independent-reviewer-model",
        },
    )
    daemon.store.complete_auto_mode_review(
        review_run_id,
        idempotency_key="auto-surface:review:complete",
        status="completed",
        verdict="pass",
        assessment={"public_summary": "Independent review passed."},
        findings=[],
    )
    terminal = daemon.store.terminate_auto_mode_run(
        f"auto-run-{root}",
        idempotency_key="auto-surface:terminal",
        status="verified",
        reason="review_passed",
    )
    return {"started": started, "candidate": candidate, "terminal": terminal}


def _tamper_completed_assessment(daemon: _TeamDaemon, root: str) -> None:
    daemon.store._conn.execute(
        "UPDATE review_runs SET assessment_digest=? WHERE review_run_id=?",
        ("f" * 64, f"review-{root}"),
    )
    daemon.store._conn.commit()


def _assert_failed_safety_boundary(payload: dict[str, Any]) -> None:
    run = payload["run"]
    assert run["status"] == "failed"
    assert run["status"] != "verified"
    assert run["terminal_reason"] == "safety_boundary"


def test_post_terminal_proof_tamper_fails_closed_over_rest_and_reopen(
    tmp_path: Path,
):
    daemon = _TeamDaemon(tmp_path, team_mode=False)
    reopened: _TeamDaemon | None = None
    try:
        _project_id, root = _new_session(daemon)
        _seed_verified(daemon, root)
        _tamper_completed_assessment(daemon, root)

        status, first = _request_json(daemon, "GET", f"/api/v1/frames/{root}/auto-mode")
        assert status == 200
        _assert_failed_safety_boundary(first)
        durable = daemon.store.project_auto_mode_run(root, root)
        assert first["last_event_id"] == durable["last_event_id"]
        assert first["last_event_ordinal"] == durable["last_event_ordinal"]
        assert (
            daemon.store._conn.execute(
                "SELECT status FROM auto_mode_runs WHERE run_id=?",
                (f"auto-run-{root}",),
            ).fetchone()[0]
            == "verified"
        )

        daemon.close()
        daemon.store.close()
        reopened = _TeamDaemon(tmp_path, team_mode=False)
        status, after_restart = _request_json(
            reopened, "GET", f"/api/v1/frames/{root}/auto-mode"
        )
        assert status == 200
        _assert_failed_safety_boundary(after_restart)
        assert after_restart["run"] == first["run"]
        assert after_restart["last_event_id"] == first["last_event_id"]
        assert after_restart["last_event_ordinal"] == first["last_event_ordinal"]
    finally:
        if reopened is not None:
            reopened.close()
            reopened.store.close()
        else:
            daemon.close()
            daemon.store.close()


def test_lost_websocket_hint_is_rebuilt_by_rest_from_sqlite(tmp_path: Path):
    class _Connection:
        alive = True

        def __init__(self) -> None:
            self.subs: set[str] = set()
            self.events: list[dict[str, Any]] = []

        def send_json(self, event: dict[str, Any]) -> None:
            self.events.append(dict(event))

    daemon = _TeamDaemon(tmp_path, team_mode=False)
    try:
        _project_id, root = _new_session(daemon)
        connection = _Connection()
        daemon.hub.add(connection)  # type: ignore[arg-type]
        daemon.hub.subscribe(root, connection)  # type: ignore[arg-type]
        connection.events.clear()

        started = _start_run(daemon, root)
        daemon.runner.auto_mode.publish_committed(started)
        assert [event["type"] for event in connection.events] == ["auto_run_started"]

        daemon.hub.remove(connection)  # type: ignore[arg-type]
        candidate = _record_candidate(daemon, root)
        daemon.runner.auto_mode.publish_committed(candidate)
        assert all(event["type"] != "candidate_ready" for event in connection.events)

        status, rest = _request_json(daemon, "GET", f"/api/v1/frames/{root}/auto-mode")
        assert status == 200
        events = daemon.store.list_auto_mode_events(root, branch_id=root)
        assert rest["run"]["status"] == "candidate"
        assert rest["last_event_id"] == candidate["event"]["event_id"]
        assert rest["last_event_id"] == events[-1]["event_id"]
        assert rest["last_event_ordinal"] == events[-1]["event_cursor"]
        assert [event["type"] for event in events] == [
            "auto_run_started",
            "candidate_ready",
        ]
    finally:
        daemon.close()
        daemon.store.close()


def test_enabled_selection_and_audits_have_real_http_success_shapes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Capture the enabled, empty, mutable, and populated REST projections.

    These calls use the real Gateway, Store, and AutoModeService.  The focused
    route-adapter tests intentionally use a fake service and are marked
    ``stubbed_backend``; without this integration case the frozen response
    artifact would publish only refusal shapes for PATCH and auto-audits, and
    would incorrectly claim that nullable no-run fields are always populated.
    """

    monkeypatch.setenv("OPENAI4S_STAGE2_AUTO_RUN_STORAGE", "1")
    daemon = _TeamDaemon(tmp_path, team_mode=False)
    try:
        _project_id, root = _new_session(daemon)

        status, empty = _request_json(daemon, "GET", f"/api/v1/frames/{root}/auto-mode")
        assert status == 200
        assert empty["feature_enabled"] is True
        assert empty["writable"] is True
        assert empty["disabled_reason"] is None
        assert empty["run"] is None
        assert empty["last_event_id"] is None
        assert empty["last_event_ordinal"] == 0

        status, updated = _request_json(
            daemon,
            "PATCH",
            f"/api/v1/frames/{root}/auto-mode",
            {
                "revision": 0,
                "preset": "off",
                "result_review_mode": "review_only",
                "approvals_reviewer": "user",
            },
        )
        assert status == 200
        assert updated["selection"]["revision"] == 1
        assert updated["selection"]["result_review_mode"] == "review_only"

        status, no_audits = _request_json(
            daemon, "GET", f"/api/v1/frames/{root}/auto-audits"
        )
        assert status == 200
        assert no_audits["subject_kind"] is None
        assert no_audits["audits"] == []
        assert no_audits["next_before"] is None
        assert no_audits["has_more"] is False

        _seed_verified(daemon, root)
        status, audits = _request_json(
            daemon,
            "GET",
            f"/api/v1/frames/{root}/auto-audits" "?subject_kind=result_review&limit=10",
        )
        assert status == 200
        assert audits["subject_kind"] == "result_review"
        assert len(audits["audits"]) == 1
        assert audits["audits"][0]["status"] == "completed"
        assert audits["audits"][0]["verdict"] == "pass"
    finally:
        daemon.close()
        daemon.store.close()


def test_corrupt_verified_proof_refuses_package_and_share_publication(
    tmp_path: Path,
):
    daemon = _TeamDaemon(tmp_path, team_mode=False)
    try:
        _project_id, root = _new_session(daemon)
        _seed_verified(daemon, root)
        _tamper_completed_assessment(daemon, root)

        status, rest = _request_json(daemon, "GET", f"/api/v1/frames/{root}/auto-mode")
        assert status == 200
        _assert_failed_safety_boundary(rest)

        with pytest.raises(
            SessionPackageError,
            match="Auto Mode history failed integrity validation",
        ) as package_error:
            daemon.runner.session_domain.session_export(root)
        assert "review assessment proof" not in str(package_error.value)
        with pytest.raises(
            SessionPackageError,
            match="Auto Mode history failed integrity validation",
        ) as share_error:
            daemon.runner.shares.builder.build(root, root)
        assert "review assessment proof" not in str(share_error.value)
    finally:
        daemon.close()
        daemon.store.close()


def test_imported_auto_mode_is_inert_over_rest_and_share(tmp_path: Path):
    daemon = _TeamDaemon(tmp_path, team_mode=False)
    try:
        _project_id, source_root = _new_session(daemon)
        _seed_verified(daemon, source_root)
        package = daemon.runner.session_domain.session_export(source_root)["data"]
        imported = daemon.runner.session_domain.session_import(package)
        imported_root = str(imported["root_frame_id"])

        status, rest = _request_json(
            daemon, "GET", f"/api/v1/frames/{imported_root}/auto-mode"
        )
        assert status == 200
        assert rest["writable"] is False
        assert rest["disabled_reason"] == "import_quarantine"
        assert {
            key: rest["selection"][key] for key in _SAFE_SELECTION
        } == _SAFE_SELECTION
        assert rest["selection"]["source"] == "import_quarantine"
        assert rest["run"]["status"] == "unverified_import"
        assert rest["run"]["status"] != "verified"

        status, refused = _request_json(
            daemon,
            "PATCH",
            f"/api/v1/frames/{imported_root}/auto-mode",
            {
                "revision": 0,
                "preset": "autonomous",
                "result_review_mode": "auto_fix",
                "approvals_reviewer": "auto_review",
            },
        )
        assert status == 423
        assert refused["error"]
        assert daemon.store.get_auto_mode_selection("frame", imported_root) is None

        active_branch = daemon.store.active_session_branch(imported_root)
        shared = daemon.runner.shares.builder.build(imported_root, active_branch)
        auto_mode = shared.review["auto_mode"]
        assert auto_mode["trust_state"] == "quarantined_import"
        assert auto_mode["effective_selection"] == _SAFE_SELECTION
        assert all(run["status"] != "verified" for run in auto_mode["runs"])
        assert all(run["status"] == "unverified_import" for run in auto_mode["runs"])
    finally:
        daemon.close()
        daemon.store.close()
