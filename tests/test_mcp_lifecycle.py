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

    connection = MCPConnection(config["command"], timeout=0.2)
    try:
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
