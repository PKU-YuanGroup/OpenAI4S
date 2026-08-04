"""Local compute-jobs manager.

Runs a shell command or Python snippet as a tracked background subprocess so the
UI (Customize → Compute → Jobs) can submit long-running work, watch its status
and output, and cancel it — the local-machine analogue of the reference daemon's
remote compute/jobs. Jobs run in a per-job workspace under the data dir.

Kept intentionally simple + stdlib-only: threads + subprocess.Popen, in-memory
registry (bounded), live output capture.
"""

from __future__ import annotations

import json
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
from openai4s.execution.budget import channel_counters
from openai4s.execution.process_group import TERM_GRACE_S as _TERM_GRACE_S
from openai4s.execution.process_group import await_group_exit as _await_group_exit
from openai4s.execution.process_group import group_alive as _group_alive
from openai4s.execution.process_group import signal_group as _signal_group
from openai4s.execution.process_group import stop_process_group as _stop_process_group
from openai4s.kernel.environment import build_kernel_environment
from openai4s.observability import carry_context

#: How many local jobs may be running or queued at once. Without a ceiling one
#: loop over the route exhausts the host.
MAX_ACTIVE_JOBS = 8

#: Per-job captured output cap, in bytes.
#:
#: It counted characters before, on a text-mode pipe. Two things were wrong with
#: that and only one of them was the unit. `for line in proc.stdout` is
#: `readline()`, which allocates until it finds a newline -- so a job printing a
#: gigabyte with no newline in it had already materialised that gigabyte in the
#: daemon before `append` got the chance to drop any of it. The cap described a
#: buffer that was only ever trimmed *after* the allocation it was supposed to
#: prevent. Reading fixed-size binary chunks bounds what the producer side can
#: ask for at all, and bytes are what a chunk boundary is measured in.
_MAX_OUTPUT_BYTES = 200_000
#: What one `read1` may hand back. The read loop's peak allocation, and the
#: granularity at which a job over its cap starts dropping.
_READ_CHUNK_BYTES = 65_536
#: Prepended, because the tail is what is kept. Without it a truncated log is
#: indistinguishable from a job that printed less than it did.
_TRUNCATION_NOTICE = (
    f"...(earlier output dropped; showing the last {_MAX_OUTPUT_BYTES} bytes)\n"
)
_MAX_JOBS = 200  # registry cap (oldest finished pruned)

#: Wall-clock ceiling on one job, and the ceiling a caller may ask for.
#:
#: The comment that used to sit on `MAX_ACTIVE_JOBS` said a local job is "a real
#: process tree with no deadline", which was accurate: eight of them could hold
#: the machine forever, because the only capacity that was ever released was the
#: one a job chose to release by exiting. The active cap bounds concurrency; it
#: cannot bound duration, and P0-3 asks for both.
DEFAULT_JOB_DEADLINE_S = 3600.0
MAX_JOB_DEADLINE_S = 24 * 3600.0

#: A job that reached one of these is finished and will not change again.
#: `abandoned` is terminal in exactly the same sense, and deliberately distinct
#: from `cancelled`: nobody cancelled it, the daemon that was watching it died.
TERMINAL_STATUSES = frozenset({"done", "failed", "cancelled", "timeout", "abandoned"})

#: What `Job.receipt` writes and `JobManager._adopt_abandoned` may read back.
#: One tuple because the two are one contract read from opposite ends of a
#: daemon restart, and a field present in the reader and absent from the writer
#: is not a bug either side can see: adoption silently substitutes a default and
#: the job comes back subtly wrong. A test walks the reader's `data.get(...)`
#: calls and fails if any key is missing here.
#:
#: `pid` is deliberately absent. It was written on every persist -- a host pid,
#: on disk, under the data dir -- and no reader ever existed: an adopted job is
#: unconditionally marked `abandoned` and never probed.
RECEIPT_FIELDS = (
    "id",
    "kind",
    "command",
    "cwd",
    "status",
    "exit_code",
    "created_at",
    "started_at",
    "finished_at",
    "deadline_s",
)


