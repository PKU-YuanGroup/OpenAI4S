"""Native data/background control tools wrap existing Host behavior."""

from __future__ import annotations

from typing import Any

import pytest

from openai4s.config import Config
from openai4s.host_dispatch import build_dispatcher
from openai4s.tools.background import (
    InterruptBackgroundExecTool,
    ListBackgroundExecsTool,
    PeekBackgroundExecTool,
    SubmitBackgroundExecTool,
)
from openai4s.tools.data import (
    FramesTool,
    LineageGetTool,
    LineageGraphTool,
    QuerySchemaTool,
    ReadOnlyQueryTool,
)
from openai4s.tools.registry import get_tool, get_tool_by_host_method


class RecordingRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def invoke(self, method: str, *arguments: Any) -> Any:
        self.calls.append((method, arguments))
        return {"method": method, "arguments": list(arguments)}


class FakeBackgroundExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def launch(self, code: str, origin: str = "agent") -> dict:
        self.calls.append(("launch", code, origin))
        return {"exec_id": "exec-1", "status": "running"}

    def list_jobs(self) -> list[dict]:
        self.calls.append(("list",))
        return [{"exec_id": "exec-1", "status": "running"}]

    def peek(self, exec_id: str) -> dict:
        self.calls.append(("peek", exec_id))
        return {"exec_id": exec_id, "status": "running", "stdout": "partial"}

    def interrupt(self, exec_id: str) -> dict:
        self.calls.append(("interrupt", exec_id))
        return {"exec_id": exec_id, "status": "interrupted"}


def test_registry_exposes_named_data_and_background_tool_classes():
    expected = {
        "query_schema": QuerySchemaTool,
        "query": ReadOnlyQueryTool,
        "frames": FramesTool,
        "lineage_get": LineageGetTool,
        "lineage_graph": LineageGraphTool,
        "exec_background": SubmitBackgroundExecTool,
        "exec_list": ListBackgroundExecsTool,
        "exec_peek": PeekBackgroundExecTool,
        "exec_interrupt": InterruptBackgroundExecTool,
    }

    for name, tool_type in expected.items():
        tool = get_tool(name)
        assert type(tool) is tool_type
        assert get_tool_by_host_method(tool.host_method) is tool

    # Scientific completion and shell remain outside the native registry.
    assert get_tool("bash") is None
    assert get_tool("submit_output") is None


def test_tool_classes_forward_normalized_arguments_to_existing_host_methods():
    runtime = RecordingRuntime()

    QuerySchemaTool().execute(runtime, {})
    ReadOnlyQueryTool().execute(runtime, {"sql": "SELECT 1", "params": []})
    FramesTool().execute(runtime, {"project_id": "p-1", "limit": 10})
    LineageGetTool().execute(runtime, {"version_id": "v-1"})
    LineageGraphTool().execute(runtime, {"version_id": "v-1"})
    SubmitBackgroundExecTool().execute(runtime, {"code": "print(1)"})
    ListBackgroundExecsTool().execute(runtime, {})
    PeekBackgroundExecTool().execute(runtime, {"exec_id": "exec-1"})
    InterruptBackgroundExecTool().execute(runtime, {"exec_id": "exec-1"})

    assert runtime.calls == [
        ("query_schema", ()),
        ("query", ({"sql": "SELECT 1", "params": []},)),
        ("frames", ({"project_id": "p-1", "limit": 10},)),
        ("lineage_get", ("v-1",)),
        (
            "lineage_graph",
            ({"version_id": "v-1"},),
        ),
        ("exec_background", ({"code": "print(1)"},)),
        ("exec_list", ()),
        ("exec_peek", ("exec-1",)),
        ("exec_interrupt", ("exec-1",)),
    ]


def test_data_and_background_policy_taxonomy_and_resources():
    for name in (
        "query_schema",
        "query",
        "frames",
        "lineage_get",
        "lineage_graph",
        "exec_list",
        "exec_peek",
    ):
        tool = get_tool(name)
        assert tool.read_only is True
        assert tool.requires_approval is False
        assert tool.side_effect_class == "read_only"

    submit = get_tool("exec_background")
    assert submit.read_only is False
    assert submit.requires_approval is True
    assert submit.side_effect_class == "runtime_mutation"
    assert submit.permission_target({"code": "print(1)"}) == "print(1)"
    assert submit.resource_keys({"code": "print(1)"}) == ("background:jobs",)

    interrupt = get_tool("exec_interrupt")
    assert interrupt.read_only is False
    assert interrupt.requires_approval is False
    assert interrupt.side_effect_class == "runtime_mutation"
    assert interrupt.resource_keys({"exec_id": "exec-1"}) == ("background_exec:exec-1",)
    assert get_tool("query").resource_keys({"sql": "SELECT 1"}) == ("database:query",)
    assert get_tool("frames").resource_keys({"project_id": "p-1"}) == ("frame:p-1",)
    assert get_tool("lineage_graph").resource_keys({"version_id": "v-1"}) == (
        "lineage:v-1",
    )


def test_native_query_remains_strictly_read_only_without_approval(tmp_path):
    dispatcher = build_dispatcher(
        Config(data_dir=tmp_path / "data"),
        workspace=tmp_path,
    )
    dispatcher.store.set_permission_rule(
        scope="global",
        scope_id="",
        tool="query",
        pattern="*",
        decision="deny",
    )

    # Read-only tools do not consult approval rules; the Store's query guard is
    # the non-bypassable boundary and still rejects every write statement.
    assert dispatcher("query", [{"sql": "SELECT 7 AS value"}]) == [{"value": 7}]
    with pytest.raises(ValueError, match="only allows read-only"):
        dispatcher("query", [{"sql": "DELETE FROM frames"}])
    assert "settings" not in dispatcher("query_schema", [])


