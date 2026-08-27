"""The delegate step card tells the truth about the child's task.

The old projection flattened every delegate result to
``({"result": _short(result, 2000)}, "done")`` — a 2000-char JSON string cut
mid-token under a hardcoded green "done", regardless of whether the child
completed, ran out of turns, was blocked, or returned a fan-out where every
element failed.  The projection now emits a structured, bounded dict whose
summary word reflects the envelope's machine-readable ``task_status``, and the
step status vocabulary gains ``warning`` so a not-done child renders amber
instead of green.  Mapping (delegate-specific — the generic
``_declared_failure_reason`` contract for env_setup/fold is untouched):

    completed                      -> done
    partial / blocked              -> warning
    stopped / cancelled / max_turns-> warning (task_status still authoritative)
    failed / malformed / transport -> error
    fan-out                        -> worst child wins
"""

from __future__ import annotations

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.host_dispatch import (
    _delegate_step_projection,
    _step_end,
    build_dispatcher,
)


def _envelope(**overrides):
    base = {
        "child_id": "c-1",
        "name": "helper",
        "stop_reason": "submitted",
        "task_status": "completed",
        "output": "final child text",
        "completion_bullets": ["did the thing"],
        "final_message": "wrote the report",
        "frame_id": "f-child",
        "turns": 3,
        "max_turns": 8,
        "environment": {
            "python": "/envs/sci/bin/python",
            "env_name": "sci",
            "env_root": "/envs/sci",
            "r_env": None,
            "generation_id": "g-1",
        },
        "limitations": [],
        "artifacts": ["report.md"],
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# unit projection: one envelope
# --------------------------------------------------------------------------


def test_completed_envelope_projects_done_with_a_structured_card():
    output, summary, status = _delegate_step_projection(_envelope(), True)

    assert status == "done"
    assert summary == "completed"  # the task_status word, never a bare "done"
    assert output["task_status"] == "completed"
    assert output["name"] == "helper"
    assert output["frame_id"] == "f-child"
    assert output["turns"] == 3 and output["max_turns"] == 8
    assert output["environment"]["env_name"] == "sci"
    assert output["artifacts"] == ["report.md"]
    assert output["summary"] == "wrote the report"
    assert isinstance(output["raw"], str) and len(output["raw"]) <= 2000


def test_partial_envelope_projects_warning_and_carries_limitations():
    output, summary, status = _delegate_step_projection(
        _envelope(task_status="partial", limitations=["no GPU available"]), True
    )

    assert status == "warning"
    assert summary == "partial"
    assert output["limitations"] == ["no GPU available"]


def test_blocked_envelope_projects_warning():
    _output, summary, status = _delegate_step_projection(
        _envelope(task_status="blocked"), True
    )
    assert status == "warning"
    assert summary == "blocked"


def test_failed_envelope_projects_error():
    _output, summary, status = _delegate_step_projection(
        _envelope(task_status="failed", error="child raised"), True
    )
    assert status == "error"
    assert summary == "failed"


def test_max_turns_envelope_stays_structured_not_a_flattened_error():
    """The max_turns envelope carries a top-level ``error`` — the generic
    projection would collapse it to ``{"error": …}``; the delegate reader must
    keep the structured card and let task_status decide the color."""

    output, summary, status = _delegate_step_projection(
        _envelope(
            task_status="partial",
            stop_reason="max_turns",
            error="max_turns exhausted before completion",
        ),
        True,
    )

    assert status == "warning"
    assert summary == "partial"
    assert output["task_status"] == "partial"
    assert "max_turns exhausted" in output["error"]


def test_stopped_result_projects_warning_not_done():
    stopped = {
        "child_id": "c-1",
        "name": "helper",
        "stop_reason": "stopped",
        "output": None,
        "completion_bullets": [],
        "error": None,
        "reason": "stopped by parent",
        "frame_id": "f-child",
    }
    _output, summary, status = _delegate_step_projection(stopped, True)
    assert status == "warning"
    assert summary == "stopped"


def test_async_handles_project_started_not_a_task_verdict():
    handle = {"child_id": "c-1", "name": "helper", "status": "pending"}
    output, summary, status = _delegate_step_projection(handle, True)
    assert status == "done"
    assert summary == "started"
    assert output["status"] == "pending"


def test_fanout_worst_child_wins():
    _output, _summary, status = _delegate_step_projection(
        [_envelope(), _envelope(task_status="partial")], True
    )
    assert status == "warning"

    output, summary, status = _delegate_step_projection(
        [_envelope(), _envelope(task_status="failed", error="boom")], True
    )
    assert status == "error"
    assert output["children"][0]["task_status"] == "completed"
    assert output["children"][1]["task_status"] == "failed"
    assert "2 children" in summary
    assert "raw" in output


def test_malformed_result_is_an_error():
    _output, summary, status = _delegate_step_projection(42, True)
    assert status == "error"
    assert summary == "malformed result"


def test_transport_error_keeps_the_failed_reason():
    output, summary, status = _delegate_step_projection({"error": "boom"}, False)
    assert status == "error"
    assert summary.startswith("failed: boom")
    assert output["error"] == "boom"


def test_step_end_delegate_branch_no_longer_flattens():
    output, summary = _step_end("delegate", "delegate", _envelope(), True)
    assert set(output.keys()) != {"result"}
    assert output["task_status"] == "completed"
    assert summary == "completed"


def test_step_end_mcp_branch_is_unchanged():
    output, summary = _step_end("mcp_call", "mcp", {"a": 1}, True)
    assert set(output.keys()) == {"result"}
    assert summary == "done"


# --------------------------------------------------------------------------
# through the real dispatcher: the step a Web session renders
# --------------------------------------------------------------------------


@pytest.fixture
def dispatcher(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    dispatcher = build_dispatcher(cfg, frame_id="f-root", workspace=workspace)
    dispatcher.store.set_permission_rule(
        scope="conversation",
        scope_id="f-root",
        tool="delegate",
        pattern="*",
        decision="allow",
    )
    return dispatcher


def _end_step(steps):
    return next(step for step in steps if step.get("phase") == "end")


def test_a_partial_child_projects_a_warning_step_end_to_end(dispatcher):
    dispatcher._delegate_fn = lambda spec: _envelope(task_status="partial")
    steps: list[dict] = []
    dispatcher.on_step = steps.append

    dispatcher("delegate", [{"request": "do it", "name": "helper", "wait": True}])

    end = _end_step(steps)
    assert end["status"] == "warning"
    assert end["summary"] == "partial"
    assert end["output"]["task_status"] == "partial"


def test_a_completed_child_projects_done_and_names_completion(dispatcher):
    dispatcher._delegate_fn = lambda spec: _envelope()
    steps: list[dict] = []
    dispatcher.on_step = steps.append

    dispatcher("delegate", [{"request": "do it", "name": "helper", "wait": True}])

    end = _end_step(steps)
    assert end["status"] == "done"
    assert end["summary"] == "completed"
    assert end["output"]["environment"]["env_name"] == "sci"


def test_a_failed_child_projects_error_end_to_end(dispatcher):
    dispatcher._delegate_fn = lambda spec: _envelope(
        task_status="failed", stop_reason="error", error="child raised"
    )
    steps: list[dict] = []
    dispatcher.on_step = steps.append

    dispatcher("delegate", [{"request": "do it", "name": "helper", "wait": True}])

    end = _end_step(steps)
    assert end["status"] == "error"
    assert end["summary"] == "failed"
    assert "child raised" in end["output"]["error"]
