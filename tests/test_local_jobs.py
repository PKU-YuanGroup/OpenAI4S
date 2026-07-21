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
import signal
import subprocess
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
    """The honest half. Previously this path returned ok:True regardless."""
    job = manager.submit("sleep 120", kind="bash")
    assert _wait_for(lambda: manager._jobs[job["id"]]._proc is not None)

    def refuses_to_die(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="sleep", timeout=1)

    monkeypatch.setattr(
        manager._jobs[job["id"]]._proc, "wait", refuses_to_die, raising=True
    )

    out = manager.cancel(job["id"])
    assert out["ok"] is False
    assert "still running" in out["error"]
    assert manager.get(job["id"])["status"] != "cancelled"

    # Clean up the process the fake wait() prevented us from reaping.
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


def test_a_normal_job_still_completes_and_captures_output(manager):
    """The process-group change must not disturb output capture."""
    job = manager.submit("echo hello-from-the-job", kind="bash")
    assert _wait_for(lambda: manager.get(job["id"])["status"] == "done")
    detail = manager.get(job["id"])
    assert detail["exit_code"] == 0
    assert "hello-from-the-job" in detail["output"]
