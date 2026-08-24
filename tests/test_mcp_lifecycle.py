"""MCP connector lifecycle: nothing outlives a fault except the connector id.

Every test drives a real subprocess written to ``tmp_path``, so the claims are
observable facts about the operating system -- a pid that changed, a process
that is gone, a thread that exited -- rather than assertions that some function
was called. Each stub appends its own pid to a file, which is the only way to
tell "the manager reconnected" apart from "the manager handed back the corpse".
"""

from __future__ import annotations

import os
import sys
import threading
import time

import pytest

from openai4s import mcp_client
from openai4s.mcp_client import MCPConnection, MCPError, MCPManager, MCPTimeout

# Answer `initialize`, then hand every later request to the injected body. One
# template rather than a stub per test, so each server differs in exactly the
# one way its test names.
_SERVER = """\
import json, os, signal, subprocess, sys, time

open({pidfile!r}, "a").write(str(os.getpid()) + chr(10))
{preamble}
served = 0
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    mid = msg.get("id")
    if mid is None:
        continue
    if msg.get("method") == "initialize":
{handshake}
    served += 1
{body}
# Closing stdin must not be what ends the connector. Without this every
# teardown assertion below would pass on a child that simply ran out of input,
# proving nothing about the signal that was supposed to reach it.
time.sleep(300)
"""

# Handshake refused: `initialize` falls straight through to the misbehaviour,
# which is how a connector fails before any MCPConnection exists to clean up.
_NO_HANDSHAKE = "        pass"
_HANDSHAKE = (
    '        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid,'
    ' "result": {}}) + chr(10))\n'
    "        sys.stdout.flush()\n"
    "        continue"
)

_HANG = "    time.sleep(300)\n"


def _answer(indent: str = "    ") -> str:
    """Stub source that answers the pending request with one tool."""
    reply = '{"jsonrpc": "2.0", "id": mid, "result": {"tools": [{"name": "ok"}]}}'
    return (
        f"{indent}sys.stdout.write(json.dumps({reply}) + chr(10))\n"
        f"{indent}sys.stdout.flush()\n"
    )


def _server(
    tmp_path,
    name: str,
    *,
    body: str,
    preamble: str = "",
    handshake: bool = True,
) -> dict:
    pidfile = tmp_path / f"{name}.pids"
    pidfile.write_text("", encoding="utf-8")
    script = tmp_path / f"srv_{name}.py"
    script.write_text(
        _SERVER.format(
            pidfile=str(pidfile),
            preamble=preamble,
            handshake=_HANDSHAKE if handshake else _NO_HANDSHAKE,
            body=body,
        ),
        encoding="utf-8",
    )
    return {"command": [sys.executable, str(script)], "pidfile": pidfile}


def _pids(config: dict) -> list[int]:
    return [int(p) for p in config["pidfile"].read_text(encoding="utf-8").split()]


def _wait_gone(pid: int, timeout: float = 20.0) -> bool:
    """True once `pid` no longer exists.

    The client reaps the children it kills, so a stopped connector really
    disappears instead of lingering as a zombie this check would still see.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return True
        time.sleep(0.05)
    return False


def _reader_threads() -> set[threading.Thread]:
    return {
        t
        for t in threading.enumerate()
        if t.name in ("mcp-reader", "mcp-stderr") and t.is_alive()
    }


# -- a failed handshake must not leave anything behind ------------------------
@pytest.mark.parametrize(
    "name, body",
    [
        # Accepts `initialize` and never answers it.
        ("hang", _HANG),
        # Answers `initialize` with a JSON-RPC error, then stays up.
        (
            "reject",
            '    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid,'
            ' "error": {"message": "no"}}) + chr(10))\n'
            "    sys.stdout.flush()\n" + _HANG,
        ),
    ],
)
def test_a_failed_handshake_leaves_no_process_and_no_reader_threads(
    tmp_path, name, body
):
    """The constructor raised and the child kept running -- forever.

    `__init__` ran `_init()` last, so a handshake that hung or was refused threw
    out of the constructor: no MCPConnection was ever bound, and the subprocess
    plus its two daemon reader threads (one parked in `stream.read(1)`) were
    unreachable from `_conns`, `_probes`, `disconnect` and `shutdown` alike.
    That is one leaked process and two leaked threads per "Test" click on a
    misbehaving connector, for the life of the daemon.
    """
    config = _server(tmp_path, name, body=body, handshake=False)
    before = _reader_threads()

    with pytest.raises(MCPError):
        MCPConnection(config["command"], timeout=2.0)

    pid = _pids(config)[0]
    assert _wait_gone(pid), f"connector {pid} outlived its failed handshake"

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and _reader_threads() - before:
        time.sleep(0.05)
    assert not _reader_threads() - before, "reader threads outlived the connection"


def test_a_failed_handshake_carries_the_connector_stderr_out_with_the_error(tmp_path):
    """Closing the failed connection is what makes the diagnostic urgent.

    `probe` used to read the stderr tail off the connection object after the
    failure. There is no object left now, so the handshake has to carry the tail
    out in the exception or the user gets a bare timeout with no cause at all.
    """
    config = _server(
        tmp_path,
        "noisy_handshake",
        preamble=(
            'sys.stderr.write("libfoo.so: cannot open shared object file" + chr(10))\n'
            "sys.stderr.flush()\n"
        ),
        body=_HANG,
        handshake=False,
    )
    with pytest.raises(MCPError) as raised:
        MCPConnection(config["command"], timeout=2.0)
    assert "cannot open shared object file" in str(raised.value)
    assert _wait_gone(_pids(config)[0])


# -- a fault must evict that exact instance; the next call reconnects ---------
def test_a_server_that_closes_stdout_is_replaced_on_the_next_call(tmp_path):
    """A live process with a dead channel poisoned the connector id.

    `MCPManager.get` asked only `alive()`, and a server that closes stdout after
    one reply is still very much alive. The cache kept handing back the same
    connection and `_request` kept raising the failure the reader had stored --
    permanently, because no code path anywhere closed or replaced it.
    """
    config = _server(
        tmp_path,
        "eof",
        body=(
            "    if served == 1:\n" + _answer("        ") + "        continue\n"
            "    os.close(1)\n" + _HANG
        ),
    )
    manager = MCPManager()
    try:
        assert manager.list_tools("c", config) == [{"name": "ok"}]
        first = _pids(config)[0]

        # The channel dies during or just before the second call, so one raised
        # MCPError is legitimate. Failing forever is not.
        served = None
        for _ in range(3):
            try:
                served = manager.list_tools("c", config)
                break
            except MCPError:
                continue
        assert served == [{"name": "ok"}], "the connector id stayed poisoned"

        assert _pids(config)[-1] != first, "the manager reused the dead connection"
        assert _wait_gone(first), f"faulted connector {first} was never stopped"
    finally:
        assert manager.shutdown() == []


def test_a_timed_out_connector_is_dropped_so_the_next_call_gets_a_new_pid(
    tmp_path, monkeypatch
):
    """A missed deadline is a fault of the connection, not just of the request.

    The timeout woke the caller and left everything else exactly as it was: the
    same process stayed cached, so every later call queued behind a server that
    had already proved it does not answer, each paying the full deadline again.
    """
    # The manager builds its own connections, so the deadline has to come from
    # the module default rather than a constructor argument.
    monkeypatch.setattr(mcp_client, "DEFAULT_TIMEOUT_S", 3.0)
    marker = tmp_path / "seen-one-process"
    config = _server(
        tmp_path,
        "silent",
        preamble=(
            f"restarted = os.path.exists({str(marker)!r})\n"
            f"open({str(marker)!r}, 'w').close()\n"
        ),
        body=(
            "    if restarted:\n" + _answer("        ") + "        continue\n" + _HANG
        ),
    )
    manager = MCPManager()
    try:
        with pytest.raises(MCPTimeout):
            manager.list_tools("c", config)
        first = _pids(config)[0]
        assert _wait_gone(first), "the connector that ignored its deadline was kept"

        assert manager.list_tools("c", config) == [{"name": "ok"}]
        assert _pids(config)[-1] != first
    finally:
        assert manager.shutdown() == []


def test_a_tool_level_error_does_not_restart_the_server(tmp_path):
    """The other half of the contract, and why this is not `except MCPError`.

    A JSON-RPC error is the connector working: an unknown tool name or bad
    arguments is an answer. Restarting the server on one would throw away the
    session the agent is in the middle of because the agent mistyped.
    """
    config = _server(
        tmp_path,
        "picky",
        body=(
            '    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid,'
            ' "error": {"message": "unknown tool"}}) + chr(10))\n'
            "    sys.stdout.flush()\n"
        ),
    )
    manager = MCPManager()
    try:
        for _ in range(3):
            with pytest.raises(MCPError, match="unknown tool"):
                manager.call_tool("c", config, "nope")
        assert len(_pids(config)) == 1, "an application error respawned the server"
    finally:
        assert manager.shutdown() == []


def test_eviction_spares_a_newer_connection_for_the_same_connector_id(tmp_path):
    """Why eviction is a compare-and-swap and not `_conns.pop(connector_id)`.

    Two callers can fault on one connector at once, or one can fault after
    another has already reconnected. Popping by id would close the healthy
    newcomer the second caller is holding, and the id would ping-pong through a
    fresh process per call.
    """
    config = _server(tmp_path, "cas", body=_answer())
    manager = MCPManager()
    try:
        stale = manager.get("c", config)
        assert manager._evict("c", stale) is True

        fresh = manager.get("c", config)
        assert fresh is not stale
        assert manager._evict("c", stale) is False, "a stale handle evicted a live one"
        assert manager.get("c", config) is fresh
        assert fresh.alive()
    finally:
        assert manager.shutdown() == []


@pytest.mark.stubbed_backend
def test_datapro_sessions_are_partitioned_by_live_store_generation(
    tmp_path, monkeypatch
):
    """Two Stores with one managed connector id must never share credentials.

    ``MCPManager`` is process-wide while embedders can host multiple Stores at
    once. Caching only by connector id made the first Store's header-provider
    closure serve every later Store. This stub executes the real runtime config
    providers at request time, then proves reuse within one Store, isolation
    across Stores, and exact-scope invalidation.
    """
    from openai4s import datapro
    from openai4s.store import Store

    first_key = "first-plan-key-canary"
    second_key = "second-plan-key-canary"
    first_store = Store(tmp_path / "first" / "openai4s.db")
    second_store = Store(tmp_path / "second" / "openai4s.db")
    manager = MCPManager()
    created = []
    requests = []

    class Connection:
        command = ["streamable_http"]

        def __init__(self, config: dict, ordinal: int):
            self.cache_scope = config["cache_scope"]
            self.headers_provider = config["headers_provider"]
            self.ordinal = ordinal
            self.closed = False

        def faulted(self) -> bool:
            return self.closed

        def list_tools(self) -> list[dict]:
            headers = self.headers_provider()
            requests.append(
                (self.cache_scope, self.ordinal, headers["X-Agent-Plan-Key"])
            )
            return [{"name": datapro.TOOL_NAME, "connection": self.ordinal}]

        def close(self) -> bool:
            self.closed = True
            return True

        def failure(self):
            return None

    def connect(config: dict):
        connection = Connection(config, len(created) + 1)
        created.append(connection)
        return connection

    monkeypatch.setattr(manager, "_connect", connect)
    try:
        datapro.save_agent_plan_key(first_store, first_key)
        datapro.save_agent_plan_key(second_store, second_key)
        connector = {"connector_id": datapro.CONNECTOR_ID}
        first_config = datapro.connector_runtime_config(first_store, connector)
        second_config = datapro.connector_runtime_config(second_store, connector)
        first_scope = datapro.runtime_cache_scope(first_store)
        second_scope = datapro.runtime_cache_scope(second_store)

        assert first_config["cache_scope"] == first_scope
        assert second_config["cache_scope"] == second_scope
        assert first_scope != second_scope
        assert first_key not in first_scope + second_scope
        assert second_key not in first_scope + second_scope

        first_result = manager.list_tools(datapro.CONNECTOR_ID, first_config)
        assert manager.list_tools(datapro.CONNECTOR_ID, first_config) == first_result
        second_result = manager.list_tools(datapro.CONNECTOR_ID, second_config)
        assert first_result[0]["connection"] == 1
        assert second_result[0]["connection"] == 2
        assert requests == [
            (first_scope, 1, first_key),
            (first_scope, 1, first_key),
            (second_scope, 2, second_key),
        ]

        manager.disconnect(datapro.CONNECTOR_ID, cache_scope=first_scope)
        assert created[0].closed is True
        assert created[1].closed is False
        assert manager.list_tools(datapro.CONNECTOR_ID, second_config) == second_result
        replacement = manager.list_tools(datapro.CONNECTOR_ID, first_config)
        assert replacement[0]["connection"] == 3
        assert requests[-2:] == [
            (second_scope, 2, second_key),
            (first_scope, 3, first_key),
        ]
    finally:
        assert manager.shutdown() == []
        first_store.close()
        second_store.close()


@pytest.mark.stubbed_backend
def test_store_close_drops_only_its_datapro_scope_and_secret_state(
    tmp_path, monkeypatch
):
    """A closed Store cannot remain reachable through the global MCP cache."""

    from openai4s import datapro
    from openai4s.store import Store

    first_store = Store(tmp_path / "first-close" / "openai4s.db")
    second_store = Store(tmp_path / "second-close" / "openai4s.db")
    manager = MCPManager()
    created = []

    class Connection:
        command = ["streamable_http"]

        def __init__(self, config: dict):
            self.cache_scope = config["cache_scope"]
            self.headers_provider = config["headers_provider"]
            self.reflection_secrets = []
            self.closed = False

        def faulted(self) -> bool:
            return self.closed

        def list_tools(self) -> list[dict]:
            key = self.headers_provider()["X-Agent-Plan-Key"]
            if key not in self.reflection_secrets:
                self.reflection_secrets.append(key)
            return [{"name": datapro.TOOL_NAME}]

        def close(self) -> bool:
            self.closed = True
            self.headers_provider = None
            self.reflection_secrets.clear()
            return True

        def failure(self):
            return None

    def connect(config: dict):
        connection = Connection(config)
        created.append(connection)
        return connection

    monkeypatch.setattr(manager, "_connect", connect)
    monkeypatch.setattr(mcp_client, "_MANAGER", manager)
    try:
        datapro.save_agent_plan_key(first_store, "first-close-key-canary")
        datapro.save_agent_plan_key(second_store, "second-close-key-canary")
        connector = {"connector_id": datapro.CONNECTOR_ID}
        first_config = datapro.connector_runtime_config(first_store, connector)
        second_config = datapro.connector_runtime_config(second_store, connector)
        first_scope = datapro.runtime_cache_scope(first_store)
        second_scope = datapro.runtime_cache_scope(second_store)

        manager.list_tools(datapro.CONNECTOR_ID, first_config)
        manager.list_tools(datapro.CONNECTOR_ID, second_config)
        assert [connection.reflection_secrets for connection in created] == [
            ["first-close-key-canary"],
            ["second-close-key-canary"],
        ]

        first_store.close()

        assert created[0].closed is True
        assert created[0].headers_provider is None
        assert created[0].reflection_secrets == []
        assert created[1].closed is False
        assert set(manager._conns) == {(datapro.CONNECTOR_ID, second_scope)}
        assert manager.list_tools(datapro.CONNECTOR_ID, second_config) == [
            {"name": datapro.TOOL_NAME}
        ]

        second_store.close()
        assert created[1].closed is True
        assert created[1].headers_provider is None
        assert created[1].reflection_secrets == []
        assert manager._conns == {}
        assert first_scope != second_scope
    finally:
        first_store.close()
        second_store.close()
        assert manager.shutdown() == []


@pytest.mark.stubbed_backend
def test_closing_store_without_mcp_use_does_not_create_manager(tmp_path, monkeypatch):
    """Store-only callers must keep the MCP subsystem completely lazy."""

    from openai4s.store import Store

    monkeypatch.setattr(mcp_client, "_MANAGER", None)
    store = Store(tmp_path / "no-mcp" / "openai4s.db")

    store.close()

    assert mcp_client._MANAGER is None


@pytest.mark.stubbed_backend
def test_scoped_disconnect_rejects_only_that_store_inflight_connection(monkeypatch):
    """Connect locks and invalidation epochs use the Store scope too."""

    connector_id = "volcengine-datapro"
    first_scope = "store:first-generation"
    second_scope = "store:second-generation"
    first_started = threading.Event()
    release_first = threading.Event()
    first_errors = []
    created = []

    class Connection:
        command = ["streamable_http"]

        def __init__(self, scope: str):
            self.scope = scope
            self.closed = False

        def faulted(self) -> bool:
            return self.closed

        def list_tools(self) -> list[dict]:
            return [{"name": "dataPro_search", "scope": self.scope}]

        def close(self) -> bool:
            self.closed = True
            return True

        def failure(self):
            return None

    def connect(config: dict):
        connection = Connection(config["cache_scope"])
        created.append(connection)
        if connection.scope == first_scope:
            first_started.set()
            assert release_first.wait(5)
        return connection

    manager = MCPManager()
    monkeypatch.setattr(manager, "_connect", connect)

    def connect_first() -> None:
        try:
            manager.list_tools(connector_id, {"cache_scope": first_scope})
        except MCPError as exc:
            first_errors.append(exc)

    first_thread = threading.Thread(target=connect_first)
    first_thread.start()
    try:
        assert first_started.wait(5)
        # If connect locks were still keyed only by connector id, this would
        # wait behind first_scope instead of completing independently.
        second = manager.list_tools(connector_id, {"cache_scope": second_scope})
        assert second == [{"name": "dataPro_search", "scope": second_scope}]

        manager.disconnect(connector_id, cache_scope=first_scope)
        release_first.set()
        first_thread.join(5)
        assert not first_thread.is_alive()
        assert len(first_errors) == 1
        assert created[0].closed is True
        assert created[1].closed is False
        assert manager.list_tools(connector_id, {"cache_scope": second_scope}) == second
    finally:
        release_first.set()
        first_thread.join(5)
        assert manager.shutdown() == []


# -- the two queues a server gets to grow ------------------------------------
def _swallow(connection: MCPConnection):
    """A thread body that parks in `list_tools` until the connection is closed."""

    def run() -> None:
        try:
            connection.list_tools()
        except MCPError:
            pass

    return run


def test_abandoned_ids_and_in_flight_requests_are_both_capped(tmp_path, monkeypatch):
    """Both grew on exactly the input a wedged connector produces.

    `_abandoned` gained one entry per timed-out request and was pruned only if
    the server chose to answer late, so the connector that never answers -- the
    one case the set exists for -- grew it without bound. `_pending` had no cap
    at all.
    """
    monkeypatch.setattr(mcp_client, "_MAX_ABANDONED", 3)
    monkeypatch.setattr(mcp_client, "_MAX_PENDING", 2)
    config = _server(tmp_path, "wedged", body=_HANG)

    connection = MCPConnection(config["command"], timeout=30.0)
    # The cap is exercised by short request deadlines, not by requiring a
    # freshly spawned Python process to initialize within 200 milliseconds.
    connection._timeout = 0.2
    try:
        # The construction timeout also governs the `initialize` handshake
        # the constructor performs against a cold CPython subprocess. A
        # short deadline set there is a bet on how fast a runner can start
        # an interpreter; the deadline this test is about belongs to the
        # request below, so it is set after the handshake has succeeded.
        connection._timeout = 0.2
        for _ in range(8):
            with pytest.raises(MCPTimeout):
                connection.list_tools()
        assert len(connection._abandoned) <= 3
    finally:
        connection.close()

    blocked = MCPConnection(config["command"], timeout=60.0)
    try:
        for _ in range(2):
            threading.Thread(target=_swallow(blocked), daemon=True).start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and len(blocked._pending) < 2:
            time.sleep(0.02)
        assert len(blocked._pending) == 2
        with pytest.raises(MCPError, match="in flight"):
            blocked.list_tools()
    finally:
        blocked.close()


# -- stderr is bounded while it is being read --------------------------------
def test_a_huge_stderr_line_is_truncated_while_it_is_read(tmp_path):
    """`for line in stream` had no size bound whatsoever.

    Only the line COUNT was capped, so a connector emitting one newline-free
    8 MB diagnostic put all 8 MB in the daemon's heap, and `stderr_tail()` then
    built a second full copy on its way to a 500-character slice.
    """
    config = _server(
        tmp_path,
        "loud",
        preamble=(
            'sys.stderr.write("x" * (8 * 1024 * 1024) + chr(10))\n'
            'sys.stderr.write("the useful part" + chr(10))\n'
            "sys.stderr.flush()\n"
        ),
        body=_answer(),
    )
    connection = MCPConnection(config["command"], timeout=30.0)
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and len(connection._stderr_tail) < 2:
            time.sleep(0.05)
        assert len(connection._stderr_tail) == 2, "stderr never arrived"
        # Bytes, because bytes are what the budget is now expressed in and
        # what the reader counts. The old assertion measured characters against
        # a constant that claimed bytes, which is exactly the disagreement that
        # let a multi-byte diagnostic use several times its stated budget.
        widest = max(len(line.encode("utf-8")) for line in connection._stderr_tail)
        assert (
            widest <= mcp_client._MAX_STDERR_LINE_BYTES
        ), f"retained a {widest}-byte stderr line"
        tail = connection.stderr_tail()
        assert len(tail) <= mcp_client._STDERR_TAIL_CHARS
        # Truncating the giant line must not cost the line after it.
        assert tail.endswith("the useful part")
    finally:
        connection.close()


# -- termination reaches the whole tree, and admits when it does not ----------
def test_close_kills_a_wrapped_servers_real_worker_without_hanging(tmp_path):
    """`terminate()` signalled the leader only.

    Connectors are routinely launched through a wrapper (`npx`, `uv run`,
    `sh -c`). Killing the wrapper left the real server running and still holding
    the stdio pipes -- which is also why `close()` could hang forever: with no
    EOF the reader thread stays parked in `read` holding the buffered stream's
    lock, and the old `close()` then blocked acquiring that same lock to close
    `stdout`.
    """
    grandchild = tmp_path / "worker.pid"
    config = _server(
        tmp_path,
        "wrapper",
        preamble=(
            "worker = subprocess.Popen([sys.executable, '-c',"
            " 'import time; time.sleep(300)'])\n"
            f"open({str(grandchild)!r}, 'w').write(str(worker.pid))\n"
        ),
        body=_answer(),
    )
    connection = MCPConnection(config["command"], timeout=30.0)
    worker_pid = int(grandchild.read_text(encoding="utf-8"))
    assert connection.list_tools() == [{"name": "ok"}]

    closed: list[bool] = []
    finished = threading.Event()

    def close_it() -> None:
        closed.append(connection.close())
        finished.set()

    threading.Thread(target=close_it, daemon=True).start()
    try:
        assert finished.wait(40), "close() blocked instead of returning"
        assert closed == [True]
        assert _wait_gone(connection._proc.pid)
        assert _wait_gone(worker_pid), "the wrapped server survived close()"
        assert not connection._reader.is_alive()
        assert not connection._stderr_thread.is_alive()
    finally:
        for pid in (connection._proc.pid, worker_pid):
            try:
                os.kill(pid, 9)
            except OSError:
                pass


def test_a_child_that_ignores_every_signal_is_reported_not_assumed_dead(
    tmp_path, monkeypatch
):
    """Teardown reported success unconditionally.

    A connector that ignores TERM -- and, under a supervisor that traps it,
    KILL -- was indistinguishable from one that exited, so the next `serve`
    started against orphans still holding whatever they held.
    """
    config = _server(tmp_path, "stubborn", body=_answer())
    manager = MCPManager()
    connection = manager.get("c", config)
    try:
        # A child no signal can reach: the signals go nowhere and the waits
        # expire. Nothing else about `close()` changes.
        monkeypatch.setattr(connection, "_signal_tree", lambda sig, fallback: None)
        monkeypatch.setattr(mcp_client, "_TERMINATE_WAIT_S", 0.2)
        monkeypatch.setattr(mcp_client, "_READER_JOIN_S", 0.2)

        survivors = manager.shutdown()
        assert survivors and "survived SIGKILL" in survivors[0]
        assert "survived SIGKILL" in (connection.failure() or "")
        assert connection.alive(), "the premise of this test is a live child"
    finally:
        monkeypatch.undo()
        connection.close()
        assert manager.shutdown() == []


# -- the budget is bytes, and the reader allocates in chunks ------------------


class _ChunkStream:
    """A raw pipe that hands back at most `chunk` bytes per read, like a pipe."""

    def __init__(self, payload: bytes, chunk: int = 7) -> None:
        self._payload = payload
        self._chunk = chunk
        self._at = 0
        self.reads = 0

    def read(self, size: int) -> bytes:
        self.reads += 1
        take = min(size, self._chunk, len(self._payload) - self._at)
        if take <= 0:
            return b""
        out = self._payload[self._at : self._at + take]
        self._at += take
        return out


def test_the_line_reader_counts_bytes_not_characters():
    """The constant said bytes; the reader counted characters off a text pipe.

    Sixteen three-byte characters is forty-eight bytes. Under a ten-byte budget
    a byte-accurate reader keeps three characters' worth; the character-counting
    predecessor kept ten characters -- thirty bytes, three times its own limit,
    and the multiplier is whatever the connector's encoding happens to be.
    """
    payload = ("中" * 16).encode("utf-8") + b"\n"
    reader = mcp_client._BoundedLineReader(
        _ChunkStream(payload), limit=10, keep_partial=True
    )

    line = reader.readline()

    assert len(line.encode("utf-8")) <= 10, line
    assert line.startswith("中")


def test_an_over_budget_frame_is_dropped_and_the_next_one_still_parses():
    """Half a JSON object is not a frame, so the reader must resynchronise.

    The residual bytes of the chunk that ran past the newline live in the
    reader, which is why it is constructed once per stream: a fresh reader per
    line would discard everything already read past the terminator.
    """
    payload = b"x" * 40 + b"\n" + b'{"id": 7}' + b"\n"
    reader = mcp_client._BoundedLineReader(_ChunkStream(payload), limit=10)

    assert reader.readline() == "", "an over-budget frame must not be returned"
    assert reader.readline() == '{"id": 7}'
    assert reader.readline() is None
    # The sentinel above is the resynchronisation contract and stays exactly as
    # it was; the counters are additive. Without them the drop is
    # indistinguishable from a blank line one layer up, which is how it came to
    # be reported as a deadline nobody missed. 40 bytes of frame, all lost --
    # the 10 that fit are discarded with the 30 that did not.
    assert reader.frames_dropped == 1
    assert reader.bytes_dropped == 40


def test_a_line_split_across_reads_is_reassembled():
    payload = b'{"jsonrpc": "2.0", "id": 1}\n{"jsonrpc": "2.0", "id": 2}\n'
    stream = _ChunkStream(payload, chunk=5)
    reader = mcp_client._BoundedLineReader(stream)

    assert reader.readline() == '{"jsonrpc": "2.0", "id": 1}'
    assert reader.readline() == '{"jsonrpc": "2.0", "id": 2}'
    assert reader.readline() is None
    # One read per chunk plus the EOF probe, not one per byte: the predecessor
    # called `read(1)` for every character, which is what made a four-megabyte
    # frame a four-million-element list of one-character strings.
    assert stream.reads <= len(payload) // 5 + 4


def test_a_trailing_line_with_no_newline_is_still_delivered():
    reader = mcp_client._BoundedLineReader(_ChunkStream(b'{"id": 3}'))

    assert reader.readline() == '{"id": 3}'
    assert reader.readline() is None


# -- connecting one connector must not stall the others -----------------------


def test_a_slow_connect_does_not_stall_a_different_connector():
    """`_connect` spawns a child and runs `initialize`; it was under the global lock.

    That lock is the one every connector's `get`, `_evict`, `disconnect` and
    `shutdown` need, so one server taking seconds to answer `initialize` — or
    hanging until its timeout — stalled every other connector in the process,
    including ones already connected and merely being looked up.

    Asserted on wall-clock ordering rather than on the lock object: what a
    caller experiences is the wait, and a test that inspected the locking would
    keep passing if the waiting moved somewhere else.
    """
    manager = mcp_client.MCPManager()
    started = threading.Event()
    release = threading.Event()

    class _Slow:
        def faulted(self):
            return False

        def close(self):
            return True

    def connect(config):
        if config.get("command") == "slow":
            started.set()
            assert release.wait(10), "the fast connect never arrived"
        return _Slow()

    manager._connect = connect
    try:
        slow = threading.Thread(
            target=manager.get, args=("slow-one", {"command": "slow"}), daemon=True
        )
        slow.start()
        assert started.wait(5), "the slow connect never started"

        # While `slow-one` is mid-`initialize`, a different connector must be
        # able to connect. Under the old global lock this call blocked until
        # the slow one finished.
        fast = manager.get("fast-one", {"command": "fast"})
        assert fast is not None
    finally:
        release.set()
        slow.join(10)
        assert not slow.is_alive()


def test_two_callers_for_the_same_connector_produce_one_child():
    """Single-flight: the second adopts what the first cached, not a second child."""
    manager = mcp_client.MCPManager()
    made: list[object] = []
    gate = threading.Event()

    class _Conn:
        def faulted(self):
            return False

        def close(self):
            return True

    def connect(config):
        del config
        gate.wait(10)
        conn = _Conn()
        made.append(conn)
        return conn

    manager._connect = connect
    got: list[object] = []
    threads = [
        threading.Thread(
            target=lambda: got.append(manager.get("same", {"command": "x"})),
            daemon=True,
        )
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    gate.set()
    for thread in threads:
        thread.join(10)

    assert len(made) == 1, f"{len(made)} children were spawned for one connector"
    assert got[0] is got[1]


def test_a_connector_that_never_reads_its_stdin_does_not_hang_the_caller():
    """The write was the one phase of a request the deadline did not cover.

    `_request` computes an absolute deadline and applies it to the reply wait.
    The send in between was a blocking `write` on the child's stdin with no
    bound at all, so a connector that never drains it fills the pipe buffer and
    blocks there forever — while `_request` holds the connection lock, so the
    caller hangs past its own timeout and every other request to that connector
    queues behind it.

    Asserted on wall clock, because a hang is what the caller experiences.
    """

    # A real pipe, filled to capacity with nobody reading it — the actual
    # condition, not a stand-in. `select` for writability on a full pipe does
    # not fire, which is precisely where the old code blocked forever.
    read_fd, write_fd = os.pipe()
    os.set_blocking(write_fd, False)
    try:
        while True:
            os.write(write_fd, b"x" * 65536)
    except BlockingIOError:
        pass

    class _FullPipe:
        def fileno(self):
            return write_fd

        def write(self, view):  # pragma: no cover - must never be reached
            raise AssertionError("the write should not have been attempted")

        def flush(self):  # pragma: no cover
            return None

    started = time.monotonic()
    try:
        with pytest.raises(mcp_client.MCPTimeout):
            mcp_client._write_all(
                _FullPipe(), b"x" * 4096, deadline=time.monotonic() + 0.5
            )
    finally:
        os.close(write_fd)
        os.close(read_fd)
    assert time.monotonic() - started < 5, "the write was not bounded by the deadline"


# --- the deadline the decision record published and nothing read -----------


def test_the_documented_deadline_override_exists_and_is_clamped(monkeypatch):
    """`docs/v03-decisions.md` D6 names `OPENAI4S_MCP_DEADLINE_S` as the single
    override for this budget. The name appeared in that table and nowhere else
    in the tree -- a documented control that does not exist, which is worse than
    an undocumented one because someone sets it and believes the deadline moved.

    Clamped rather than trusted: zero is not "no deadline", it is a connector
    that can never answer, and a day is the unbounded wait `DEFAULT_TIMEOUT_S`
    was introduced to remove. Anything unusable falls back rather than refusing
    to start -- an env var must not be able to make the daemon unbootable.
    """
    from openai4s import mcp_client

    cases = {
        "": mcp_client.DEFAULT_TIMEOUT_S,
        "5": 5.0,
        "0": mcp_client.DEFAULT_TIMEOUT_S,
        "-3": mcp_client.DEFAULT_TIMEOUT_S,
        "abc": mcp_client.DEFAULT_TIMEOUT_S,
        "inf": mcp_client.DEFAULT_TIMEOUT_S,
        "0.5": mcp_client.MIN_TIMEOUT_S,
        "99999": mcp_client.MAX_TIMEOUT_S,
    }
    for raw, expected in cases.items():
        monkeypatch.setenv(mcp_client.DEADLINE_ENV, raw)
        assert mcp_client._deadline_default() == expected, raw

    monkeypatch.delenv(mcp_client.DEADLINE_ENV, raising=False)
    assert mcp_client._deadline_default() == mcp_client.DEFAULT_TIMEOUT_S


def test_the_override_reaches_a_connection_and_an_argument_still_wins(monkeypatch):
    """The wiring, not just the helper. A default nothing reads is the defect
    this ticket is about, one layer along.
    """
    from openai4s import mcp_client

    monkeypatch.setenv(mcp_client.DEADLINE_ENV, "7")
    assert mcp_client._deadline_default() == 7.0

    # An explicit argument is not overridden by the environment: several tests
    # pass `timeout=0.2` and must keep it.
    import inspect

    source = inspect.getsource(mcp_client.MCPConnection.__init__)
    assert "float(timeout) if timeout is not None else _deadline_default()" in source


def test_the_dead_timeout_constant_is_gone():
    """`_DEFAULT_TIMEOUT = 30.0` sat three lines above the live
    `DEFAULT_TIMEOUT_S = 60.0` with no reader anywhere in the tree. Two
    similarly named constants, one of them wrong and unused, is how someone
    wires the wrong knob."""
    from pathlib import Path

    source = Path("openai4s/mcp_client.py").read_text(encoding="utf-8")
    assert "_DEFAULT_TIMEOUT = " not in source


# -- an answer refused for its size is not an answer that never came ----------


_OVERSIZED = (
    "    sys.stdout.write('x' * (4 * 1024 * 1024 + 64) + chr(10))\n"
    "    sys.stdout.flush()\n" + _HANG
)


def test_a_frame_too_large_is_reported_as_refused_not_as_a_deadline(tmp_path):
    """The connector answered at once. It was reported as one that never did.

    The reader drops an over-budget frame -- correctly, half a JSON object is
    not a frame -- and returns `""`, which the read loop skips as it skips a
    blank line. Nothing else on the channel knew, so the only thing that ever
    surfaced was the caller's own deadline: a connector that replied in
    milliseconds was described to the agent as one that spent sixty seconds not
    replying, which is not a fact about it and not a thing the agent can act on.
    """
    config = _server(tmp_path, "toobig", body=_OVERSIZED)
    connection = MCPConnection(config["command"], timeout=3.0)
    try:
        with pytest.raises(mcp_client.MCPOversizedResponse) as caught:
            connection.list_tools()
        # A sibling of MCPTimeout, never a subclass: the whole point is that the
        # layer above must be able to tell them apart.
        assert not isinstance(caught.value, MCPTimeout)
        assert isinstance(caught.value, mcp_client.MCPError)
        assert "refused, not lost" in str(caught.value)
        assert connection._oversized_frames == 1
    finally:
        connection.close()


def test_a_silent_connector_is_still_a_deadline(tmp_path):
    """The negative arm. A branch that swallowed the real timeout state would
    make every wedged connector look like an oversized one."""
    config = _server(tmp_path, "silent", body=_HANG)
    connection = MCPConnection(config["command"], timeout=30.0)
    # Keep environment-dependent process startup outside the deadline under
    # test; the silent request itself must still time out after 300 ms.
    connection._timeout = 0.3
    try:
        # The construction timeout also governs the `initialize` handshake
        # the constructor performs against a cold CPython subprocess. A
        # short deadline set there is a bet on how fast a runner can start
        # an interpreter; the deadline this test is about belongs to the
        # request below, so it is set after the handshake has succeeded.
        connection._timeout = 0.3
        with pytest.raises(MCPTimeout) as caught:
            connection.list_tools()
        assert not isinstance(caught.value, mcp_client.MCPOversizedResponse)
    finally:
        connection.close()


def test_a_connector_that_overflowed_is_not_torn_down(tmp_path):
    """The wiring, not the mechanism.

    `_request` raising the right class proves nothing about what `_invoke`
    above it does with it: eviction is keyed on the exception, and while the
    state was `MCPTimeout` this connector was closed and respawned every time
    it answered too largely. The pid is the assertion -- a cache entry can be
    replaced by an object that looks the same.
    """
    config = _server(tmp_path, "toobig_mgr", body=_OVERSIZED)
    manager = mcp_client.MCPManager()
    try:
        before = manager.get("c1", config)
        with pytest.raises(mcp_client.MCPOversizedResponse):
            manager.list_tools("c1", config)
        after = manager.get("c1", config)
        assert after is before, "the connector was evicted for answering"
        assert after.alive()
    finally:
        manager.shutdown()


def test_the_refusal_state_is_a_sibling_of_the_deadline_state(tmp_path):
    """The class hierarchy IS the guard, so it gets its own assertion.

    `MCPManager._invoke` evicts on `MCPTimeout` and keeps a connection that is
    merely `MCPError` and still alive. Nothing else distinguishes the two, so
    making `MCPOversizedResponse` a subclass -- the natural-looking edit, since
    both are "the caller got nothing" -- silently restores the teardown that
    `test_a_connector_that_overflowed_is_not_torn_down` exists to forbid, and
    does it in a line no reviewer would read as a behaviour change.
    """
    del tmp_path
    assert issubclass(mcp_client.MCPOversizedResponse, mcp_client.MCPError)
    assert not issubclass(mcp_client.MCPOversizedResponse, MCPTimeout)


# -- the wrong-ID flood: the one fault arm whose budget nothing guarded --------


_WRONG_ID_FLOOD = (
    "    for k in range(200):\n"
    "        sys.stdout.write(json.dumps("
    "{'jsonrpc': '2.0', 'id': 900000 + k, 'result': {}}) + chr(10))\n"
    "    sys.stdout.flush()\n" + _HANG
)

_A_FEW_WRONG_IDS = (
    "    for k in range(10):\n"
    "        sys.stdout.write(json.dumps("
    "{'jsonrpc': '2.0', 'id': 900000 + k, 'result': {}}) + chr(10))\n"
    "    sys.stdout.flush()\n" + _answer()
)


def test_a_server_answering_ids_nobody_asked_for_is_dropped(tmp_path):
    """`_MAX_INVALID_IDS` had zero call sites outside production.

    Six fault arms are named in the exit criteria and five had tests; deleting
    this budget entirely would have turned nothing red. A desynchronised server
    is not a slow one -- it is answering promptly, with answers that belong to
    nobody -- so the failure it produces has to be its own state and has to
    arrive without waiting out a deadline.

    The generous timeout is the assertion. If the budget never fires, the waiter
    sits until 30 s and raises `MCPTimeout`; the run is red either way, but the
    *shape* of the red says which. `pytest -q` finishing this test in well under
    a second is the observable that the budget, not the clock, ended it.
    """
    config = _server(tmp_path, "wrongid", body=_WRONG_ID_FLOOD)
    started = time.monotonic()
    connection = MCPConnection(config["command"], timeout=30.0)
    try:
        with pytest.raises(MCPError) as caught:
            connection.list_tools()
    finally:
        connection.close()
    elapsed = time.monotonic() - started

    assert not isinstance(
        caught.value, MCPTimeout
    ), "reported as a deadline; the budget never fired and the clock did"
    assert "desynchronised" in str(caught.value), str(caught.value)
    assert "unrequested ids" in str(caught.value)
    assert elapsed < 15, f"took {elapsed:.1f}s: this was the deadline, not the budget"


def test_the_desynchronised_connector_does_not_survive_the_verdict(tmp_path):
    """Detaching used to be the whole response: the read loop returned and the
    child kept running, holding its pipes, while the manager handed the same
    dead connection to every later caller. A channel declared desynchronised
    must take its process with it."""
    config = _server(tmp_path, "wrongid_proc", body=_WRONG_ID_FLOOD)
    connection = MCPConnection(config["command"], timeout=30.0)
    try:
        with pytest.raises(MCPError):
            connection.list_tools()
        pid = _pids(config)[0]
        assert _wait_gone(pid), "the desynchronised connector was left running"
    finally:
        connection.close()


def test_a_few_unrequested_ids_are_not_a_desynchronised_channel(tmp_path):
    """The negative arm, and the reason the budget is a budget and not a switch.

    A late reply to an abandoned request, a duplicate, a notification carrying an
    id -- all produce an unmatched id and none of them means the channel has
    lost its framing. A guard that dropped the connector on the first one would
    turn every timed-out request into a respawn, which is the failure
    `_abandoned` exists to prevent.
    """
    config = _server(tmp_path, "fewids", body=_A_FEW_WRONG_IDS)
    connection = MCPConnection(config["command"], timeout=15.0)
    try:
        assert connection.list_tools() == [{"name": "ok"}]
    finally:
        connection.close()


def test_an_abandoned_request_answered_late_does_not_count_against_the_budget(
    tmp_path,
):
    """The interaction the two mechanisms have to get right together.

    A request that timed out leaves its id in `_abandoned`; the server answering
    it afterwards is the *expected* behaviour of a slow connector, not evidence
    of desynchronisation. Counting those would make a connector that is merely
    slow indistinguishable from one that has lost framing, and the budget would
    fire on the healthy case.
    """
    body = (
        "    if served == 1:\n"
        "        time.sleep(3)\n"
        "        sys.stdout.write(json.dumps("
        "{'jsonrpc': '2.0', 'id': mid, 'result': {'tools': []}}) + chr(10))\n"
        "        sys.stdout.flush()\n"
        "        continue\n" + _answer()
    )
    config = _server(tmp_path, "lateid", body=body)
    connection = MCPConnection(config["command"], timeout=30.0)
    try:
        # The construction timeout also governs the `initialize` handshake
        # the constructor performs against a cold CPython subprocess. A
        # short deadline set there is a bet on how fast a runner can start
        # an interpreter; the deadline this test is about belongs to the
        # request below, so it is set after the handshake has succeeded.
        connection._timeout = 0.4
        with pytest.raises(MCPTimeout):
            connection.list_tools()
        # id 1 was `initialize`; the timed-out call is the next one.
        assert list(connection._abandoned) == [2]

        # Wait for the observable, not for four seconds. The reader pops the
        # abandoned id when the late reply finally lands (`mcp_client` drains
        # it in the background), so the state this test needs is a fact it can
        # watch rather than a duration it has to guess.
        deadline = time.time() + 30.0
        while time.time() < deadline and list(connection._abandoned) == [2]:
            time.sleep(0.05)
        assert list(connection._abandoned) != [2], (
            "the late answer to id 1 never arrived, so the discard path this "
            "test is about was never exercised"
        )

        # The connection is still usable, and the late reply was discarded
        # rather than counted.
        connection._timeout = 15.0
        assert connection.list_tools() == [{"name": "ok"}]
        assert connection.failure() is None
    finally:
        connection.close()


def test_a_flood_of_late_answers_to_abandoned_ids_is_not_desynchronisation(
    tmp_path, monkeypatch
):
    """The `continue` that makes `_abandoned` and the budget compatible.

    A single late reply cannot distinguish the two: one increment is far below
    any threshold, so removing the `continue` changes nothing observable. It
    takes more late answers than the budget allows -- which is exactly the
    shape of a connector that is *slow*, answering a backlog after its callers
    gave up. Without the `continue` that connector is declared desynchronised
    and torn down for eventually answering, and the budget fires hardest on the
    case `_abandoned` was written to protect.

    The budget is lowered rather than the backlog raised: 65 real timeouts is
    seconds of wall clock to assert a relationship between two numbers, and the
    relationship is what this pins.
    """
    monkeypatch.setattr(mcp_client, "_MAX_INVALID_IDS", 4)
    rounds = 8
    config = _server(
        tmp_path,
        "latebacklog",
        preamble="pending = []",
        body=(
            "    pending.append(mid)\n"
            f"    if served < {rounds}:\n"
            "        continue\n"
            "    for old in pending:\n"
            "        sys.stdout.write(json.dumps("
            "{'jsonrpc': '2.0', 'id': old, 'result': {'tools': []}}) + chr(10))\n"
            "    sys.stdout.flush()\n"
            "    pending = []\n"
            "    continue\n"
        ),
    )
    connection = MCPConnection(config["command"], timeout=30.0)
    # Backlog semantics need short per-request deadlines, while subprocess
    # initialization is unrelated setup and gets a stable startup budget.
    connection._timeout = 0.3
    try:
        # The construction timeout also governs the `initialize` handshake
        # the constructor performs against a cold CPython subprocess. A
        # short deadline set there is a bet on how fast a runner can start
        # an interpreter; the deadline this test is about belongs to the
        # request below, so it is set after the handshake has succeeded.
        connection._timeout = 0.3
        for _ in range(rounds - 1):
            with pytest.raises(MCPTimeout):
                connection.list_tools()
        assert len(connection._abandoned) == rounds - 1

        # The request that triggers the flush. Its own answer is in the
        # backlog, so it succeeds -- unless the backlog was counted, in which
        # case the channel is declared desynchronised before this reply is read.
        connection._timeout = 15.0
        assert connection.list_tools() == []
        assert connection.failure() is None
    finally:
        connection.close()


# -- the kernel's own stderr channel, which is not the MCP one ----------------


def test_a_kernel_flooding_its_own_stderr_stays_bounded_and_keeps_serving(tmp_path):
    """A distinct channel from the MCP stderr flood, and the arm with no test.

    `tests/test_producer_output_budgets.py` feeds `_StderrTail` by hand and
    `tests/test_channel_counter_contract.py` drives a worker that floods and
    then *dies*. Neither covers the case the exit criteria actually name: a
    worker that floods fd2 and keeps running. That is the one where an unbounded
    tail is a live leak rather than a one-off allocation -- the cell finishes,
    the kernel stays up, and the daemon is holding every byte the child wrote.

    Ten megabytes, sustained, and then the kernel has to still answer.
    """
    from openai4s.kernel.manager import _STDERR_TAIL_BYTES, Kernel

    flood = 10 * 1024 * 1024
    kernel = Kernel(cwd=str(tmp_path))
    try:
        result = kernel.execute(
            "import os\n" f"os.write(2, b'F' * {flood})\n" "print('cell finished')\n"
        )
        assert "cell finished" in result["stdout"]

        # The bound is on what the daemon holds, not on what the child wrote.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if kernel._stderr_tail.seen_bytes >= flood:
                break
            time.sleep(0.05)
        tail = kernel._stderr_tail
        assert tail.seen_bytes >= flood, tail.seen_bytes
        assert tail.retained_bytes <= _STDERR_TAIL_BYTES
        assert tail.dropped_bytes >= flood - _STDERR_TAIL_BYTES

        # ...and the kernel is still the same one, still serving.
        assert kernel.execute("print(2 + 2)")["stdout"].strip() == "4"
    finally:
        kernel.shutdown()
