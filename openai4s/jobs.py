"""Local compute-jobs manager.

Runs a shell command or Python snippet as a tracked background subprocess so the
UI (Customize → Compute → Jobs) can submit long-running work, watch its status
and output, and cancel it — the local-machine analogue of the reference daemon's
remote compute/jobs. Jobs run in a per-job workspace under the data dir.

Kept intentionally simple + stdlib-only: threads + subprocess.Popen, in-memory
registry (bounded), live output capture.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

_MAX_OUTPUT = 200_000  # per-job captured output cap (bytes)
_MAX_JOBS = 200  # registry cap (oldest finished pruned)
_TERM_GRACE_S = 5.0  # how long a job may take to honour SIGTERM


def _group_alive(pgid: int | None) -> bool:
    """Is anything at all still in the job's process group?

    ``killpg(pgid, 0)`` raises ESRCH only when the group holds no process, so
    this answers for the whole tree rather than for the one pid we happen to
    hold a handle to. A group we are not permitted to signal counts as alive:
    the honest reading of "I cannot tell" is not "it is gone".
    """
    if pgid is None:
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _signal_group(proc: subprocess.Popen, pgid: int | None, sig: int) -> None:
    """Deliver one signal to the whole group, or to the leader if there is none.

    A named indirection rather than an inline call so a test can simulate a
    signal that is accepted and goes nowhere — the "no permission to signal"
    case — without reaching into the `os` module the rest of the process
    shares.
    """
    if pgid is not None:
        os.killpg(pgid, sig)
    else:  # no process group (Windows, or the child already reaped)
        proc.send_signal(sig)


def _await_group_exit(proc: subprocess.Popen, pgid: int | None, timeout: float) -> bool:
    """Wait for the *group* to empty, not for the leader to be reaped.

    ``proc.wait()`` answers about one process. A shell that honours SIGTERM
    while the work it started ignores it satisfies ``wait()`` immediately and
    leaves the job running, which is exactly the reproduction: the leader
    exited on SIGTERM, the child had SIG_IGN installed, and cancel reported
    success. The wait here is also what reaps the leader, so a zombie does not
    keep the group looking populated.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            proc.wait(timeout=0)
        except subprocess.TimeoutExpired:
            pass
        except Exception:  # noqa: BLE001 - another waiter got there first
            pass
        if pgid is None:
            if proc.poll() is not None:
                return True
        elif not _group_alive(pgid):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _stop_process_group(
    proc: subprocess.Popen, pgid: int | None = None
) -> tuple[bool, str]:
    """TERM the job's process group, escalate to KILL, then confirm.

    Returns ``(stopped, detail)``. The confirmation is the point: a cancel
    that reports success without checking is indistinguishable from one that
    worked, and the job carries on holding whatever it holds.

    The group, not the process — the job is a shell, and the work is its
    child. Signalling only the shell leaves the work running, and so does
    *believing* the shell: both `proc.poll()` and `proc.wait()` answer about
    the leader alone.

    ``pgid`` is passed in because it must be read at spawn time. Looking it up
    here fails once the leader has been reaped, which is precisely the case
    where the surviving group most needs signalling.

    Stated limit: once the leader has been reaped, a pgid is a number the OS
    may eventually reuse, so a group probe cannot be perfectly certain it is
    asking about the same job. Nothing short of pidfd closes that window, and
    the previous code had the same exposure through `os.getpgid`. The window is
    the interval between reaping the leader and this call, which is short, and
    erring toward signalling a group that may be gone is safer here than
    reporting a cancellation that did not happen.
    """
    if pgid is None:
        try:
            pgid = os.getpgid(proc.pid)
        except (OSError, AttributeError):
            pgid = None

    if proc.poll() is not None and not _group_alive(pgid):
        # Leader gone *and* group empty. Checking the group as well is what
        # stops `work & exit 0` from being reported as an exited job: the
        # shell finished immediately, and the work it left behind held the
        # job's stdout pipe open while nothing ever signalled it.
        return True, "already exited"

    def _signal(sig: int) -> None:
        _signal_group(proc, pgid, sig)

    try:
        _signal(signal.SIGTERM)
    except ProcessLookupError:
        return True, "already exited"
    except OSError as e:
        return False, f"could not signal the job ({e})"

    if _await_group_exit(proc, pgid, _TERM_GRACE_S):
        return True, "exited on SIGTERM"

    try:
        _signal(signal.SIGKILL)
    except ProcessLookupError:
        return True, "already exited"
    except OSError as e:
        return False, f"ignored SIGTERM and could not be killed ({e})"

    if _await_group_exit(proc, pgid, _TERM_GRACE_S):
        return True, "killed"
    # Unkillable means uninterruptible sleep, almost always blocked I/O.
    # Saying so is far more useful than reporting a cancellation.
    return False, "did not die after SIGKILL (likely blocked in the kernel)"


