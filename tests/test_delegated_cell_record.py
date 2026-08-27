"""Delegated child Cells become durable execution_log rows (D1, recorder half).

Before this recorder existed, a delegated child's executed code lived only in
the Action Ledger and the capped delegation result projection: no
``execution_log`` row, so ``cell_detail`` returned None, lineage said
``cell_recorded: false``, and a daemon restart kept nothing a Notebook reader
could reach.  The recorder writes every child Cell — failed ones included —
keyed ``frame_id = root_frame_id = <child delegate frame>`` with
``origin="delegate"``, which keeps child cells out of the root Notebook
projection by construction while making ``frame_detail(child)`` and the
lineage projection truthful.

The end-to-end tests run the real DelegationRunner with a real local kernel;
only the LLM is scripted.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import openai4s.agent.loop as loop_mod
from openai4s.agent.cell_record import (
    ComposedCellHooks,
    DelegatedCellRecorder,
    compose_cell_hooks,
)
from openai4s.agent.delegation import DelegationRunner
from openai4s.config import get_config
from openai4s.server.execution_views import ExecutionViewService
from openai4s.store import get_store


class ScriptedLLM:
    """Returns queued replies in order; each call pops one."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = []

    def __call__(self, messages, cfg, **kw):
        self.calls.append(messages)
        content = (
            self._replies.pop(0)
            if self._replies
            else ("```python\nhost.submit_output({}, ['Finished the task'])\n```")
        )
        return {
            "content": content,
            "reasoning": None,
            "usage": {},
            "finish_reason": "stop",
            "raw": {},
        }


def _cell(code: str, language: str = "python"):
    return SimpleNamespace(code=code, language=language)


def _store_and_child(project_id: str | None = None):
    store = get_store(get_config().db_path)
    kwargs = {"kind": "turn", "status": "ready"}
    if project_id is not None:
        kwargs["project_id"] = project_id
    parent = store.new_frame(**kwargs)
    child = store.new_frame(parent_id=parent, kind="delegate", name="worker", depth=1)
    return store, parent, child


# --------------------------------------------------------------------------
# Recorder unit contracts
# --------------------------------------------------------------------------


def test_recorder_writes_ok_cells_keyed_under_the_child_frame():
    store = get_store(get_config().db_path)
    project = store.create_project(name="rec", description="", context="")
    store2, parent, child = _store_and_child(project_id=project["project_id"])
    recorder = DelegatedCellRecorder(
        store, child, generation_id_for=lambda language: "gen-py-1"
    )

    action = _cell("print('hi')")
    token = recorder.before(action)
    recorder.after(
        action,
        token,
        {
            "id": "cell-child-1",
            "stdout": "hi\n",
            "stderr": "",
            "error": None,
            "usage": {"wall_s": 0.5, "cpu_s": 0.1, "peak_rss_kb": 2048},
        },
    )

    row = store.cell_detail("cell-child-1")
    assert row is not None
    assert row["frame_id"] == child
    assert row["root_frame_id"] == child
    assert row["origin"] == "delegate"
    assert row["status"] == "ok"
    assert row["code"] == "print('hi')"
    assert row["stdout"] == "hi\n"
    assert row["language"] == "python"
    assert row["kernel_id"] == "python"
    assert row["cell_seq"] == 1
    assert row["cell_index"] == 1
    assert row["state_revision"] == 1
    assert row["wall_s"] == 0.5
    assert row["cpu_s"] == 0.1
    assert row["peak_rss_kb"] == 2048
    assert row["generation_id"] == "gen-py-1"
    # The child frame inherits the parent session's project scope, and the
    # recorded row follows it.
    assert row["project_id"] == project["project_id"]
    # Child-keyed rows never enter the parent's Notebook projection.
    assert store.list_cells(parent) == []
    assert [c["producing_cell_id"] for c in store.list_cells(child)] == ["cell-child-1"]


