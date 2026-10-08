"""Wave-4 composition: completion evidence over the real daemon composition.

These cases use the real SessionRunner, its session dispatchers, the real
manager and the stdlib toy provider in its own process (the same rig as
``test_lab_integration_w3.py``), so a seam between the completion gate, the
turn scope and the Lab ledger turns a test red.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from openai4s.benchmark import lab as benchmark_lab
from openai4s.benchmark import load_workflows, run_case
from openai4s.host.delegation_policy import ChildExecutionPolicy
from openai4s.host_dispatch import build_dispatcher
from openai4s.lab.models import CommandOrigin, LabCaller, LabError
from openai4s.tools.registry import get_tool
from tests.test_lab_completion import finish
from tests.test_lab_integration_w3 import PROFILE, TOY, _Daemon, transfer


@pytest.fixture
def daemon(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_LAB_ENABLE_TOY", "1")
    monkeypatch.delenv("OPENAI4S_LAB_CHEMGYMRL_PYTHON", raising=False)
    built = _Daemon(tmp_path)
    yield built
    built.runner.close()


def completed(run_ids, **extra):
    return {
        "summary": "The toy simulation reached its declared goal.",
        "completion_bullets": ["Verified the toy run receipts"],
        "lab_runs": [{"run_id": r, "status": "completed"} for r in run_ids],
        **extra,
    }


def plain(**extra):
    return {
        "summary": "Paris is the capital of France.",
        "completion_bullets": ["Answered the question"],
        **extra,
    }


def _ended_run(host, *, stop=False, key="run"):
    run_id = host.lab.create(TOY, PROFILE, seed=7, idempotency_key=key)["run"]["run_id"]
    assert host.lab.execute(run_id, **transfer(key + "-move"))["command"]["state"] == (
        "succeeded"
    )
    if stop:
        host.lab.stop(run_id)
    else:
        ended = host.lab.execute(
            run_id, "end_experiment", expected_revision=1, idempotency_key=key + "-end"
        )
        assert ended["run"]["end_reason"] == "end_action"
    return run_id


def test_the_toy_goal_lets_both_directions_of_the_gate_run_for_real(daemon):
    # No patching: the toy profile's declared CI goal, real receipts and the
    # real evaluation decide. 200 mL moved, so the goal is met.
    fid, dispatcher, host = daemon.session()
    dispatcher.set_task_evidence_scope(turn_id="turn-1")
    done = _ended_run(host, key="done")
    assert dispatcher.verify_code_evidence(completed([done])) is None
    output = {k: v for k, v in completed([done]).items() if k != "completion_bullets"}
    assert host.submit_output(
        output, completion_bullets=completed([done])["completion_bullets"]
    ) == {"status": "ok"}
    stopped = _ended_run(host, stop=True, key="stopped")
    refusal = dispatcher.verify_code_evidence(completed([done]))
    assert "Declare every Lab run used" in refusal and stopped in refusal
    refusal = dispatcher.verify_code_evidence(completed([done, stopped]))
    assert "not ended normally" in refusal


def test_a_later_turn_without_lab_work_completes_normally(daemon):
    fid, dispatcher, host = daemon.session()
    dispatcher.set_task_evidence_scope(turn_id="turn-1")
    stopped = _ended_run(host, stop=True)
    assert "explicit lab_runs" in dispatcher.verify_code_evidence(plain())
    dispatcher.set_task_evidence_scope(turn_id="turn-2")
    assert dispatcher.verify_code_evidence(plain()) is None
    # Lab work in this turn brings the evidence requirement back.
    _ended_run(host, key="again")
    refusal = dispatcher.verify_code_evidence(plain())
    assert "explicit lab_runs" in refusal and stopped not in refusal


def test_delegated_children_are_not_held_to_lab_evidence(daemon):
    fid, dispatcher, host = daemon.session()
    _ended_run(host, stop=True)
    assert "explicit lab_runs" in dispatcher.verify_code_evidence(plain())
    child_frame = daemon.store.new_frame(parent_id=fid)
    child = build_dispatcher(daemon.cfg, frame_id=child_frame)
    child.set_child_execution_policy(
        ChildExecutionPolicy(restricted=False, allowed=frozenset(), permissions={})
    )
    assert child.verify_code_evidence(plain()) is None
    child.set_child_execution_policy(None)
    assert "explicit lab_runs" in child.verify_code_evidence(plain())


@pytest.mark.stubbed_backend
def test_the_lab_seed_probe_retries_after_a_transient_failure(daemon, monkeypatch):
    runner = daemon.runner
    calls = []

    def devices(caller):
        calls.append(caller.root_frame_id)
        if len(calls) == 1:
            raise RuntimeError("ledger busy")
        return [{"available": True}]

    monkeypatch.setattr(runner.lab_manager, "list_devices", devices)
    intro = 'First load_skill("lab-simulation")'
    first = runner._state(runner.create_session(daemon.project_id), daemon.project_id)
    runner._seed_messages(first)
    assert intro not in str(first.messages)
    second = runner._state(runner.create_session(daemon.project_id), daemon.project_id)
    runner._seed_messages(second)
    third = runner._state(runner.create_session(daemon.project_id), daemon.project_id)
    runner._seed_messages(third)
    assert intro in str(second.messages) and intro in str(third.messages)
    assert len(calls) == 2


def _allow_export(daemon, fid):
    daemon.store.set_permission_rule(
        scope="conversation",
        scope_id=fid,
        tool="lab_export",
        pattern="*",
        decision="allow",
    )


def _files(daemon, result):
    files = {}
    for item in result["artifacts"]:
        version = daemon.store.version_meta(item["version_id"])
        raw = Path(version["snapshot_path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == item["checksum"]
        files[item["kind"]] = raw.decode("utf-8")
    return files


def _value_free(refusal, *run_ids):
    # Fixed refusal sentences name runs, never an evaluated number or field.
    text = refusal
    for run_id in run_ids:
        text = text.replace(run_id, "")
    assert not re.search(r"\d", text), refusal
    for word in ("reward", "ground_truth", "moles", "purity", "volume_L"):
        assert word not in refusal


def test_end_export_and_completion_close_the_loop_over_one_ledger(daemon):
    fid, dispatcher, host = daemon.session()
    _allow_export(daemon, fid)
    st = daemon.runner._state(fid, daemon.project_id)
    dispatcher.set_task_evidence_scope(turn_id="turn-1")
    done = _ended_run(host, key="done")

    call = {"id": "w4-export", "name": "lab_export", "arguments": {"run_id": done}}
    native = daemon.runner._invoke_control_with_artifacts(
        st,
        call,
        lambda event: None,
        lambda: get_tool("lab_export").invoke(dispatcher, call["arguments"]),
    )
    assert "error" not in native, native
    kinds = {a["kind"]: a for a in native["artifacts"]}
    assert set(kinds) == {"actions", "observations_json", "observations_csv", "report"}
    assert "ground_truth" not in native
    first_version = kinds["observations_json"]["version_id"]
    rows = daemon.store.lab.list_observations(done, limit=1000)
    assert rows and all(r["artifact_version_id"] == first_version for r in rows)

    with st.trusted_capture.capture(), dispatcher.bind_artifact_receipt_scope():
        sdk = host.lab.export(done)
    status, rest = daemon.request("POST", f"/frames/{fid}/lab/runs/{done}/export", {})
    assert status == 200, rest
    assert native.keys() == sdk.keys() == rest.keys()
    for kind in ("actions", "observations_json", "observations_csv"):
        assert _files(daemon, native)[kind] == _files(daemon, sdk)[kind]
        assert _files(daemon, sdk)[kind] == _files(daemon, rest)[kind]
    # Later exports are new versions; the observations keep the first one.
    assert all(
        r["artifact_version_id"] == first_version
        for r in daemon.store.lab.list_observations(done, limit=1000)
    )

    # Replay reads exactly what the workbench pages through, and writes nothing.
    cursor = daemon.store.lab.latest_event_seq(fid)
    status, commands = daemon.request(
        "GET", f"/frames/{fid}/lab/runs/{done}/commands?after_seq=0&limit=200"
    )
    assert status == 200 and [c["seq"] for c in commands["commands"]] == [1, 2]
    status, observations = daemon.request(
        "GET",
        f"/frames/{fid}/lab/runs/{done}/observations?after_sequence=-1&limit=200&full=true",
    )
    assert status == 200 and [o["sequence"] for o in observations["observations"]] == [
        0,
        1,
        2,
    ]
    assert daemon.store.lab.latest_event_seq(fid) == cursor
    assert done not in daemon.runner.lab_manager._live

    # Only a person's workbench export carries ground truth, inline, once.
    status, truth = daemon.request(
        "POST", f"/frames/{fid}/lab/runs/{done}/export", {"include_evaluation": True}
    )
    assert status == 200 and truth["ground_truth"]["filename"].endswith(
        "-simulation-ground-truth.json"
    )
    assert json.loads(truth["ground_truth"]["content"])["evaluations"]
    # A raw Host call that bypasses the SDK still meets the closed tool schema.
    refused = dispatcher("lab_export", [{"run_id": done, "include_evaluation": True}])
    assert refused["error_kind"] == "invalid_parameters"
    assert "ground_truth" not in refused
    # The rows GET /frames/{fid}/artifacts serves, read from the Store so this
    # Lab test adds no observation to that non-Lab route's frozen shape.
    listed = daemon.store.list_artifacts({"root_frame_id": fid})
    assert len(listed) == 4
    assert "ground" not in json.dumps(listed, default=str)
    assert not [p for p in st.workspace.rglob("*") if "ground" in p.name]

    # The same turn's completion is accepted on both doors.
    payload = completed([done])
    accepted, error = finish("web", dispatcher._completion_service, payload)
    assert (
        accepted is not None and accepted["output"]["lab_runs"] == payload["lab_runs"]
    )
    output = {k: v for k, v in payload.items() if k != "completion_bullets"}
    assert host.submit_output(
        output, completion_bullets=payload["completion_bullets"]
    ) == {"status": "ok"}


@pytest.mark.parametrize("ending", ["stop", "unknown"])
def test_a_stopped_or_unresolved_run_cannot_be_reported_complete(daemon, ending):
    fid, dispatcher, host = daemon.session()
    dispatcher.set_task_evidence_scope(turn_id="turn-1")
    if ending == "stop":
        run_id = _ended_run(host, stop=True, key="halt")
    else:
        # A provider that dies mid-step leaves the command's outcome unknown.
        manager = daemon.runner.lab_manager
        caller = LabCaller(fid, fid, None, CommandOrigin.HOST_SDK, None, None)
        run_id = manager.create_run(
            caller,
            {
                "device_id": TOY,
                "profile": PROFILE,
                "seed": 7,
                "options": {"crash_on_execute": True},
                "idempotency_key": "crash",
            },
        )["run"]["run_id"]
        try:
            manager.execute(caller, {"run_id": run_id, **transfer("lost")})
        except LabError:
            pass
        commands = daemon.store.lab.list_commands(run_id, limit=10)
        assert [c["state"] for c in commands] == ["outcome_unknown"]
        assert run_id not in manager._live
    payload = completed([run_id])
    for door in ("web", "submit"):
        done, refusal = finish(door, dispatcher._completion_service, payload)
        assert done is None and "not ended normally" in refusal
        _value_free(refusal, run_id)
    leaving = {
        "summary": "Simulation experiment is still running.",
        "completion_bullets": ["Left the run open"],
        "task_status": "partial",
        "lab_runs": [{"run_id": run_id, "status": "running"}],
    }
    done, refusal = finish("web", dispatcher._completion_service, leaving)
    assert done is None and "no longer running" in refusal
    _value_free(refusal, run_id)
    honest = {
        "summary": f"The run {ending} before the goal could be evaluated.",
        "completion_bullets": ["Reported the recorded run state"],
        "task_status": "partial",
    }
    done, refusal = finish("web", dispatcher._completion_service, honest)
    assert done is not None, refusal


LAB_WORKFLOW = next(w for w in load_workflows() if w.id == "lab-simulation")


@pytest.mark.parametrize("case", LAB_WORKFLOW.cases, ids=lambda c: c.id)
def test_every_lab_benchmark_case_passes_and_releases_its_providers(
    case, tmp_path, monkeypatch
):
    managers = []
    build = benchmark_lab.build_lab_manager

    def tracked(**kwargs):
        managers.append(build(**kwargs))
        return managers[-1]

    monkeypatch.setattr(benchmark_lab, "build_lab_manager", tracked)
    result = run_case(LAB_WORKFLOW, case, root=tmp_path)
    assert result.passed, result.detail
    assert managers and all(not manager._live for manager in managers)
