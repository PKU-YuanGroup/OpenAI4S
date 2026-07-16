"""Direct contracts for host-side MCP connector behavior."""

from __future__ import annotations

import pytest

from openai4s.host.mcp import MCPService


class FakeStore:
    def __init__(self, connectors: list[dict]) -> None:
        self.connectors = connectors
        self.lookups: list[object] = []
        self.list_calls = 0

    def get_connector(self, connector_id):
        self.lookups.append(connector_id)
        return next(
            (
                connector
                for connector in self.connectors
                if connector.get("connector_id") == connector_id
            ),
            None,
        )

    def list_connectors(self):
        self.list_calls += 1
        return self.connectors

    def connector_env(self, connector):
        """Mirror the real Store: the row's env may hold broker references, so
        the launch path resolves it rather than passing the row through. These
        fixtures use plaintext env, which resolves to itself."""
        env = connector.get("env")
        return dict(env) if isinstance(env, dict) else {}


class FakeManager:
    def __init__(self) -> None:
        self.list_result = [{"name": "search"}]
        self.call_result = {"text": "done"}
        self.resources_result = {"resources": [{"uri": "science://dataset"}]}
        self.resource_result = {
            "contents": [{"uri": "science://dataset", "text": "measurements"}]
        }
        self.prompts_result = {"prompts": [{"name": "analyze"}]}
        self.prompt_result = {
            "messages": [{"role": "user", "content": {"type": "text"}}]
        }
        self.list_calls: list[tuple] = []
        self.tool_calls: list[tuple] = []
        self.resource_list_calls: list[tuple] = []
        self.resource_read_calls: list[tuple] = []
        self.prompt_list_calls: list[tuple] = []
        self.prompt_get_calls: list[tuple] = []
        self.list_error: Exception | None = None
        self.call_error: Exception | None = None
        self.resource_error: Exception | None = None
        self.prompt_error: Exception | None = None

    def list_tools(self, connector_id, config):
        self.list_calls.append((connector_id, config))
        if self.list_error is not None:
            raise self.list_error
        return self.list_result

    def call_tool(self, connector_id, config, tool, args):
        self.tool_calls.append((connector_id, config, tool, args))
        if self.call_error is not None:
            raise self.call_error
        return self.call_result

    def list_resources(self, connector_id, config, cursor):
        self.resource_list_calls.append((connector_id, config, cursor))
        if self.resource_error is not None:
            raise self.resource_error
        return self.resources_result

    def read_resource(self, connector_id, config, uri):
        self.resource_read_calls.append((connector_id, config, uri))
        if self.resource_error is not None:
            raise self.resource_error
        return self.resource_result

    def list_prompts(self, connector_id, config, cursor):
        self.prompt_list_calls.append((connector_id, config, cursor))
        if self.prompt_error is not None:
            raise self.prompt_error
        return self.prompts_result

    def get_prompt(self, connector_id, config, name, arguments):
        self.prompt_get_calls.append((connector_id, config, name, arguments))
        if self.prompt_error is not None:
            raise self.prompt_error
        return self.prompt_result


def _connector(
    connector_id: str,
    name: str,
    *,
    enabled: bool = True,
    **extra,
) -> dict:
    return {
        "connector_id": connector_id,
        "name": name,
        "description": extra.pop("description", None),
        "command": extra.pop("command", ["python", "server.py"]),
        "args": extra.pop("args", ["--stdio"]),
        "env": extra.pop("env", {"TOKEN": "test"}),
        "enabled": enabled,
        **extra,
    }


def test_connector_prefers_id_then_falls_back_to_exact_name():
    by_id = _connector("target", "id-wins")
    by_name = _connector("other", "target")
    store = FakeStore([by_name, by_id])
    service = MCPService(store, manager_factory=lambda: FakeManager())

    assert service.connector("target") is by_id
    assert store.list_calls == 0
    assert service.connector("id-wins") is by_id
    assert store.list_calls == 1
    assert service.connector("missing") is None


def test_list_projects_enabled_connectors_only_and_preserves_hard_key_errors():
    store = FakeStore(
        [
            _connector("enabled", "Enabled", description="ready"),
            _connector("disabled", "Disabled", enabled=False),
        ]
    )
    service = MCPService(store, manager_factory=lambda: FakeManager())

    assert service.list() == [
        {"id": "enabled", "name": "Enabled", "description": "ready"}
    ]

    store.connectors = [{"name": "broken", "enabled": True}]
    with pytest.raises(KeyError, match="connector_id"):
        service.list()


def test_tools_calls_disabled_connector_with_strict_config_and_dynamic_factory():
    connector = _connector(
        "disabled-id",
        "disabled-name",
        enabled=False,
        ignored="not passed",
    )
    store = FakeStore([connector])
    managers = [FakeManager(), FakeManager()]
    factory_calls = []

    def manager_factory():
        factory_calls.append(len(factory_calls))
        return managers[len(factory_calls) - 1]

    service = MCPService(store, manager_factory=manager_factory)
    assert factory_calls == []

    assert service.tools("disabled-id") == {"tools": [{"name": "search"}]}
    assert service.tools("disabled-id") == {"tools": [{"name": "search"}]}
    expected_config = {
        "command": ["python", "server.py"],
        "args": ["--stdio"],
        "env": {"TOKEN": "test"},
    }
    assert managers[0].list_calls == [("disabled-id", expected_config)]
    assert managers[1].list_calls == [("disabled-id", expected_config)]
    assert factory_calls == [0, 1]


