"""Cancelling a local background job must actually stop it.

`JobManager` backs the Customize -> Compute -> Jobs panel, which runs a shell
command on the daemon's own machine. Two things made its cancel dishonest:

  * it wrote ``status = "cancelled"`` *before* attempting ``terminate()`` and
    then swallowed any exception, so a process that ignored the signal — or
    one we had no permission to signal — was reported cancelled while it
    carried on running;
  * it spawned ``bash -lc <command>`` in the daemon's own process group, so
    ``terminate()`` reached the shell and nothing else. `bash -lc "python
    train.py"` lost the shell and kept the python, which is the process
    actually holding the GPU.

These use real processes. A mocked ``Popen`` cannot show either bug: the first
needs a process that outlives the signal, and the second needs a real child of
a real shell.
"""
import os
import shlex
import signal
import subprocess
import sys
import threading
import time

import pytest

from openai4s.jobs import JobManager


def _wait_for(predicate, timeout=10.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


@pytest.fixture
def manager(tmp_path):
    return JobManager(tmp_path / "jobs")


def test_a_cancelled_job_really_stops(manager):
    job = manager.submit("sleep 120", kind="bash")
    assert _wait_for(lambda: manager.get(job["id"])["status"] == "running")

    out = manager.cancel(job["id"])
    assert out["ok"] is True
    assert out["status"] == "cancelled"

    proc = manager._jobs[job["id"]]._proc
    assert proc.poll() is not None, "the shell must be gone once cancel returns"


def test_cancel_kills_the_child_the_shell_started(manager):
    """The bug the process group exists for. `terminate()` on the shell alone
    left the real work running, and the job was still reported cancelled."""
    marker = manager.root / "child.pid"
    # The shell backgrounds a child, records its pid, and then waits. Killing
    # only the shell would leave that child alive.
    job = manager.submit(
        f"sleep 120 & echo $! > {marker}; wait",
        kind="bash",
    )
    assert _wait_for(lambda: marker.exists() and marker.read_text().strip())
    child_pid = int(marker.read_text().strip())
    assert _is_alive(child_pid), "the child should be running before we cancel"

    manager.cancel(job["id"])

    assert _wait_for(
        lambda: not _is_alive(child_pid)
    ), "cancel must reach the whole process group, not just the shell"


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_the_job_runs_in_its_own_process_group(manager):
    job = manager.submit("sleep 60", kind="bash")
    assert _wait_for(lambda: manager._jobs[job["id"]]._proc is not None)
    proc = manager._jobs[job["id"]]._proc
    assert os.getpgid(proc.pid) != os.getpgid(0), (
        "sharing the daemon's process group means a group signal would reach "
        "the daemon itself"
    )
    manager.cancel(job["id"])


def test_a_cancel_that_cannot_stop_the_job_reports_failure(manager, monkeypatch):
    """The honest half. Previously this path returned ok:True regardless.

    Nothing survives SIGKILL except uninterruptible I/O, so what is simulated
    is the *delivery* failing — a signal that is accepted and reaches nothing,
    which is the "no permission to signal" case. The liveness probe is real,
    the process is real, and it really is still running when cancel answers.
    """
    job = manager.submit("sleep 120", kind="bash")
    assert _wait_for(lambda: manager._jobs[job["id"]]._proc is not None)

    monkeypatch.setattr("openai4s.jobs._signal_group", lambda proc, pgid, sig: None)
    monkeypatch.setattr("openai4s.jobs._TERM_GRACE_S", 0.2)

    out = manager.cancel(job["id"])
    assert out["ok"] is False
    assert "still running" in out["error"]
    assert manager.get(job["id"])["status"] != "cancelled"

    # Clean up the process the undeliverable signals left running.
    real = manager._jobs[job["id"]]._proc
    try:
        os.killpg(os.getpgid(real.pid), signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass


def test_cancelling_a_finished_job_is_a_no_op(manager):
    job = manager.submit("true", kind="bash")
    assert _wait_for(lambda: manager.get(job["id"])["status"] == "done")
    out = manager.cancel(job["id"])
    assert out["ok"] is True
    assert out["status"] == "done"


# --------------------------------------------------------------------------
# confirming the *group*, not the leader
# --------------------------------------------------------------------------


def test_cancel_confirms_a_child_that_ignores_sigterm(manager, tmp_path):
    """A real process that ignores SIGTERM, started by a shell that does not.

    This is the shape review reproduced: the shell leader honours the signal
    and exits, ``proc.wait()`` returns, and cancel reports success while the
    work carries on. Only a real process can refuse a real signal.
    """
    marker = tmp_path / "stubborn.pid"
    script = tmp_path / "stubborn.py"
    script.write_text(
        "import os, signal, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )
    job = manager.submit(
        f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} "
        f"{shlex.quote(str(marker))} & wait",
        kind="bash",
    )
    assert _wait_for(lambda: marker.exists() and marker.read_text().strip())
    child_pid = int(marker.read_text().strip())
    assert _is_alive(child_pid)

    out = manager.cancel(job["id"])

    assert out["ok"] is True
    assert not _is_alive(child_pid), (
        "the shell exited on SIGTERM but its child did not; cancel must "
        "escalate to the surviving group rather than believe the leader"
    )


def test_cancel_reaches_the_group_when_the_leader_has_already_exited(manager, tmp_path):
    """The leader exits on its own and leaves the work behind.

    ``proc.poll()`` is already non-None when cancel arrives, so the early
    "already exited" return skipped signalling entirely — while the child kept
    the job's stdout pipe open, and the job kept reporting ``running``.
    """
    marker = tmp_path / "orphan.pid"
    script = tmp_path / "orphan.py"
    script.write_text(
        "import os, sys, time\n"
        "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )
    job = manager.submit(
        f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} "
        f"{shlex.quote(str(marker))} & exit 0",
        kind="bash",
    )
    assert _wait_for(lambda: marker.exists() and marker.read_text().strip())
    child_pid = int(marker.read_text().strip())
    proc = manager._jobs[job["id"]]._proc
    assert _wait_for(lambda: proc.poll() is not None), "the shell should be gone"
    assert _is_alive(child_pid)

    out = manager.cancel(job["id"])

    assert out["ok"] is True
    assert not _is_alive(child_pid), "an exited leader is not an exited job"


def test_cancelling_before_the_spawn_never_leaves_a_process_running(manager, tmp_path):
    """The pre-spawn race, stepped deterministically.

    ``cancel`` used to release the job lock after finding no process, and
    reacquire it to write ``cancelled``. ``_run`` slipping between the two saw
    a job that was still ``queued``, spawned it, and the cancel then labelled a
    running process ``cancelled`` without ever signalling it.
    """
    gate = threading.Event()
    run_finished = threading.Event()
    real_run = JobManager._run

    def gated_run(self, job):
        gate.wait(timeout=10)
        try:
            real_run(self, job)
        finally:
            run_finished.set()

    JobManager._run = gated_run
    try:
        marker = tmp_path / "raced.pid"
        script = tmp_path / "raced.py"
        script.write_text(
            "import os, sys, time\n"
            "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
            "time.sleep(120)\n",
            encoding="utf-8",
        )
        submitted = manager.submit(
            f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} "
            f"{shlex.quote(str(marker))}",
            kind="bash",
        )
        job = manager._jobs[submitted["id"]]

        class _SteppedLock:
            """Real mutual exclusion, with one scheduled interleaving.

            The first time the cancelling thread lets go, ``_run`` is released
            and allowed to reach its spawn before cancel continues.
            """

            def __init__(self):
                self._lock = threading.Lock()
                self._stepped = False

            def acquire(self, *a, **k):
                return self._lock.acquire(*a, **k)

            def release(self):
                self._lock.release()

            def __enter__(self):
                self._lock.acquire()
                return self

            def __exit__(self, *exc):
                self._lock.release()
                if not self._stepped and threading.current_thread().name == "canceller":
                    self._stepped = True
                    gate.set()
                    deadline = time.time() + 10
                    while time.time() < deadline:
                        if job._proc is not None or run_finished.is_set():
                            break
                        time.sleep(0.01)
                return False

        job._lock = _SteppedLock()

        answer = {}
        canceller = threading.Thread(
            target=lambda: answer.update(manager.cancel(submitted["id"])),
            name="canceller",
        )
        canceller.start()
        canceller.join(timeout=20)
        gate.set()
        run_finished.wait(timeout=10)

        assert answer.get("status") == "cancelled"
        proc = job._proc
        if proc is not None:
            assert _wait_for(lambda: proc.poll() is not None), (
                "cancel answered 'cancelled' while the process it raced with "
                "kept running"
            )
        if marker.exists() and marker.read_text().strip():
            assert not _is_alive(int(marker.read_text().strip()))
    finally:
        JobManager._run = real_run
        gate.set()


def test_a_normal_job_still_completes_and_captures_output(manager):
    """The process-group change must not disturb output capture."""
    job = manager.submit("echo hello-from-the-job", kind="bash")
    assert _wait_for(lambda: manager.get(job["id"])["status"] == "done")
    detail = manager.get(job["id"])
    assert detail["exit_code"] == 0
    assert "hello-from-the-job" in detail["output"]


def test_cancel_preserves_a_terminal_result_when_the_job_already_finished(
    manager, tmp_path, monkeypatch
):
    """The race review named: a job exits on its own *after* cancel's initial
    check but *before* `_stop_process_group` returns. `_run` records the real
    terminal result during that window, and cancel must not overwrite it with
    `cancelled` — a job that finished was not cancelled."""
    job = manager.submit("sleep 60", kind="bash")
    assert _wait_for(lambda: manager._jobs[job["id"]]._proc is not None)
    j = manager._jobs[job["id"]]

    # Simulate the process finishing on its own during the stop call: `_run`
    # recorded `failed`, and the stop helper reports the group was already gone.
    def already_exited(proc, pgid=None):
        with j._lock:
            j.status = "failed"  # as _run would, on the natural exit
        return True, "already exited"

    monkeypatch.setattr("openai4s.jobs._stop_process_group", already_exited)

    out = manager.cancel(job["id"])

    assert (
        out["status"] == "failed"
    ), "cancel overwrote the real terminal result with 'cancelled'"
    assert manager.get(job["id"])["status"] == "failed"

    # Clean up the real sleep the fake stop did not touch.
    real = j._proc
    try:
        os.killpg(os.getpgid(real.pid), signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass
