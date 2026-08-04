"""Booting the daemon on a fresh data dir must do nothing on the user's behalf.

The example analysis used to seed itself on first boot: the daemon bound its
port and then, on a background thread, started a Python kernel, executed six
cells, called the UniProt and RCSB REST APIs, spawned the bundled MCP connector
and wrote four artifacts -- before the user had typed anything. Every one of
those is something this application otherwise asks permission for.

That was fixed by flipping ``OPENAI4S_SEED_DEMO`` to default off and moving the
example behind a confirmed ``POST /example/session``. ``tests/test_gateway.py``
pins the flag and the route. This module pins the *property the flag exists to
produce*, at the boundaries where it can actually be observed:

* no outbound socket -- guarded at ``socket.socket.connect``, because a flag
  read tells you what the code intends and a connect tells you what it did;
* no child process -- guarded at ``subprocess.Popen``, which is where a kernel,
  an R worker and an MCP server all become real, whatever the caller is named;
* nothing persisted -- no frame, no artifact, no kernel generation.

A guard rather than a flag assertion, because the original defect ran on a
background thread: the startup function could return "seeding disabled" while a
seed was already under way. Only the boundary sees that.

The last test in the file is the positive control, and it is the reason none
of the tests above can be satisfied by deleting the feature:
``OPENAI4S_SEED_DEMO=1`` must still start the seed. A test suite that only
forbids things eventually passes on an empty program.
"""
import socket
import sqlite3
import subprocess
import threading
import time

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.store import get_store


def _cfg(tmp_path):
    return Config(
        data_dir=tmp_path,
        # Port 0: ask the OS for a free port rather than fighting a daemon the
        # developer may have running on 8760.
        port=0,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=3,
    )


class _BoundaryGuard:
    """Records every outbound connect and every child process, and blocks both.

    Blocking matters as much as recording. If a regression reintroduces the
    implicit seed, this test should not be the thing that posts to UniProt from
    a CI runner or leaves an orphaned kernel behind; it should be the thing
    that reports the attempt. The recorded lists are what the assertions read,
    so an attempt that some caller's broad ``except`` swallows is still caught.
    """

    def __init__(self):
        self.connects: list[object] = []
        self.processes: list[object] = []

    def install(self, monkeypatch):
        def _connect(_sock, address, *a, **k):
            self.connects.append(address)
            raise OSError("startup must not open an outbound connection")

        def _popen(_self, args, *a, **k):
            self.processes.append(args)
            raise OSError("startup must not spawn a child process")

        # raising=True on both: if either boundary is ever renamed or replaced,
        # this test must fail loudly rather than quietly guarding nothing.
        monkeypatch.setattr(socket.socket, "connect", _connect, raising=True)
        monkeypatch.setattr(subprocess.Popen, "__init__", _popen, raising=True)