def test_failed_and_interrupted_cells_are_recorded_with_advancing_ordinals():
    store, _parent, child = _store_and_child()
    recorder = DelegatedCellRecorder(store, child)

    recorder.after(
        _cell("raise ValueError('boom')"),
        None,
        {"id": "c-fail", "stdout": "", "stderr": "", "error": "ValueError: boom"},
    )
    recorder.after(
        _cell("time.sleep(60)"),
        None,
        {"id": "c-int", "stdout": "", "stderr": "", "error": None, "interrupted": True},
    )

    rows = store.list_cells(child)
    assert [r["producing_cell_id"] for r in rows] == ["c-fail", "c-int"]
    assert [r["status"] for r in rows] == ["error", "interrupted"]
    assert [r["cell_index"] for r in rows] == [1, 2]
    assert [r["state_revision"] for r in rows] == [1, 2]
    assert [store.cell_detail(r["producing_cell_id"])["cell_seq"] for r in rows] == [
        1,
        2,
    ]
    assert rows[0]["error"] == "ValueError: boom"
    assert store.cell_detail("c-int")["interrupted"] == 1


def test_host_side_failure_records_a_synthetic_error_row():
    store, _parent, child = _store_and_child()
    recorder = DelegatedCellRecorder(store, child)

    # agent/runtime.py calls after(action, token, None) when the execution
    # raised host-side; the cell must still enter the durable record.
    recorder.after(_cell("explode()"), None, None)

    rows = store.list_cells(child)
    assert len(rows) == 1
    assert rows[0]["status"] == "error"
    assert rows[0]["code"] == "explode()"
    detail = store.cell_detail(rows[0]["producing_cell_id"])
    assert detail["frame_id"] == child
    assert detail["root_frame_id"] == child
    assert detail["origin"] == "delegate"
    assert "host-side" in (detail["error"] or "")


def test_r_cells_record_language_kernel_id_and_their_own_generation():
    store, _parent, child = _store_and_child()
    generations = {"python": "gen-py", "r": "gen-r"}
    recorder = DelegatedCellRecorder(store, child, generation_id_for=generations.get)

    recorder.after(
        _cell("summary(x)", language="r"),
        None,
        {"id": "r-cell-1", "stdout": "ok\n", "stderr": "", "error": None},
    )

    row = store.cell_detail("r-cell-1")
    assert row is not None
    assert row["language"] == "r"
    assert row["kernel_id"] == "r"
    assert row["generation_id"] == "gen-r"


def test_recorder_swallows_storage_and_generation_failures():
    class ExplodingStore:
        def log_cell(self, **_kwargs):
            raise RuntimeError("disk full")

    logged: list[str] = []
    recorder = DelegatedCellRecorder(
        ExplodingStore(),
        "f-child",
        generation_id_for=lambda language: (_ for _ in ()).throw(
            RuntimeError("no source")
        ),
        log=logged.append,
    )

    # Must not raise into the executor: the cell already ran, and losing its
    # observation is strictly better than failing the child run over a
    # bookkeeping write.
    recorder.after(_cell("print(1)"), None, {"id": "x", "stdout": ""})
    assert logged and "disk full" in logged[0]


def test_generation_source_failure_still_records_the_row():
    store, _parent, child = _store_and_child()
    recorder = DelegatedCellRecorder(
        store,
        child,
        generation_id_for=lambda language: (_ for _ in ()).throw(RuntimeError("gone")),
    )
    recorder.after(_cell("print(2)"), None, {"id": "c-nogen", "stdout": ""})
    row = store.cell_detail("c-nogen")
    assert row is not None
    assert row["generation_id"] is None


# --------------------------------------------------------------------------
# Composition with the stage-1 capture hooks
# --------------------------------------------------------------------------


def test_compose_returns_none_or_the_single_hook_unwrapped():
    assert compose_cell_hooks(None, None) is None
    store, _parent, child = _store_and_child()
    recorder = DelegatedCellRecorder(store, child)
    assert compose_cell_hooks(recorder, None) is recorder
    assert compose_cell_hooks(None, recorder) is recorder
    composed = compose_cell_hooks(recorder, object())
    assert isinstance(composed, ComposedCellHooks)


