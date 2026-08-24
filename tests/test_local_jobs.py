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

    # The seam lives with the ladder, which `jobs` and the kernel-side bash
    # executor now share. Patching `openai4s.jobs` would silently stop
    # reaching it -- `stop_process_group` resolves these from its own module.
    monkeypatch.setattr(
        "openai4s.execution.process_group.signal_group",
        lambda proc, pgid, sig: None,
    )
    monkeypatch.setattr("openai4s.execution.process_group.TERM_GRACE_S", 0.2)

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


def test_cancel_does_not_mislabel_a_natural_finish_still_being_recorded(
    manager, tmp_path, monkeypatch
):
    """The narrower window the earlier fix missed.

    `_stop_process_group` returns "already exited" once the process is *reaped*,
    which does not imply `_run` has written its terminal status yet — `_run`
    records asynchronously, after the reap. The earlier guard only preserved the
    natural result when `job.status` was *already* terminal, so a cancel landing
    in the gap (status still `running`) stamped `cancelled` over a job that
    actually exited 0. cancel must never write `cancelled` when it did not stop
    the process; it must wait for `_run` to publish the true outcome.
    """
    from openai4s import jobs as jobs_mod
    from openai4s.jobs import Job

    # A real, already-exited process (rc 0) — nothing here is hypothetical.
    proc = subprocess.Popen(["true"])
    proc.wait()

    job = Job("bash", "true", str(tmp_path))
    job.status = "running"
    job._proc = proc
    job._pgid = None

    # Stand in for `_run`'s still-pending terminal write: it publishes the true
    # exit exactly as `_run` does — only if cancel has not stamped cancelled.
    release = threading.Event()

    def deferred_run():
        release.wait(5.0)
        with job._lock:
            if job.status != "cancelled":
                job.status = "done"
        job.exit_code = 0

    worker = threading.Thread(target=deferred_run, daemon=True)
    job._thread = worker
    worker.start()
    manager._jobs[job.id] = job

    # The process is gone, so the stop helper reports "already exited" while the
    # status is still `running` (the write is pending on `deferred_run`).
    monkeypatch.setattr(
        jobs_mod, "_stop_process_group", lambda p, g=None: (True, "already exited")
    )
    # Let `_run` publish the true result while cancel is waiting on it.
    threading.Timer(0.1, release.set).start()

    out = manager.cancel(job.id)

    assert out["status"] == "done", (
        "cancel mislabelled a job that finished on its own (rc 0) as cancelled "
        "because _run had not yet recorded the terminal status"
    )
    assert job.status == "done"
    assert job.exit_code == 0


def test_a_truncated_job_log_says_so():
    """A job whose output outgrew the cap looked like one that printed less.

    `append` drops from the front to keep the tail -- right for a job log,
    where the end is what explains how it finished -- but it dropped silently.
    Nothing in the result distinguished "this is the whole log" from "this is
    the last 200k characters of it", so a user reading the top of the output
    was reading the middle of the run.

    The notice is prepended rather than appended precisely because the tail is
    what survived: a marker at the end would sit after the last line and imply
    the loss happened there.
    """
    from openai4s.jobs import _MAX_OUTPUT_BYTES, Job

    job = Job("bash", "echo hi", "/tmp")

    job.append("short output\n")
    assert job.output() == "short output\n"
    assert "dropped" not in job.output()

    job.append("A" * (_MAX_OUTPUT_BYTES + 500))
    seen = job.output()
    assert seen.startswith("...(earlier output dropped")
    assert "short output" not in seen
    # Still bounded, and still the tail.
    assert seen.rstrip().endswith("A")
    assert len(seen) <= _MAX_OUTPUT_BYTES + len(_TRUNCATION_NOTICE_LEN_PROBE) + 8


# The cap is bytes now, not characters -- the pipe is read as bytes so that a
# line with no newline in it cannot be allocated whole before being trimmed.
# ASCII above, so the two units coincide and this assertion still measures the
# same thing.
_TRUNCATION_NOTICE_LEN_PROBE = (
    "...(earlier output dropped; showing the last 200000 bytes)\n"
)


def test_pruning_does_not_promote_a_running_job_to_newest():
    """A long-running job climbed to the top of the Jobs panel.

    `_prune_locked` re-appended a still-running oldest job to the end of
    `_order` before breaking, and `list()` returns `reversed(_order)` -- so the
    job that had been running longest was displayed as the most recent
    submission. That is the one row a user is most likely to read as "the thing
    I just started".
    """
    import tempfile
    from pathlib import Path as _Path

    from openai4s.jobs import _MAX_JOBS, Job, JobManager

    manager = JobManager(_Path(tempfile.mkdtemp()))
    live = Job("bash", "sleep", "/tmp")
    live.status = "running"
    with manager._lock:
        manager._jobs[live.id] = live
        manager._order.append(live.id)
        for index in range(_MAX_JOBS + 5):
            done = Job("bash", f"echo {index}", "/tmp")
            done.status = "done"
            manager._jobs[done.id] = done
            manager._order.append(done.id)
        manager._prune_locked()

    listed = [row["id"] for row in manager.list()]
    assert listed, "everything was pruned"
    # The live job was submitted first, so it must be LAST in a newest-first
    # list -- never first.
    assert listed[0] != live.id
    assert live.id in listed, "a running job must never be evicted"
    assert listed[-1] == live.id