class Job:
    def __init__(self, kind: str, command: str, cwd: str) -> None:
        self.id = "job-" + uuid.uuid4().hex[:12]
        self.kind = kind  # "bash" | "python"
        self.command = command
        self.cwd = cwd
        self.status = "queued"  # queued|running|done|failed|cancelled
        self.created_at = time.time()
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.exit_code: int | None = None
        self._out: list[str] = []
        self._proc: subprocess.Popen | None = None
        # Read at spawn and kept: once the leader is reaped, `os.getpgid` on
        # its pid raises, and the surviving group becomes unreachable exactly
        # when it most needs signalling.
        self._pgid: int | None = None
        self._lock = threading.Lock()

    def append(self, text: str) -> None:
        with self._lock:
            self._out.append(text)
            # keep bounded
            total = sum(len(x) for x in self._out)
            while total > _MAX_OUTPUT and len(self._out) > 1:
                total -= len(self._out.pop(0))
            # a single line larger than the cap must still be truncated, or the
            # per-job memory bound is defeated by one giant no-newline blob
            if total > _MAX_OUTPUT and len(self._out) == 1:
                self._out[0] = self._out[0][-_MAX_OUTPUT:]

    def output(self) -> str:
        with self._lock:
            return "".join(self._out)

    def to_dict(self, *, with_output: bool = False) -> dict:
        d = {
            "id": self.id,
            "kind": self.kind,
            "command": self.command,
            "status": self.status,
            "exit_code": self.exit_code,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": (
                round((self.finished_at or time.time()) - self.started_at, 1)
                if self.started_at
                else None
            ),
        }
        if with_output:
            d["output"] = self.output()
        return d


