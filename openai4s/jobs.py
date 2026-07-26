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

# One implementation of "stop this job", shared with the kernel-side bash
# executor. Two would have disagreed about the case that matters -- the shell
# exits, the work it started does not.
from openai4s.execution.process_group import TERM_GRACE_S as _TERM_GRACE_S
from openai4s.execution.process_group import await_group_exit as _await_group_exit
from openai4s.execution.process_group import group_alive as _group_alive
from openai4s.execution.process_group import signal_group as _signal_group
from openai4s.execution.process_group import stop_process_group as _stop_process_group

#: Per-job captured output cap. Characters, not bytes: `append` measures
#: `len()` on a `str`, and the comment here said bytes for long enough that
#: the two readings of the same number stopped agreeing. The R worker gates
#: on bytes and says bytes; this one counts characters and now says so.
_MAX_OUTPUT = 200_000
#: Prepended, because the tail is what is kept. Without it a truncated log is
#: indistinguishable from a job that printed less than it did.
_TRUNCATION_NOTICE = (
    f"...(earlier output dropped; showing the last {_MAX_OUTPUT} characters)\n"
)
_MAX_JOBS = 200  # registry cap (oldest finished pruned)


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
        self._truncated = False
        self._proc: subprocess.Popen | None = None
        # Read at spawn and kept: once the leader is reaped, `os.getpgid` on
        # its pid raises, and the surviving group becomes unreachable exactly
        # when it most needs signalling.
        self._pgid: int | None = None
        self._lock = threading.Lock()
        # The worker thread running `_run`. `cancel` joins it briefly when the
        # process turns out to have already exited, so it can report the real
        # terminal result `_run` is about to publish rather than a transient
        # `running` or a mislabelled `cancelled`.
        self._thread: threading.Thread | None = None

    def append(self, text: str) -> None:
        with self._lock:
            self._out.append(text)
            # keep bounded
            total = sum(len(x) for x in self._out)
            while total > _MAX_OUTPUT and len(self._out) > 1:
                total -= len(self._out.pop(0))
                self._truncated = True
            # a single line larger than the cap must still be truncated, or the
            # per-job memory bound is defeated by one giant no-newline blob
            if total > _MAX_OUTPUT and len(self._out) == 1:
                self._out[0] = self._out[0][-_MAX_OUTPUT:]
                self._truncated = True

    def output(self) -> str:
        with self._lock:
            value = "".join(self._out)
            if not self._truncated:
                return value
            # The tail is kept on purpose -- for a job log the end is what
            # explains how it finished. But the caller has to be told, or a
            # silently short log reads as a job that simply printed less.
            return _TRUNCATION_NOTICE + value

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
    #: How long `cancel` waits for `_run` to publish the true terminal result of
    #: a job that turned out to have already exited. The process is reaped, so
    #: `_run` only has to finish draining a now-closed stdout pipe; this is a
    #: generous cap on that, not an expected wait.
    _TERMINAL_JOIN_S = 5.0

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
        thread = threading.Thread(
            target=self._run, args=(job,), daemon=True, name=f"os-job-{job.id}"
        )
        job._thread = thread
        thread.start()
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
        # "already exited" means the process was gone before we signalled — it
        # finished on its own, not because we stopped it. Any other success
        # detail ("exited on SIGTERM", "killed") means our signal ended it.
        finished_on_its_own = detail == "already exited"
        if stopped and finished_on_its_own:
            # The process was reaped before our signal did anything, so `_run`
            # is about to publish (or already published) its real terminal exit
            # code. Gating on `job.status` already being terminal was a race:
            # `_run` records asynchronously *after* the process is reaped, so a
            # cancel landing in that window found status still `running`, took
            # the else branch, and stamped `cancelled` over a job that actually
            # succeeded (rc 0). Never write `cancelled` when we did not stop it;
            # join `_run` briefly so we report the true outcome, not a transient
            # `running`.
            worker = job._thread
            if worker is not None and worker is not threading.current_thread():
                worker.join(timeout=self._TERMINAL_JOIN_S)
            with job._lock:
                status = job.status
        else:
            with job._lock:
                if stopped:
                    # We ended it (or it is not terminal yet), which overrides
                    # the `failed` `_run` derives from the signal-killed exit.
                    job.status = "cancelled"
                status = job.status
        if not stopped:
            job.append(f"\n[job] cancel failed: {detail}\n")
            return {
                "ok": False,
                "status": status,
                "error": f"the job is still running: {detail}",
            }
        return {"ok": True, "status": status}

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
            # Evict the oldest TERMINAL job, without disturbing the order of
            # the rest. Re-appending a live job to the end used to move it:
            # `list()` returns `reversed(self._order)`, so a long-running job
            # climbed to the *top* of the Jobs panel and read as the newest
            # submission -- the one entry a user is most likely to trust as
            # "what I just started".
            for index, job_id in enumerate(self._order):
                job = self._jobs.get(job_id)
                if job is None or job.status in ("done", "failed", "cancelled"):
                    self._order.pop(index)
                    self._jobs.pop(job_id, None)
                    break
            else:
                # Every job is live. The registry stays over its cap rather
                # than forgetting a job that is still running.
                return
