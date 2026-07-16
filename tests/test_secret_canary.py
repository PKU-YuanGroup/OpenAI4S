"""Canary regressions: a credential must never ride an API response.

Each test plants a distinctive secret value, then asserts that the exact bytes
never appear in what the browser receives. The assertions are on the *value*,
not on a field name, deliberately: a denylist of known key names silently fails
open the moment someone adds a field, whereas a canary fails the moment the
secret escapes by any route at all.

Background: `GET /connectors` built its payload with `{**c}`, spreading every
column of the row — including `env`, which holds the credentials an MCP server
is launched with — straight to the browser. `POST /connectors` echoed the same
row back because upsert re-reads it. The model-profile path already had the
right shape (`public_profile`), so this pins the connector side to it.
"""
import json

import pytest

from openai4s.config import Config
from openai4s.storage.connectors import public_connector
from openai4s.store import get_store

_CANARY = "sk-canary-9f3a1c7e-MUST-NOT-REACH-THE-BROWSER"


@pytest.fixture
def store(tmp_path):
    return get_store(Config(data_dir=tmp_path).db_path)


def _connector(store):
    return store.upsert_connector(
        connector_id="lab",
        name="Lab MCP",
        description="internal",
        command=["python", "server.py"],
        args=["--stdio"],
        env={"LAB_API_TOKEN": _CANARY, "MODE": "test"},
        enabled=True,
    )


# --------------------------------------------------------------------------
# the projection itself
# --------------------------------------------------------------------------


def test_public_connector_drops_env_values(store):
    pub = public_connector(_connector(store))
    assert _CANARY not in json.dumps(pub)
    assert "env" not in pub


def test_public_connector_keeps_env_names_as_metadata(store):
    """Which variables are configured is metadata the UI needs; the values are
    the secret. `configured`-style booleans, never contents."""
    pub = public_connector(_connector(store))
    assert pub["env_keys"] == ["LAB_API_TOKEN", "MODE"]
    assert pub["has_env"] is True


def test_public_connector_preserves_the_fields_the_ui_renders(store):
    pub = public_connector(_connector(store))
    assert pub["connector_id"] == "lab"
    assert pub["name"] == "Lab MCP"
    assert pub["description"] == "internal"
    assert pub["command"] == ["python", "server.py"]
    assert pub["args"] == ["--stdio"]
    assert pub["enabled"] is True


def test_public_connector_handles_absent_env(store):
    store.upsert_connector(
        connector_id="bare", name="Bare", command=["x"], env=None, enabled=True
    )
    pub = public_connector(store.get_connector("bare"))
    assert pub["env_keys"] == []
    assert pub["has_env"] is False


# --------------------------------------------------------------------------
# the process-spawning callers must still see the real thing
# --------------------------------------------------------------------------


def test_the_repository_still_yields_env_to_launchers(store):
    """The projection is an API boundary, not a storage change. host/mcp.py and
    the probe/call routes launch the MCP server and genuinely need the env — if
    this regresses, connectors stop working entirely."""
    _connector(store)
    raw = store.get_connector("lab")
    assert raw["env"] == {"LAB_API_TOKEN": _CANARY, "MODE": "test"}


def test_mcp_service_list_projection_excludes_env(store):
    from openai4s.host.mcp import MCPService

    _connector(store)
    listed = MCPService(store, manager_factory=lambda: None).list()
    assert _CANARY not in json.dumps(listed)


# --------------------------------------------------------------------------
# the payload builders the routes actually call
# --------------------------------------------------------------------------


def test_connectors_payload_never_contains_the_canary(store):
    """Mirrors _connectors_payload's construction. The old `{**c}` spread put
    the canary in this string."""
    _connector(store)
    payload = [
        {
            **public_connector(c),
            "command_display": " ".join(c["command"])
            if isinstance(c.get("command"), list)
            else str(c.get("command")),
        }
        for c in store.list_connectors()
    ]
    assert _CANARY not in json.dumps(payload)
    assert payload[0]["command_display"] == "python server.py"


def test_upsert_response_projection_never_contains_the_canary(store):
    """POST /connectors echoed upsert's return value, which re-reads the row."""
    assert _CANARY not in json.dumps(public_connector(_connector(store)))


# --------------------------------------------------------------------------
# model profiles: the pattern this fix was modelled on
# --------------------------------------------------------------------------


def test_public_profile_drops_the_api_key():
    from openai4s.server.model_profiles import ModelProfileService

    pub = ModelProfileService.public_profile(
        {
            "id": "p1",
            "name": "prod",
            "provider": "deepseek",
            "base_url": "https://x",
            "model": "m",
            "api_key": _CANARY,
        }
    )
    assert _CANARY not in json.dumps(pub)
    assert pub["has_api_key"] is True
