"""Backgrounded cell execution for exec_peek / exec_interrupt.

`host.exec_background(code)` launches a cell that may run for a long time
(training a model, a long simulation) WITHOUT blocking the agent's turn loop.
It returns an `exec_id` immediately. The agent then:

    host.exec_peek(exec_id) -> read the cell's ACCUMULATED stdout so far,
        without waiting: {status, stdout, done}.
    host.exec_interrupt(exec_id) -> stop it. For a python cell this is a SINGLE
        SIGINT (the worker's one-shot handler keeps
        the kernel alive); it is idempotent.

Each background job owns its OWN kernel subprocess (so a long cell never blocks
the foreground kernel). stdout is streamed live into a thread-safe buffer that
exec_peek reads at any time.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

#: Head cap on what a background cell's buffer retains, matching the worker's
#: own `MAX_OUTPUT` so `exec_peek` and the final response truncate at the same
#: point rather than disagreeing about what the cell printed.
#:
#: The worker now bounds its own stream, so in practice this is a backstop --
#: but it is the buffer's own contract that was missing. `_buf` was an
#: unbounded list appended to per chunk, and `stdout_so_far` re-joined all of
#: it on every peek, so a chatty long-running cell grew the daemon's memory for
#: the life of the job and made each poll more expensive than the last.
MAX_PEEK_CHARS = 1_000_000
_TRUNCATION_MARKER = f"\n...(truncated at {MAX_PEEK_CHARS} characters)"


class _BackgroundJob:
    __slots__ = (
        "exec_id",
        "code",
        "status",
        "_buf",
        "_buf_len",
        "_buf_truncated",
        "_lock",
        "_kernel",
        "_thread",
        "result",
        "error",
        "started_at",
        "ended_at",
        "interrupted",
        "_lifetime",
    )

    def __init__(self, exec_id: str, code: str):
        self.exec_id = exec_id
        self.code = code
        self.status = "running"  # running|done|failed|interrupted
        self._buf: list[str] = []
        self._buf_len = 0
        self._buf_truncated = False
        self._lock = threading.Lock()
        self._kernel: Any = None
        self._thread: threading.Thread | None = None
        self.result: dict | None = None
        self.error: str | None = None
        self.started_at = int(time.time() * 1000)
        self.ended_at: int | None = None
        self.interrupted = False
        self._lifetime: Any = None

    def _on_chunk(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            room = MAX_PEEK_CHARS - self._buf_len
            if room <= 0:
                self._buf_truncated = True
                return
            kept = text[:room]
            self._buf.append(kept)
            self._buf_len += len(kept)
            if len(kept) < len(text):
                self._buf_truncated = True

    def stdout_so_far(self) -> str:
        with self._lock:
            value = "".join(self._buf)
            return value + _TRUNCATION_MARKER if self._buf_truncated else value

    def peek(self) -> dict:
        """Non-blocking snapshot of the running cell."""
        with self._lock:
            stdout = "".join(self._buf)
            if self._buf_truncated:
                stdout += _TRUNCATION_MARKER
            return {
                "exec_id": self.exec_id,
                "status": self.status,
                "done": self.status != "running",
                "stdout": stdout,
                "interrupted": self.interrupted,
                "error": self.error,
                "started_at": self.started_at,
                "ended_at": self.ended_at,
            }


class BackgroundExecutor:
    """Registry of backgrounded cells, wired onto the dispatcher."""

    def __init__(
        self,
        kernel_factory: Any,
        dispatcher: Any,
        *,
        lifetime_factory: Any = None,
    ):
        # kernel_factory -> a fresh Kernel bound to `dispatcher`.
        self._kernel_factory = kernel_factory
        self._dispatcher = dispatcher
        # Optional context-manager factory whose lease spans spawn through the
        # worker thread's final shutdown.  Web Stage 1 uses it to make a
        # background launch atomic against foreground Artifact capture.
        self._lifetime_factory = lifetime_factory
        self._jobs: dict[str, _BackgroundJob] = {}
        self._lock = threading.Lock()
        self._closed = False

    #: Concurrently RUNNING background cells. Each one owns a kernel
    #: subprocess of its own, so this bounds PROCESSES rather than
    #: bookkeeping: a finished job stays in the registry for `exec_peek` and
    #: does not hold a slot. There was no cap at all -- a loop calling
    #: `host.exec_background` forked a worker per iteration until the machine
    #: ran out of pids or memory, and nothing on the path said no.
    MAX_ACTIVE_JOBS = 16

    def _enter_lifetime(self) -> Any:
        if self._lifetime_factory is None:
            return None
        lifetime = self._lifetime_factory()
        enter = getattr(lifetime, "__enter__", None)
        exit_ = getattr(lifetime, "__exit__", None)
        if not callable(enter) or not callable(exit_):
            raise RuntimeError("background execution admission is unavailable")
        enter()
        return lifetime

    @staticmethod
    def _exit_lifetime(job: _BackgroundJob) -> None:
        lifetime = job._lifetime
        job._lifetime = None
        if lifetime is None:
            return
        exit_ = getattr(lifetime, "__exit__", None)
        if not callable(exit_):
            raise RuntimeError("background execution admission is unavailable")
        exit_(None, None, None)

    def launch(self, code: str, origin: str = "agent") -> dict:
        exec_id = f"exec-{uuid.uuid4().hex[:12]}"
        job = _BackgroundJob(exec_id, code)
        # Enter before claiming a process slot.  Foreground capture and this
        # increment are decided under the coordinator's one short lock, so a
        # background worker can never appear in the check/start gap.
        job._lifetime = self._enter_lifetime()
        # Claim the slot BEFORE the kernel exists. The old order checked
        # `_closed`, released the lock, spawned, and registered afterwards --
        # so any number of concurrent launches passed the check together and
        # every one of them spawned. A limit tested there would have been
        # tested against processes that already existed, which is not a limit.
        # Registering first makes the slot count the thing being limited.
        with self._lock:
            if self._closed:
                self._exit_lifetime(job)
                raise RuntimeError("background executor is closed")
            active = sum(1 for j in self._jobs.values() if j.status == "running")
            if active >= self.MAX_ACTIVE_JOBS:
                self._exit_lifetime(job)
                raise RuntimeError(
                    f"{active} background cells are already running (limit "
                    f"{self.MAX_ACTIVE_JOBS}); interrupt one with "
                    f"host.exec_interrupt or wait for it to finish"
                )
            self._jobs[exec_id] = job
        try:
            job._kernel = self._kernel_factory()
        except BaseException:
            # A spawn failure has to give the slot back, or the cap leaks one
            # slot per failure and eventually refuses every launch on a machine
            # that is now perfectly able to serve them.
            with self._lock:
                self._jobs.pop(exec_id, None)
            self._exit_lifetime(job)
            raise
        with self._lock:
            if self._closed:
                # `shutdown()` ran while we were spawning. It walked a job whose
                # `_kernel` was still None, so this worker is ours to stop --
                # nobody else has a handle on it.
                self._jobs.pop(exec_id, None)
                try:
                    job._kernel.shutdown()
                finally:
                    self._exit_lifetime(job)
                    raise RuntimeError("background executor is closed")

        def _run() -> None:
            terminal_status = "failed"
            terminal_error: str | None = None
            terminal_interrupted = False
            terminal_result: dict | None = None
            try:
                res = job._kernel.execute(code, origin=origin, on_chunk=job._on_chunk)
                terminal_result = res
                if res.get("interrupted"):
                    terminal_status = "interrupted"
                    terminal_interrupted = True
                elif res.get("error"):
                    terminal_status = "failed"
                    terminal_error = res.get("error")
                else:
                    terminal_status = "done"
            except BaseException:  # a dead thread must never remain "running"
                # This record is returned directly by exec_peek. Kernel and
                # transport exceptions can contain worker stderr, absolute
                # paths, or provider details, so expose one stable error while
                # still converging KeyboardInterrupt/SystemExit fault paths to
                # a terminal state that releases their process slot.
                terminal_status = "failed"
                terminal_error = "background execution failed"
            finally:
                try:
                    try:
                        job._kernel.shutdown()
                    except BaseException:
                        # A cleanup KeyboardInterrupt/SystemExit happens on this
                        # daemon-owned thread, not at the public interrupt API.
                        # Converge it to a fixed terminal failure and,
                        # critically, continue on to release the admission
                        # lifetime.
                        terminal_status = "failed"
                        terminal_error = "background execution cleanup failed"
                finally:
                    try:
                        self._exit_lifetime(job)
                    except BaseException:
                        # An admission-release failure means the lifecycle can
                        # no longer be trusted. Keep the public record
                        # fail-closed and fixed rather than exposing an
                        # arbitrary exception.
                        terminal_status = "failed"
                        terminal_error = "background execution admission release failed"
                # Until BOTH cleanup boundaries finish, public status remains
                # running. Session shutdown therefore still sees and joins or
                # exact-kills this thread instead of popping its SessionState
                # while the background lifetime is live. Publish the complete
                # terminal snapshot under the job lock, with status last.
                with job._lock:
                    job.result = terminal_result
                    job.error = terminal_error
                    job.interrupted = terminal_interrupted
                    job.ended_at = int(time.time() * 1000)
                    job.status = terminal_status

        try:
            thread = threading.Thread(target=_run, daemon=True)
            # Close the last launch/shutdown window atomically.  If shutdown
            # won before this lock, no worker starts after the executor closed;
            # if start wins, shutdown observes a real thread it can join/kill.
            with self._lock:
                if self._closed:
                    raise RuntimeError("background executor is closed")
                job._thread = thread
                thread.start()
        except BaseException:
            with self._lock:
                self._jobs.pop(exec_id, None)
            try:
                job._kernel.shutdown()
            finally:
                self._exit_lifetime(job)
            raise
        return {"exec_id": exec_id, "status": "running"}

    def _get(self, exec_id: str) -> _BackgroundJob:
        with self._lock:
            job = self._jobs.get(exec_id)
        if job is None:
            raise KeyError(f"no background exec {exec_id!r}")
        return job

    def peek(self, exec_id: str) -> dict:
        return self._get(exec_id).peek()

    def interrupt(self, exec_id: str) -> dict:
        job = self._get(exec_id)
        if job.status != "running":
            return job.peek()  # idempotent: already finished
        # ONE SIGINT — the worker's one-shot handler keeps the kernel alive.
        delivery = job._kernel.interrupt()
        # give the interrupt a beat to unwind and produce the response frame.
        if job._thread is not None:
            job._thread.join(timeout=5.0)
        report = job.peek()
        # `None` (a kernel double, or an older transport) means "no claim
        # either way", not "not delivered" -- inventing a failure out of an
        # absent answer is the same dishonesty pointed the other way.
        if delivery is not None and not delivery:
            # The stop reached nobody. `status` already says "running", but a
            # caller reading that cannot tell "still unwinding" from "this
            # request did nothing and repeating it will do nothing either" --
            # and the sandbox's diagnosis of why went to stderr, where the
            # agent that asked for the stop cannot see it.
            report["interrupt_undelivered"] = (
                delivery.reason or "the stop request did not reach the worker"
            )
        return report

    def list_jobs(self) -> list[dict]:
        with self._lock:
            return [j.peek() for j in self._jobs.values()]

    def shutdown(self, timeout_per_job: float = 5.0) -> int:
        """Interrupt then exact-kill every running background worker."""

        with self._lock:
            self._closed = True
            jobs = list(self._jobs.values())
        stopped = 0
        for job in jobs:
            if job.status != "running":
                continue
            stopped += 1
            kernel = job._kernel
            if kernel is not None:
                try:
                    kernel.interrupt()
                except Exception:  # noqa: BLE001 — advance to the exact hard stop
                    pass
            thread = job._thread
            if thread is not None:
                thread.join(timeout=max(0.0, timeout_per_job))
            # ``thread`` is None during the launch() window between claiming a
            # slot and starting the runner, and ``_kernel`` is None for the part
            # of that window before the spawn returns -- the slot is claimed
            # first, on purpose. A worker that appears after this point is not
            # leaked: launch() re-reads ``_closed`` once it has spawned and
            # shuts down the worker it just created. Re-read ``_kernel`` here
            # rather than reuse the value from before the join, which may have
            # been None then and real now.
            if thread is None or thread.is_alive():
                kernel = job._kernel
                if kernel is not None:
                    try:
                        kernel.kill_worker()
                    except Exception:  # noqa: BLE001 — worker may already be dead
                        pass
                if thread is not None:
                    thread.join(timeout=max(0.0, timeout_per_job))
        return stopped