# --- one job, one terminal state -------------------------------------------
#
# `_run` stated the precedence rule -- "the deadline is what actually ended
# this process, and a receipt that says somebody cancelled it is the wrong
# account of why the work stopped" -- and `cancel` overwrote it sixty lines
# away. Measured before the fix: `running -> timeout -> cancelled` on 5/5
# trials, with the persisted receipt ending at `cancelled`. The deadline
# account was destroyed every time.
#
# The rule had eight direct writers across `_run`, `_expire`, `cancel` and
# `close`, each deciding for itself whether it was allowed to write. It now has
# one: `Job._finish_locked`, plus `_claim_stop_locked` for the intent, claimed
# before any signal so `_run` publishes the claimer's name rather than deriving
# `failed` from a signal it did not know was sent.


def test_a_cancel_cannot_rewrite_a_timeout_published_while_it_stopped(
    tmp_path, monkeypatch
):
    """Direction A, at the only width it actually has.

    Sleeping past the deadline does not reach it: `cancel` opens with a terminal
    check, so a `timeout` already published sends it home before it can write.
    A first version of this test slept 1.15s, passed against the unfixed code,
    and measured nothing -- the window is between that check and the post-stop
    write, while `_stop_process_group` is running.

    So the interleaving is forced rather than raced for: the stop ladder is
    replaced by one that publishes `timeout` the way `_expire` would, mid-call.
    Everything else is the real manager.
    """
    import openai4s.jobs as jobs_mod

    manager = JobManager(tmp_path)
    try:
        job_id = manager.submit("sleep 30", deadline_s=60.0)["id"]
        # The premise of the whole test is that the worker thread has reached
        # `Popen` -- `_claim_stop_locked` below is only interesting once there
        # is a process to signal. A third of a second was a guess at how long
        # that takes on the machine that wrote it. `_run` sets `status`, `_proc`
        # and `_pgid` under one `job._lock` hold and `to_dict` takes the same
        # lock, so observing "running" from outside proves all three.
        assert _wait_for(lambda: manager.get(job_id)["status"] == "running")
        job = manager._jobs[job_id]

        # The deadline got there first: it claimed and signalled, and `_run`
        # has not published yet. The status is still `running`, so `cancel`'s
        # opening terminal check waves it through -- which is the only way into
        # the window at all.
        with job._lock:
            assert job._claim_stop_locked("timeout") is True
        real_stop = jobs_mod._stop_process_group

        def _stop_then_publish(proc, pgid=None):
            outcome = real_stop(proc, pgid)
            job.finish("timeout")  # what `_run` does with the claim
            return outcome

        monkeypatch.setattr(jobs_mod, "_stop_process_group", _stop_then_publish)

        result = manager.cancel(job_id)
        assert _wait_for(
            lambda: manager.get(job_id)["status"] in {"timeout", "cancelled"}
        )

        assert (
            manager.get(job_id)["status"] == "timeout"
        ), "cancel overwrote a terminal state the deadline had already earned"
        assert result["status"] == "timeout"
    finally:
        manager.close()


def test_an_ordinary_cancel_is_still_cancelled(tmp_path):
    """The refusal must not be an outage.

    `_run` derives `failed` from a signal-killed exit because it does not know a
    signal was sent, and the old unconditional write existed to correct that.
    Claiming the stop first is what preserves the correction without also
    overwriting a terminal state somebody else earned.
    """
    manager = JobManager(tmp_path)
    try:
        job_id = manager.submit("sleep 30", deadline_s=60.0)["id"]
        # "running" is what makes this a cancel of a live process rather than
        # of a job that has not started, which is the path under test.
        assert _wait_for(lambda: manager.get(job_id)["status"] == "running")

        result = manager.cancel(job_id)
        assert _wait_for(lambda: manager.get(job_id)["status"] == "cancelled")

        assert result["status"] == "cancelled"
        assert manager.get(job_id)["status"] == "cancelled"
    finally:
        manager.close()


def test_the_first_claim_wins_and_a_terminal_state_is_final(tmp_path):
    """The primitive itself, at both layers.

    Direction B never crosses an entry point -- `_expire` is a Timer callback --
    so a guard added inside `cancel` could not have caught it. The claim and the
    transition are on `Job` for that reason.
    """
    from openai4s.jobs import Job

    job = Job("bash", "true", str(tmp_path))
    assert job.claim_stop("timeout") is True
    assert job.claim_stop("cancelled") is False, "the second claim overrode the first"
    assert job._stop_reason == "timeout"

    assert job.finish("timeout") == "timeout"
    # Every later transition is refused, and reports what stands.
    for later in ("cancelled", "failed", "done", "abandoned"):
        assert job.finish(later) == "timeout", later
    assert job.status == "timeout"
