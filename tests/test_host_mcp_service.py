"""Direct contracts for host-side MCP connector behavior."""

from __future__ import annotations

import pytest

from openai4s.host.mcp import MCPService


class FakeStore:
    def __init__(
        self,
        connectors: list[dict],
        *,
        settings: dict[str, str] | None = None,
        secrets: dict[str, str] | None = None,
    ) -> None:
        self.connectors = connectors
        self.settings = settings or {}
        self.secret_settings = secrets or {}
        self.lookups: list[object] = []
        self.list_calls = 0
        self.indexed: list[dict] = []

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

    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def get_secret_setting(self, key):
        return self.secret_settings.get(key, "")

    def set_secret_setting(self, key, value, *, scope):
        self.secret_settings[key] = value
        return f"secret://test/{scope}/{key}"

    def index_datapro_result(self, **kwargs):
        self.indexed.append(kwargs)
        return {
            "batch_id": "dpb-test",
            "entry_count": 1,
            "source_leaf_count": 2,
            "indexed_leaf_count": 2,
            "source_digest": "same-digest",
            "indexed_digest": "same-digest",
            "complete": True,
        }


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


def test_tools_is_zero_spawn_for_a_disabled_connector():
    """`enabled` gates the spawn, not just the invocation.

    This previously listed tools for a disabled connector, which meant an agent
    calling host.mcp.tools() could make the host launch a command out of a
    connector row the user had explicitly turned off — `call` refused, but
    discovery is what starts the process. The manager must never be reached.

    The UI's "Test" button is unaffected: it goes through the separate
    /connectors/<id>/probe route, not this agent-facing path.
    """
    store = FakeStore([_connector("disabled-id", "disabled-name", enabled=False)])
    factory_calls = []

    def manager_factory():
        factory_calls.append(1)
        raise AssertionError("a disabled connector must not reach the manager")

    service = MCPService(store, manager_factory=manager_factory)
    result = service.tools("disabled-id")
    assert "disabled" in result["error"]
    assert factory_calls == [], "zero spawn"


def test_tools_still_works_for_an_enabled_connector():
    store = FakeStore([_connector("enabled-id", "enabled-name", enabled=True)])
    manager = FakeManager()
    service = MCPService(store, manager_factory=lambda: manager)
    assert service.tools("enabled-id") == {"tools": [{"name": "search"}]}
    assert manager.list_calls == [
        (
            "enabled-id",
            {
                "command": ["python", "server.py"],
                "args": ["--stdio"],
                "env": {"TOKEN": "test"},
            },
        )
    ]


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


def test_resource_and_prompt_discovery_preserve_cursor():
    connector = _connector("srv", "Server", enabled=True)
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


def test_resource_and_prompt_discovery_are_zero_spawn_when_disabled():
    """Same rule as tools(): discovery is what launches the process, so
    `enabled` has to gate it."""
    store = FakeStore([_connector("srv", "Server", enabled=False)])

    def manager_factory():
        raise AssertionError("a disabled connector must not reach the manager")

    service = MCPService(store, manager_factory=manager_factory)
    assert "disabled" in service.resources({"server": "srv"})["error"]
    assert "disabled" in service.prompts({"server": "srv"})["error"]


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


