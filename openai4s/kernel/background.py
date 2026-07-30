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
        return {
            "exec_id": self.exec_id,
            "status": self.status,
            "done": self.status != "running",
            "stdout": self.stdout_so_far(),
            "interrupted": self.interrupted,
            "error": self.error,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }


class BackgroundExecutor:
    """Registry of backgrounded cells, wired onto the dispatcher."""

    def __init__(self, kernel_factory: Any, dispatcher: Any):
        # kernel_factory -> a fresh Kernel bound to `dispatcher`.
        self._kernel_factory = kernel_factory
        self._dispatcher = dispatcher
        self._jobs: dict[str, _BackgroundJob] = {}
        self._lock = threading.Lock()
        self._closed = False

    def launch(self, code: str, origin: str = "agent") -> dict:
        with self._lock:
            if self._closed:
                raise RuntimeError("background executor is closed")
        exec_id = f"exec-{uuid.uuid4().hex[:12]}"
        job = _BackgroundJob(exec_id, code)
        job._kernel = self._kernel_factory()
        with self._lock:
            if self._closed:
                try:
                    job._kernel.shutdown()
                finally:
                    raise RuntimeError("background executor is closed")
            self._jobs[exec_id] = job

        def _run() -> None:
            try:
                res = job._kernel.execute(code, origin=origin, on_chunk=job._on_chunk)
                job.result = res
                if res.get("interrupted"):
                    job.status = "interrupted"
                    job.interrupted = True
                elif res.get("error"):
                    job.status = "failed"
                    job.error = res.get("error")
                else:
                    job.status = "done"
            except Exception as e:  # noqa: BLE001
                job.status = "failed"
                job.error = str(e)
            finally:
                job.ended_at = int(time.time() * 1000)
                try:
                    job._kernel.shutdown()
                except Exception:  # noqa: BLE001
                    pass

        job._thread = threading.Thread(target=_run, daemon=True)
        job._thread.start()
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
        job._kernel.interrupt()
        # give the interrupt a beat to unwind and produce the response frame.
        if job._thread is not None:
            job._thread.join(timeout=5.0)
        return job.peek()

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
            try:
                job._kernel.interrupt()
            except Exception:  # noqa: BLE001 — advance to the exact hard stop
                pass
            thread = job._thread
            if thread is not None:
                thread.join(timeout=max(0.0, timeout_per_job))
            # ``thread`` is None during the launch() window between registering
            # the job and assigning its thread; the kernel worker already exists
            # (created before registration), so it must still be hard-killed
            # here or it leaks a running background worker past teardown.
            if thread is None or thread.is_alive():
                try:
                    job._kernel.kill_worker()
                except Exception:  # noqa: BLE001 — worker may already be dead
                    pass
                if thread is not None:
                    thread.join(timeout=max(0.0, timeout_per_job))
        return stopped