def _record_diagnostic(exc: BaseException, *, surface: str) -> None:
    """Route a failure to the one operator-side sink, without importing it early.

    `openai4s.server.errors` is the single place that decides what an operator
    record holds -- `redact_text` plus a collapsed home directory -- and a
    second copy of that policy here would keep passing after the real one
    changed. It is imported inside the call because importing it runs
    `openai4s/server/__init__.py`, which pulls the entire gateway: this module
    is also used from the CLI, where nothing should pay for that, and the
    gateway imports this module back. On the daemon the module is already
    loaded by the time a job can fail, so the deferral costs nothing there.
    """
    try:
        from openai4s.server.errors import record_diagnostic

        record_diagnostic(exc, surface=surface)
    except Exception:  # noqa: BLE001 - diagnostics must not fail a job
        pass


class Job:
    def __init__(
        self,
        kind: str,
        command: str,
        cwd: str,
        *,
        deadline_s: float = DEFAULT_JOB_DEADLINE_S,
        job_id: str | None = None,
    ) -> None:
        self.id = job_id or ("job-" + uuid.uuid4().hex[:12])
        self.kind = kind  # "bash" | "python"
        self.command = command
        self.cwd = cwd
        # queued|running|done|failed|cancelled|timeout|abandoned
        self.status = "queued"
        self.deadline_s = deadline_s
        self.created_at = time.time()
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.exit_code: int | None = None
        self._out: list[bytes] = []
        self._truncated = False
        self._proc: subprocess.Popen | None = None
        #: Which authority decided to end this job, claimed BEFORE anything is
        #: signalled, and the terminal name that decision must publish.
        #:
        #: Was a `_deadline_expired` boolean, which could only answer "was it
        #: the deadline", so every other writer had to decide the precedence
        #: question for itself -- and they disagreed. `_run` documented the rule
        #: ("the deadline is what actually ended this process, and a receipt
        #: that says somebody cancelled it is the wrong account of why the work
        #: stopped") and `cancel` then wrote `cancelled` unconditionally after
        #: its stop ladder returned. Same file, one function stating the rule
        #: and another overwriting it.
        #:
        #: First claim wins, so a deadline that fired while a cancel's SIGTERM
        #: was in flight keeps `timeout`.
        self._stop_reason: str | None = None
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
        #: Everything this job ever produced, whether or not it was retained.
        #: `truncated` alone said that *something* was dropped; a reader deciding
        #: whether the tail is enough needs to know how much.
        self._seen_bytes = 0

    def append_bytes(self, chunk: bytes) -> None:
        """Retain the tail of ``chunk``, and count all of it."""
        if not chunk:
            return
        with self._lock:
            # Counted before anything is dropped: accounting that reports only
            # what survived cannot say how much did not.
            self._seen_bytes += len(chunk)
            self._out.append(chunk)
            total = sum(len(part) for part in self._out)
            while total > _MAX_OUTPUT_BYTES and len(self._out) > 1:
                total -= len(self._out.pop(0))
                self._truncated = True
            # One chunk larger than the whole cap still has to be cut, or the
            # bound is defeated by a single oversized read.
            if total > _MAX_OUTPUT_BYTES and len(self._out) == 1:
                self._out[0] = self._out[0][-_MAX_OUTPUT_BYTES:]
                self._truncated = True

    def _finish_locked(self, status: str, *, exit_code: int | None = None) -> str:
        """Publish a terminal status, or keep the one already published.

        The single transition primitive. `status` had eight direct writers
        across `_run`, `_expire`, `cancel`, `close` and the abandoned-receipt
        adoption, each re-deciding under its own lock hold whether it was
        allowed to write -- which is how the precedence rule came to hold in one
        of them and not the others. A terminal state is a terminal state: the
        first one published is the account of what happened.

        Returns whatever is published afterwards, so a caller can report the
        real outcome rather than the one it proposed.
        """
        if self.status in TERMINAL_STATUSES:
            return self.status
        self.status = status
        self.finished_at = time.time()
        if exit_code is not None:
            self.exit_code = exit_code
        return status

    def finish(self, status: str, *, exit_code: int | None = None) -> str:
        with self._lock:
            return self._finish_locked(status, exit_code=exit_code)

    def _claim_stop_locked(self, reason: str) -> bool:
        """Claim the right to end this job, before any signal is sent.

        Three conditions, in this order: it is not already terminal, nobody has
        claimed it, and there is still something to stop. The middle one is the
        precedence rule -- a deadline that fired first keeps `timeout` even
        though the cancel's signal is what the process finally sees.
        """
        if self.status in TERMINAL_STATUSES:
            return False
        if self._stop_reason is not None:
            return False
        self._stop_reason = reason
        return True

    def claim_stop(self, reason: str) -> bool:
        with self._lock:
            return self._claim_stop_locked(reason)

    def append(self, text: str) -> None:
        """Append daemon-authored text to the log, in the buffer's own units."""
        self.append_bytes(text.encode("utf-8", "replace"))

    def output(self) -> str:
        with self._lock:
            raw = b"".join(self._out)
            truncated = self._truncated
        # `errors="replace"` alone is not enough. Dropping whole chunks cuts on
        # a read boundary, which lands mid-character often enough to matter, and
        # a leading replacement character in every truncated log is a corruption
        # the daemon introduced rather than one the job produced. Walk the
        # continuation bytes off the front first; at most three are skipped.
        if truncated:
            start = 0
            while start < len(raw) and start < 3 and (raw[start] & 0xC0) == 0x80:
                start += 1
            raw = raw[start:]
        value = raw.decode("utf-8", "replace")
        if not truncated:
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
            "deadline_s": self.deadline_s,
        }
        with self._lock:
            retained = sum(len(chunk) for chunk in self._out)
            # Same four keys, from the shared definition. `_truncated` stays as
            # the drain loop's own bookkeeping; what goes on the wire is derived
            # from the counts, so the flag and the numbers cannot disagree.
            d.update(channel_counters(seen=self._seen_bytes, retained=retained))
        if with_output:
            d["output"] = self.output()
        return d

    def receipt(self) -> dict:
        """The durable record that lets the next boot name a job this daemon
        did not start. That is the whole definition of the word here.

        Two other things in this tree are called receipts and are not this one:
        `compute_jobs`'s `receipt` column holds a provider handle (a remote pid
        or a sandbox id), and `host/files.py` used the word for an item-count
        report. Both are right in their own domain; only the shared name was
        wrong, and it is the prose that has been corrected rather than a column.

        Deliberately not the output. The log of a job the daemon was watching
        when it died is gone with the pipe that carried it, and writing a
        partial one to disk on every chunk would turn a bounded in-memory
        buffer into an unbounded file.
        """
        values = {
            "id": self.id,
            "kind": self.kind,
            "command": self.command,
            "cwd": self.cwd,
            "status": self.status,
            "exit_code": self.exit_code,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "deadline_s": self.deadline_s,
        }
        return {field: values[field] for field in RECEIPT_FIELDS}


