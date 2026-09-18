"""The "Saving ..." step card must name the runtime that wrote the files.

A Cell that writes files gets a completed `artifact` step -- the "Saving
r_table.csv" card in the timeline -- whose input carries `environment`. The
Web writer filled that in from the Python kernel's label whatever language the
Cell was, so every R Cell's card was stored and shown as "environment python"
while the artifact's own environment snapshot for the same file said kind `r`.
The same fact recorded twice, by two writers, disagreeing: provenance that is
wrong rather than absent.

New rows are written with the Cell's own runtime label and its language.
Rows 0.2.x already stored carry no language; they are read through the
producing Cell (or its generation), which is the record that knew.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None

    def has_subscriber(self, root_frame_id):
        return False

    def drop_frame(self, root_frame_id):
        return None


class _FakeRKernel:
    """Stands in for the R worker, which needs an R install with jsonlite.

    Everything around it is the production path: the kernel/execute route, the
    execution coordinator, CellExecutionService, capture and the step writer.
    """

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)

    def is_alive(self) -> bool:
        return True

    def execute(self, code, origin="agent", on_chunk=None, *, cell_id=None):
        (self.workspace / "r_table.csv").write_text("k\n1\n2\n", encoding="utf-8")
        return {"id": cell_id, "stdout": "", "stderr": "", "error": None}

    def interrupt(self) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def restart(self) -> None:
        return None


def _call(runner, method, path, body=None):
    handler = object.__new__(gateway_mod.make_handler(runner.cfg, _Hub(), runner))
    replies: list[tuple[int, dict]] = []
    handler._query = lambda: {}
    handler._body = lambda: dict(body or {})
    handler._json = lambda value, code=200: replies.append((code, value))
    handler._api(method, path)
    assert replies, f"{method} {path} produced no reply"
    return replies[-1]


@pytest.fixture
def runner(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_NOTEBOOK_REPL", "1")
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=1,
    )
    assert cfg.notebook_repl is True
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    try:
        yield runner
    finally:
        runner.close()


def _artifact_steps(runner, frame_id):
    code, listed = _call(runner, "GET", f"/frames/{frame_id}/steps")
    assert code == 200
    return {
        step["title"]: step["input"]
        for step in listed["steps"]
        if step["kind"] == "artifact"
    }


@pytest.mark.stubbed_backend
def test_an_r_cell_s_saving_step_names_the_r_runtime(runner):
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    state.kernels.ensure("r", None, lambda: _FakeRKernel(state.workspace))
    runner._ensure_r_kernel = lambda _state: None

    code, reply = _call(
        runner,
        "POST",
        f"/frames/{frame_id}/kernel/execute",
        {
            "language": "r",
            "code": "write.csv(data.frame(k=1:2), 'r_table.csv')",
            "wait": True,
        },
    )
    assert code == 200, reply
    assert reply["cell"]["kernel_id"] == "r"
    assert reply["cell"]["files_written"] == ["r_table.csv"]

    code, reply = _call(
        runner,
        "POST",
        f"/frames/{frame_id}/kernel/execute",
        {
            "language": "python",
            "code": "open('py_table.csv', 'w').write('x\\n1\\n')",
            "wait": True,
        },
    )
    assert code == 200, reply
    assert reply["cell"]["files_written"] == ["py_table.csv"]

    steps = _artifact_steps(runner, frame_id)
    assert steps["Saving r_table.csv"]["environment"] == "r"
    assert steps["Saving r_table.csv"]["language"] == "r"
    assert steps["Saving py_table.csv"]["environment"] == "python"
    assert steps["Saving py_table.csv"]["language"] == "python"

    # The other record of the same fact must agree with the card.
    artifact = runner.store.artifact_by_filename("r_table.csv", frame_id, strict=True)
    snapshot = runner.store.env_snapshot_for_artifact(artifact["artifact_id"])
    assert snapshot["kind"] == "r"


def _legacy_capture_step(
    runner,
    frame_id,
    *,
    index,
    filename,
    language,
    kernel_id=None,
    environment="python",
    marker=None,
):
    """A capture step with the shape a 0.2.x daemon stored: no language key,
    the Python kernel's label, and the artifact produced by a logged Cell."""
    store = runner.store
    cell_id = store.log_cell(
        frame_id=frame_id,
        root_frame_id=frame_id,
        code="write.csv(x)" if language == "r" else "x.to_csv()",
        result={"stdout": "", "stderr": "", "error": None},
        cell_index=index,
        kernel_id=kernel_id or ("r" if language == "r" else "python"),
        language=language,
    )
    path = Path(runner.cfg.data_dir) / filename
    path.write_text("k\n1\n", encoding="utf-8")
    record = store.save_artifact(
        path=str(path),
        filename=filename,
        content_type="text/csv",
        size_bytes=path.stat().st_size,
        checksum=hashlib.sha256(path.read_bytes()).hexdigest(),
        producing_cell_id=cell_id,
        frame_id=frame_id,
        root_frame_id=frame_id,
    )
    step_input = {"files": [filename], "environment": environment}
    if marker is not None:
        step_input["language"] = marker
    step_id = f"s-{filename.replace('.', '-')}"
    store.add_step(
        step_id=step_id,
        frame_id=frame_id,
        kind="artifact",
        title=f"Saving {filename}",
        input=step_input,
        status="done",
    )
    store.update_step(
        step_id,
        output={
            "artifacts": [
                {
                    "artifact_id": record["artifact_id"],
                    "version_id": record["version_id"],
                    "filename": filename,
                }
            ]
        },
    )


def test_a_0_2_x_r_capture_step_is_read_through_its_producing_cell(runner):
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    _legacy_capture_step(
        runner, frame_id, index=1, filename="r_table.csv", language="r"
    )
    _legacy_capture_step(
        runner, frame_id, index=2, filename="py_table.csv", language="python"
    )
    _legacy_capture_step(
        runner,
        frame_id,
        index=3,
        filename="env_table.csv",
        language="r",
        kernel_id="r — r-mini",
    )

    steps = _artifact_steps(runner, frame_id)

    assert steps["Saving r_table.csv"]["environment"] == "r"
    assert steps["Saving py_table.csv"]["environment"] == "python"
    assert steps["Saving env_table.csv"]["environment"] == "r — r-mini"
    # Every consumer reads the same Store call: the review, the session
    # package and the share projection must not carry the stale label onward.
    stored = {
        step["title"]: step["input"]["environment"]
        for step in runner.store.list_steps(frame_id)
        if step["kind"] == "artifact"
    }
    assert stored == {
        "Saving r_table.csv": "r",
        "Saving py_table.csv": "python",
        "Saving env_table.csv": "r — r-mini",
    }


def test_a_step_that_recorded_its_language_is_read_as_written(runner):
    """A row that says what it was is the authority; the producing-Cell lookup
    is only for rows that never said. An identical rewrite reuses an earlier
    version, so a Python Cell's card can point at a version an R Cell made."""
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    _legacy_capture_step(
        runner, frame_id, index=1, filename="shared.csv", language="r", marker="python"
    )

    assert _artifact_steps(runner, frame_id)["Saving shared.csv"] == {
        "files": ["shared.csv"],
        "environment": "python",
        "language": "python",
    }
