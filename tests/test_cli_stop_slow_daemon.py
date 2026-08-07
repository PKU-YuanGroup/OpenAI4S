"""``openai4s stop`` against a daemon that outlives the shutdown grace period.

``cmd_stop`` sent SIGTERM, polled for five seconds, then discarded the poll's
outcome: it cleared the pidfile, printed "daemon stopped" and returned 0
unconditionally. A daemon held past the grace period by an in-flight cell (a
measured single-cell shutdown ran 4.8s) was thereby orphaned — ``status`` and a
second ``stop`` read the missing pidfile as "not running", the port stayed
bound, and the next ``serve`` crashed into it with a bare "Address already in
use" *after* printing its success banner, because the banner used to precede
the bind.

These tests drive the real ``cmd_stop`` against a real child process that
ignores SIGTERM. The state files must outlive the process they describe: on
timeout the pidfile survives, the exit code is non-zero, and ``--force``
escalates to SIGKILL. The poll is the real ``_wait_pid_exit`` shortened via its
keyword parameters, not a stub.
"""

from __future__ import annotations

import importlib
import os
import signal
import socket
import subprocess
import sys
import threading
import time

import pytest

# ``openai4s.cli.__init__`` re-exports a FUNCTION named ``main`` that shadows
# the module of the same name; resolve the module the way the sibling CLI
# tests do.
cli_main = importlib.import_module("openai4s.cli.main")


def _spawn_sigterm_ignoring_child() -> subprocess.Popen:
    """A stand-in for a daemon whose shutdown outlasts the stop grace period.

    Prints ``ready`` only after the SIGTERM handler is installed, so the test
    cannot signal it during the interpreter's default-disposition window.
    """
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal, sys, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "print('ready', flush=True)\n"
            "time.sleep(120)\n",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None and proc.stdout.readline().strip() == "ready"
    return proc


@pytest.fixture
def slow_daemon(monkeypatch):
    """A SIGTERM-ignoring child registered in the pidfile, with a fast poll.

    The reaper thread matters: the child is *our* child, so once SIGKILLed it
    would sit as a zombie — which ``os.kill(pid, 0)`` still counts as alive —
    unless something wait()s it. The real daemon is never the CLI's child, so
    only the test needs this.
    """
    cfg = cli_main.get_config()
    proc = _spawn_sigterm_ignoring_child()
    threading.Thread(target=proc.wait, daemon=True).start()
    cfg.pidfile.write_text(str(proc.pid), "utf-8")

    real_wait = cli_main._wait_pid_exit
    monkeypatch.setattr(
        cli_main,
        "_wait_pid_exit",
        lambda pid: real_wait(pid, attempts=5, interval=0.02),
    )
    try:
        yield cfg, proc
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def test_stop_timeout_keeps_pidfile_and_fails(slow_daemon, capsys):
    cfg, proc = slow_daemon

    rc = cli_main.main(["stop"])

    out, err = capsys.readouterr()
    assert rc == 2
    assert proc.poll() is None, "the child must still be running to prove the timeout"
    assert cfg.pidfile.read_text("utf-8").strip() == str(proc.pid)
    assert "daemon stopped" not in out
    assert "still shutting down" in err
    assert "--force" in err


def test_stop_timeout_leaves_status_and_retry_able_to_see_the_daemon(
    slow_daemon, capsys
):
    """The point of keeping the pidfile: the CLI converges instead of lying.

    After the failed stop, the daemon is still visible; once the process is
    really gone, the next ``stop`` observes that and clears the state files.
    """
    cfg, proc = slow_daemon

    assert cli_main.main(["stop"]) == 2
    proc.kill()
    proc.wait(timeout=10)
    for _ in range(100):  # the reaper thread races us to wait(); give it time
        if not cli_main._pid_alive(proc.pid):
            break
        time.sleep(0.05)

    rc = cli_main.main(["stop"])

    out, _ = capsys.readouterr()
    assert rc == 1
    assert "daemon: not running" in out
    assert not cfg.pidfile.exists()


def test_stop_force_escalates_to_sigkill(slow_daemon, capsys):
    cfg, proc = slow_daemon

    rc = cli_main.main(["stop", "--force"])

    out, err = capsys.readouterr()
    assert rc == 0
    assert "daemon stopped" in out
    assert "still shutting down" not in err
    assert not cfg.pidfile.exists()
    assert proc.poll() is not None, "SIGKILL must actually have taken the child down"


def test_stop_of_a_promptly_exiting_daemon_still_reports_success(monkeypatch, capsys):
    """The happy path the fix must not regress: SIGTERM honoured, exit 0."""
    cfg = cli_main.get_config()
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    threading.Thread(target=proc.wait, daemon=True).start()
    cfg.pidfile.write_text(str(proc.pid), "utf-8")
    try:
        rc = cli_main.main(["stop"])
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)

    assert rc == 0
    assert "daemon stopped" in capsys.readouterr().out
    assert not cfg.pidfile.exists()


def test_serve_bind_failure_prints_no_success_banner(monkeypatch, capsys):
    """``serve`` used to print "listening"/"web UI ready" before the bind, so a
    port collision surfaced as a traceback after a success banner. The banner
    now requires the bind; the collision is one clear error and exit 1."""
    placeholder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    placeholder.bind(("127.0.0.1", 0))
    placeholder.listen(1)
    port = placeholder.getsockname()[1]
    monkeypatch.setenv("OPENAI4S_NO_OPEN", "1")
    # Config.port resolves OPENAI4S_PORT at class-definition time, so setenv
    # after import cannot steer it; patch the live singleton instead.
    cfg = cli_main.get_config()
    monkeypatch.setattr(cfg, "port", port)

    try:
        rc = cli_main.main(["serve", "--no-open"])
    finally:
        placeholder.close()

    out, err = capsys.readouterr()
    assert rc == 1
    assert "listening" not in out
    assert "web UI ready" not in out
    assert "address already in use" in err
    assert f"{port}" in err
    assert not cfg.pidfile.exists(), "a failed bind must not leave daemon state"


def test_stop_survives_the_pid_exiting_between_check_and_signal(monkeypatch, capsys):
    """The aliveness check and the SIGTERM are not atomic; a pid that vanishes
    in between is a completed stop, not a traceback.

    ``_pid_alive`` is forced True so the real ``os.kill`` — aimed at a pid no
    platform allocates — raises the genuine ``ProcessLookupError``.
    """
    cfg = cli_main.get_config()
    cfg.pidfile.write_text("99999999", "utf-8")
    monkeypatch.setattr(cli_main, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(cli_main, "_wait_pid_exit", lambda pid, **kw: True)

    rc = cli_main.main(["stop"])

    assert rc == 0
    assert "daemon stopped" in capsys.readouterr().out
    assert not cfg.pidfile.exists()
