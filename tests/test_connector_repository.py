"""Direct contracts for persisted MCP connector configuration."""

from __future__ import annotations

import itertools
import sqlite3

import pytest

from openai4s.config import Config
from openai4s.host.mcp import MCPService
from openai4s.storage.connectors import ConnectorRepository
from openai4s.store import get_store


def _repository(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    ticks = itertools.count(1000)
    repository = ConnectorRepository(
        store._conn,
        store._lock,
        clock_ms=lambda: next(ticks),
    )
    return store, repository


def test_insert_update_round_trip_and_preserve_identity(tmp_path):
    store, repository = _repository(tmp_path)
    inserted = repository.upsert(
        connector_id="lab",
        name="Lab server",
        description="first",
        command=["python", "server.py"],
        args=None,
        env=None,
        enabled=True,
    )
    assert inserted == {
        "connector_id": "lab",
        "name": "Lab server",
        "description": "first",
        "command": ["python", "server.py"],
        "args": [],
        "env": {},
        "enabled": True,
        "created_at": 1000,
        "updated_at": 1000,
    }

    updated = repository.upsert(
        connector_id="lab",
        name="Renamed server",
        description="second",
        command="serve --stdio",
        args=("--port", 9000),
        env={"TOKEN": "test"},
        enabled=False,
    )
    assert updated == {
        "connector_id": "lab",
        "name": "Renamed server",
        "description": "second",
        "command": "serve --stdio",
        "args": ["--port", 9000],
        "env": {"TOKEN": "test"},
        "enabled": False,
        "created_at": 1000,
        "updated_at": 1001,
    }

    with sqlite3.connect(store.db_path) as independent:
        row = independent.execute(
            "SELECT command,args,env,enabled,created_at,updated_at "
            "FROM connectors WHERE connector_id='lab'"
        ).fetchone()
    assert row == (
        '"serve --stdio"',
        '["--port", 9000]',
        '{"TOKEN": "test"}',
        0,
        1000,
        1001,
    )


def test_list_orders_and_preserves_legacy_json_decoding_edges(tmp_path):
    store, repository = _repository(tmp_path)
    rows = [
        (
            "z",
            "Zulu",
            "",
            '"shell command"',
            "not-json",
            "",
            0,
            1,
            1,
        ),
        (
            "a",
            "Alpha",
            None,
            '["python"]',
            "false",
            "null",
            2,
            2,
            2,
        ),
    ]
    with store._lock:
        store._conn.executemany(
            "INSERT INTO connectors(connector_id,name,description,command,args,env,"
            "enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            rows,
        )
        store._conn.commit()

    connectors = repository.list()
    assert [connector["connector_id"] for connector in connectors] == ["a", "z"]
    assert connectors[0]["command"] == ["python"]
    assert connectors[0]["args"] is False
    assert connectors[0]["env"] is None
    assert connectors[0]["enabled"] is True
    assert connectors[1]["command"] == "shell command"
    assert connectors[1]["args"] == "not-json"
    assert connectors[1]["env"] == ""
    assert connectors[1]["enabled"] is False
    assert repository.get("A") is None
    assert repository.get("a")["name"] == "Alpha"


def test_falsy_normalization_serialization_errors_and_unknown_noops(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    clock_calls = []

    def clock_ms():
        value = 3000 + len(clock_calls)
        clock_calls.append(value)
        return value

    repository = ConnectorRepository(
        store._conn,
        store._lock,
        clock_ms=clock_ms,
    )
    connector = repository.upsert(
        connector_id="falsy",
        name="Falsy",
        command=["server"],
        args=0,
        env=False,
        enabled="yes",
    )
    assert clock_calls == [3000]
    assert connector["args"] == []
    assert connector["env"] == {}
    assert connector["enabled"] is True

    with pytest.raises(TypeError):
        repository.upsert(
            connector_id="bad",
            name="Bad",
            command={object()},
        )
    assert clock_calls == [3000, 3001]
    assert repository.get("bad") is None

    repository.set_enabled("missing", False)
    assert clock_calls == [3000, 3001, 3002]
    repository.delete("missing")
    assert clock_calls == [3000, 3001, 3002]
    with sqlite3.connect(store.db_path) as independent:
        assert independent.execute("SELECT COUNT(*) FROM connectors").fetchone() == (1,)


def test_enabled_toggle_delete_and_store_facade_feed_mcp_service(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    created = store.upsert_connector(
        connector_id="science",
        name="Science MCP",
        description="metadata API",
        command=["python", "science_server.py"],
        args=["--stdio"],
        env={"MODE": "test"},
        enabled=True,
    )
    assert isinstance(store._connectors, ConnectorRepository)
    assert created["enabled"] is True

    service = MCPService(store, manager_factory=lambda: None)
    assert service.list() == [
        {
            "id": "science",
            "name": "Science MCP",
            "description": "metadata API",
        }
    ]

    previous_updated_at = created["updated_at"]
    store.set_connector_enabled("science", False)
    disabled = store.get_connector("science")
    assert disabled["enabled"] is False
    assert disabled["updated_at"] >= previous_updated_at
    assert service.list() == []

    store.delete_connector("science")
    assert store.get_connector("science") is None
    with sqlite3.connect(store.db_path) as independent:
        assert independent.execute("SELECT COUNT(*) FROM connectors").fetchone() == (0,)


def test_patch_connector_preserves_unmentioned_env_and_retires_replaced_refs(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    store.upsert_connector(
        connector_id="science",
        name="Science MCP",
        command=["python", "server.py"],
        args=["--stdio"],
        env={"KEEP": "keep-secret", "REPLACE": "old-secret", "REMOVE": "gone"},
    )
    before = dict(store.get_connector("science")["env"])

    updated = store.patch_connector(
        "science",
        name="Science MCP v2",
        env_updates={"REPLACE": "new-secret", "NEW": "fresh"},
        remove_env=["REMOVE"],
    )

    assert updated is not None
    assert updated["name"] == "Science MCP v2"
    assert updated["command"] == ["python", "server.py"]
    assert updated["args"] == ["--stdio"]
    assert store.connector_env(updated) == {
        "KEEP": "keep-secret",
        "REPLACE": "new-secret",
        "NEW": "fresh",
    }
    assert updated["env"]["KEEP"] == before["KEEP"]
    # SecretBroker references are stable per connector/name, so replacement
    # overwrites the value behind the same ref instead of minting a new one.
    assert updated["env"]["REPLACE"] == before["REPLACE"]
    assert store.secrets.get(before["REPLACE"]) == "new-secret"
    assert store.secrets.get(before["REMOVE"]) is None


def test_patch_connector_rejects_ambiguous_env_changes(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    store.upsert_connector(connector_id="science", name="Science", command=["x"])

    with pytest.raises(ValueError, match="updated and removed"):
        store.patch_connector(
            "science", env_updates={"TOKEN": "new"}, remove_env=["TOKEN"]
        )


def test_datapro_connector_persists_no_key_or_headers_and_resolves_broker_late(
    tmp_path,
):
    from openai4s import datapro
    from openai4s.security.secret_broker import is_ref
    from openai4s.storage.connectors import public_connector

    canary = "agent-plan-key-db-canary"
    store = get_store(Config(data_dir=tmp_path).db_path)
    connector = store.upsert_connector(
        connector_id=datapro.CONNECTOR_ID,
        name="Volcengine DataPro",
        description="Professional dataset search.",
        command=datapro.managed_connector_command(),
        enabled=True,
    )
    datapro.save_agent_plan_key(store, canary)

    raw_setting = store.get_setting(datapro.AGENT_PLAN_KEY_SETTING)
    assert is_ref(raw_setting)
    assert canary not in str(connector)
    assert "X-Agent-Plan-Key" not in str(connector)
    assert "X-Hqd-Extra-Info" not in str(connector)
    assert canary not in str(public_connector(connector))

    runtime = datapro.connector_runtime_config(store, connector)
    assert "headers" not in runtime
    headers = runtime["headers_provider"]()
    assert headers == {
        "X-Agent-Plan-Key": canary,
        "X-Hqd-Extra-Info": "openai4s",
    }

    with sqlite3.connect(store.db_path) as independent:
        command, args, env = independent.execute(
            "SELECT command,args,env FROM connectors WHERE connector_id=?",
            (datapro.CONNECTOR_ID,),
        ).fetchone()
    assert canary not in "".join((command, args, env))
    assert "X-Agent-Plan-Key" not in "".join((command, args, env))


def test_agent_plan_key_mirror_reads_the_profile_not_the_settings(tmp_path):
    """The gate must inspect the profile whose credential is being replaced.

    `activate()` is what copies a profile's provider/base_url into the settings
    row, so an edited-but-not-reactivated profile disagrees with them. Gating on
    the settings therefore authorised overwriting -- and, through `_forget_key`,
    destroying -- a credential belonging to an endpoint nobody checked.
    """

    from openai4s import datapro

    store = get_store(Config(data_dir=tmp_path).db_path)
    # Settings claim a Volcengine Ark setup...
    store.set_setting("llm_provider", "ark")
    store.set_setting("llm_base_url", "https://ark.cn-beijing.volces.com/api/plan/v3")
    store.set_setting("active_model_profile", "mp-corp")
    # ...while the active profile actually points at another vendor.
    store.mutate_model_profiles(
        lambda profiles: profiles.append(
            {
                "id": "mp-corp",
                "name": "corporate gateway",
                "provider": "ark",
                "base_url": "https://internal-llm-gw.corp/v1",
                "api_key": "corp-key-must-survive",
            }
        )
    )

    profile = next(
        item for item in store.list_model_profiles() if item["id"] == "mp-corp"
    )
    eligible = str(
        profile.get("provider") or ""
    ).strip().lower() == "ark" and datapro.is_volcengine_endpoint(
        str(profile.get("base_url") or "")
    )
    assert eligible is False, "a non-Volcengine profile must never be rotated"

    # The same predicate against the settings row would have said yes.
    assert datapro.is_volcengine_endpoint(str(store.get_setting("llm_base_url") or ""))