def test_composed_hooks_record_even_when_capture_raises():
    events: list[object] = []

    class Capture:
        def before(self, _action):
            events.append("before")
            return "cap-token"

        def after(self, _action, token, _result):
            events.append(("after", token))
            raise RuntimeError("capture broke")

    store, _parent, child = _store_and_child()
    recorder = DelegatedCellRecorder(store, child)
    hooks = compose_cell_hooks(recorder, Capture())

    action = _cell("open('f.txt','w').write('x')")
    token = hooks.before(action)
    with pytest.raises(RuntimeError, match="capture broke"):
        hooks.after(action, token, {"id": "c-composed", "stdout": ""})

    # Each inner hook received its own token, and the durable record was
    # written before the capture failure propagated.
    assert events == ["before", ("after", "cap-token")]
    assert store.cell_detail("c-composed") is not None


def test_composed_native_hooks_reach_only_the_capture_half():
    class Capture:
        def __init__(self):
            self.calls = []

        def before(self, _action):
            return "cell-token"

        def after(self, _action, _token, _result):
            self.calls.append("after")

        def before_native(self, _action):
            self.calls.append("before_native")
            return "n-token"

        def after_native(self, call, token, result):
            self.calls.append(("after_native", token, result))

        def after_native_with_receipts(self, call, token, result, receipts):
            self.calls.append(("receipts", token, list(receipts)))

    store, _parent, child = _store_and_child()
    recorder = DelegatedCellRecorder(store, child)
    capture = Capture()
    hooks = compose_cell_hooks(recorder, capture)

    call = SimpleNamespace(name="write_file")
    token = hooks.before_native(call)
    hooks.after_native_with_receipts(call, token, "res", [{"filename": "x"}])
    hooks.after_native(call, token, "res2")

    assert capture.calls == [
        "before_native",
        ("receipts", "n-token", [{"filename": "x"}]),
        ("after_native", "n-token", "res2"),
    ]
    # A native action has no Cell identity; the recorder writes nothing.
    assert store.list_cells(child) == []


# --------------------------------------------------------------------------
# End to end: the real delegation path, a real kernel, scripted LLM only
# --------------------------------------------------------------------------


def test_delegated_child_cells_are_durable_end_to_end(monkeypatch, tmp_path):
    scripted = ScriptedLLM(
        [
            "```python\nprint('recorded child marker')\n```",
            # queue empty -> default host.submit_output completion cell
        ]
    )
    monkeypatch.setattr(loop_mod, "chat", scripted)
    workspace = tmp_path / "ws"
    workspace.mkdir()

    cfg = get_config()
    store = get_store(cfg.db_path)
    parent = store.new_frame(kind="turn", status="ready")
    runner = DelegationRunner(
        cfg, parent_frame_id=parent, store=store, workspace=workspace
    )
    try:
        result = runner({"request": "print a marker"})
    finally:
        runner.close()

    assert result["stop_reason"] == "submitted"
    child = result["frame_id"]
    rows = store.list_cells(child)
    assert len(rows) == 2, "both child cells (work + completion) must be recorded"
    assert [r["origin"] for r in rows] == ["delegate", "delegate"]
    assert rows[0]["status"] == "ok"
    assert "recorded child marker" in rows[0]["stdout"]
    assert "print('recorded child marker')" in rows[0]["code"]
    assert "host.submit_output" in rows[1]["code"]
    assert [r["cell_index"] for r in rows] == [1, 2]

    # Keyed under the child frame on both columns; never the parent's.
    detail = store.cell_detail(rows[0]["producing_cell_id"])
    assert detail["frame_id"] == child
    assert detail["root_frame_id"] == child
    assert store.list_cells(parent) == []

    # The generation stamped on the row is the child kernel's real durable
    # generation (S2), not a fabrication.
    generation = store.latest_kernel_generation(child, "python")
    assert generation is not None
    assert rows[0]["generation_id"] == generation["generation_id"]
    assert rows[1]["generation_id"] == generation["generation_id"]

    # frame_detail(child) serves the recorded cells with zero projection work.
    frame_view = store.frame_detail(child)
    assert [c["producing_cell_id"] for c in frame_view["cells"]] == [
        r["producing_cell_id"] for r in rows
    ]

    # Restart durability: a fresh Store generation on the same path still
    # serves the rows.
    cell_id = rows[0]["producing_cell_id"]
    store.close()
    reopened = get_store(cfg.db_path)
    row = reopened.cell_detail(cell_id)
    assert row is not None
    assert row["frame_id"] == child
    assert "recorded child marker" in row["stdout"]


