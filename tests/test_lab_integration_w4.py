"""Wave-4 composition: completion evidence over the real daemon composition.

These cases use the real SessionRunner, its session dispatchers, the real
manager and the stdlib toy provider in its own process (the same rig as
``test_lab_integration_w3.py``), so a seam between the completion gate, the
turn scope and the Lab ledger turns a test red.
"""

from __future__ import annotations

import pytest

from openai4s.host.delegation_policy import ChildExecutionPolicy
from openai4s.host_dispatch import build_dispatcher
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