def test_tools_preserves_not_found_soft_failure_exception_text_and_keyerror():
    store = FakeStore([])
    manager = FakeManager()
    service = MCPService(store, manager_factory=lambda: manager)

    assert service.tools("missing") == {"error": "connector 'missing' not found"}

    store.connectors = [_connector("srv", "Server")]
    manager.list_error = RuntimeError("transport down")
    assert service.tools("srv") == {"error": "mcp tools failed: transport down"}

    store.connectors = [
        {
            "connector_id": "broken",
            "name": "Broken",
            "enabled": True,
        }
    ]
    with pytest.raises(KeyError, match="command"):
        service.tools("broken")


def test_call_rejects_disabled_and_preserves_lookup_and_argument_contracts():
    disabled = _connector("disabled-id", "Disabled", enabled=False)
    enabled = _connector("enabled-id", "Enabled")
    store = FakeStore([disabled, enabled])
    manager = FakeManager()
    factory_calls = []

    def manager_factory():
        factory_calls.append(True)
        return manager

    service = MCPService(store, manager_factory=manager_factory)

    assert service.call({"server": "missing", "tool": "search"}) == {
        "error": "connector 'missing' not found"
    }
    assert service.call({"server": "Disabled", "tool": "search"}) == {
        "error": "connector 'Disabled' is disabled"
    }
    assert factory_calls == []

    assert service.call({"server": "Enabled", "tool": "search", "args": None}) == {
        "text": "done"
    }
    assert manager.tool_calls == [
        (
            "enabled-id",
            {
                "command": ["python", "server.py"],
                "args": ["--stdio"],
                "env": {"TOKEN": "test"},
            },
            "search",
            {},
        )
    ]


def test_call_preserves_exception_text_and_command_keyerror_boundary():
    store = FakeStore([_connector("srv", "Server")])
    manager = FakeManager()
    manager.call_error = ValueError("bad payload")
    service = MCPService(store, manager_factory=lambda: manager)

    assert service.call({"server": "srv", "tool": "lookup", "args": {"q": "x"}}) == {
        "error": "mcp_call(srv.lookup) failed: bad payload"
    }

    store.connectors = [
        {
            "connector_id": "broken",
            "name": "Broken",
            "enabled": True,
        }
    ]
    with pytest.raises(KeyError, match="command"):
        service.call({"server": "broken", "tool": "lookup"})


def test_resource_and_prompt_discovery_preserve_cursor_and_disabled_introspection():
    connector = _connector("srv", "Server", enabled=False)
    store = FakeStore([connector])
    manager = FakeManager()
    service = MCPService(store, manager_factory=lambda: manager)
    config = {
        "command": ["python", "server.py"],
        "args": ["--stdio"],
        "env": {"TOKEN": "test"},
    }

    assert service.resources({"server": "Server", "cursor": "resources-2"}) == (
        manager.resources_result
    )
    assert manager.resource_list_calls == [("srv", config, "resources-2")]
    assert service.prompts({"server": "srv", "cursor": "prompts-2"}) == (
        manager.prompts_result
    )
    assert manager.prompt_list_calls == [("srv", config, "prompts-2")]


def test_resource_read_and_prompt_get_require_enabled_connector_and_route_payloads():
    disabled = _connector("off", "Off", enabled=False)
    enabled = _connector("srv", "Server")
    store = FakeStore([disabled, enabled])
    manager = FakeManager()
    service = MCPService(store, manager_factory=lambda: manager)

    assert service.read_resource({"server": "missing", "uri": "science://dataset"}) == {
        "error": "connector 'missing' not found"
    }
    assert service.read_resource({"server": "Off", "uri": "science://dataset"}) == {
        "error": "connector 'Off' is disabled"
    }
    assert service.get_prompt({"server": "Off", "name": "analyze"}) == {
        "error": "connector 'Off' is disabled"
    }

    assert (
        service.read_resource({"server": "Server", "uri": "science://dataset"})
        == manager.resource_result
    )
    assert (
        service.get_prompt(
            {
                "server": "srv",
                "name": "analyze",
                "arguments": {"dataset": "science://dataset"},
            }
        )
        == manager.prompt_result
    )
    assert manager.resource_read_calls[0][2] == "science://dataset"
    assert manager.prompt_get_calls[0][2:] == (
        "analyze",
        {"dataset": "science://dataset"},
    )


def test_resource_and_prompt_failures_use_soft_error_contract():
    store = FakeStore([_connector("srv", "Server")])
    manager = FakeManager()
    service = MCPService(store, manager_factory=lambda: manager)

    manager.resource_error = RuntimeError("resource transport down")
    assert service.resources({"server": "srv"}) == {
        "error": "mcp resources failed: resource transport down"
    }
    assert service.read_resource({"server": "srv", "uri": "science://dataset"}) == {
        "error": (
            "mcp resource read(srv:science://dataset) failed: "
            "resource transport down"
        )
    }

    manager.prompt_error = RuntimeError("prompt transport down")
    assert service.prompts({"server": "srv"}) == {
        "error": "mcp prompts failed: prompt transport down"
    }
    assert service.get_prompt({"server": "srv", "name": "analyze"}) == {
        "error": "mcp prompt get(srv.analyze) failed: prompt transport down"
    }