def _kernel_generation_count(db_path) -> int:
    """Read the table directly: it is on the ``Store.query`` denylist, so the
    agent-facing read-only API cannot see it, and a boot that spawned a kernel
    would leave its evidence exactly here."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT COUNT(*) FROM kernel_generations").fetchone()
    finally:
        conn.close()
    return int(row[0])


@pytest.fixture
def fresh_boot(tmp_path, monkeypatch):
    """Build the real server on a brand-new data dir, guards already installed.

    Driven through ``build_app_server`` -- the function ``openai4s serve``
    calls -- rather than through the seeder, because the defect was in the
    wiring, not in ``_seed_demo_session``.
    """
    monkeypatch.delenv("OPENAI4S_SEED_DEMO", raising=False)
    guard = _BoundaryGuard()
    guard.install(monkeypatch)
    executed: list[object] = []
    _real_run_repl = gateway_mod.SessionRunner.run_repl

    def _record_and_run(self, *a, **k):
        # Records and then *delegates*. A stub here would make the three
        # boundary assertions vacuous on a real regression: the seed's network
        # calls and kernel spawn all happen inside the cell it swallowed, so a
        # reintroduced seed would trip only this counter and none of the guards
        # that describe the actual harm. The guards block, so delegating cannot
        # reach UniProt or leave a kernel behind.
        executed.append(a)
        return _real_run_repl(self, *a, **k)

    monkeypatch.setattr(
        gateway_mod.SessionRunner, "run_repl", _record_and_run, raising=True
    )
    cfg = _cfg(tmp_path)
    httpd = gateway_mod.build_app_server(cfg)
    # A seed starts on a background thread, so a boot that started one is only
    # distinguishable from one that did not after that thread has had a moment.
    time.sleep(1.5)
    try:
        yield cfg, guard, executed, httpd
    finally:
        httpd.server_close()
        httpd.runner.close()


def test_a_fresh_boot_opens_no_outbound_connection(fresh_boot):
    """No telemetry post, no update check, no connector probe, no relay dial.

    This is the belt, not the braces. A reintroduced example seed is caught one
    step earlier, at the kernel spawn, because its network calls happen inside
    a cell; what this catches is the daemon process itself dialling out, which
    is the shape the offline suite has actually shipped before.
    """
    _cfg_, guard, _executed, _httpd = fresh_boot
    assert (
        guard.connects == []
    ), f"starting the daemon reached the network: {guard.connects}"
    # An assertion that nothing was recorded is worth exactly as much as the
    # recorder. 203.0.113.0/24 is TEST-NET-3: reserved, never routed, so this
    # probe cannot leave the machine even if the patch is not in place.
    with pytest.raises(OSError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("203.0.113.1", 80))
    assert guard.connects == [("203.0.113.1", 80)], "the connect guard was not live"


def test_a_fresh_boot_spawns_no_child_process(fresh_boot):
    """No kernel, no R worker, no MCP server, no pip."""
    _cfg_, guard, _executed, _httpd = fresh_boot
    assert guard.processes == [], (
        "starting the daemon spawned a child process: " f"{guard.processes}"
    )


def test_a_fresh_boot_executes_no_cell_and_records_no_kernel_generation(fresh_boot):
    cfg, _guard, executed, _httpd = fresh_boot
    assert executed == [], "a fresh boot executed a cell"
    assert _kernel_generation_count(cfg.db_path) == 0


def test_a_fresh_boot_persists_no_session_and_no_artifact(fresh_boot):
    cfg, _guard, _executed, _httpd = fresh_boot
    store = get_store(cfg.db_path)
    assert store.list_artifacts({}) == [], "a fresh boot created an artifact"
    # `project_id=None` means every project. The default is the literal string
    # "default", and the example session is created in `proj_example` -- so the
    # obvious call would have looked in the one project the seed never writes
    # to and passed while the example sat in the database.
    assert (
        store.browse_frames(project_id=None, roots_only=True, limit=50) == []
    ), "a fresh boot created a session"


def test_an_unconfirmed_post_to_the_example_route_reaches_nothing(
    tmp_path, monkeypatch
):
    """The route is the other way the expensive work can start by accident.

    ``tests/test_gateway.py`` asserts the 400 and that the seeder is not
    called. This asserts the consequence at the same boundary as the boot
    tests: enumerating verbs against a running daemon -- a contract capture, a
    route sweep, a client retrying a queue -- opens no socket and starts no
    process. Driven through the real ``Handler._api`` so the refusal is the
    production one.
    """
    monkeypatch.delenv("OPENAI4S_SEED_DEMO", raising=False)
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, gateway_mod.WSHub())
    handler_cls = gateway_mod.make_handler(cfg, gateway_mod.WSHub(), runner)
    guard = _BoundaryGuard()
    guard.install(monkeypatch)
    try:
        handler = object.__new__(handler_cls)
        handler.path = "/api/v1/example/session"
        seen: list[tuple[dict, int]] = []
        handler._json = lambda obj, code=200: seen.append((obj, code))
        handler._body = lambda: {}

        with pytest.raises(gateway_mod.GatewayError) as refused:
            handler._api("POST", "/example/session")
        assert refused.value.code == 400
        time.sleep(0.3)  # a seed thread would have reached a boundary by now
        assert guard.connects == []
        assert guard.processes == []

        handler._api("GET", "/example/session")
        assert seen[-1][0]["seeded"] is False
        assert guard.connects == [], "a status GET reached the network"
    finally:
        runner.close()


def test_the_opt_in_variable_still_starts_the_seed_at_boot(tmp_path, monkeypatch):
    """The positive control.

    Every assertion above is satisfied by a build of this program with the
    example deleted, and `OPENAI4S_SEED_DEMO` is documented as the way a demo
    machine comes up pre-populated. The seeder itself is stubbed: what is under
    test is that startup still reaches it, not the six live cells behind it.
    """
    monkeypatch.setenv("OPENAI4S_SEED_DEMO", "1")
    started = threading.Event()
    monkeypatch.setattr(
        gateway_mod, "_seed_demo_session", lambda *a, **k: started.set(), raising=True
    )
    cfg = _cfg(tmp_path)
    httpd = gateway_mod.build_app_server(cfg)
    try:
        assert started.wait(10.0), "OPENAI4S_SEED_DEMO=1 no longer seeds at startup"
    finally:
        httpd.server_close()
        httpd.runner.close()
