"""`mcp_tools` must accept the SDK's positional-string server argument.

`host.mcp.tools("volcengine-datapro")` is the one MCP SDK method that puts a
bare string on the wire (`["<server>"]`); the other five all send a spec dict.
When `mcp_tools` grew a control tool (`ListMCPToolsTool`), the dispatcher's
`__call__` began routing the RPC through `control_tool.execute(ctx, args[0])`
— and `execute` read `arguments.get("server")` off what is actually a `str`.
Every kernel-side discovery call then died with
``'str' object has no attribute 'get'`` while `host.mcp.list()` (empty args)
and `host.mcp.call(...)` (dict spec) kept working, which is exactly the
confusing triple users reported.

The existing connector tests drive `dispatcher._m_mcp_tools(...)` directly and
so never crossed the control-tool adaptation under test; everything here goes
through `dispatcher.__call__`, the path the worker RPC actually takes.
"""

from __future__ import annotations

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.host_dispatch import build_dispatcher
from openai4s.tools.mcp import ListMCPToolsTool


class SpyManager:
    """Stands in for the MCP process manager and records every launch."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def list_tools(self, connector_id, config):
        self.calls.append(("list_tools", connector_id))
        return [{"name": "search", "description": "find things"}]


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """A real dispatcher over a real store, with only the launcher replaced."""

    workspace = tmp_path / "ws"
    workspace.mkdir()
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    dispatcher = build_dispatcher(cfg, frame_id="f-mcp", workspace=workspace)
    dispatcher.store.upsert_connector(
        connector_id="example",
        name="Example",
        command=["python", "-c", "pass"],
        enabled=True,
    )
    spy = SpyManager()
    import openai4s.mcp_client as mcp_client

    monkeypatch.setattr(mcp_client, "manager", lambda: spy)
    return dispatcher, spy


def test_positional_server_string_reaches_discovery(wired):
    """The kernel SDK wire shape: dispatcher("mcp_tools", ["<server>"]).

    Before the fix this raised ``AttributeError: 'str' object has no attribute
    'get'`` inside the control tool, so the worker saw a RuntimeError instead
    of the tool list.
    """

    dispatcher, spy = wired

    result = dispatcher("mcp_tools", ["example"])

    assert result == {"tools": [{"name": "search", "description": "find things"}]}
    assert spy.calls == [("list_tools", "example")]


def test_datapro_discovery_answers_locally_through_the_rpc_path(wired):
    """The managed connector's fixed descriptor must survive the same path.

    This is the exact reported call: ``host.mcp.tools("volcengine-datapro")``
    after ``host.mcp.list()`` had shown the connector as available.
    """

    dispatcher, spy = wired
    dispatcher.store.upsert_connector(
        connector_id="volcengine-datapro",
        name="Volcengine DataPro",
        command=["python", "-c", "pass"],
        enabled=True,
    )

    result = dispatcher("mcp_tools", ["volcengine-datapro"])

    assert isinstance(result, dict) and "tools" in result, result
    names = [tool["name"] for tool in result["tools"]]
    assert names == ["dataPro_search"]
    # Discovery of the managed connector is answered locally, zero-spawn.
    assert spy.calls == []


def test_dict_spec_still_works(wired):
    """The native control-tool shape ({"server": ...}) must keep working."""

    dispatcher, spy = wired

    result = dispatcher("mcp_tools", [{"server": "example"}])

    assert result == {"tools": [{"name": "search", "description": "find things"}]}
    assert spy.calls == [("list_tools", "example")]


def test_unknown_server_is_a_soft_error_not_a_crash(wired):
    dispatcher, _spy = wired

    result = dispatcher("mcp_tools", ["missing"])

    assert result == {"error": "connector 'missing' not found"}


def test_resource_keys_accept_the_positional_string_too():
    """Audit metadata must name the server, not degrade to the wildcard.

    The dispatcher computes ``resource_keys(args[0])`` before dispatching, so
    the string shape reached this method as well; the base implementation
    silently recorded ``mcp:*`` for every discovery. Same dual-shape contract
    `LineageGetTool` already implements.
    """

    tool = ListMCPToolsTool()

    assert tool.resource_keys("example") == tool.resource_keys({"server": "example"})
    assert "example" in tool.resource_keys("example")[0]