def test_background_submit_is_gated_but_exact_interrupt_stays_available(tmp_path):
    dispatcher = build_dispatcher(
        Config(data_dir=tmp_path / "data"),
        workspace=tmp_path,
    )
    background = FakeBackgroundExecutor()
    dispatcher._bg_executor = background
    dispatcher.store.set_permission_rule(
        scope="global",
        scope_id="",
        tool="exec_background",
        pattern="*",
        decision="deny",
    )

    denied = dispatcher("exec_background", [{"code": "print(1)"}])
    assert set(denied) == {"error"}
    assert background.calls == []

    dispatcher.store.set_permission_rule(
        scope="global",
        scope_id="",
        tool="exec_background",
        pattern="*",
        decision="allow",
    )
    assert dispatcher("exec_background", [{"code": "print(1)"}]) == {
        "exec_id": "exec-1",
        "status": "running",
    }
    assert dispatcher("exec_list", []) == [{"exec_id": "exec-1", "status": "running"}]
    # Existing in-kernel Host SDK calls remain positional after registration.
    assert dispatcher("exec_peek", ["exec-1"])["stdout"] == "partial"

    # A stop cannot create work or widen authority, so it remains available
    # even if a stale deny rule exists for this formerly non-gateable method.
    dispatcher.store.set_permission_rule(
        scope="global",
        scope_id="",
        tool="exec_interrupt",
        pattern="*",
        decision="deny",
    )
    assert dispatcher("exec_interrupt", ["exec-1"])["status"] == "interrupted"
    assert background.calls == [
        ("launch", "print(1)", "agent"),
        ("list",),
        ("peek", "exec-1"),
        ("interrupt", "exec-1"),
    ]


def test_team_dispatcher_refuses_bare_background_kernel_fallback(tmp_path):
    cfg = Config(data_dir=tmp_path / "data")
    cfg.team_mode = True
    dispatcher = build_dispatcher(
        cfg,
        workspace=tmp_path / "data" / "agent-workspaces" / "session",
    )

    with pytest.raises(RuntimeError, match="session-scoped kernel factory"):
        dispatcher._new_background_kernel()


def test_tool_only_turn_background_kernel_shares_the_write_file_workspace(
    tmp_path, monkeypatch
):
    """A Web turn that never spawns a foreground kernel still gets a
    workspace-anchored background kernel.

    Regression for a real Web session: the model wrote ``plot_data.py`` with
    the native write_file tool, then ran ``exec(open('plot_data.py').read())``
    through the native exec_background tool. ``background_kernel_factory`` is
    only wired when a foreground kernel spawns, so the tool-only turn fell to
    the bare ``Kernel(dispatcher=self)`` fallback, whose cwd was the daemon's
    launch directory — the repo checkout. The cell could not see the file the
    control plane had just written, its own ``savefig`` polluted the checkout,
    and the follow-up save_artifact failed because the artifact service
    resolves relative paths against the session workspace.
    """
    import time

    daemon_cwd = tmp_path / "daemon-cwd"
    daemon_cwd.mkdir()
    monkeypatch.chdir(daemon_cwd)

    data_dir = tmp_path / "data"
    workspace = data_dir / "agent-workspaces" / "f-regress"
    dispatcher = build_dispatcher(
        Config(data_dir=data_dir),
        workspace=workspace,
    )
    dispatcher.frame_id = dispatcher.store.new_frame(
        kind="turn", project_id="proj-bg-cwd", status="ready"
    )
    for tool in ("write_file", "exec_background", "save_artifact"):
        dispatcher.store.set_permission_rule(
            scope="global", scope_id="", tool=tool, pattern="*", decision="allow"
        )

    written = dispatcher("write_file", [{"path": "notes.txt", "content": "payload"}])
    assert written.get("error") is None
    assert (workspace / "notes.txt").is_file()

    launch = dispatcher(
        "exec_background",
        [
            {
                "code": (
                    "content = open('notes.txt').read()\n"
                    "open('made_by_cell.txt', 'w').write(content.upper())\n"
                    "import os\n"
                    "print(os.getcwd())\n"
                )
            }
        ],
    )
    exec_id = launch["exec_id"]
    try:
        deadline = time.monotonic() + 30
        while True:
            peek = dispatcher("exec_peek", [exec_id])
            if peek.get("done"):
                break
            assert time.monotonic() < deadline, f"background cell hung: {peek}"
            time.sleep(0.1)

        assert peek.get("error") is None, peek
        # The cell ran where write_file wrote, and never in the daemon's cwd.
        assert peek["stdout"].strip() == str(workspace.resolve())
        assert (workspace / "made_by_cell.txt").is_file()
        assert list(daemon_cwd.iterdir()) == []

        # The relative-path output is artifact-capturable: the artifact
        # service resolves against the same workspace the cell wrote into.
        record = dispatcher("save_artifact", [{"path": "made_by_cell.txt"}])
        assert record.get("version_id")
        assert record.get("filename") == "made_by_cell.txt"
    finally:
        executor = dispatcher._bg_executor
        if executor is not None:
            executor.shutdown(timeout_per_job=1.0)