@pytest.mark.stubbed_backend
def test_datapro_host_path_is_narrow_shared_and_redacts_an_echoed_key():
    from openai4s import datapro

    canary = "agent-plan-canary-do-not-project"
    connector = _connector(
        datapro.CONNECTOR_ID,
        "Volcengine DataPro",
        command=datapro.managed_connector_command(),
    )
    store = FakeStore(
        [connector],
        settings={"llm_provider": "ark"},
        secrets={"llm_api_key": canary},
    )
    manager = FakeManager()
    manager.list_result = [
        {"name": datapro.TOOL_NAME},
        {"name": "unrelated_tool"},
    ]
    manager.call_result = {
        "is_error": False,
        "text": "echo " + canary,
        "raw": {
            "content": [{"type": "text", "text": "echo " + canary}],
            "structuredContent": {
                "code": 0,
                "value": canary,
                canary: "reflected-key",
            },
        },
    }
    service = MCPService(store, manager_factory=lambda: manager)

    # Discovery for the managed connector is answered locally, so it neither
    # dials out nor puts the credential on the wire: `mcp_tools` carries
    # `requires_approval = False`, which was decided when discovery could only
    # fork/exec a local binary. The narrow surface is unchanged, and an echoed
    # key cannot be reflected because nothing from upstream is returned.
    listed = service.tools(datapro.CONNECTOR_ID)
    assert [tool["name"] for tool in listed["tools"]] == [datapro.TOOL_NAME]
    assert canary not in str(listed)
    assert manager.list_calls == []

    result = service.call(
        {
            "server": datapro.CONNECTOR_ID,
            "tool": datapro.TOOL_NAME,
            "args": {"query": "find evidence"},
        }
    )
    assert canary not in str(result)
    assert "[REDACTED]" in str(result)
    assert result["index"]["complete"] is True
    assert store.indexed[0]["query"] == "find evidence"
    assert canary not in str(store.indexed)
    connector_id, call_config, tool, args = manager.tool_calls[0]
    assert connector_id == datapro.CONNECTOR_ID
    assert call_config["transport"] == "streamable_http"
    assert tool == datapro.TOOL_NAME
    assert args == {"query": "find evidence"}

    before = list(manager.tool_calls)
    assert service.call(
        {
            "server": datapro.CONNECTOR_ID,
            "tool": "unrelated_tool",
            "args": {"query": "x"},
        }
    ) == {"error": "volcengine-datapro only permits dataPro_search"}
    assert service.call(
        {
            "server": datapro.CONNECTOR_ID,
            "tool": datapro.TOOL_NAME,
            "args": {"query": "x", "extra": True},
        }
    ) == {"error": "dataPro_search requires exactly one string query"}
    assert manager.tool_calls == before

    narrow = {"error": "volcengine-datapro only permits dataPro_search"}
    assert service.resources({"server": datapro.CONNECTOR_ID}) == narrow
    assert (
        service.read_resource(
            {"server": datapro.CONNECTOR_ID, "uri": "science://secret"}
        )
        == narrow
    )
    assert service.prompts({"server": datapro.CONNECTOR_ID}) == narrow
    assert (
        service.get_prompt({"server": datapro.CONNECTOR_ID, "name": "secret"}) == narrow
    )
    assert manager.resource_list_calls == []
    assert manager.resource_read_calls == []
    assert manager.prompt_list_calls == []
    assert manager.prompt_get_calls == []


def test_datapro_never_reuses_a_non_ark_model_key():
    from openai4s import datapro

    connector = _connector(
        datapro.CONNECTOR_ID,
        "Volcengine DataPro",
        command=datapro.managed_connector_command(),
    )
    store = FakeStore(
        [connector],
        settings={"llm_provider": "openai"},
        secrets={
            "llm_api_key": "openai-key-must-not-leave",
            datapro.AGENT_PLAN_KEY_SETTING: "dedicated-plan-key",
        },
    )
    config = datapro.connector_runtime_config(store, connector)
    headers = config["headers_provider"]()
    assert headers["X-Agent-Plan-Key"] == "dedicated-plan-key"
    assert "openai-key-must-not-leave" not in str(headers)


def test_datapro_one_key_flow_reuses_and_updates_the_active_ark_key():
    from openai4s import datapro

    store = FakeStore(
        [],
        settings={"llm_provider": "ark"},
        secrets={"llm_api_key": "existing-ark-key"},
    )
    assert datapro.credential_state(store) == {
        "key_configured": True,
        "ark_key_reused": True,
    }
    assert datapro.resolve_agent_plan_key(store) == "existing-ark-key"

    datapro.save_agent_plan_key(store, "new-shared-key")
    assert store.secret_settings[datapro.AGENT_PLAN_KEY_SETTING] == "new-shared-key"
    assert store.secret_settings["llm_api_key"] == "new-shared-key"
    assert datapro.resolve_agent_plan_key(store) == "new-shared-key"


@pytest.mark.stubbed_backend
def test_datapro_short_invalid_key_preserves_host_4011_protocol_shape():
    from openai4s import datapro

    connector = _connector(
        datapro.CONNECTOR_ID,
        "Volcengine DataPro",
        command=datapro.managed_connector_command(),
    )
    store = FakeStore(
        [connector],
        settings={"llm_provider": "openai"},
        secrets={datapro.AGENT_PLAN_KEY_SETTING: "r"},
    )
    manager = FakeManager()
    manager.call_result = {
        "is_error": False,
        "text": "error r",
        "raw": {
            "content": [{"type": "text", "text": "error r"}],
            "structuredContent": {"code": 4011, "r": "reflected-key"},
        },
    }
    result = MCPService(store, manager_factory=lambda: manager).call(
        {
            "server": datapro.CONNECTOR_ID,
            "tool": datapro.TOOL_NAME,
            "args": {"query": "find evidence"},
        }
    )
    assert result["raw"]["structuredContent"]["code"] == 4011
    assert result["raw"]["structuredContent"]["[REDACTED]"] == "[REDACTED]eflected-key"
    projected = datapro.public_search_result(result, "r")
    assert projected["code"] == 4011
    assert projected["message"] == datapro.AUTH_FAILURE_MESSAGE


