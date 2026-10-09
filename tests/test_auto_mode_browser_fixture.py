"""Local-only fixture for the Auto Mode status browser acceptance (issue #217).

`tests/browser_auto_mode_status.mjs` runs this file in a separate process to
seed durable Auto Mode state into its disposable daemon's SQLite database
through the production ``Store``: selection rows, Auto Runs, real budget
reservations, review and permission audits, and a second logical branch. No
model is called and the daemon grows no test-only route.

The tests below pin, against the real ``AutoModeService`` reading the same
store, the projections the browser then has to render. If a seed stops
producing the state its scenario names, they fail here first instead of as a
confusing browser assertion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Callable

import pytest

from openai4s.config import AutoModeConfig, Config
from openai4s.server.auto_mode import AutoModeService
from openai4s.store import Store, get_store

_SCHEMA_VERSION = 1
_OWNER = "auto-mode-browser-fixture"
_AUTONOMOUS = {
    "preset": "autonomous",
    "result_review_mode": "auto_fix",
    "approvals_reviewer": "auto_review",
    "source": "frame",
}
#: Seeded result reviews and permission reviews for the audit scenario. With
#: the workbench's 20-row page, "all kinds" takes two pages and each kind's
#: own filter takes one.
RESULT_AUDITS = 15
PERMISSION_AUDITS = 10


def _canonical_sha(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _budgets(config: Config) -> dict[str, Any]:
    return asdict(config.auto_mode.budgets)


def _start_run(
    store: Store,
    config: Config,
    root: str,
    *,
    key: str,
    branch_id: str | None = None,
) -> str:
    branch = branch_id or root
    store.ensure_session_branch(root_frame_id=root, branch_id=branch)
    run_id = f"auto-{root}-{key}"
    store.start_auto_mode_run(
        run_id=run_id,
        idempotency_key=f"{key}:auto-run",
        root_frame_id=root,
        branch_id=branch,
        turn_id=f"turn-{key}",
        execution_id=f"execution-{key}",
        mode="auto_fix",
        selection=dict(_AUTONOMOUS),
        budgets=_budgets(config),
        owner_instance_id=_OWNER,
    )
    return run_id


def _candidate(store: Store, run_id: str, *, key: str) -> dict[str, Any]:
    evidence = {"candidate_id": f"candidate-{key}", "complete": True}
    store.record_auto_mode_candidate(
        run_id,
        idempotency_key=f"candidate:{key}",
        candidate_id=f"candidate-{key}",
        candidate_snapshot_sha256=_canonical_sha({"candidate": key}),
        evidence_snapshot_sha256=_canonical_sha(evidence),
        artifact_set_sha256=_canonical_sha({"artifacts": key}),
        candidate_version_ids=[f"version-{key}"],
    )
    return evidence


def _review(
    store: Store,
    run_id: str,
    evidence: dict[str, Any],
    *,
    key: str,
    index: int,
    complete: bool = True,
) -> None:
    review_run_id = f"review-{key}-{index}"
    store.start_auto_mode_review(
        run_id,
        review_run_id=review_run_id,
        audit_id=f"audit-result-{key}-{index:02d}",
        idempotency_key=f"{review_run_id}:start",
        candidate_id=f"candidate-{key}",
        candidate_snapshot_sha256=_canonical_sha({"candidate": key}),
        evidence_snapshot=evidence,
        evidence_snapshot_sha256=_canonical_sha(evidence),
        round_index=0,
        attempt=index + 1,
        reviewer={
            "profile_id": "scientific-reviewer",
            "profile_revision": 1,
            "model_fingerprint": "fixture-reviewer",
        },
    )
    if complete:
        store.complete_auto_mode_review(
            review_run_id,
            idempotency_key=f"{review_run_id}:complete",
            status="unavailable",
            verdict="unavailable",
            assessment={
                "public_summary": f"Result review {index:02d}: reviewer timed out."
            },
            findings=[],
        )


def _permission_review(store: Store, root: str, run_id: str, *, index: int) -> None:
    decision_id = f"decision-{root}-{index:02d}"
    group = store.append_action_group(
        root_frame_id=root,
        branch_id=root,
        turn_id="turn-audits",
        kind="native_tools",
        assistant_content="Propose one exact action",
    )
    store.create_permission_request(
        decision_id=decision_id,
        root_frame_id=root,
        frame_id=root,
        action_group_id=group["group_id"],
        action_id=f"action-{index:02d}",
        tool="write_file",
        target=f"result-{index:02d}.txt",
        side_effect_class="workspace_write",
        resource_keys=[f"workspace:result-{index:02d}.txt"],
        canonical_arguments=[
            {"path": f"result-{index:02d}.txt", "content": f"exact content {index}"}
        ],
        expires_at=10**15,
    )
    assessment_id = f"assessment-{root}-{index:02d}"
    store.start_permission_review_assessment(
        run_id,
        assessment_id=assessment_id,
        audit_id=f"audit-permission-{index:02d}",
        decision_id=decision_id,
        action_digest=store.permission_request_action_digest(decision_id),
        policy_version="guardian-v1",
        idempotency_key=f"{assessment_id}:start",
    )
    store.complete_permission_review_assessment(
        assessment_id,
        idempotency_key=f"{assessment_id}:complete",
        status="completed",
        outcome="denied",
        risk="high",
        assessment={
            "public_summary": f"Permission review {index:02d}: denied by policy."
        },
    )
    # Stage 2 records the assessment and leaves the request pending; resolve
    # it so the workbench draws no live approval card for a fixture.
    store.resolve_permission_request(decision_id, state="denied")


def _commit_budget(store: Store, run_id: str, consumer: str, count: int) -> None:
    for index in range(count):
        admission = f"{run_id}:{consumer}:{index}"
        store.reserve_auto_mode_budget(
            run_id=run_id,
            admission_id=admission,
            consumer=consumer,
            action_group_id=f"group-{consumer}-{index}",
            amount=1,
        )
        store.commit_auto_mode_budget(admission, committed_amount=1)


def _set_frame_selection(store: Store, root: str, values: dict[str, str]) -> dict:
    current = store.get_auto_mode_selection("frame", root)
    revision = int((current or {}).get("revision") or 0)
    return store.set_auto_mode_selection("frame", root, values, revision)


def seed_frame_autonomous(
    store: Store, config: Config, root: str, project: str
) -> dict:
    """A saved autonomous frame override, and no run."""

    row = _set_frame_selection(
        store,
        root,
        {
            "preset": "autonomous",
            "result_review_mode": "auto_fix",
            "approvals_reviewer": "auto_review",
        },
    )
    return {"revision": row["revision"]}


def seed_selection_bump(store: Store, config: Config, root: str, project: str) -> dict:
    """Change the frame override again. A selection save emits no event."""

    row = _set_frame_selection(
        store,
        root,
        {
            "preset": "off",
            "result_review_mode": "review_only",
            "approvals_reviewer": "user",
        },
    )
    return {"revision": row["revision"]}


def seed_project_review_only(
    store: Store, config: Config, root: str, project: str
) -> dict:
    row = store.set_auto_mode_selection(
        "project",
        project,
        {
            "preset": "off",
            "result_review_mode": "review_only",
            "approvals_reviewer": "user",
        },
        0,
    )
    return {"revision": row["revision"]}


def seed_legacy_review(store: Store, config: Config, root: str, project: str) -> dict:
    """The old per-frame Auto review switch, which Auto Mode inherits from."""

    store.set_setting(f"review:auto:{root}", "1")
    return {}


def seed_budget_meters(store: Store, config: Config, root: str, project: str) -> dict:
    """A running run near and at its ceilings, then refused once more.

    25 of 30 extra Cells is near the ceiling (5 x 5 <= 30), 2 of 2 review
    rounds is at it, one repair round is reserved, and the token ceiling is
    not frozen. The refused third review trips the durable circuit with
    ``budget_exhausted``; no terminal is committed, so the run is still in
    progress while its user truth reads Paused.
    """

    run_id = _start_run(store, config, root, key="budget")
    _candidate(store, run_id, key="budget")
    _commit_budget(store, run_id, "extra_cell", 25)
    _commit_budget(store, run_id, "review", 2)
    store.reserve_auto_mode_budget(
        run_id=run_id,
        admission_id=f"{run_id}:repair:0",
        consumer="repair",
        action_group_id="group-repair-0",
        amount=1,
    )
    try:
        store.reserve_auto_mode_budget(
            run_id=run_id,
            admission_id=f"{run_id}:review:refused",
            consumer="review",
            action_group_id="group-review-refused",
            amount=1,
        )
    except Exception as exc:  # AutoBudgetDenied is the point of this step
        if "budget exhausted" not in str(exc):
            raise
    else:  # pragma: no cover - would mean the ceiling no longer binds
        raise RuntimeError("the third review was admitted past its ceiling")
    return {"run_id": run_id}


def seed_measurement_unavailable(
    store: Store, config: Config, root: str, project: str
) -> dict:
    run_id = _start_run(store, config, root, key="measure")
    store.trip_auto_mode_budget_circuit(
        run_id, reason="budget_measurement_unavailable", field="extra_token_multiplier"
    )
    return {"run_id": run_id}


def seed_terminal_safety(store: Store, config: Config, root: str, project: str) -> dict:
    run_id = _start_run(store, config, root, key="safety")
    store.terminate_auto_mode_run(
        run_id,
        idempotency_key=f"{run_id}:terminal",
        status="failed",
        reason="safety_boundary",
    )
    return {"run_id": run_id}


def seed_audits(store: Store, config: Config, root: str, project: str) -> dict:
    """15 result reviews and 10 permission reviews on one run, newest last."""

    run_id = _start_run(store, config, root, key="audits")
    evidence = _candidate(store, run_id, key="audits")
    for index in range(RESULT_AUDITS):
        _review(store, run_id, evidence, key="audits", index=index)
    for index in range(PERMISSION_AUDITS):
        _permission_review(store, root, run_id, index=index)
    return {
        "run_id": run_id,
        "result_audits": RESULT_AUDITS,
        "permission_audits": PERMISSION_AUDITS,
    }


def _branch_id(root: str) -> str:
    return f"{root}-branch-b"


def seed_branch_runs(store: Store, config: Config, root: str, project: str) -> dict:
    """A running run on the root branch and a candidate on a second branch."""

    main = _start_run(store, config, root, key="branch-a")
    other = _start_run(store, config, root, key="branch-b", branch_id=_branch_id(root))
    _candidate(store, other, key="branch-b")
    return {"main_run_id": main, "branch_run_id": other, "branch_id": _branch_id(root)}


def seed_branch_activate(store: Store, config: Config, root: str, project: str) -> dict:
    """Make the second branch the active one, as another client's activation would.

    A real activation also restores checkpoint state; the Auto Mode
    projection reads only the selected branch, which is this one row.
    """

    branch = _branch_id(root)
    with store._lock:
        store._conn.execute(
            "INSERT INTO session_branch_selection(root_frame_id,current_branch_id,"
            "updated_at) VALUES(?,?,?) ON CONFLICT(root_frame_id) DO UPDATE SET "
            "current_branch_id=excluded.current_branch_id,updated_at=excluded.updated_at",
            (root, branch, 1),
        )
        store._conn.commit()
    return {"branch_id": store.active_session_branch(root)}


def seed_source_for_import(
    store: Store, config: Config, root: str, project: str
) -> dict:
    """A finished run whose package the harness exports and imports.

    The package carries Auto Mode history only for turns it also carries, so
    the run's turn gets the action group a real turn would have written.
    """

    store.append_action_group(
        root_frame_id=root,
        branch_id=root,
        turn_id="turn-source",
        kind="native_tools",
        assistant_content="The turn the exported run belongs to",
    )
    run_id = _start_run(store, config, root, key="source")
    store.terminate_auto_mode_run(
        run_id,
        idempotency_key=f"{run_id}:terminal",
        status="cancelled",
        reason="user_cancelled",
    )
    return {"run_id": run_id}


SCENARIOS: dict[str, Callable[[Store, Config, str, str], dict]] = {
    "frame_autonomous": seed_frame_autonomous,
    "selection_bump": seed_selection_bump,
    "project_review_only": seed_project_review_only,
    "legacy_review": seed_legacy_review,
    "budget_meters": seed_budget_meters,
    "measurement_unavailable": seed_measurement_unavailable,
    "terminal_safety": seed_terminal_safety,
    "audits": seed_audits,
    "branch_runs": seed_branch_runs,
    "branch_activate": seed_branch_activate,
    "source_for_import": seed_source_for_import,
}


def seed(data_dir: Path, scenario: str, *, root_frame_id: str, project_id: str) -> dict:
    config = Config(data_dir=data_dir.expanduser().resolve())
    store = get_store(config.db_path)
    try:
        detail = SCENARIOS[scenario](store, config, root_frame_id, project_id)
    finally:
        store.close()
    return {
        "schema_version": _SCHEMA_VERSION,
        "scenario": scenario,
        "root_frame_id": root_frame_id,
        **detail,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    command = subparsers.add_parser("seed")
    command.add_argument("--data-dir", required=True, type=Path)
    command.add_argument("--scenario", required=True, choices=sorted(SCENARIOS))
    command.add_argument("--root-frame-id", required=True)
    command.add_argument("--project-id", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    value = seed(
        args.data_dir,
        args.scenario,
        root_frame_id=args.root_frame_id,
        project_id=args.project_id,
    )
    json.dump(value, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    return 0


# --------------------------------------------------------------------------
# The projections the browser reads, pinned against the production service.


def _scope(tmp_path: Path) -> tuple[Store, Config, str, str]:
    config = Config(data_dir=tmp_path)
    store = Store(config.db_path)
    project = store.create_project(name="Auto Mode browser fixture")
    root = store.new_frame(project_id=project["project_id"], kind="turn")
    store.ensure_session_branch(root_frame_id=root, branch_id=root)
    return store, config, root, project["project_id"]


def _service(store: Store, *, stage2: bool = True, deployment=None) -> AutoModeService:
    config = Config()
    config.roadmap_features = replace(
        config.roadmap_features, stage2_auto_run_storage=stage2
    )
    if deployment is not None:
        config.auto_mode = deployment
    return AutoModeService(store=store, config=config)


@pytest.fixture
def scope(tmp_path):
    store, config, root, project = _scope(tmp_path)
    yield store, config, root, project
    store.close()


def test_the_worked_example_needs_no_seed(scope):
    store, _config, root, _project = scope
    deployment = AutoModeConfig(
        enabled=True,
        result_review_mode="auto_fix",
        approvals_reviewer="auto_review",
        deployment_explicit=True,
        deployment_explicit_fields=("preset",),
    )
    view = _service(store, stage2=False, deployment=deployment).get(root)
    assert view["feature_enabled"] is False
    assert view["disabled_reason"] == "stage2_feature_disabled"
    assert view["selection"]["preset"] == "autonomous"
    assert view["selection"]["source"] == "deployment_explicit"
    assert view["deployment"]["explicit_fields"] == ["preset"]
    assert view["run"] is None


def test_selection_scenarios_win_in_precedence_order(scope):
    store, config, root, project = scope
    service = _service(store)
    seed_legacy_review(store, config, root, project)
    assert service.get(root)["selection"]["source"] == "legacy_result_review"
    seed_project_review_only(store, config, root, project)
    assert service.get(root)["selection"]["source"] == "project"
    seed_frame_autonomous(store, config, root, project)
    first = service.get(root)
    assert first["selection"]["source"] == "frame"
    assert first["selection"]["preset"] == "autonomous"
    seed_selection_bump(store, config, root, project)
    second = service.get(root)
    assert second["selection"]["result_review_mode"] == "review_only"
    assert second["selection"]["revision"] == first["selection"]["revision"] + 1
    # The save moved the revision and nothing else: no event, same cursor.
    assert second["last_event_ordinal"] == first["last_event_ordinal"]


def test_budget_meters_scenario_is_near_at_reserved_and_tripped(scope):
    store, config, root, project = scope
    seed_budget_meters(store, config, root, project)
    run = _service(store).get(root)["run"]
    usage = run["budget_usage"]
    assert run["status"] == "candidate"
    assert run["legacy"] is False
    assert usage["max_extra_cells"] == {
        "limit": 30,
        "used": 25,
        "reserved": 0,
        "remaining": 5,
        "exhausted": False,
        "authority": "auto_budget",
    }
    assert usage["max_review_rounds"]["exhausted"] is True
    assert usage["max_repair_rounds"]["reserved"] == 1
    assert usage["extra_token_multiplier"]["limit"] == 0
    assert usage["extra_token_multiplier"]["exhausted"] is False
    assert run["circuit"]["state"] == "tripped"
    assert run["circuit"]["reason"] == "budget_exhausted"
    assert run["user_truth"] == "Paused · Budget exhausted"


def test_measurement_and_terminal_scenarios(tmp_path):
    store, config, root, project = _scope(tmp_path / "measure")
    seed_measurement_unavailable(store, config, root, project)
    run = _service(store).get(root)["run"]
    assert run["circuit"]["reason"] == "budget_measurement_unavailable"
    assert run["user_truth"] == "无法验证 token 预算"
    store.close()

    store, config, root, project = _scope(tmp_path / "safety")
    seed_terminal_safety(store, config, root, project)
    run = _service(store).get(root)["run"]
    assert run["status"] == "failed"
    assert run["terminal_reason"] == "safety_boundary"
    store.close()


def test_audit_scenario_pages_and_filters_like_the_workbench(scope):
    store, config, root, project = scope
    seed_audits(store, config, root, project)
    service = _service(store)
    first = service.list_audits(root, limit=20)
    assert len(first["audits"]) == 20
    assert first["has_more"] is True
    second = service.list_audits(root, before=first["next_before"], limit=20)
    assert len(second["audits"]) == RESULT_AUDITS + PERMISSION_AUDITS - 20
    assert second["has_more"] is False
    seen = [row["audit_id"] for row in first["audits"] + second["audits"]]
    assert len(seen) == len(set(seen)) == RESULT_AUDITS + PERMISSION_AUDITS
    # Newest first: the permission reviews were seeded last.
    assert (
        first["audits"][0]["audit_id"]
        == f"audit-permission-{PERMISSION_AUDITS - 1:02d}"
    )
    for kind, expected in (
        ("result_review", RESULT_AUDITS),
        ("permission_review", PERMISSION_AUDITS),
    ):
        page = service.list_audits(root, subject_kind=kind, limit=20)
        assert len(page["audits"]) == expected
        assert {row["subject_kind"] for row in page["audits"]} == {kind}
        assert page["has_more"] is False
    for row in first["audits"]:
        assert row.get("status") != "failed", row  # no integrity downgrade
        assert row["public_summary"].endswith(".")


def test_branch_scenarios_project_the_selected_branch(scope):
    store, config, root, project = scope
    detail = seed_branch_runs(store, config, root, project)
    service = _service(store)
    before = service.get(root)
    assert before["branch_id"] == root
    assert before["run"]["run_id"] == detail["main_run_id"]
    assert before["run"]["status"] == "running"
    seed_branch_activate(store, config, root, project)
    after = service.get(root)
    assert after["branch_id"] == detail["branch_id"]
    assert after["run"]["run_id"] == detail["branch_run_id"]
    assert after["run"]["status"] == "candidate"


def test_cli_seeds_through_a_fresh_store(tmp_path, capsys):
    store, _config, root, project = _scope(tmp_path)
    store.close()
    assert (
        main(
            [
                "seed",
                "--data-dir",
                str(tmp_path),
                "--scenario",
                "frame_autonomous",
                "--root-frame-id",
                root,
                "--project-id",
                project,
            ]
        )
        == 0
    )
    value = json.loads(capsys.readouterr().out)
    assert value == {
        "schema_version": 1,
        "scenario": "frame_autonomous",
        "root_frame_id": root,
        "revision": 1,
    }


if __name__ == "__main__":
    raise SystemExit(main())
