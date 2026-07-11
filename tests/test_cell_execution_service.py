"""Transaction-order contracts for the Web scientific cell service."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from openai4s.execution import CaptureResult, CellRequest
from openai4s.kernel import KernelSupervisor
from openai4s.server.cell_run import CellExecutionPorts, CellExecutionService


class Harness:
    def __init__(self) -> None:
        self.order: list[str] = []
        self.records: list[dict] = []
        self.runtime_error: str | None = None
        self.refusal: str | None = None
        self.run_result = {"stdout": "ok", "stderr": "", "error": None}
        self.capture_result = CaptureResult()
        self.completion = None
        self.fail_run: BaseException | None = None
        self.run_hook = None
        self.seen_lease = None
        self.run_cell_id = None
        self.capture_cell_id = None

    def ports(self) -> CellExecutionPorts:
        return CellExecutionPorts(
            prepare_language=self.prepare_language,
            kernel_id=self.kernel_id,
            snapshot=self.snapshot,
            protect_versions=self.protect_versions,
            safety_refusal=self.safety_refusal,
            run=self.run,
            capture=self.capture,
            emit_artifact_step=self.emit_artifact_step,
            record_cell=self.record_cell,
        )

    def prepare_language(self, session, language):
        self.order.append("prepare")
        return self.runtime_error

    def kernel_id(self, session, language):
        self.order.append("label")
        return "r" if language == "r" else "python — struct"

    def snapshot(self, workspace):
        self.order.append("snapshot")
        return {"before": 1}

    def protect_versions(self, session):
        self.order.append("protect")

    def safety_refusal(self, code, origin):
        self.order.append("safety")
        return self.refusal

    def run(self, session, request, cell_id, on_chunk, lease):
        self.order.append("run")
        self.run_cell_id = cell_id
        self.seen_lease = lease
        if self.run_hook is not None:
            self.run_hook(session, request, lease)
        if self.fail_run is not None:
            raise self.fail_run
        if on_chunk is not None:
            on_chunk("live output")
        # Simulate the mid-cell host.submit_output RPC. The service must still
        # capture and record the cell before returning to AgentEngine.
        self.completion = {"artifact": "result.csv"}
        return dict(self.run_result)

    def capture(self, session, index, cell_id, before, emit, language):
        assert self.completion is not None
        self.order.append("capture")
        self.capture_cell_id = cell_id
        return self.capture_result

    def emit_artifact_step(self, session, title, artifacts, emit):
        self.order.append("artifact_step")

    def record_cell(self, **record):
        self.order.append("record")
        self.records.append(record)


def _session(tmp_path):
    return SimpleNamespace(
        root_frame_id="frame-1",
        project_id="project-1",
        workspace=tmp_path,
        cell_index=0,
        kernels=KernelSupervisor(),
    )


def test_submit_output_does_not_skip_capture_or_execution_log(tmp_path):
    harness = Harness()
    harness.capture_result = CaptureResult(
        figures=["figure-1.png"],
        files_written=["result.csv"],
        artifacts=[{"artifact_id": "artifact-1", "filename": "result.csv"}],
    )
    service = CellExecutionService(harness.ports(), id_factory=lambda: "cell-1")
    session = _session(tmp_path)
    events = []

    result = service.execute(
        session,
        CellRequest("# Analyze\nprint('ok')", "agent"),
        events.append,
    )

    assert harness.order == [
        "prepare",
        "label",
        "snapshot",
        "protect",
        "safety",
        "run",
        "capture",
        "artifact_step",
        "record",
    ]
    assert result.result["id"] == "cell-1"
    assert harness.run_cell_id == harness.capture_cell_id == result.cell_id
    assert result.capture.files_written == ["result.csv"]
    assert harness.records[0]["result"] is result.result
    assert harness.records[0]["figures"] == ["figure-1.png"]
    assert [event["type"] for event in events] == [
        "notebook_cell_start",
        "text_chunk",
        "text_chunk",
        "notebook_cell_chunk",
        "text_chunk",
        "notebook_cell_finished",
    ]
    assert events[0] == {
        "type": "notebook_cell_start",
        "frame_id": "frame-1",
        "root_frame_id": "frame-1",
        "producing_cell_id": "cell-1",
        "cell_index": 1,
        "kernel_id": "python — struct",
        "language": "python",
        "origin": "agent",
        "source": "# Analyze\nprint('ok')",
        "title": "Analyze",
        "status": "running",
    }
    assert events[1]["chunk"] == "⚙Analyze\n"
    assert events[2]["chunk"].endswith("----- output -----\n")
    assert events[3]["chunk"] == "live output"
    assert events[3]["producing_cell_id"] == "cell-1"
    assert events[4]["chunk"] == "live output"
    finished = events[-1]
    assert finished["producing_cell_id"] == "cell-1"
    assert finished["status"] == "ok"
    assert finished["origin"] == "agent"
    assert finished["stdout"] == "ok"
    assert finished["figures"] == ["figure-1.png"]
    assert finished["files_written"] == ["result.csv"]


def test_protocol_only_submit_is_audited_without_streaming_a_notebook_cell(tmp_path):
    harness = Harness()
    service = CellExecutionService(harness.ports(), id_factory=lambda: "cell-submit")
    session = _session(tmp_path)
    events = []

    result = service.execute(
        session,
        CellRequest(
            "host.submit_output({'ok': True}, ['Completed the analysis'])",
            "agent",
        ),
        events.append,
    )

    assert harness.order == [
        "prepare",
        "label",
        "snapshot",
        "protect",
        "safety",
        "run",
        "capture",
        "record",
    ]
    assert events == []
    assert result.cell_id == "cell-submit"
    assert harness.records[0]["code"].startswith("host.submit_output")


def test_safety_refusal_is_a_logged_soft_error_without_runtime_or_capture(tmp_path):
    harness = Harness()
    harness.refusal = "blocked by safety policy"
    service = CellExecutionService(harness.ports(), id_factory=lambda: "cell-safe")
    events = []

    result = service.execute(
        _session(tmp_path),
        CellRequest("dangerous()", "agent"),
        events.append,
    )

    assert harness.order == [
        "prepare",
        "label",
        "snapshot",
        "protect",
        "safety",
        "record",
    ]
    assert result.result["error"] == "blocked by safety policy"
    assert result.capture == CaptureResult()
    assert harness.records[0]["files_written"] == []
    assert events[-2]["chunk"] == "\nblocked by safety policy"
    assert events[-2]["producing_cell_id"] == "cell-safe"
    assert events[-1]["type"] == "notebook_cell_finished"
    assert events[-1]["producing_cell_id"] == "cell-safe"
    assert events[-1]["status"] == "error"
    assert events[-1]["error"] == "blocked by safety policy"


def test_missing_r_runtime_is_a_logged_soft_error(tmp_path):
    harness = Harness()
    harness.runtime_error = "R kernel unavailable: Rscript missing"
    service = CellExecutionService(harness.ports(), id_factory=lambda: "cell-r")

    result = service.execute(
        _session(tmp_path),
        CellRequest("summary(data)", "agent", language="r", stream=False),
        lambda event: pytest.fail(f"unexpected stream event: {event}"),
    )

    assert result.result["error"].startswith("R kernel unavailable")
    assert harness.order[-1] == "record"
    assert "run" not in harness.order and "capture" not in harness.order
    assert harness.records[0]["kernel_id"] == "r"
    assert harness.records[0]["language"] == "r"


def test_r_protocol_exception_shuts_down_only_the_executing_lease(tmp_path):
    harness = Harness()
    harness.fail_run = RuntimeError("malformed R frame")
    session = _session(tmp_path)

    class RKernel:
        def __init__(self):
            self.live = True
            self.shutdown_calls = 0

        def is_alive(self):
            return self.live

        def shutdown(self):
            self.shutdown_calls += 1
            self.live = False

    kernel = RKernel()
    lease = session.kernels.ensure("r", None, lambda: kernel)
    service = CellExecutionService(harness.ports(), id_factory=lambda: "cell-r-bad")

    events = []
    with pytest.raises(RuntimeError, match="malformed R frame"):
        service.execute(
            session,
            CellRequest("bad()", "agent", language="r"),
            events.append,
        )

    assert session.kernels.current("r") is None
    assert harness.seen_lease == lease
    assert kernel.shutdown_calls == 1
    assert "capture" not in harness.order and "record" not in harness.order
    assert events[-1]["type"] == "notebook_cell_finished"
    assert events[-1]["producing_cell_id"] == "cell-r-bad"
    assert events[-1]["status"] == "error"
    assert events[-1]["error"] == "malformed R frame"


def test_r_exception_from_stale_lease_does_not_close_replacement(tmp_path):
    harness = Harness()
    harness.fail_run = RuntimeError("old R reader failed")
    session = _session(tmp_path)

    class RKernel:
        def __init__(self, name):
            self.name = name
            self.live = True
            self.shutdown_calls = 0

        def is_alive(self):
            return self.live

        def shutdown(self):
            self.shutdown_calls += 1
            self.live = False

    old = RKernel("old")
    replacement = RKernel("replacement")
    old_lease = session.kernels.ensure("r", "old", lambda: old)

    def replace_during_run(active_session, request, lease):
        active_session.kernels.ensure("r", "new", lambda: replacement)

    harness.run_hook = replace_during_run
    service = CellExecutionService(harness.ports(), id_factory=lambda: "cell-r-stale")

    with pytest.raises(RuntimeError, match="old R reader failed"):
        service.execute(
            session,
            CellRequest("bad()", "agent", language="r"),
            lambda event: None,
        )

    current = session.kernels.current("r")
    assert harness.seen_lease == old_lease
    assert current is not None and current.kernel is replacement
    assert old.shutdown_calls == 1
    assert replacement.live and replacement.shutdown_calls == 0


def test_non_streaming_cell_still_captures_and_records_without_activity_step(tmp_path):
    harness = Harness()
    harness.capture_result = CaptureResult(
        artifacts=[{"artifact_id": "artifact-1", "filename": "table.csv"}]
    )
    service = CellExecutionService(harness.ports(), id_factory=lambda: "cell-repl")
    events = []

    service.execute(
        _session(tmp_path),
        CellRequest("print(1)", "user", stream=False),
        events.append,
    )

    assert events == []
    assert "capture" in harness.order and harness.order[-1] == "record"
    assert "artifact_step" not in harness.order


def test_attempt_is_allocated_before_prepare_and_completes_all_milestones(tmp_path):
    harness = Harness()
    attempts: list[tuple] = []
    ports = replace(
        harness.ports(),
        allocate_attempt=lambda session, request, cell_id, group_id: (
            attempts.append(
                ("allocated", cell_id, group_id, list(harness.order))
            )
            or "attempt-1"
        ),
        mark_attempt_started=lambda attempt_id: attempts.append(
            ("started", attempt_id)
        ),
        mark_attempt_response=lambda attempt_id: attempts.append(
            ("response", attempt_id)
        ),
        mark_attempt_capture=lambda attempt_id: attempts.append(
            ("capture", attempt_id)
        ),
        finish_attempt=lambda attempt_id, state, error: attempts.append(
            ("finished", attempt_id, state, error)
        ),
    )
    service = CellExecutionService(ports, id_factory=lambda: "cell-ledger")

    service.execute(
        _session(tmp_path),
        CellRequest("print(1)", "agent", stream=False),
        lambda event: None,
        action_group_id="group-1",
    )

    assert attempts == [
        ("allocated", "cell-ledger", "group-1", []),
        ("started", "attempt-1"),
        ("response", "attempt-1"),
        ("capture", "attempt-1"),
        ("finished", "attempt-1", "completed", None),
    ]
    assert harness.order[:2] == ["prepare", "label"]


def test_worker_exception_still_finishes_allocated_attempt(tmp_path):
    harness = Harness()
    harness.fail_run = EOFError("worker exited")
    attempts: list[tuple] = []
    ports = replace(
        harness.ports(),
        allocate_attempt=lambda *args: "attempt-dead",
        mark_attempt_started=lambda attempt_id: attempts.append(
            ("started", attempt_id)
        ),
        mark_attempt_response=lambda attempt_id: attempts.append(
            ("response", attempt_id)
        ),
        mark_attempt_capture=lambda attempt_id: attempts.append(
            ("capture", attempt_id)
        ),
        finish_attempt=lambda attempt_id, state, error: attempts.append(
            ("finished", attempt_id, state, error)
        ),
    )
    service = CellExecutionService(ports, id_factory=lambda: "cell-dead")

    with pytest.raises(EOFError, match="worker exited"):
        service.execute(
            _session(tmp_path),
            CellRequest("print(1)", "agent", stream=False),
            lambda event: None,
            action_group_id="group-dead",
        )

    assert attempts[0] == ("started", "attempt-dead")
    assert attempts[-1][:3] == ("finished", "attempt-dead", "worker_died")
    assert attempts[-1][3] == {"kind": "EOFError", "message": "worker exited"}
    assert not any(item[0] in {"response", "capture"} for item in attempts)


def test_record_failure_is_not_misclassified_as_capture_failure(tmp_path):
    harness = Harness()
    attempts: list[tuple] = []

    def fail_record(**record):
        del record
        harness.order.append("record")
        raise OSError("sqlite unavailable")

    ports = replace(
        harness.ports(),
        record_cell=fail_record,
        allocate_attempt=lambda *args: "attempt-record",
        mark_attempt_started=lambda attempt_id: None,
        mark_attempt_response=lambda attempt_id: None,
        mark_attempt_capture=lambda attempt_id: None,
        finish_attempt=lambda attempt_id, state, error: attempts.append(
            (attempt_id, state, error)
        ),
    )
    service = CellExecutionService(ports, id_factory=lambda: "cell-record")

    with pytest.raises(OSError, match="sqlite unavailable"):
        service.execute(
            _session(tmp_path),
            CellRequest("print(1)", "agent", stream=False),
            lambda event: None,
            action_group_id="group-record",
        )

    assert "capture" in harness.order
    assert attempts == [
        (
            "attempt-record",
            "record_failed",
            {"kind": "OSError", "message": "sqlite unavailable"},
        )
    ]
