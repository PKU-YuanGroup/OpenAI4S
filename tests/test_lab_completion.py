"""Exact-run completion evidence across native and in-cell completion doors."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from openai4s.agent.actions import FinalizeAction
from openai4s.agent.finalize import validate_finalize_arguments
from openai4s.agent.models import ModelReply, RunState
from openai4s.host.completion import CompletionService
from openai4s.lab.evidence import (
    INCOMPLETE_WORDING,
    RUNNING_NOTICE,
    lab_completion_check,
)
from tests.test_lab_evaluation import rows
from tests.test_structured_finalize import _call, _local_executor, _web_executor

pytestmark = pytest.mark.stubbed_backend


class SnapshotLedger:
    def __init__(self):
        self.run, self.commands, self.observations, self.evaluations = rows()
        self.run.update(root_frame_id="root", end_reason="end_action")
        self.cursor = 1
        self.extra_runs = []
        self.events = []

    def list_runs(self, root, *, limit):
        runs = ([self.run] if self.run else []) + self.extra_runs
        return runs[:limit] if root == "root" else []

    def get_run(self, run_id):
        return next(
            (r for r in [self.run, *self.extra_runs] if r and r["run_id"] == run_id),
            None,
        )

    def events_since(self, root, *, after_seq=0, limit=200):
        rows = [e for e in self.events if e["event_seq"] > after_seq]
        return rows[:limit] if root == "root" else []

    def use(self, run_id, kind="command", state="succeeded"):
        self.cursor += 1
        self.events.append(
            {"event_seq": self.cursor, "run_id": run_id, "kind": kind, "state": state}
        )

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


@pytest.mark.parametrize("door", ["web", "cli", "submit"])
@pytest.mark.parametrize("task_status", ["partial", "blocked", "failed"])
@pytest.mark.parametrize(
    "wording",
    [
        "Completed the experiment",
        "实验已完成",
        "Reported that the simulation experiment is complete.",
    ],
)
@pytest.mark.parametrize("location", ["summary", "completion_bullets"])
def test_incomplete_status_cannot_hide_completion_prose(
    door, task_status, wording, location
):
    ledger = SnapshotLedger()
    ledger.run.update(status="ready", end_reason=None)
    service = CompletionService(
        lab_evidence=lambda p: lab_completion_check(ledger, "root", p)
    )
    payload = claim(task_status=task_status)
    payload[location] = [wording] if location == "completion_bullets" else wording
    del payload["lab_runs"]
    completed, error = finish(door, service, payload)
    assert completed is None and INCOMPLETE_WORDING[:40] in error
    # A running label and the required progress notice also cannot hide it.
    payload.update(
        lab_runs=[{"run_id": "run", "status": "running"}],
        task_status="partial",
        summary=RUNNING_NOTICE,
    )
    if location == "summary":
        payload["summary"] += " " + wording
    completed, error = finish(door, service, payload)
    assert completed is None and "completion wording" in error
    # An honest failure report remains possible after Stop.
    ledger.run.update(status="ended", end_reason="stopped")
    del payload["lab_runs"]
    payload.update(
        summary="The experiment was stopped.",
        completion_bullets=["Reported the stopped experiment"],
        task_status=task_status,
    )
    completed, _ = finish(door, service, payload)
    assert completed is not None


def test_running_notice_survives_public_summary_projection():
    from openai4s.server.completions import completion_message

    ledger = SnapshotLedger()
    ledger.run.update(status="ready", end_reason=None)
    service = CompletionService(
        lab_evidence=lambda p: lab_completion_check(ledger, "root", p)
    )
    payload = claim(
        "running",
        task_status="partial",
        summary="Observed sensor records. " * 200 + RUNNING_NOTICE,
    )
    completed, error = finish("submit", service, payload)
    assert completed is None and "summary" in error
    payload["summary"] = RUNNING_NOTICE + " " + "Observed sensor records. " * 200
    completed, _ = finish("submit", service, payload)
    assert RUNNING_NOTICE in completion_message(completed)


def test_structured_success_key_cannot_hide_in_partial_output():
    ledger = SnapshotLedger()
    ledger.run.update(status="ready", end_reason=None)
    service = CompletionService(
        lab_evidence=lambda p: lab_completion_check(ledger, "root", p)
    )
    result = service.submit(
        {
            "output": {"experiment_completed": True},
            "completion_bullets": ["Recorded the experiment result"],
            "task_status": "partial",
        }
    )
    assert INCOMPLETE_WORDING[:40] in result["error"]
    assert service.last_output is None


@pytest.mark.parametrize(
    "output",
    [
        {"findings": [{"run_id": "run", "task_status": "completed"}]},
        {"experiment": {"lab_runs": [{"run_id": "run", "status": "completed"}]}},
        {"summary": "Observed the run.", "task_status": "completed"},
    ],
)
def test_nested_machine_fields_do_not_hide_success(output):
    ledger = SnapshotLedger()
    ledger.run.update(status="ready", end_reason=None)
    service = CompletionService(
        lab_evidence=lambda p: lab_completion_check(ledger, "root", p)
    )
    result = service.submit(
        {
            "output": output,
            "completion_bullets": ["Recorded the experiment result"],
            "task_status": "partial",
        }
    )
    assert "error" in result
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


def _two_runs():
    """Run "run" ended and met its goal; run "stopped" did not."""
    ledger = SnapshotLedger()
    stopped = deepcopy(ledger.run)
    stopped.update(run_id="stopped", status="ended", end_reason="stopped")
    ledger.extra_runs.append(stopped)
    return ledger


@pytest.mark.parametrize("door", ["web", "cli", "submit"])
@pytest.mark.parametrize("scope", ["turn", "session"])
def test_a_verified_run_cannot_vouch_for_one_this_turn_used(door, scope):
    ledger = _two_runs()
    turn = ledger.cursor
    ledger.use("stopped")
    cursor = turn if scope == "turn" else None
    service = CompletionService(
        lab_evidence=lambda p: lab_completion_check(
            ledger, "root", p, turn_cursor=cursor
        )
    )
    payload = claim(summary="The stopped experiment met its goal.")
    completed, error = finish(door, service, payload)
    assert completed is None and "Declare every Lab run used" in error
    assert "stopped" in error
    payload["lab_runs"].append({"run_id": "stopped", "status": "completed"})
    completed, error = finish(door, service, payload)
    assert completed is None and "not ended normally" in error
    # The honest report about the stopped run is still possible.
    honest = claim(
        task_status="partial",
        summary="The experiment was stopped before the goal was achieved.",
        completion_bullets=["Completed two transfers before the stop"],
    )
    del honest["lab_runs"]
    completed, error = finish(door, service, honest)
    assert completed is not None, error


@pytest.mark.parametrize("door", ["web", "cli", "submit"])
def test_a_turn_without_lab_work_needs_no_declaration(door):
    ledger = _two_runs()
    ledger.use("stopped")
    later_turn = ledger.cursor
    service = CompletionService(
        lab_evidence=lambda p: lab_completion_check(
            ledger, "root", p, turn_cursor=later_turn
        )
    )
    payload = claim(summary="Paris is the capital of France.")
    del payload["lab_runs"]
    completed, error = finish(door, service, payload)
    assert completed is not None, error
    # Lab work later in the same turn makes the run part of this turn again.
    ledger.use("stopped")
    service = CompletionService(
        lab_evidence=lambda p: lab_completion_check(
            ledger, "root", p, turn_cursor=later_turn
        )
    )
    completed, error = finish(door, service, payload)
    assert completed is None and "explicit lab_runs" in error


@pytest.mark.parametrize("door", ["web", "cli", "submit"])
def test_sweeps_and_stops_of_untouched_runs_are_not_this_turns_work(door):
    ledger = _two_runs()
    turn = ledger.cursor
    ledger.use("stopped", kind="run", state="ended")
    ledger.use("run", kind="observation", state=None)
    service = CompletionService(
        lab_evidence=lambda p: lab_completion_check(ledger, "root", p, turn_cursor=turn)
    )
    payload = claim(summary="Summarized the earlier result.")
    del payload["lab_runs"]
    completed, error = finish(door, service, payload)
    assert completed is not None, error


@pytest.mark.parametrize("door", ["web", "cli", "submit"])
def test_lab_runs_is_plain_output_in_a_session_without_lab(door):
    ledger = SnapshotLedger()
    ledger.run = None
    service = CompletionService(
        lab_evidence=lambda p: lab_completion_check(ledger, "root", p)
    )
    for value in ([{"run_id": "R1", "yield": 0.8}], []):
        payload = claim(lab_runs=value, summary="Tabulated the wet-lab yields.")
        if door != "submit":
            # The native schema stays closed; only submit_output's free-form
            # output can carry an arbitrary key of that name.
            assert validate_finalize_arguments(payload)
            continue
        completed, error = finish(door, service, payload)
        assert completed is not None, error


@pytest.mark.parametrize("door", ["web", "cli", "submit"])
@pytest.mark.parametrize(
    "wording",
    [
        "实验未完成，已停止。",
        "The experiment did not complete.",
        "The run was stopped before the goal was achieved.",
    ],
)
def test_honest_incomplete_reports_pass(door, wording):
    ledger = SnapshotLedger()
    ledger.run.update(status="ended", end_reason="stopped")
    service = CompletionService(
        lab_evidence=lambda p: lab_completion_check(ledger, "root", p)
    )
    payload = claim(
        task_status="partial",
        summary=wording,
        completion_bullets=["Completed two transfers before the stop"],
    )
    del payload["lab_runs"]
    completed, error = finish(door, service, payload)
    assert completed is not None, error


@pytest.mark.parametrize("door", ["web", "cli", "submit"])
@pytest.mark.parametrize(
    "wording",
    [
        "ｃｏｍｐｌｅｔｅｄ the experiment",
        "comp\u200bleted the experiment",
        "目标已实现",
    ],
)
def test_normalized_success_wording_still_needs_evidence(door, wording):
    ledger = SnapshotLedger()
    ledger.run.update(status="ended", end_reason="stopped")
    service = CompletionService(
        lab_evidence=lambda p: lab_completion_check(ledger, "root", p)
    )
    payload = claim(task_status="partial", summary=wording)
    del payload["lab_runs"]
    completed, error = finish(door, service, payload)
    assert completed is None and INCOMPLETE_WORDING[:40] in error


@pytest.mark.parametrize("door", ["web", "cli", "submit"])
def test_running_runs_can_be_reported_blocked(door):
    ledger = SnapshotLedger()
    ledger.run.update(status="ready", end_reason=None)
    service = CompletionService(
        lab_evidence=lambda p: lab_completion_check(ledger, "root", p)
    )
    payload = claim("running", task_status="blocked", summary=RUNNING_NOTICE)
    completed, error = finish(door, service, payload)
    assert completed is not None, error


def test_a_profile_without_a_goal_is_told_to_report_partial():
    ledger = SnapshotLedger()
    ledger.run["profile"] = "unmodelled-profile"
    error = lab_completion_check(ledger, "root", claim())
    assert "defines no completion goal" in error and "partial" in error
