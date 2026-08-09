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

    def _short_wait(pid):
        return real_wait(pid, attempts=5, interval=0.02)

    # Kept reachable so a test needing a different budget (the --force
    # escalation's zombie-reap window) can rebuild its own wrapper.
    _short_wait.original = real_wait
    monkeypatch.setattr(cli_main, "_wait_pid_exit", _short_wait)
    try:
        yield cfg, proc
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def slow_daemon_real_wait(module):
    """The pristine ``_wait_pid_exit`` beneath the fixture's short wrapper."""
    return getattr(module._wait_pid_exit, "original", module._wait_pid_exit)


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


def test_stop_force_escalates_to_sigkill(slow_daemon, monkeypatch, capsys):
    cfg, proc = slow_daemon
    # A wider poll than the fixture's 5×0.02s: after SIGKILL the child sits
    # as a zombie — which os.kill(pid, 0) counts as alive — until the reaper
    # thread gets scheduled to wait() it, and on a busy runner that can
    # exceed 0.1s, turning a real escalation into an intermittent "it
    # ignored SIGKILL". 2s gives the reaper a realistic budget; the SIGTERM
    # half still times out (the child ignores it), just a little later.
    original = slow_daemon_real_wait(cli_main)
    monkeypatch.setattr(
        cli_main,
        "_wait_pid_exit",
        lambda pid: original(pid, attempts=100, interval=0.02),
    )

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


def test_sigterm_handler_preserves_state_files_until_teardown_completes():
    """The daemon must not delete its own pidfile at SIGTERM-arrival.

    The old inline handler ran ``_clear_state`` the instant the signal
    landed — before the (possibly >5s) runner/kernel teardown — so a stop
    that timed out told the user to retry against a pidfile the daemon had
    already removed: the retry and ``stop --force`` both saw "not running"
    while the port stayed bound, and SIGKILL was never sent.  State is
    cleared in the serve loop's finally, after teardown.
    """
    cfg = cli_main.get_config()
    cfg.pidfile.write_text(str(os.getpid()), "utf-8")

    with pytest.raises(KeyboardInterrupt):
        cli_main._sigterm_to_keyboard_interrupt(signal.SIGTERM, None)

    assert cfg.pidfile.read_text("utf-8").strip() == str(os.getpid())


def test_serve_pidfile_covers_the_build_window(monkeypatch, capsys):
    """The singleton pidfile must exist during the whole boot, not post-bind.

    Written after ``build_server``, a second ``serve`` racing a slow store
    open/migration passed the already-running check and booted the same data
    dir concurrently, while ``stop``/``status`` called the mid-boot daemon
    "not running".  A failed build still clears the state files.
    """
    import errno as errno_mod

    import openai4s.server as server_pkg

    cfg = cli_main.get_config()
    seen: dict[str, bool] = {}

    def probe(_cfg):
        seen["pidfile_during_build"] = cfg.pidfile.exists()
        raise OSError(errno_mod.EADDRINUSE, "address already in use")

    monkeypatch.setattr(server_pkg, "build_server", probe)
    monkeypatch.setenv("OPENAI4S_NO_OPEN", "1")

    rc = cli_main.main(["serve", "--no-open"])

    out, err = capsys.readouterr()
    assert rc == 1
    assert seen["pidfile_during_build"] is True
    assert "address already in use" in err
    assert "listening" not in out
    assert not cfg.pidfile.exists(), "a failed build must not leave daemon state"


def _failing_build(monkeypatch, exc: OSError):
    import openai4s.server as server_pkg

    monkeypatch.setattr(
        server_pkg, "build_server", lambda cfg: (_ for _ in ()).throw(exc)
    )
    monkeypatch.setenv("OPENAI4S_NO_OPEN", "1")


def test_serve_privileged_port_prints_one_clear_error(monkeypatch, capsys):
    """EACCES on a privileged port gets the same one-line treatment as a
    port collision, naming OPENAI4S_PORT — not a bare traceback."""
    import errno as errno_mod

    cfg = cli_main.get_config()
    monkeypatch.setattr(cfg, "port", 80)
    _failing_build(monkeypatch, OSError(errno_mod.EACCES, "permission denied"))

    rc = cli_main.main(["serve", "--no-open"])

    out, err = capsys.readouterr()
    assert rc == 1
    assert "listening" not in out
    assert "permission denied" in err and "OPENAI4S_PORT" in err
    assert not cfg.pidfile.exists()


def test_serve_unwritable_data_dir_eacces_still_raises(monkeypatch):
    """EACCES with an unprivileged port is not a port problem: build_server
    does more than bind, and a read-only data dir must stay a traceback
    pointing at the real path instead of blaming OPENAI4S_PORT."""
    import errno as errno_mod

    cfg = cli_main.get_config()
    assert cfg.port >= 1024
    _failing_build(monkeypatch, OSError(errno_mod.EACCES, "permission denied"))

    with pytest.raises(OSError):
        cli_main.main(["serve", "--no-open"])
    assert not cfg.pidfile.exists(), "a failed build must not leave daemon state"


def test_serve_unavailable_address_prints_one_clear_error(monkeypatch, capsys):
    """EADDRNOTAVAIL (a mis-set OPENAI4S_HOST) names the env var to fix."""
    import errno as errno_mod

    cfg = cli_main.get_config()
    _failing_build(
        monkeypatch, OSError(errno_mod.EADDRNOTAVAIL, "cannot assign address")
    )

    rc = cli_main.main(["serve", "--no-open"])

    out, err = capsys.readouterr()
    assert rc == 1
    assert "listening" not in out
    assert "OPENAI4S_HOST" in err
    assert not cfg.pidfile.exists()


def test_serve_drives_the_shared_run_server_lifecycle(monkeypatch, capsys):
    """`openai4s serve` and `serve_app` must share one service loop.

    cmd_serve used to inline a copy of serve_app's serve/teardown, so a
    lifecycle fix could land on one path and silently miss the daemon every
    real user runs.  This pins the CLI to gateway.run_server."""
    import openai4s.server as server_pkg

    cfg = cli_main.get_config()
    sentinel = object()
    driven: list[object] = []

    monkeypatch.setattr(server_pkg, "build_server", lambda _cfg: sentinel)
    monkeypatch.setattr(server_pkg, "run_server", driven.append)
    monkeypatch.setenv("OPENAI4S_NO_OPEN", "1")

    rc = cli_main.main(["serve", "--no-open"])

    assert rc == 0
    assert driven == [sentinel]
    assert "listening" in capsys.readouterr().out
    assert not cfg.pidfile.exists(), "state clears after the loop returns"


def test_stop_grace_period_is_the_shared_process_group_budget():
    """The 5s stop grace is TERM_GRACE_S, not a re-derived constant: tuning
    the job-stop budget must tune the daemon stop with it."""
    from openai4s.execution import process_group

    assert cli_main.TERM_GRACE_S is process_group.TERM_GRACE_S
    # The default attempts derive from the shared budget (attempts × interval
    # == TERM_GRACE_S).  A dead pid exercises the default path without
    # sleeping: the first poll observes the exit.
    assert cli_main._wait_pid_exit(99999999) is True
