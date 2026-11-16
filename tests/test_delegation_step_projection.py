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

import json

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


@pytest.mark.parametrize(
    ("envelope", "audit_ok", "rl_kind"),
    [
        (
            {"task_status": "failed", "stop_reason": "error", "error": "child raised"},
            0,
            "host_call.failed",
        ),
        (
            {"task_status": "partial", "stop_reason": "max_turns"},
            1,
            "host_call.completed",
        ),
    ],
)
def test_the_audit_row_and_rl_agree_with_the_tool_verdict(
    dispatcher, envelope, audit_ok, rl_kind
):
    """A failed child is a failed call everywhere, not only in the tool result.

    ``execute_tool_call`` reads the envelope through ``declared_failure``; the
    dispatcher's own ``ok`` only knew the single-key soft-fail, so the audit
    row and the RL lifecycle recorded the same call as a success.
    """
    dispatcher._delegate_fn = lambda spec: _envelope(**envelope)
    steps: list[dict] = []
    dispatcher.on_step = steps.append

    with dispatcher.bind_rl_context({"episode_id": "f-root", "decision_id": "d-1"}):
        dispatcher("delegate", [{"request": "do it", "name": "helper", "wait": True}])

    row = dispatcher.store._conn.execute(
        "SELECT ok FROM host_call_log WHERE method='delegate'"
    ).fetchone()
    assert row["ok"] == audit_ok
    kinds = [
        kind
        for (kind,) in dispatcher.store._conn.execute(
            "SELECT kind FROM rl_execution_events WHERE episode_id='f-root' "
            "ORDER BY sequence"
        ).fetchall()
    ]
    assert kinds == ["host_call.started", rl_kind]
    # The Step card keeps the structured projection either way.
    assert _end_step(steps)["output"]["task_status"] == envelope["task_status"]


def _leaky_child(data_dir: str) -> dict:
    """A child result whose host paths would fill the card's raw budget.

    ``environment`` is first, and its interpreter path is longer than the
    2000-character raw bound, so truncating before scrubbing keeps the path
    and drops the conclusion. The prose itself does not mention the data
    directory or ``durable_path``.
    """

    prefix = str(data_dir)
    return {
        "environment": {
            "python": f"{prefix}/envs/sci/bin/python" + ("p" * 4000),
            "env_name": "sci",
            "env_root": f"{prefix}/envs/sci",
            "windows": "C:/Host/python.exe",
            "r_env": None,
            "generation_id": "g-1",
        },
        "artifact_refs": [
            {
                "artifact_id": "art-1",
                "version_id": "ver-1",
                "filename": "report.md",
                "checksum": "abc",
                "frame_id": "f-child",
                "path": "reports/report.md",
                "durable_path": f"{prefix}/delegation/c-1/report.md",
            },
            {
                "artifact_id": "art-2",
                "version_id": "ver-2",
                "filename": f"{prefix}/secret.txt",
                "checksum": "def",
                "frame_id": "f-child",
                "path": f"{prefix}/ws/out.txt",
                "durable_path": f"{prefix}/scratch/out.txt",
            },
        ],
        "artifacts": ["report.md", f"{prefix}/leak.txt"],
        "output": "BODY_MARKER the child kept this paragraph",
        "completion_bullets": ["BULLET_MARKER"],
        "final_message": "CONCLUSION_MARKER",
        "task_status": "completed",
        "name": "helper",
        "child_id": "c-1",
        "frame_id": "f-child",
        "stop_reason": "submitted",
        "turns": 2,
        "max_turns": 8,
    }


def _assert_no_host_paths(output: dict, data_dir: str) -> None:
    rendered = json.dumps(output)
    assert data_dir not in rendered
    assert "durable_path" not in rendered
    assert "C:/Host" not in rendered
    assert "BODY_MARKER the child kept this paragraph" in output["raw"]
    assert "BULLET_MARKER" in output["raw"]
    assert "CONCLUSION_MARKER" in rendered
    assert "reports/report.md" in rendered
    assert "report.md" in rendered
    assert output["environment"]["env_name"] == "sci"
    assert "python" not in output["environment"]
    assert "env_root" not in output["environment"]


