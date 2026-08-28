"""Transaction-order contracts for the Web scientific cell service."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from openai4s.execution import CaptureResult, CellRequest
from openai4s.kernel import KernelSupervisor
from openai4s.server import cell_run
from openai4s.server.cell_run import CellExecutionPorts, CellExecutionService
from openai4s.server.errors import GatewayError


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
        self.capture_receipts = None

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

    def safety_refusal(self, session, code, origin):
        # Takes the session as well as the cell: the biosecurity screener
        # judges a trajectory, not one cell, and a port that saw only the cell
        # could never run it — which is why the Web path ran the static
        # classifier alone while the CLI ran both.
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

    def capture(
        self, session, index, cell_id, before, emit, language, artifact_receipts
    ):
        assert self.completion is not None
        self.order.append("capture")
        self.capture_cell_id = cell_id
        self.capture_receipts = artifact_receipts
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


@pytest.mark.parametrize("origin", ["agent", "user"])
def test_readiness_admission_precedes_cell_revision_attempt_and_runtime(
    tmp_path, origin
):
    """Agent and direct Notebook cells share the same zero-side-effect refusal."""
    harness = Harness()
    calls = {"admit": 0, "cell_id": 0, "attempt": 0}

    def refuse(_session, request):
        calls["admit"] += 1
        assert request.origin == origin
        raise GatewayError(
            409,
            "The standard scientific environment is not ready.",
            "environment_not_ready",
        )

    def allocate(*_args):
        calls["attempt"] += 1
        raise AssertionError("a refused cell allocated a durable attempt")

    def cell_id():
        calls["cell_id"] += 1
        raise AssertionError("a refused cell allocated an id")

    service = CellExecutionService(
        replace(
            harness.ports(),
            admit=refuse,
            allocate_attempt=allocate,
        ),
        id_factory=cell_id,
    )
    session = _session(tmp_path)
    events: list[dict] = []

    with pytest.raises(GatewayError) as refused:
        service.execute(
            session,
            CellRequest("import missing_science_package", origin, stream=True),
            events.append,
        )

    assert refused.value.code == 409
    assert refused.value.error_code == "environment_not_ready"
    assert calls == {"admit": 1, "cell_id": 0, "attempt": 0}
    assert session.cell_index == 0
    assert harness.order == []
    assert harness.records == []
    assert events == []


def test_malformed_capture_lease_fails_before_cell_identity_or_admission(tmp_path):
    """An unparseable lease is a refusal, never permission to run unguarded."""

    harness = Harness()
    calls = {"admit": 0, "cell_id": 0}

    def admit(_session, _request):
        calls["admit"] += 1

    def cell_id():
        calls["cell_id"] += 1
        return "must-not-be-created"

    service = CellExecutionService(
        replace(
            harness.ports(),
            admit=admit,
            capture_lease=lambda _session, _request: object(),
        ),
        id_factory=cell_id,
    )
    session = _session(tmp_path)

    with pytest.raises(TypeError, match="capture_lease must return a context manager"):
        service.execute(
            session,
            CellRequest("print('must not run')", "agent"),
            lambda _event: None,
        )

    assert calls == {"admit": 0, "cell_id": 0}
    assert session.cell_index == 0
    assert harness.order == []


def test_skill_network_admission_runs_before_kernel_run(tmp_path, monkeypatch):
    """The Cell sink always consults skill-network admission, even with no loads."""
    calls = {"admit": 0}

    def _admit(**kwargs):
        calls["admit"] += 1
        from openai4s.server.skill_network_admission import AdmissionDecision

        return AdmissionDecision(
            allowed=True,
            reason=None,
            blocked_on=(),
            sink="cell",
        )

    monkeypatch.setattr("openai4s.server.skill_network_admission.admit_cell", _admit)
    harness = Harness()
    service = CellExecutionService(harness.ports())
    service.execute(
        _session(tmp_path),
        CellRequest("print(1)", "agent"),
        lambda _event: None,
    )
    assert calls["admit"] == 1
    assert "run" in harness.order


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
        "state_revision": 1,
        "generation_id": None,
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
    assert finished["state_revision"] == 1
    assert finished["generation_id"] is None
    assert finished["status"] == "ok"
    assert finished["origin"] == "agent"
    assert finished["stdout"] == "ok"
    assert finished["figures"] == ["figure-1.png"]
    assert finished["files_written"] == ["result.csv"]
    assert harness.records[0]["state_revision"] == 1
    assert result.state_revision == 1
    assert result.generation_id is None


def test_bind_lineage_records_host_side_reads(tmp_path):
    harness = Harness()
    harness.capture_result = CaptureResult(
        files_written=["table.json"],
        artifacts=[{"version_id": "v-out", "filename": "table.json"}],
    )

    harness.run_result["files_read"] = ["table.csv"]

    def bind(session, request, before, capture, cell_id):
        assert "table.csv" in request.code
        assert capture.files_written == ["table.json"]
        assert capture.files_read == ["table.csv"]
        return ["table.csv"]

    service = CellExecutionService(
        replace(harness.ports(), bind_lineage=bind),
        id_factory=lambda: "cell-lineage",
    )
    result = service.execute(
        _session(tmp_path),
        CellRequest('json.dump(open("table.csv"))', "user"),
        lambda event: None,
    )
    assert result.capture.files_read == ["table.csv"]
    assert harness.records[0]["files_read"] == ["table.csv"]


def test_missing_runtime_read_observation_never_falls_back_to_source(tmp_path):
    harness = Harness()
    seen = []

    def bind(_session, _request, _before, capture, _cell_id):
        seen.extend(capture.files_read)
        return list(capture.files_read)

    service = CellExecutionService(
        replace(harness.ports(), bind_lineage=bind),
        id_factory=lambda: "cell-no-read-proof",
    )
    result = service.execute(
        _session(tmp_path),
        CellRequest('print("table.csv")\nopen("out.json", "w").write("x")', "user"),
        lambda _event: None,
    )
    assert seen == []
    assert result.capture.files_read == []


def test_lineage_binding_failure_refuses_to_publish_a_successful_cell(tmp_path):
    harness = Harness()
    harness.capture_result = CaptureResult(
        files_written=["table.json"],
        artifacts=[{"version_id": "v-out", "filename": "table.json"}],
    )

    def fail_binding(*_args):
        raise OSError("lineage store unavailable")

    service = CellExecutionService(
        replace(harness.ports(), bind_lineage=fail_binding),
        id_factory=lambda: "cell-lineage-failed",
    )
    with pytest.raises(OSError, match="lineage store unavailable"):
        service.execute(
            _session(tmp_path),
            CellRequest('open("table.csv").read()', "user"),
            lambda _event: None,
        )
    assert "record" not in harness.order
    assert harness.records == []


def test_live_and_finished_events_use_the_exact_persistent_generation(tmp_path):
    harness = Harness()
    session = _session(tmp_path)
    bound_generations = []

    class Kernel:
        def is_alive(self):
            return True

        def shutdown(self):
            return None

    lease = session.kernels.ensure("python", "base", Kernel)
    ports = replace(
        harness.ports(),
        allocate_attempt=lambda *args: "attempt-gen",
        bind_attempt_generation=lambda attempt_id, active, language: (
            bound_generations.append(
                (attempt_id, active.kernels.status(language).get("generation_id"))
            )
        ),
    )
    service = CellExecutionService(ports, id_factory=lambda: "cell-gen")
    events = []

    result = service.execute(
        session,
        CellRequest("print('generation')", "agent"),
        events.append,
    )

    start = next(event for event in events if event["type"] == "notebook_cell_start")
    finished = events[-1]
    assert start["state_revision"] == finished["state_revision"] == 1
    assert start["generation_id"] == finished["generation_id"] == lease.generation_id
    assert bound_generations == [("attempt-gen", lease.generation_id)]
    assert result.state_revision == 1
    assert result.generation_id == lease.generation_id


def test_live_cell_output_is_bounded_once_for_notebook_and_activity(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(cell_run, "LIVE_CELL_OUTPUT_CHARS", 8)
    harness = Harness()

    def run(session, request, cell_id, on_chunk, lease):
        del session, request, cell_id, lease
        harness.order.append("run")
        assert on_chunk is not None
        on_chunk("123456")
        on_chunk("7890")
        on_chunk("must-not-appear")
        harness.completion = {"ok": True}
        return dict(harness.run_result)

    service = CellExecutionService(
        replace(harness.ports(), run=run), id_factory=lambda: "cell-bounded"
    )
    events = []

    service.execute(
        _session(tmp_path), CellRequest("print('bounded')", "agent"), events.append
    )

    notebook_chunks = [
        event["chunk"] for event in events if event["type"] == "notebook_cell_chunk"
    ]
    activity_chunks = [
        event["chunk"]
        for event in events
        if event["type"] == "text_chunk"
        and event.get("producing_cell_id") == "cell-bounded"
        and not event.get("cell_index")
        and cell_run.NOTEBOOK_DIVIDER not in event.get("chunk", "")
    ]
    expected = ["123456", "78" + cell_run.LIVE_OUTPUT_TRUNCATION]
    assert notebook_chunks == expected
    assert activity_chunks == expected
    assert "must-not-appear" not in "".join(notebook_chunks + activity_chunks)


def test_finished_event_is_bounded_without_mutating_result_or_record(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(cell_run, "LIVE_CELL_OUTPUT_CHARS", 8)
    harness = Harness()
    original = {
        "stdout": "stdout-more-than-eight",
        "stderr": "stderr-more-than-eight",
        "error": "error-more-than-eight",
    }
    harness.run_result = dict(original)
    service = CellExecutionService(harness.ports(), id_factory=lambda: "cell-terminal")
    events = []

    result = service.execute(
        _session(tmp_path), CellRequest("raise RuntimeError()", "agent"), events.append
    )

    finished = events[-1]
    assert finished["type"] == "notebook_cell_finished"
    for field, value in original.items():
        assert finished[field] == value[:8] + cell_run.LIVE_OUTPUT_TRUNCATION
        assert finished[field].count(cell_run.LIVE_OUTPUT_TRUNCATION) == 1
    stored = result.result
    assert stored["id"] == "cell-terminal"
    for field, value in original.items():
        assert stored[field] == value
    assert stored["skill_network"]["allowed"] is True
    assert harness.records[0]["result"] is result.result


def test_interrupted_result_wins_over_its_error_text_in_notebook_event(tmp_path):
    harness = Harness()
    harness.run_result = {
        "stdout": "",
        "stderr": "",
        "error": "Interrupted",
        "interrupted": True,
    }
    service = CellExecutionService(harness.ports(), id_factory=lambda: "cell-stop")
    events = []

    result = service.execute(
        _session(tmp_path),
        CellRequest("long_running_call()", "user"),
        events.append,
    )

    assert result.result["interrupted"] is True
    assert events[-1]["type"] == "notebook_cell_finished"
    assert events[-1]["status"] == "interrupted"
    assert events[-1]["error"] == "Interrupted"
    assert harness.records[0]["result"] is result.result
    assert harness.records[0]["result"]["interrupted"] is True


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
    assert harness.records[0]["visibility"] == "system"
    assert harness.records[0]["pin"] is False
    assert harness.records[0]["replay_policy"] == "never"


def test_cell_projection_labels_are_forwarded_to_the_immutable_record(tmp_path):
    harness = Harness()
    service = CellExecutionService(harness.ports(), id_factory=lambda: "cell-labels")

    service.execute(
        _session(tmp_path),
        CellRequest(
            "probe = inspect_state()",
            "user",
            stream=False,
            visibility="scratch",
            pin=True,
            replay_policy="conditional",
        ),
        lambda event: None,
    )

    record = harness.records[0]
    assert record["visibility"] == "scratch"
    assert record["pin"] is True
    assert record["replay_policy"] == "conditional"


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
    assert "capture" not in harness.order and harness.order[-1] == "record"
    assert harness.records[0]["state_revision"] == 1
    # Not `str(exc)`. A worker/protocol failure carries the drained stderr tail
    # -- traceback, absolute paths, argv -- and this row is persisted and
    # replayed through `notebook_cell_finished` and the execution log. The
    # exception's own text is a `RuntimeError`, so it has unknown provenance by
    # `public_exception`'s rule and gets the authored sentence plus a stable
    # code; the detail goes to the diagnostic log.
    published = harness.records[0]["result"]["error"]
    assert published.startswith("the kernel did not finish this cell")
    assert "kernel_execution_failed" in published
    assert "malformed R frame" not in published
    assert events[-1]["type"] == "notebook_cell_finished"
    assert events[-1]["producing_cell_id"] == "cell-r-bad"
    assert events[-1]["status"] == "error"
    assert events[-1]["error"] == published


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
            attempts.append(("allocated", cell_id, group_id, list(harness.order)))
            or "attempt-1"
        ),
        mark_attempt_started=lambda attempt_id: attempts.append(
            ("started", attempt_id)
        ),
        bind_attempt_generation=lambda attempt_id, session, language: attempts.append(
            ("bound", attempt_id, language, list(harness.order))
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
        ("bound", "attempt-1", "python", ["prepare", "label"]),
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
    # The attempt row carries no exception text now. It is projected into
    # the Action Timeline the UI renders and written into the exported
    # Session package, and Plan item 16 puts credential, absolute-path and
    # shell-command canaries on exactly those surfaces. `kind` survives
    # because a class name carries no argument from the instance; the
    # original goes to `record_diagnostic`, which is neither served nor
    # exported. See `test_public_exception_projector.py`.
    assert attempts[-1][3] == {
        "kind": "EOFError",
        "message": "the execution attempt failed",
        "code": "attempt_failed",
    }
    assert not any(item[0] in {"response", "capture"} for item in attempts)
    assert harness.records[0]["state_revision"] == 1
    published = harness.records[0]["result"]["error"]
    assert published.startswith("the kernel did not finish this cell")
    assert "worker exited" not in published


def test_watchdog_hard_cancel_is_durably_classified_as_cancelled(tmp_path):
    """Drive the service boundary that persists both Cell and attempt state."""
    from openai4s.execution.watchdog import KernelCancellation

    harness = Harness()
    harness.fail_run = KernelCancellation("private worker detail")
    attempts: list[tuple] = []
    ports = replace(
        harness.ports(),
        allocate_attempt=lambda *args: "attempt-cancelled",
        mark_attempt_started=lambda attempt_id: attempts.append(
            ("started", attempt_id)
        ),
        finish_attempt=lambda attempt_id, state, error: attempts.append(
            ("finished", attempt_id, state, error)
        ),
    )
    service = CellExecutionService(ports, id_factory=lambda: "cell-cancelled")

    with pytest.raises(KernelCancellation):
        service.execute(
            _session(tmp_path),
            CellRequest("while True: pass", "agent", stream=False),
            lambda event: None,
            action_group_id="group-cancelled",
        )

    assert attempts[-1] == (
        "finished",
        "attempt-cancelled",
        "cancelled",
        {
            "kind": "ExecutionCancelled",
            "message": "the execution attempt was cancelled",
            "code": "cell_cancelled",
        },
    )
    persisted = harness.records[0]["result"]["error"]
    assert "cancelled" in persisted
    assert "cell_cancelled" in persisted
    assert "time limit" not in persisted
    assert "private worker detail" not in persisted


def test_attempt_milestone_write_failure_still_finalizes_attempt(tmp_path):
    """A milestone write that raises (e.g. SQLite 'database is locked') must
    still finalize the attempt as record_failed; otherwise terminal_state stays
    NULL and the action timeline shows the group 'running' forever."""
    harness = Harness()
    attempts: list[tuple] = []

    def _boom(_attempt_id):
        raise RuntimeError("database is locked")

    ports = replace(
        harness.ports(),
        allocate_attempt=lambda *args: "attempt-lock",
        mark_attempt_started=_boom,
        finish_attempt=lambda attempt_id, state, error: attempts.append(
            ("finished", attempt_id, state, error)
        ),
    )
    service = CellExecutionService(ports, id_factory=lambda: "cell-lock")

    with pytest.raises(RuntimeError, match="database is locked"):
        service.execute(
            _session(tmp_path),
            CellRequest("print(1)", "agent", stream=False),
            lambda event: None,
            action_group_id="group-lock",
        )

    assert attempts[-1][:3] == ("finished", "attempt-lock", "record_failed")
    # The attempt row carries no exception text now. It is projected into
    # the Action Timeline the UI renders and written into the exported
    # Session package, and Plan item 16 puts credential, absolute-path and
    # shell-command canaries on exactly those surfaces. `kind` survives
    # because a class name carries no argument from the instance; the
    # original goes to `record_diagnostic`, which is neither served nor
    # exported. See `test_public_exception_projector.py`.
    assert attempts[-1][3] == {
        "kind": "RuntimeError",
        "message": "the execution attempt failed",
        "code": "attempt_failed",
    }


def test_worker_failure_is_recorded_once_and_retry_uses_a_new_cell_id(tmp_path):
    from openai4s.config import Config
    from openai4s.store import get_store

    store = get_store(Config(data_dir=tmp_path).db_path)
    frame_id = store.new_frame(project_id="project-1")
    session = _session(tmp_path)
    session.root_frame_id = frame_id
    ids = iter(("cell-dead-once", "cell-retry"))
    harness = Harness()
    harness.fail_run = EOFError("worker exited")
    ports = replace(harness.ports(), record_cell=store.log_cell)
    service = CellExecutionService(ports, id_factory=lambda: next(ids))

    with pytest.raises(EOFError, match="worker exited"):
        service.execute(
            session,
            CellRequest("fragile()", "agent", stream=False),
            lambda event: None,
        )

    harness.fail_run = None
    service.execute(
        session,
        CellRequest("retry()", "agent", stream=False),
        lambda event: None,
    )

    cells = store.list_cells(frame_id)
    assert [cell["producing_cell_id"] for cell in cells] == [
        "cell-dead-once",
        "cell-retry",
    ]
    assert [cell["state_revision"] for cell in cells] == [1, 2]
    assert cells[0]["status"] == "error"


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
            {
                "kind": "OSError",
                "message": "the execution attempt failed",
                "code": "attempt_failed",
            },
        )
    ]


def test_a_dead_worker_does_not_publish_its_stderr():
    """`str(exc)` for a worker death is the drained stderr tail.

    `manager.py` raises `kernel worker exited unexpectedly: <tail>`, and the
    tail is the worker's traceback, an R `system()`'s output, or anything any
    child wrote to fd2 -- with the absolute paths and argv that come with it.
    It reached the persisted cell row, `notebook_cell_finished` and
    `GET /frames/{id}/execution-log`, so one crash published it three times and
    kept it.
    """
    from openai4s.server.cell_run import _worker_failure_text
    from openai4s.server.errors import public_exception

    tail = (
        "Traceback (most recent call last):\n"
        '  File "/Users/alice/private-study/patients.py", line 3\n'
        "RuntimeError: cohort key AK-2026-88 rejected"
    )
    exc = RuntimeError(f"kernel worker exited unexpectedly: {tail}")
    public, _status = public_exception(
        exc, surface="cell:worker", error_code="kernel_execution_failed"
    )
    published = _worker_failure_text(exc, public)

    for leaked in ("/Users/alice", "patients.py", "AK-2026-88", "Traceback"):
        assert leaked not in published, published
    assert published.startswith("the kernel did not finish this cell")
    assert "kernel_execution_failed" in published


def test_a_timeout_still_says_the_kernel_was_reset():
    """The refusal must not cost the user the actionable half."""
    from openai4s.server.cell_run import _worker_failure_text
    from openai4s.server.errors import public_exception

    exc = TimeoutError("cell exceeded 120s in /srv/run/cell-9.py")
    public, _status = public_exception(
        exc, surface="cell:worker", error_code="cell_timeout"
    )
    published = _worker_failure_text(exc, public)

    assert "variables from earlier cells were cleared" in published
    assert "/srv/run" not in published


def test_a_watchdog_hard_cancel_is_projected_as_cancellation_not_timeout():
    from openai4s.execution.watchdog import KernelCancellation
    from openai4s.server.cell_run import _worker_failure_text
    from openai4s.server.errors import public_exception

    exc = KernelCancellation("private worker detail")
    public, _status = public_exception(
        exc, surface="cell:worker", error_code="cell_cancelled"
    )
    published = _worker_failure_text(exc, public)

    assert "cancelled" in published
    assert "cell_cancelled" in published
    assert "time limit" not in published
    assert "private worker detail" not in published


def test_a_bootstrap_failure_after_reset_does_not_claim_cluster_work_continues():
    from openai4s.execution.watchdog import KernelResetUnavailableTimeout
    from openai4s.server.cell_run import _worker_failure_text
    from openai4s.server.errors import public_exception

    exc = KernelResetUnavailableTimeout("private bootstrap detail")
    public, _status = public_exception(
        exc, surface="cell:worker", error_code="cell_timeout"
    )
    published = _worker_failure_text(exc, public)

    assert "variables from earlier cells were cleared" in published
    assert "replacement could not be initialized" in published
    assert "cluster" not in published and "allocation" not in published