@pytest.mark.parametrize(
    ("code", "available"),
    [
        (0, True),
        (False, False),
        ("0", False),
        (0.0, False),
        (None, False),
        (4011, False),
        (-1, False),
    ],
)
def test_datapro_availability_requires_strict_structured_integer_zero(code, available):
    from openai4s import datapro

    projected = datapro.public_search_result(
        {
            "is_error": False,
            "raw": {
                "content": [],
                "structuredContent": {"code": code},
            },
        },
        "",
    )
    assert projected["available"] is available
    if code == 4011:
        assert projected["message"] == datapro.AUTH_FAILURE_MESSAGE


@pytest.mark.stubbed_backend
def test_cua_host_path_answers_discovery_locally_and_redacts_an_echoed_key():
    from openai4s import cua

    canary = "cua-key-canary-do-not-project"
    connector = _connector(
        cua.CONNECTOR_ID,
        "CUA Cloud Desktop",
        command=cua.managed_connector_command(),
    )
    store = FakeStore([connector], secrets={cua.CUA_API_KEY_SETTING: canary})
    manager = FakeManager()
    manager.list_result = [
        {"name": "cua_ping"},
        {"name": "unrelated_tool"},
    ]
    manager.call_result = {
        "is_error": False,
        "text": "echo " + canary,
        "raw": {
            "content": [{"type": "text", "text": "echo " + canary}],
            "structuredContent": {
                "ok": True,
                "value": canary,
                canary: "reflected-key",
            },
        },
    }
    service = MCPService(store, manager_factory=lambda: manager)

    # Discovery for the managed connector is answered locally: the six-tool
    # surface is fixed, and dialling for it would put the Bearer credential on
    # the wire behind the ungated `mcp_tools` capability. Zero dial, zero
    # upstream reflection.
    listed = service.tools(cua.CONNECTOR_ID)
    assert [tool["name"] for tool in listed["tools"]] == list(cua.TOOL_NAMES)
    assert canary not in str(listed)
    assert manager.list_calls == []

    result = service.call({"server": cua.CONNECTOR_ID, "tool": "cua_ping", "args": {}})
    assert canary not in str(result)
    assert "[REDACTED]" in str(result)
    assert "index" not in result, "CUA results are envelopes, never indexed"
    connector_id, call_config, tool, args = manager.tool_calls[0]
    assert connector_id == cua.CONNECTOR_ID
    assert call_config["transport"] == "streamable_http"
    assert call_config["url"] == cua.ENDPOINT
    assert call_config["timeout"] == cua.REQUEST_TIMEOUT_SECONDS
    assert tool == "cua_ping"
    assert args == {}

    before = list(manager.tool_calls)
    narrow = {"error": "cua only permits cua_* tools"}
    assert (
        service.call({"server": cua.CONNECTOR_ID, "tool": "dataPro_search", "args": {}})
        == narrow
    )
    assert service.call(
        {"server": cua.CONNECTOR_ID, "tool": "cua_delegate", "args": "objective"}
    ) == {"error": "cua tool arguments must be an object"}
    assert manager.tool_calls == before

    assert service.resources({"server": cua.CONNECTOR_ID}) == narrow
    assert (
        service.read_resource({"server": cua.CONNECTOR_ID, "uri": "science://secret"})
        == narrow
    )
    assert service.prompts({"server": cua.CONNECTOR_ID}) == narrow
    assert service.get_prompt({"server": cua.CONNECTOR_ID, "name": "secret"}) == narrow
    assert manager.resource_list_calls == []
    assert manager.resource_read_calls == []
    assert manager.prompt_list_calls == []
    assert manager.prompt_get_calls == []


def test_cua_discovery_and_call_are_zero_spawn_when_disabled():
    from openai4s import cua

    store = FakeStore(
        [
            _connector(
                cua.CONNECTOR_ID,
                "CUA Cloud Desktop",
                command=cua.managed_connector_command(),
                enabled=False,
            )
        ]
    )

    def manager_factory():
        raise AssertionError("a disabled managed connector must not be dialled")

    service = MCPService(store, manager_factory=manager_factory)
    assert "disabled" in service.tools(cua.CONNECTOR_ID)["error"]
    assert (
        "disabled"
        in service.call({"server": cua.CONNECTOR_ID, "tool": "cua_ping", "args": {}})[
            "error"
        ]
    )
    assert "disabled" in service.resources({"server": cua.CONNECTOR_ID})["error"]
    assert "disabled" in service.prompts({"server": cua.CONNECTOR_ID})["error"]