def test_delegate_step_card_drops_host_paths_before_truncation(tmp_path):
    data_dir = str(tmp_path / "data")
    child = _leaky_child(data_dir)

    output, summary, status = _delegate_step_projection(child, True)

    assert status == "done"
    assert summary == "completed"
    assert output["summary"] == "CONCLUSION_MARKER"
    _assert_no_host_paths(output, data_dir)
    assert len(output["raw"]) <= 2000
    # The original child is what materialize still reads. The card must not
    # scrub it in place.
    assert "durable_path" in child["artifact_refs"][0]

    fanout, _summary, fanout_status = _delegate_step_projection([child, child], True)
    assert fanout_status == "done"
    rendered = json.dumps(fanout)
    assert data_dir not in rendered
    assert "durable_path" not in rendered
    assert "BODY_MARKER the child kept this paragraph" in fanout["raw"]
    assert fanout["children"][0]["environment"]["env_name"] == "sci"
    assert "python" not in fanout["children"][0]["environment"]


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


@pytest.mark.stubbed_backend
def test_steps_route_returns_the_scrubbed_delegate_card(tmp_path):
    """GET /frames/{fid}/steps is the handler the workbench reopens.

    The child result is injected. The route, the store row, and the step
    projection are the ones a session uses. ``stubbed_backend`` keeps this
    fabricated child out of the frozen response schema.
    """

    from openai4s.server import gateway as gateway_mod
    from openai4s.server import local_auth

    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    try:
        store = runner.store
        store.create_project(name="p", description="", context="")
        project_id = store.list_projects()[0]["project_id"]
        frame_id = runner.create_session(project_id)
        workspace = tmp_path / "ws"
        workspace.mkdir()
        dispatcher = build_dispatcher(cfg, frame_id=frame_id, workspace=workspace)
        dispatcher.store.set_permission_rule(
            scope="conversation",
            scope_id=frame_id,
            tool="delegate",
            pattern="*",
            decision="allow",
        )
        data_dir = str(cfg.data_dir)

        def sink(ev: dict) -> None:
            if ev.get("phase") == "begin":
                store.add_step(
                    step_id=ev["step_id"],
                    frame_id=frame_id,
                    kind=ev.get("kind"),
                    title=ev.get("title"),
                    input=ev.get("input"),
                    status="running",
                )
            else:
                store.update_step(
                    ev["step_id"],
                    status=ev.get("status"),
                    output=ev.get("output"),
                    summary=ev.get("summary"),
                )

        dispatcher.on_step = sink
        dispatcher._delegate_fn = lambda spec: _leaky_child(data_dir)
        dispatcher("delegate", [{"request": "do it", "name": "helper", "wait": True}])

        handler_class = gateway_mod.make_handler(cfg, _Hub(), runner)
        handler = object.__new__(handler_class)
        sent: dict = {}

        def _send(code, payload, ctype, extra=None):
            sent["code"] = code
            sent["body"] = json.loads(payload.decode("utf-8"))

        handler._send = _send
        handler.command = "GET"
        handler.path = f"/api/v1/frames/{frame_id}/steps"
        handler.headers = {
            "Content-Length": "0",
            local_auth.TOKEN_HEADER: local_auth.read_token(cfg.data_dir) or "",
        }
        handler._route("GET")
    finally:
        runner.close()

    assert sent["code"] == 200
    rendered = json.dumps(sent["body"])
    assert data_dir not in rendered
    assert "durable_path" not in rendered
    assert "BODY_MARKER the child kept this paragraph" in rendered
    assert "BULLET_MARKER" in rendered
    assert "CONCLUSION_MARKER" in rendered
    assert "reports/report.md" in rendered