def test_nested_child_cells_are_keyed_under_the_nested_child_frame(
    monkeypatch, tmp_path
):
    scripted = ScriptedLLM(
        [
            # child turn 1: delegate mid-cell to a grandchild
            "```python\n"
            "nested = host.delegate({'request': 'nested marker'})\n"
            "print('nested stop:', nested['stop_reason'])\n"
            "```",
            # grandchild turn 1
            "```python\nprint('grandchild marker')\n```",
            # grandchild turn 2
            "```python\nhost.submit_output({'ok': True}, ['grandchild done'])\n```",
            # child turn 2 -> default submit fallback
        ]
    )
    monkeypatch.setattr(loop_mod, "chat", scripted)
    workspace = tmp_path / "ws"
    workspace.mkdir()

    cfg = get_config()
    store = get_store(cfg.db_path)
    parent = store.new_frame(kind="turn", status="ready")
    runner = DelegationRunner(
        cfg, parent_frame_id=parent, store=store, workspace=workspace
    )
    try:
        result = runner({"request": "delegate deeper"})
    finally:
        runner.close()

    assert result["stop_reason"] == "submitted"
    child = result["frame_id"]
    grandchildren = [
        f for f in store.frame_detail(child)["children"] if f["kind"] == "delegate"
    ]
    assert len(grandchildren) == 1
    grand = grandchildren[0]["frame_id"]

    grand_rows = store.list_cells(grand)
    assert any(
        "grandchild marker" in (r["stdout"] or "") for r in grand_rows
    ), "the nested child's cell was not recorded under the nested frame"
    for row in grand_rows:
        detail = store.cell_detail(row["producing_cell_id"])
        assert detail["frame_id"] == grand
        assert detail["root_frame_id"] == grand
        assert detail["origin"] == "delegate"

    child_rows = store.list_cells(child)
    assert any("host.delegate" in r["code"] for r in child_rows)
    assert not any(
        "grandchild marker" in (r["stdout"] or "") for r in child_rows
    ), "a nested child's cell leaked into its parent's frame"
    assert store.list_cells(parent) == []


def test_lineage_reports_cell_recorded_for_a_delegated_producer(monkeypatch, tmp_path):
    """CLI-side twin of the gateway test: recorder without stage-1 hooks.

    stage1_trusted_delivery defaults OFF, so this path has no capture hooks at
    all — the recorder must still run (it must never ride cell_hooks_factory).
    """
    scripted = ScriptedLLM(
        [
            "```python\nprint('plain child')\n```",
        ]
    )
    monkeypatch.setattr(loop_mod, "chat", scripted)
    workspace = tmp_path / "ws"
    workspace.mkdir()

    cfg = get_config()
    store = get_store(cfg.db_path)
    parent = store.new_frame(kind="turn", status="ready")
    runner = DelegationRunner(
        cfg, parent_frame_id=parent, store=store, workspace=workspace
    )
    try:
        result = runner({"request": "run one plain cell"})
    finally:
        runner.close()

    child = result["frame_id"]
    rows = store.list_cells(child)
    assert rows, "no capture hooks were present, yet the cells must be recorded"

    # A synthetic artifact version claiming that cell as its producer now
    # resolves cell_recorded true through the unchanged lineage view.
    target = workspace / "produced.txt"
    target.write_text("bytes", encoding="utf-8")
    saved = store.save_artifact(
        path=str(target),
        filename="produced.txt",
        content_type="text/plain",
        size_bytes=5,
        checksum="c",
        frame_id=child,
        producing_cell_id=rows[0]["producing_cell_id"],
    )
    lineage = ExecutionViewService(
        store=store,
        format_timestamp=lambda value: str(value) if value is not None else None,
    ).artifact_lineage(saved["artifact_id"])
    assert lineage["producer"]["cell_recorded"] is True
    assert lineage["producer"]["producing_cell_id"] == rows[0]["producing_cell_id"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
