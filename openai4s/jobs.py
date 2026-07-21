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


def _stop_process_group(proc: subprocess.Popen) -> tuple[bool, str]:
    """TERM the job's process group, escalate to KILL, then confirm.

    Returns ``(stopped, detail)``. The confirmation is the point: a cancel
    that reports success without checking is indistinguishable from one that
    worked, and the job carries on holding whatever it holds.

    The group, not the process — the job is a shell, and the work is its
    child. Signalling only the shell leaves the work running.
    """
    if proc.poll() is not None:
        return True, "already exited"
    try:
        pgid = os.getpgid(proc.pid)
    except (OSError, AttributeError):
        pgid = None

    def _signal(sig: int) -> None:
        if pgid is not None:
            os.killpg(pgid, sig)
        else:  # no process group (Windows, or the child already reaped)
            proc.send_signal(sig)

    try:
        _signal(signal.SIGTERM)
    except ProcessLookupError:
        return True, "already exited"
    except OSError as e:
        return False, f"could not signal the job ({e})"

    try:
        proc.wait(timeout=_TERM_GRACE_S)
        return True, "exited on SIGTERM"
    except subprocess.TimeoutExpired:
        pass

    try:
        _signal(signal.SIGKILL)
    except ProcessLookupError:
        return True, "already exited"
    except OSError as e:
        return False, f"ignored SIGTERM and could not be killed ({e})"

    try:
        proc.wait(timeout=_TERM_GRACE_S)
        return True, "killed"
    except subprocess.TimeoutExpired:
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
        """
        job = self._jobs.get(job_id)
        if not job:
            return {"error": "job not found"}
        with job._lock:  # atomic with _run's terminal-status write
            if job.status in ("done", "failed", "cancelled"):
                return {"ok": True, "status": job.status}
            proc = job._proc
        if proc is None:
            # Not spawned yet; _run sees the flag before it starts anything.
            with job._lock:
                job.status = "cancelled"
            return {"ok": True, "status": "cancelled"}

        stopped, detail = _stop_process_group(proc)
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