class JobManager:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()

    def submit(self, command: str, kind: str = "bash", cwd: str | None = None) -> dict:
        command = (command or "").strip()
        if not command:
            return {"error": "empty command"}
        kind = kind if kind in ("bash", "python") else "bash"
        # Confine the working directory to the jobs root: normalize a caller-supplied
        # cwd and require it to share the root as a common path prefix, so it cannot
        # escape via ".." traversal or an absolute path (no path injection).
        base = os.path.realpath(str(self.root))
        if cwd:
            wd = os.path.normpath(os.path.join(base, cwd))
            if os.path.commonpath((base, wd)) != base:
                return {"error": "cwd escapes the jobs root"}
        else:
            wd = base
        Path(wd).mkdir(parents=True, exist_ok=True)
        job = Job(kind, command, wd)
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._prune_locked()
        threading.Thread(
            target=self._run, args=(job,), daemon=True, name=f"os-job-{job.id}"
        ).start()
        return job.to_dict()

    def _run(self, job: Job) -> None:
        if job.kind == "python":
            argv = [sys.executable, "-u", "-c", job.command]
        else:
            argv = ["bash", "-lc", job.command]
        try:
            with job._lock:
                # Cancelling in the window between submit() and this spawn used
                # to mark the job cancelled and then start the process anyway:
                # the work ran to completion under a `cancelled` label. Claim
                # the transition to `running` and the spawn under one lock so
                # cancel either arrives first and wins outright, or arrives
                # after and has a process to signal.
                if job.status == "cancelled":
                    return
                job.status = "running"
                job.started_at = time.time()
                job._proc = proc = subprocess.Popen(
                    argv,
                    cwd=job.cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    # Its own process group, so cancel can signal the whole
                    # tree. Without this, `bash -lc "python train.py"` gave
                    # terminate() only the shell: bash died, python kept
                    # running and kept the GPU, and the job was reported
                    # cancelled.
                    start_new_session=True,
                )
                try:
                    job._pgid = os.getpgid(proc.pid)
                except (OSError, AttributeError):
                    job._pgid = None
            # Belt and braces for the pre-spawn race: `cancel` claims the
            # cancellation under the same lock this spawn was claimed under, so
            # it cannot land in between — but if it ever does, stop what we
            # started instead of running it under a `cancelled` label.
            with job._lock:
                already_cancelled = job.status == "cancelled"
            if already_cancelled:
                _stop_process_group(proc, job._pgid)
                return
            assert proc.stdout is not None
            for line in proc.stdout:
                job.append(line)
            proc.wait()
            job.exit_code = proc.returncode
            # only claim done/failed if cancel() didn't already win the race
            with job._lock:
                if job.status != "cancelled":
                    job.status = "done" if proc.returncode == 0 else "failed"
        except Exception as e:  # noqa: BLE001
            job.append(f"\n[job error] {e}\n")
            with job._lock:
                if job.status != "cancelled":
                    job.status = "failed"
            job.exit_code = -1
        finally:
            job.finished_at = time.time()

    def cancel(self, job_id: str) -> dict:
        """Stop a job, and report whether it actually stopped.

        This used to write ``cancelled`` first, then attempt ``terminate()``
        and swallow whatever happened — so a process that ignored the signal,
        or one we had no permission to signal, was reported cancelled while it
        carried on running. The status is now written only once the process is
        confirmed gone.

        The pre-spawn case is claimed under a *single* lock hold. Observing
        "nothing to signal", releasing, and reacquiring to write the status let
        `_run` slip between the two: it saw a still-`queued` job, spawned the
        process, and the write that followed labelled a running process
        `cancelled` without ever signalling it.
        """
        job = self._jobs.get(job_id)
        if not job:
            return {"error": "job not found"}
        with job._lock:  # atomic with _run's spawn claim and terminal write
            if job.status in ("done", "failed", "cancelled"):
                return {"ok": True, "status": job.status}
            proc = job._proc
            pgid = job._pgid
            if proc is None:
                # Not spawned yet, and `_run` claims its transition to
                # `running` under this same lock — so it will see this and
                # start nothing.
                job.status = "cancelled"
        if proc is None:
            late = job._proc
            if late is not None:  # a spawn we did not expect: stop it anyway
                _stop_process_group(late, job._pgid)
            return {"ok": True, "status": "cancelled"}

        stopped, detail = _stop_process_group(proc, pgid)
        with job._lock:
            if stopped:
                job.status = "cancelled"
            status = job.status
        if not stopped:
            job.append(f"\n[job] cancel failed: {detail}\n")
            return {
                "ok": False,
                "status": status,
                "error": f"the job is still running: {detail}",
            }
        return {"ok": True, "status": "cancelled"}

    def list(self) -> list[dict]:
        with self._lock:
            ids = list(reversed(self._order))
        return [self._jobs[i].to_dict() for i in ids if i in self._jobs]

    def get(self, job_id: str) -> dict:
        job = self._jobs.get(job_id)
        if not job:
            return {"error": "job not found"}
        return job.to_dict(with_output=True)

    def _prune_locked(self) -> None:
        while len(self._order) > _MAX_JOBS:
            old = self._order.pop(0)
            j = self._jobs.get(old)
            if j and j.status in ("done", "failed", "cancelled"):
                self._jobs.pop(old, None)
            elif j:  # still running — keep it, drop from prune scan
                self._order.append(old)
                break
