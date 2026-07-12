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
