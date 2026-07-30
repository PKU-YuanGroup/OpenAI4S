"""Offline MCP protocol and child-environment contracts."""

from __future__ import annotations

import os
import sys
import threading
import time

import pytest

from openai4s import mcp_client
from openai4s.mcp_client import (
    MCPConnection,
    MCPError,
    MCPManager,
    _connector_environment,
    example_server_config,
)
from openai4s.mcp_servers.example_server import RESOURCE_URI


def test_connector_environment_is_allowlisted_and_explicit_env_is_the_secret_boundary():
    source = {
        "PATH": "/safe/bin",
        "HOME": "/safe/home",
        "LANG": "en_US.UTF-8",
        "OPENAI4S_LLM_API_KEY": "daemon-provider-secret",
        "AWS_SECRET_ACCESS_KEY": "daemon-cloud-secret",
        "HTTP_PROXY": "https://user:password@proxy.invalid",
        "PYTHONPATH": "/untrusted/imports",
        "NODE_OPTIONS": "--require=/untrusted/bootstrap.js",
    }

    env = _connector_environment(
        {"SCIENCE_MCP_TOKEN": "connector-secret", "MODE": 7},
        source=source,
    )

    assert env["PATH"] == "/safe/bin"
    assert env["HOME"] == "/safe/home"
    assert env["LANG"] == "en_US.UTF-8"
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["SCIENCE_MCP_TOKEN"] == "connector-secret"
    assert env["MODE"] == "7"
    assert set(env).isdisjoint(
        {
            "OPENAI4S_LLM_API_KEY",
            "AWS_SECRET_ACCESS_KEY",
            "HTTP_PROXY",
            "PYTHONPATH",
            "NODE_OPTIONS",
        }
    )


def test_connector_environment_has_a_path_fallback_and_rejects_invalid_entries():
    assert _connector_environment(source={})["PATH"] == os.defpath

    with pytest.raises(MCPError, match="must be an object"):
        _connector_environment([("TOKEN", "value")], source={})
    with pytest.raises(MCPError, match="invalid connector env name"):
        _connector_environment({"BAD=NAME": "value"}, source={})
    with pytest.raises(MCPError, match="cannot be null"):
        _connector_environment({"TOKEN": None}, source={})
    with pytest.raises(MCPError, match="contains NUL"):
        _connector_environment({"TOKEN": "bad\x00value"}, source={})


