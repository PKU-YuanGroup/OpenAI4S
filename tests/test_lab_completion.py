"""Exact-run completion evidence across native and in-cell completion doors."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from openai4s.agent.actions import FinalizeAction
from openai4s.agent.finalize import validate_finalize_arguments
from openai4s.agent.models import ModelReply, RunState
from openai4s.host.completion import CompletionService
from openai4s.lab.evidence import RUNNING_NOTICE, lab_completion_check
from tests.test_lab_evaluation import rows
from tests.test_structured_finalize import _call, _local_executor, _web_executor

pytestmark = pytest.mark.stubbed_backend


class SnapshotLedger:
    def __init__(self):
        self.run, self.commands, self.observations, self.evaluations = rows()
        self.run.update(root_frame_id="root", end_reason="end_action")
        self.cursor = 1

    def list_runs(self, root, *, limit):
        return [self.run] if self.run and root == "root" else []

    def get_run(self, run_id):
        return self.run if self.run and self.run["run_id"] == run_id else None

    def latest_event_seq(self, root):
        return self.cursor

    def list_commands(self, run_id, *, limit):
        assert limit > 1000  # Never silently use a default first page.
        return deepcopy(self.commands)

    def list_observations(self, run_id, *, limit):
        assert limit > 1000
        return deepcopy(self.observations)

    def list_evaluations(self, run_id, *, limit):
        assert limit > 1000
        return deepcopy(self.evaluations)


def claim(status="completed", **extra):
    return {
        "summary": "The simulation evidence was checked.",
        "completion_bullets": ["Reviewed the simulation evidence"],
        "lab_runs": [{"run_id": "run", "status": status}],
        **extra,
    }


def finish(door, service, payload):
    if door == "submit":
        spec = dict(payload)
        bullets = spec.pop("completion_bullets")
        task_status = spec.pop("task_status", None)
        reply = service.submit(
            {"output": spec, "completion_bullets": bullets, "task_status": task_status}
        )
        return service.last_output, reply.get("error", "")
    executor = _web_executor() if door == "web" else _local_executor()
    dispatcher = SimpleNamespace(verify_code_evidence=service.verify_code_claims)
    executor.dispatcher = (lambda: dispatcher) if door == "web" else dispatcher
    call = _call(payload)
    state = RunState([], metadata={"execution_evidence": {"cells": 0, "tool_calls": 9}})
    result = executor.execute(
        FinalizeAction(call), ModelReply(tool_calls=(call,)), state
    )
    return result.completion, result.observation


@pytest.mark.parametrize("door", ["web", "cli", "submit"])
@pytest.mark.parametrize(
    "problem,reason",
    [
        ("started", "not ended normally"),
        ("gym_done", "not ended normally"),
        ("stopped", "not ended normally"),
        ("unknown", "unresolved command"),
        ("goal_false", "goal evaluation"),
        ("goal_unknown", "goal evaluation"),
        ("missing_observation", "matching observation"),
        ("wrong_observation", "matching observation"),
        ("foreign_run", "not available in this session"),
        ("missing_run", "not available in this session"),
        ("no_declaration", "explicit lab_runs"),
    ],
)
def test_incomplete_lab_claims_fail_at_every_door(door, problem, reason, caplog):
    ledger = SnapshotLedger()
    payload = claim(task_status="completed")
    if problem == "started":
        ledger.run.update(status="ready", end_reason=None)
    elif problem in {"gym_done", "stopped"}:
        ledger.run["end_reason"] = (
            "env_terminated" if problem == "gym_done" else "stopped"
        )
    elif problem == "unknown":
        ledger.commands.append({"state": "outcome_unknown"})
    elif problem == "goal_false":
        ledger.evaluations[-1]["ground_truth"]["vessels"][0]["moles"][
            "C6H14"
        ] = 827.19463
    elif problem == "goal_unknown":
        ledger.evaluations.pop()
    elif problem == "missing_observation":
        ledger.observations.clear()
    elif problem == "wrong_observation":
        ledger.observations[0]["command_id"] = "another-command"
    elif problem == "foreign_run":
        ledger.run["root_frame_id"] = "another-root"
    elif problem == "missing_run":
        payload["lab_runs"][0]["run_id"] = "missing"
    else:
        del payload["lab_runs"]
    service = CompletionService(
        lab_evidence=lambda p: lab_completion_check(ledger, "root", p)
    )
    completed, error = finish(door, service, payload)
    assert completed is None
    assert reason in error
    # Even error paths must not reflect reward, composition, or evaluator output.
    public = str(error) + caplog.text
    for secret in ("827.19463", "297", "0.6", "0.98", "-3", "ground_truth", "purity"):
        assert secret not in public


@pytest.mark.parametrize("door", ["web", "cli", "submit"])
@pytest.mark.parametrize("end_reason", ["end_action", "max_steps"])
def test_completed_lab_claims_pass(door, end_reason):
    ledger = SnapshotLedger()
    ledger.run["end_reason"] = end_reason
    service = CompletionService(
        lab_evidence=lambda p: lab_completion_check(ledger, "root", p)
    )
    completed, _ = finish(door, service, claim())
    assert completed["output"]["lab_runs"] == claim()["lab_runs"]


@pytest.mark.parametrize("door", ["web", "cli", "submit"])
@pytest.mark.parametrize("valid", [True, False])
def test_running_is_explicit_partial_progress(door, valid):
    ledger = SnapshotLedger()
    ledger.run.update(status="ready", end_reason=None)
    service = CompletionService(
        lab_evidence=lambda p: lab_completion_check(ledger, "root", p)
    )
    payload = claim("running", task_status="partial", summary=RUNNING_NOTICE)
    if not valid:
        payload["task_status"] = "completed"
    completed, error = finish(door, service, payload)
    if valid:
        assert RUNNING_NOTICE in json.dumps(completed)
        assert "partial" in json.dumps(completed)
    else:
        assert completed is None and "partial" in error


@pytest.mark.parametrize("door", ["web", "cli", "submit"])
def test_no_lab_completion_is_byte_identical(door):
    ledger = SnapshotLedger()
    ledger.run = None
    payload = claim()
    del payload["lab_runs"]
    legacy = finish(door, CompletionService(), deepcopy(payload))
    checked = finish(
        door,
        CompletionService(
            lab_evidence=lambda p: lab_completion_check(ledger, "root", p)
        ),
        payload,
    )
    assert json.dumps(checked) == json.dumps(legacy)


def test_pending_submission_rechecks_lab_after_the_cell():
    ledger = SnapshotLedger()
    ledger.run.update(status="ready", end_reason=None)
    service = CompletionService(
        lab_evidence=lambda p: lab_completion_check(ledger, "root", p)
    )
    completed, _ = finish(
        "submit",
        service,
        claim("running", task_status="partial", summary=RUNNING_NOTICE),
    )
    assert completed and service.revalidate_pending_completion() is None
    ledger.run.update(status="ended", end_reason="stopped")
    assert "no longer running" in service.revalidate_pending_completion()
    assert service.last_output is None


def test_evidence_failure_and_concurrent_changes_fail_closed(caplog):
    ledger = SnapshotLedger()

    def broken(*args, **kwargs):
        raise RuntimeError("secret 827.19463")

    ledger.list_evaluations = broken
    error = lab_completion_check(ledger, "root", claim())
    assert error == "Lab completion evidence could not be verified."
    assert "827.19463" not in error + caplog.text
    service = CompletionService(lab_evidence=broken)
    assert service.verify_code_claims(claim()) == error
    ledger = SnapshotLedger()
    original = ledger.list_evaluations

    def changed(*args, **kwargs):
        ledger.cursor += 1
        return original(*args, **kwargs)

    ledger.list_evaluations = changed
    assert "changed during verification" in lab_completion_check(
        ledger, "root", claim()
    )


@pytest.mark.parametrize(
    "value",
    [
        [],
        [{"run_id": "run", "status": "done"}],
        [{"run_id": "run", "status": "completed", "reward": 123}],
    ],
)
def test_lab_declaration_schema_is_closed_at_both_doors(value):
    assert validate_finalize_arguments(claim()) is None
    payload = claim(lab_runs=value)
    assert validate_finalize_arguments(payload)
    assert lab_completion_check(SnapshotLedger(), "root", payload)


def test_wurtz_goal_comes_from_initial_public_target():
    ledger = SnapshotLedger()
    ledger.run["profile"] = "GenWurtzExtract-v2"
    ledger.evaluations[-1]["ground_truth"]["vessels"][0]["moles"].pop("H2O")
    # Truth is identical; only the public target determines the goal.
    initial = {
        "run_id": "run",
        "sequence": 0,
        "channels": [
            {
                "name": "targets",
                "value": "NaCl",
                "quality": "ok",
                "source": "simulated_sensor",
            }
        ],
    }
    ledger.observations.insert(0, initial)
    assert lab_completion_check(ledger, "root", claim()) is None
    initial["channels"][0]["value"] = "dodecane"
    assert "goal evaluation" in lab_completion_check(ledger, "root", claim())