class JobManager:
    #: How long `cancel` waits for `_run` to publish the true terminal result of
    #: a job that turned out to have already exited. The process is reaped, so
    #: `_run` only has to finish draining a now-closed stdout pipe; this is a
    #: generous cap on that, not an expected wait.
    _TERMINAL_JOIN_S = 5.0

    #: How long `close` waits for the worker threads to return after their
    #: process groups have been stopped. They are draining a closed pipe by
    #: then, so this is a cap on a wait that should not happen, not a budget.
    _CLOSE_JOIN_S = 10.0

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._receipts = self.root / "receipts"
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._closed = False
        self._adopt_abandoned()

    # ---------------------------------------------------------------- receipts

    def _adopt_abandoned(self) -> None:
        """Name the jobs the previous daemon was still watching when it died.

        Not revived -- read the status this writes. The process tree a local job
        owned does not survive the daemon in any state worth resuming: it was
        either killed with the session or reparented to init with its stdout
        pipe closed, and in both cases nothing is left to attach to. What was
        wrong before was quieter than a wrong resume: the registry was purely
        in-memory, so a restart made those jobs *disappear*, and a user who
        submitted a four-hour run came back to an empty Jobs panel with no way
        to tell "it finished and was pruned" from "the daemon died holding it".

        `abandoned` is its own terminal state for the reason P0-4 gives: calling
        it `failed` claims the job's own command failed, and calling it
        `cancelled` claims somebody meant to stop it. Neither happened.
        """
        try:
            entries = sorted(self._receipts.glob("*.json"))
        except OSError:
            return
        for path in entries:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict) or not data.get("id"):
                continue
            status = str(data.get("status") or "")
            if status in TERMINAL_STATUSES and status != "abandoned":
                # Finished cleanly under the previous daemon. Its receipt has
                # done its job; keeping it would grow the directory without
                # bound for jobs nobody can ask about any more.
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
            job = Job(
                str(data.get("kind") or "bash"),
                str(data.get("command") or ""),
                str(data.get("cwd") or str(self.root)),
                deadline_s=float(data.get("deadline_s") or DEFAULT_JOB_DEADLINE_S),
                job_id=str(data["id"]),
            )
            job.status = "abandoned"
            job.created_at = float(data.get("created_at") or job.created_at)
            started = data.get("started_at")
            job.started_at = float(started) if started is not None else None
            job.finished_at = float(data.get("finished_at") or time.time())
            job.append(
                "\n[job] the daemon that was running this job exited before it "
                "finished; its output was not retained.\n"
            )
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._persist(job)

    def _persist(self, job: Job) -> None:
        """Write the job's receipt, atomically, best-effort.

        Best-effort on purpose: a receipt is a courtesy to the next boot, and a
        full disk must not be able to fail a job that is otherwise running fine.
        """
        try:
            self._receipts.mkdir(parents=True, exist_ok=True)
            target = self._receipts / f"{job.id}.json"
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(job.receipt(), ensure_ascii=False), encoding="utf-8"
            )
            os.replace(tmp, target)
        except OSError:
            pass

    def submit(
        self,
        command: str,
        kind: str = "bash",
        cwd: str | None = None,
        *,
        deadline_s: float | None = None,
    ) -> dict:
        command = (command or "").strip()
        if not command:
            return {"error": "empty command"}
        kind = kind if kind in ("bash", "python") else "bash"
        try:
            deadline = float(
                DEFAULT_JOB_DEADLINE_S if deadline_s is None else deadline_s
            )
        except (TypeError, ValueError):
            return {"error": "deadline_s must be a number", "code": "job_bad_deadline"}
        if deadline <= 0 or deadline > MAX_JOB_DEADLINE_S:
            return {
                "error": (
                    f"deadline_s must be between 0 and {MAX_JOB_DEADLINE_S:g} seconds"
                ),
                "code": "job_bad_deadline",
            }
        # Confine the working directory to the jobs root. `normpath` is lexical --
        # it resolves ".." in the string and knows nothing about symlinks -- so a
        # link created inside the root passed this test and the process then ran
        # outside it. `realpath` resolves the link first, which is what the
        # `commonpath` comparison has to be made against.
        base = os.path.realpath(str(self.root))
        if cwd:
            candidate = os.path.join(base, cwd)
            # Resolve and check BEFORE creating anything. This used to
            # `mkdir(parents=True)` on the candidate's parent first, so a cwd
            # that was then correctly refused had already created directories
            # wherever it pointed: the refusal was real and the side effect
            # happened anyway. `realpath` does not need the path to exist, so
            # nothing was gained by making it first.
            wd = os.path.realpath(candidate)
            if wd != base and os.path.commonpath((base, wd)) != base:
                return {"error": "cwd escapes the jobs root"}
        else:
            wd = base
        try:
            Path(wd).mkdir(parents=True, exist_ok=True)
        except OSError as error:
            # An unwritable target used to raise straight out of `submit`,
            # which reaches the caller as a crash rather than as the error
            # dict every other refusal here returns.
            #
            # The message is the daemon's own, not the OSError's. `strerror`
            # here arrives with the absolute path it failed on -- the data dir,
            # so the account's username -- and this dict is returned to a client
            # over HTTP. The original goes to the operator-side diagnostic,
            # which is what a support ticket quotes.
            _record_diagnostic(error, surface="jobs:submit")
            return {
                "error": "could not create the job working directory",
                "code": "job_workspace_unavailable",
            }
        job = Job(kind, command, wd, deadline_s=deadline)
        with self._lock:
            if self._closed:
                return {
                    "error": "the daemon is shutting down and is not accepting jobs",
                    "code": "job_manager_closed",
                }
            # Claimed inside the lock and before the Thread exists. Nothing
            # counted running jobs at all, so a loop over `POST /compute/jobs`
            # spawned a thread and a process per call with no ceiling. A check
            # made after the spawn is a report, not a limit.
            active = sum(
                1
                for existing in self._jobs.values()
                if existing.status in ("queued", "running")
            )
            if active >= MAX_ACTIVE_JOBS:
                return {
                    "error": (
                        f"{active} local jobs are already running; the limit is "
                        f"{MAX_ACTIVE_JOBS}. Wait for one to finish or cancel it."
                    ),
                    "code": "job_capacity",
                }
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._prune_locked()
        self._persist(job)
        thread = threading.Thread(
            # Carries the caller's correlation id: a local job is started on
            # behalf of one request or one cell, and its failures are read
            # alongside that request's.
            target=carry_context(self._run),
            args=(job,),
            daemon=True,
            name=f"os-job-{job.id}",
        )
        job._thread = thread
        thread.start()
        return job.to_dict()

    def _run(self, job: Job) -> None:
        if job.kind == "python":
            argv = [sys.executable, "-u", "-c", job.command]
        else:
            # `-c`, not `-lc`. A login shell sources the user's profile, so what a
            # job does -- and what its log contains -- depended on a dotfile the
            # daemon never saw and cannot reason about.
            argv = ["bash", "-c", job.command]
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
                    # Rebuilt from the strict allowlist, not inherited. With no
                    # `env=` the child got the daemon's environment verbatim, so
                    # every provider API key the daemon holds was handed to every
                    # local job. The project already owns this function and calls
                    # it from the kernel manager, the dynamic tools and preinstall
                    # -- it was simply never called from here, while the
                    # architecture doc claimed child environments are rebuilt
                    # rather than copied.
                    env=build_kernel_environment(cwd=job.cwd),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    # Binary and unbuffered on this side. `text=True,
                    # bufsize=1` made the read below `readline()`, which
                    # allocates until it finds a newline: the per-job cap was
                    # applied *after* an unbounded allocation the cap existed
                    # to prevent. `read1` on a raw pipe returns whatever has
                    # arrived, never more than it is asked for.
                    bufsize=0,
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
            # Armed only once the process exists, so the timer can never fire
            # against a `_proc` that is still None. Daemon, because a deadline
            # that keeps the interpreter alive is a worse bug than a deadline
            # that does not fire during shutdown -- `close` stops the group
            # anyway.
            timer = threading.Timer(job.deadline_s, self._expire, args=(job,))
            timer.daemon = True
            timer.start()
            try:
                assert proc.stdout is not None
                # `os.read` rather than any file-object method: it is one
                # `read(2)` of at most this many bytes and it returns what has
                # arrived, so neither the buffering mode of the wrapper nor the
                # presence of a newline can change how much is allocated. A
                # buffered `.read(n)` would block for the full n, and
                # `readline()` -- what this used to be -- has no bound at all.
                fd = proc.stdout.fileno()
                while True:
                    chunk = os.read(fd, _READ_CHUNK_BYTES)
                    if not chunk:
                        break
                    job.append_bytes(chunk)
                proc.wait()
            finally:
                timer.cancel()
            # One lock hold, one transition. Whoever claimed the stop names
            # the terminal state; nobody claimed it means the process ended on
            # its own terms.
            with job._lock:
                reason = job._stop_reason
                natural = "done" if proc.returncode == 0 else "failed"
                published = job._finish_locked(
                    reason or natural, exit_code=proc.returncode
                )
                expired = published == "timeout"
            # Outside the lock. `append` takes `job._lock` itself and
            # `threading.Lock` is not reentrant, so writing this notice from
            # inside the block above deadlocked the worker against itself --
            # and, because `to_dict` wants the same lock, against every reader
            # of the Jobs panel too. The status is already published; the
            # notice is only the human-readable half of it.
            if expired:
                job.append(
                    f"\n[job] stopped: the {job.deadline_s:g}s deadline expired.\n"
                )
        except Exception as error:  # noqa: BLE001
            # The exception text is not the user's. An `OSError` from a spawn
            # quotes the argv it tried to run and a `PermissionError` names an
            # absolute path, and this log is served by `GET /compute/jobs/<id>`.
            # The original goes to the operator-side diagnostic instead, where
            # it is redacted once and paired with this job's id.
            _record_diagnostic(error, surface=f"jobs:_run:{job.id}")
            job.append(
                "\n[job error] the job could not be run; the daemon diagnostics "
                f"record the reason under job {job.id}.\n"
            )
            # Refuses over every terminal state, not just `cancelled`:
            # `abandoned` and `timeout` are equally somebody else's account.
            job.finish("failed", exit_code=-1)
        finally:
            job.finished_at = time.time()
            self._persist(job)

    def _expire(self, job: Job) -> None:
        """Stop a job that outlived its deadline, and say so.

        The flag is claimed under the job lock before anything is signalled, so
        `_run` cannot publish `done` in the window between the decision and the
        signal and leave a killed process reported as a success.
        """
        with job._lock:
            # The claim is the guard. `status != "running"` alone let the timer
            # arm itself against a process that had already exited -- direction
            # B, which never crosses an entry point, so no check in `cancel`
            # could have caught it.
            if job._proc is None or not job._claim_stop_locked("timeout"):
                return
            proc, pgid = job._proc, job._pgid
        _stop_process_group(proc, pgid)

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
            if job.status in TERMINAL_STATUSES:
                return {"ok": True, "status": job.status}
            proc = job._proc
            pgid = job._pgid
            if proc is None:
                # Not spawned yet, and `_run` claims its transition to
                # `running` under this same lock — so it will see this and
                # start nothing.
                job._finish_locked("cancelled")
            else:
                # Claimed BEFORE the signal, for the same reason `_expire`
                # claims: `_run` derives `failed` from a signal-killed exit
                # because it does not know a signal was sent. Overriding that
                # afterwards is what the old unconditional write did, and it
                # could not tell `failed` (a derivation) from `timeout`
                # (another authority's verdict). Claiming first makes `_run`
                # publish `cancelled` itself, so there is nothing to override.
                job._claim_stop_locked("cancelled")
        if proc is None:
            late = job._proc
            if late is not None:  # a spawn we did not expect: stop it anyway
                _stop_process_group(late, job._pgid)
            self._persist(job)
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
                    # Overriding the `failed` that `_run` derives from a
                    # signal-killed exit is the point; overriding `timeout` was
                    # not, and this wrote unconditionally. `_finish_locked`
                    # keeps whatever terminal state is already published, and
                    # the deadline publishes one only when it claimed the stop
                    # first.
                    job._finish_locked("cancelled")
                status = job.status
        if not stopped:
            job.append(f"\n[job] cancel failed: {detail}\n")
            self._persist(job)
            return {
                "ok": False,
                "status": status,
                "error": f"the job is still running: {detail}",
            }
        self._persist(job)
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

    def close(self) -> dict:
        """Stop every live job, wait for its worker, and refuse new ones.

        Owned by the server rather than by the process exiting, which is what
        P0-3 asks for and what was missing: `JobManager` had no close at all.
        The threads are daemon threads and the jobs are in their own process
        groups, so nothing here was reaped on shutdown -- the interpreter simply
        stopped, and whatever `bash -c` had started went on running, reparented,
        with its stdout pipe closed and no record that it existed.

        Idempotent, and safe to call from `server_close` on the way out of a
        failed start: a manager that never ran any job closes to an empty
        report rather than raising.
        """
        with self._lock:
            if self._closed:
                return {"ok": True, "closed": True, "stopped": [], "still_alive": []}
            self._closed = True
            jobs = [self._jobs[i] for i in self._order if i in self._jobs]

        stopped: list[str] = []
        still_alive: list[str] = []
        for job in jobs:
            with job._lock:
                if job.status in TERMINAL_STATUSES:
                    continue
                proc, pgid = job._proc, job._pgid
                if proc is None:
                    # Never spawned. `_run` claims its transition to `running`
                    # under this same lock, so it will see this and start
                    # nothing -- the same interlock `cancel` relies on.
                    job._finish_locked("cancelled")
            if proc is None:
                self._persist(job)
                stopped.append(job.id)
                continue
            ok, _detail = _stop_process_group(proc, pgid)
            with job._lock:
                # Already correct; routed through the primitive so the rule has
                # one implementation rather than four that happen to agree.
                job._finish_locked("cancelled" if ok else "abandoned")
            (stopped if ok else still_alive).append(job.id)

        # Join after signalling, not before: a worker blocked on `read1` returns
        # when the pipe closes, and the pipe closes when the group dies. Joining
        # first would wait the full budget for every job and then still have to
        # signal them.
        deadline = time.monotonic() + self._CLOSE_JOIN_S
        for job in jobs:
            worker = job._thread
            if worker is None or worker is threading.current_thread():
                continue
            worker.join(max(0.0, deadline - time.monotonic()))
            if worker.is_alive() and job.id not in still_alive:
                still_alive.append(job.id)
        for job in jobs:
            self._persist(job)
        return {
            "ok": not still_alive,
            "closed": True,
            "stopped": stopped,
            "still_alive": still_alive,
        }

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
                if job is None or job.status in TERMINAL_STATUSES:
                    self._order.pop(index)
                    self._jobs.pop(job_id, None)
                    break
            else:
                # Every job is live. The registry stays over its cap rather
                # than forgetting a job that is still running.
                return