def test_manager_connect_never_passes_ambient_secrets_to_popen(monkeypatch):
    captured = {}

    class CapturingConnection:
        def __init__(self, command, env=None, cwd=None):
            captured.update(command=command, env=env, cwd=cwd)

    monkeypatch.setenv("OPENAI4S_LLM_API_KEY", "ambient-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "ambient-cloud-secret")
    monkeypatch.setattr(mcp_client, "MCPConnection", CapturingConnection)

    manager = MCPManager()
    connection = manager._connect(
        {
            "command": ["science-mcp"],
            "args": ["--stdio"],
            "env": {"SCIENCE_MCP_TOKEN": "declared-secret"},
            "cwd": "/connector/workspace",
        }
    )

    assert isinstance(connection, CapturingConnection)
    assert captured["command"] == ["science-mcp", "--stdio"]
    assert captured["cwd"] == "/connector/workspace"
    assert captured["env"]["SCIENCE_MCP_TOKEN"] == "declared-secret"
    assert "OPENAI4S_LLM_API_KEY" not in captured["env"]
    assert "AWS_SECRET_ACCESS_KEY" not in captured["env"]


def test_connection_uses_standard_resource_and_prompt_method_shapes():
    connection = object.__new__(MCPConnection)
    calls: list[tuple[str, dict | None]] = []

    def request(method, params=None):
        calls.append((method, params))
        return {
            "resources/list": {"resources": [], "nextCursor": "r-next"},
            "resources/read": {"contents": []},
            "prompts/list": {"prompts": [], "nextCursor": "p-next"},
            "prompts/get": {"messages": []},
        }[method]

    connection._request = request

    assert connection.list_resources("r-1")["nextCursor"] == "r-next"
    assert connection.read_resource("science://dataset") == {"contents": []}
    assert connection.list_prompts("p-1")["nextCursor"] == "p-next"
    assert connection.get_prompt("analyze", {"kind": "fast"}) == {"messages": []}
    assert calls == [
        ("resources/list", {"cursor": "r-1"}),
        ("resources/read", {"uri": "science://dataset"}),
        ("prompts/list", {"cursor": "p-1"}),
        (
            "prompts/get",
            {"name": "analyze", "arguments": {"kind": "fast"}},
        ),
    ]


def test_bundled_server_supports_resources_and_prompts_end_to_end():
    manager = MCPManager()
    config = example_server_config()
    try:
        resources = manager.list_resources("example", config)
        assert resources["resources"][0]["uri"] == RESOURCE_URI

        content = manager.read_resource("example", config, RESOURCE_URI)
        assert content["contents"][0]["uri"] == RESOURCE_URI
        assert "third-party packages" in content["contents"][0]["text"]

        prompts = manager.list_prompts("example", config)
        assert prompts["prompts"][0]["name"] == "summarize"

        rendered = manager.get_prompt(
            "example",
            config,
            "summarize",
            {"text": "alpha beta gamma"},
        )
        message = rendered["messages"][0]
        assert message["role"] == "user"
        assert "alpha beta gamma" in message["content"]["text"]
    finally:
        manager.shutdown()


def _silent_server_config(tmp_path, *, behaviour: str) -> dict:
    """A connector that answers `initialize` then misbehaves in one exact way."""
    script = tmp_path / f"srv_{behaviour}.py"
    script.write_text(
        "import json, sys, time\n"
        "for line in sys.stdin:\n"
        "    line = line.strip()\n"
        "    if not line:\n"
        "        continue\n"
        "    msg = json.loads(line)\n"
        "    mid = msg.get('id')\n"
        "    if mid is None:\n"
        "        continue\n"
        "    if msg.get('method') == 'initialize':\n"
        "        sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':mid,"
        "'result':{}}) + '\\n')\n"
        "        sys.stdout.flush()\n"
        "        continue\n"
        f"    behaviour = {behaviour!r}\n"
        "    if behaviour == 'silent':\n"
        "        time.sleep(120)\n"
        "    elif behaviour == 'late':\n"
        "        time.sleep(1.5)\n"
        "        sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':mid,"
        "'result':{'tools':[{'name':'STALE'}]}}) + '\\n')\n"
        "        sys.stdout.flush()\n",
        encoding="utf-8",
    )
    return {"command": [sys.executable, str(script)]}


def test_a_silent_connector_times_out_instead_of_holding_its_caller_forever(tmp_path):
    """`_read_reply` looped on `readline()` with no deadline.

    A connector that accepted a request and never answered held its caller for
    the life of the process. Worse, `MCPManager.get` takes its own lock across
    connect, so one silent connector wedged every other connector too -- and a
    hung call from a kernel cell survived cell recovery, leaving MCP dead
    process-wide.
    """
    connection = MCPConnection(
        _silent_server_config(tmp_path, behaviour="silent")["command"], timeout=1.0
    )
    try:
        started = time.monotonic()
        with pytest.raises(MCPError) as raised:
            connection.list_tools()
        elapsed = time.monotonic() - started
        assert elapsed < 20, f"took {elapsed:.1f}s -- the deadline did not apply"
        assert "exceeded" in str(raised.value)
    finally:
        connection.close()


def test_a_late_reply_is_discarded_rather_than_read_as_the_next_answer(tmp_path):
    """The reason a bare `readline` timeout would have been a regression.

    After a request gives up, the server may still answer it. With one shared
    stream and no demux, that stale reply is sitting in the pipe for the NEXT
    request to read as its own -- a caller asking B and being handed A's answer,
    silently and with the right JSON shape.
    """
    connection = MCPConnection(
        _silent_server_config(tmp_path, behaviour="late")["command"], timeout=0.3
    )
    try:
        with pytest.raises(MCPError):
            connection.list_tools()  # abandons id 2; the server answers it later
        time.sleep(2.0)  # the stale reply lands while nobody is waiting

        # The next request must not be handed the abandoned one's result.
        with pytest.raises(MCPError) as raised:
            connection.list_tools()
        assert "STALE" not in str(raised.value)
    finally:
        connection.close()


def test_a_closed_connection_wakes_its_waiters(tmp_path):
    """Closing must not leave a caller blocked on a reply that cannot come."""
    connection = MCPConnection(
        _silent_server_config(tmp_path, behaviour="silent")["command"], timeout=30.0
    )
    results: list[str] = []

    def call():
        try:
            connection.list_tools()
        except MCPError as error:
            results.append(str(error))

    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    time.sleep(0.3)
    connection.close()
    worker.join(timeout=10)
    assert not worker.is_alive(), "close() left a caller blocked"
    assert results and "closed" in results[0].lower()


def test_editing_or_disabling_a_connector_drops_its_cached_process(tmp_path):
    """A cached connection outlived the configuration that created it.

    Only DELETE called `disconnect`. Editing a connector's command or env wrote
    the new row and left the old child running and answering, so the connector
    the user just reconfigured kept serving from the previous configuration --
    including the previous credentials. Disabling one wrote `enabled=0` and
    likewise left the process alive, so "off" meant "hidden", not "stopped".
    """
    from openai4s.config import Config, LLMConfig
    from openai4s.server import gateway as gateway_mod

    class _Hub:
        def emitter(self, root_frame_id):
            return lambda event: None

        def broadcast(self, root_frame_id, event):
            return None

    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    try:
        handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
        handler = object.__new__(handler_cls)
        replies: list = []
        handler._query = lambda: {}
        handler._json = lambda obj, code=200: replies.append((code, obj))

        dropped: list[str] = []
        real = mcp_client.manager()
        original = real.disconnect
        real.disconnect = lambda cid: dropped.append(cid)  # type: ignore[assignment]
        try:
            handler._body = lambda: {
                "connector_id": "c-1",
                "name": "c1",
                "command": ["true"],
            }
            handler._api("POST", "/connectors")
            assert dropped == ["c-1"], "an edit must drop the stale process first"

            dropped.clear()
            handler._body = lambda: {"enabled": False}
            handler._api("PUT", "/connectors/c-1/enabled")
            assert dropped == ["c-1"], "disabling must stop the process"

            dropped.clear()
            handler._body = lambda: {"enabled": True}
            handler._api("PUT", "/connectors/c-1/enabled")
            assert dropped == [], "enabling need not drop anything"
        finally:
            real.disconnect = original  # type: ignore[assignment]
    finally:
        runner.close()
