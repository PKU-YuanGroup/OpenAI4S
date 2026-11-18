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

#: Process-wide exec ids whose worker this process still owns. Slot claim
#: inserts; publishing a terminal state, or failing the launch, removes.
#: A non-terminal receipt whose id is absent reads as ``outcome_unknown``.
#: That derivation does not rewrite the row.
_live_guard = threading.Lock()
_live_exec_ids: set[str] = set()

#: At most one post-terminal receipt cleanup per process per this interval.
#: The stamp is the last attempt, successful or not, so a failing pass does
#: not retry on every later finish. A clock that moves backwards is due.
PRUNE_INTERVAL_MS = 10 * 60 * 1000
_prune_guard = threading.Lock()
_receipt_prune_last_ms: int | None = None


def background_job_is_live(exec_id: str) -> bool:
    """True when this process still holds ``exec_id``."""

    with _live_guard:
        return str(exec_id) in _live_exec_ids


def _claim_live(exec_id: str) -> None:
    with _live_guard:
        _live_exec_ids.add(str(exec_id))


def _forget_live(exec_id: str) -> None:
    with _live_guard:
        _live_exec_ids.discard(str(exec_id))


def _reserve_receipt_prune(now: int) -> bool:
    """Return whether this process may run receipt cleanup at ``now``."""

    global _receipt_prune_last_ms
    with _prune_guard:
        last = _receipt_prune_last_ms
        if last is not None and 0 <= now - last < PRUNE_INTERVAL_MS:
            return False
        _receipt_prune_last_ms = now
        return True


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
        "persistent",
        "receipt_degraded",
        "_buf_bytes",
        "_flushed_bytes",
        "_last_flush_ms",
        "_flush_lock",
        "_flush_timer",
        "turn_id",
        "stop_requested",
    )

    def __init__(self, exec_id: str, code: str, *, persistent: bool = False):
        self.exec_id = exec_id
        self.code = code
        self.status = "running"  # running|done|failed|interrupted
        self._buf: list[str] = []
        self._buf_len = 0
        self._buf_truncated = False
        self._buf_bytes = 0
        self._flushed_bytes = 0
        self._lock = threading.Lock()
        self._kernel: Any = None
        self._thread: threading.Thread | None = None
        self.result: dict | None = None
        self.error: str | None = None
        self.started_at = int(time.time() * 1000)
        self._last_flush_ms = self.started_at
        # Orders the chunk thread and the flush timer; see `_maybe_flush`.
        self._flush_lock = threading.Lock()
        self._flush_timer: threading.Timer | None = None
        self.ended_at: int | None = None
        self.interrupted = False
        self._lifetime: Any = None
        self.persistent = persistent
        self.receipt_degraded = False
        # The agent turn that launched this cell, so stopping that turn can
        # stop it too; None for a user/REPL launch, which no turn owns.
        self.turn_id: str | None = None
        self.stop_requested = False

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
            self._buf_bytes += len(kept.encode("utf-8"))
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
            snapshot = {
                "exec_id": self.exec_id,
                "status": self.status,
                "done": self.status != "running",
                "stdout": stdout,
                "interrupted": self.interrupted,
                "error": self.error,
                "started_at": self.started_at,
                "ended_at": self.ended_at,
                "persistent": self.persistent,
            }
            if self.receipt_degraded:
                # A receipt write failed: after a restart the durable copy
                # may be older than what this peek shows.
                snapshot["receipt_degraded"] = True
            return snapshot


class BackgroundExecutor:
    """Registry of backgrounded cells, wired onto the dispatcher."""

    def __init__(
        self,
        kernel_factory: Any,
        dispatcher: Any,
        *,
        lifetime_factory: Any = None,
        receipts: Any = None,
        clock_ms: Any = None,
    ):
        # kernel_factory -> a fresh Kernel bound to `dispatcher`.
        self._kernel_factory = kernel_factory
        self._dispatcher = dispatcher
        # Optional context-manager factory whose lease spans spawn through the
        # worker thread's final shutdown.  Web Stage 1 uses it to make a
        # background launch atomic against foreground Artifact capture.
        self._lifetime_factory = lifetime_factory
        # Bound Web receipt adapter. None keeps the job in process memory.
        self.receipts = receipts
        # Milliseconds. Tests inject this so a rate-limited prune does not sleep.
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
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
    #: Persist the stdout head once this many new UTF-8 bytes have arrived,
    #: or once this long has passed with anything still unflushed. The first
    #: byte waits for one of those; ``started_at`` is the initial mark.
    FLUSH_BYTES = 16 * 1024
    FLUSH_INTERVAL_MS = 1000

    def _enter_lifetime(self, exec_id: str | None = None) -> Any:
        if self._lifetime_factory is None:
            return None
        lifetime = self._lifetime_factory()
        # A lifetime that records what the job produced (the Web session's
        # capture of the files it wrote) needs to know which job that is.
        bind = getattr(lifetime, "bind_background_exec", None)
        if exec_id and callable(bind):
            bind(exec_id)
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

    def launch(
        self, code: str, origin: str = "agent", turn_id: str | None = None
    ) -> dict:
        exec_id = f"exec-{uuid.uuid4().hex[:12]}"
        persistent = self.receipts is not None
        job = _BackgroundJob(exec_id, code, persistent=persistent)
        job.turn_id = str(turn_id) if turn_id else None
        # Enter before claiming a process slot.  Foreground capture and this
        # increment are decided under the coordinator's one short lock, so a
        # background worker can never appear in the check/start gap.
        job._lifetime = self._enter_lifetime(exec_id)
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
            _claim_live(exec_id)
        if self.receipts is not None:
            try:
                self.receipts.begin(exec_id=exec_id, code=code, origin=origin)
            except Exception:
                with self._lock:
                    self._jobs.pop(exec_id, None)
                _forget_live(exec_id)
                self._exit_lifetime(job)
                raise RuntimeError(
                    "background receipt could not be recorded; refusing to start"
                ) from None
            except BaseException:
                with self._lock:
                    self._jobs.pop(exec_id, None)
                _forget_live(exec_id)
                self._exit_lifetime(job)
                raise
        try:
            job._kernel = self._kernel_factory()
        except BaseException:
            # A spawn failure has to give the slot back, or the cap leaks one
            # slot per failure and eventually refuses every launch on a machine
            # that is now perfectly able to serve them.
            with self._lock:
                self._jobs.pop(exec_id, None)
            try:
                self._exit_lifetime(job)
            finally:
                self._record_launch_failed(
                    exec_id, "background kernel could not be created"
                )
            raise
        self._note_env_generation(job)
        with self._lock:
            if self._closed:
                # `shutdown()` ran while we were spawning. It walked a job whose
                # `_kernel` was still None, so this worker is ours to stop --
                # nobody else has a handle on it.
                self._jobs.pop(exec_id, None)
                try:
                    try:
                        job._kernel.shutdown()
                    finally:
                        self._exit_lifetime(job)
                finally:
                    # A lifetime-exit error must not skip the launch_failed
                    # receipt. The explicit raise then replaces that error,
                    # the same way an egress refusal keeps its own exception.
                    self._record_launch_failed(exec_id, "background executor is closed")
                    raise RuntimeError("background executor is closed")

        from openai4s.egress import (
            EgressBoundaryUnavailable,
            cell_admission_refusal,
            egress_mode,
        )

        if egress_mode() == "allowlist":
            try:
                posture = getattr(job._kernel, "sandbox_status", None)
            except Exception:  # noqa: BLE001 - missing posture fails closed
                posture = None
            decision = cell_admission_refusal(posture)
            if decision is not None:
                code_name = str(decision.get("code") or "egress_boundary_unavailable")
                reason = str(decision.get("reason") or "")
                with self._lock:
                    self._jobs.pop(exec_id, None)
                try:
                    try:
                        job._kernel.shutdown()
                    finally:
                        self._exit_lifetime(job)
                finally:
                    self._record_launch_failed(exec_id, f"{code_name}: {reason}")
                    raise EgressBoundaryUnavailable(decision)

        def _run() -> None:
            terminal_status = "failed"
            terminal_error: str | None = None
            terminal_interrupted = False
            terminal_result: dict | None = None

            def _chunk(text: str) -> None:
                job._on_chunk(text)
                self._maybe_flush(job)

            try:
                res = job._kernel.execute(code, origin=origin, on_chunk=_chunk)
                terminal_result = res
                if res.get("interrupted"):
                    terminal_status = "interrupted"
                    terminal_interrupted = True
                elif res.get("error"):
                    terminal_status = "failed"
                    terminal_error = res.get("error")
                else:
                    terminal_status = "done"
            except EgressBoundaryUnavailable as exc:
                terminal_status = "failed"
                terminal_error = f"{exc.code}: {exc.decision.get('reason') or ''}"
            except BaseException:  # a dead thread must never remain "running"
                # This record is returned directly by exec_peek. Kernel and
                # transport exceptions can contain worker stderr, absolute
                # paths, or provider details, so expose one stable error while
                # still converging KeyboardInterrupt/SystemExit fault paths to
                # a terminal state that releases their process slot.
                if job.stop_requested:
                    # Stopping the turn killed a worker that ignored SIGINT:
                    # that is the stop working, not the cell failing.
                    terminal_status = "interrupted"
                    terminal_interrupted = True
                else:
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
                # Public completion also waits for the terminal receipt. If
                # its SQLite write is waiting on the Store lock, shutdown
                # must still see a running job and join its daemon thread;
                # publishing completion first let normal exit lose the write.
                ended_at = int(time.time() * 1000)
                try:
                    self._finish_receipt(
                        job,
                        status=terminal_status,
                        error=terminal_error,
                        interrupted=terminal_interrupted,
                        ended_at=ended_at,
                    )
                finally:
                    # Never hold the snapshot lock while writing a receipt:
                    # peek stays non-blocking even when SQLite is busy. A
                    # failed receipt still publishes the execution outcome
                    # with receipt_degraded, rather than staying running.
                    with job._lock:
                        job.result = terminal_result
                        job.error = terminal_error
                        job.interrupted = terminal_interrupted
                        job.ended_at = ended_at
                        job.status = terminal_status

        try:
            thread = threading.Thread(target=_run, daemon=True)
            # Close the last launch/shutdown window atomically.  If shutdown
            # won before this lock, no worker starts after the executor closed;
            # if start wins, shutdown observes a real thread it can join/kill.
            with self._lock:
                if self._closed:
                    raise RuntimeError("background executor is closed")
                if job.stop_requested:
                    # Its turn was stopped while the worker was spawning.
                    raise RuntimeError("background execution stopped with its turn")
                job._thread = thread
                thread.start()
        except BaseException:
            with self._lock:
                self._jobs.pop(exec_id, None)
            try:
                try:
                    job._kernel.shutdown()
                finally:
                    self._exit_lifetime(job)
            finally:
                self._record_launch_failed(
                    exec_id, "background worker thread could not be started"
                )
            raise
        self._mark_running(job)
        return {
            "exec_id": exec_id,
            "status": "running",
            "persistent": persistent,
        }

    def _record_launch_failed(self, exec_id: str, error: str) -> None:
        receipts = self.receipts
        try:
            if receipts is None:
                return
            try:
                receipts.finish(
                    exec_id,
                    status="launch_failed",
                    error=error,
                    interrupted=False,
                    ended_at=int(self._clock_ms()),
                    output="",
                    truncated=False,
                )
            except Exception:
                return
        finally:
            # After the terminal write, as in `_finish_receipt`: a reader that
            # finds the job gone from the live set must find the terminal row.
            _forget_live(exec_id)
        self._prune_after_terminal()

    def _prune_after_terminal(self) -> None:
        """Clean receipts after a terminal write, at most once per interval.

        The job's terminal state is already recorded, so a cleanup failure
        says nothing about that receipt: it is swallowed, never raised to the
        caller, and never marks the job `receipt_degraded`.
        """

        receipts = self.receipts
        if receipts is None:
            return
        now = int(self._clock_ms())
        if not _reserve_receipt_prune(now):
            return
        try:
            receipts.prune(now)
        except Exception:  # noqa: BLE001 - cleanup is best effort
            return

    def _mark_running(self, job: _BackgroundJob) -> None:
        receipts = self.receipts
        if receipts is None:
            return
        try:
            receipts.mark_running(job.exec_id, job.started_at)
        except Exception:
            job.receipt_degraded = True

    def _note_env_generation(self, job: _BackgroundJob) -> None:
        receipts = self.receipts
        kernel = job._kernel
        if receipts is None or kernel is None:
            return
        generation: str | None = None
        for name in ("authorization_generation", "generation_id", "env_generation"):
            value = getattr(kernel, name, None)
            if isinstance(value, str) and value.strip():
                generation = value.strip()
                break
        if generation is None:
            return
        note = getattr(receipts, "note_env_generation", None)
        if not callable(note):
            return
        try:
            note(job.exec_id, generation)
        except Exception:
            job.receipt_degraded = True

    def _maybe_flush(self, job: _BackgroundJob) -> None:
        """Persist the stdout head once it is due.

        Due is ``FLUSH_BYTES`` unwritten bytes, or ``FLUSH_INTERVAL_MS``
        since the last write with anything unwritten. Output that goes quiet
        before either arms a one-shot timer for the rest of the interval:
        otherwise the tail of a burst waited for a later chunk that may never
        come, and a crash in that silence lost it. After a failed write,
        attempts back off to one per interval. ``_flush_lock`` orders the
        chunk thread and the timer; ``save_output`` only touches a row that
        is still non-terminal, so a timer that fires after ``finish`` is a
        no-op.
        """

        receipts = self.receipts
        if receipts is None:
            return
        with job._flush_lock:
            with job._lock:
                if job.status != "running":
                    return
                pending = job._buf_bytes - job._flushed_bytes
                if pending <= 0:
                    return
                now = int(time.time() * 1000)
                waited = now - job._last_flush_ms
                due = waited >= self.FLUSH_INTERVAL_MS or (
                    pending >= self.FLUSH_BYTES and not job.receipt_degraded
                )
                if not due:
                    self._arm_flush_timer(job, self.FLUSH_INTERVAL_MS - waited)
                    return
                text = "".join(job._buf)
                truncated = job._buf_truncated
                flushed = job._buf_bytes
            try:
                receipts.save_output(job.exec_id, text, truncated)
            except Exception:
                with job._lock:
                    job.receipt_degraded = True
                    job._last_flush_ms = now
                return
            with job._lock:
                job._last_flush_ms = now
                if flushed >= job._flushed_bytes:
                    job._flushed_bytes = flushed

    def _arm_flush_timer(self, job: _BackgroundJob, delay_ms: int) -> None:
        """Schedule one deferred flush for ``job``. Caller holds ``job._lock``."""

        timer = job._flush_timer
        if timer is not None and timer.is_alive():
            return
        timer = threading.Timer(
            max(int(delay_ms), 1) / 1000.0, self._flush_on_timer, args=(job,)
        )
        timer.daemon = True
        job._flush_timer = timer
        timer.start()

    def _flush_on_timer(self, job: _BackgroundJob) -> None:
        with job._lock:
            if job._flush_timer is threading.current_thread():
                job._flush_timer = None
        self._maybe_flush(job)

    def _finish_receipt(
        self,
        job: _BackgroundJob,
        *,
        status: str,
        error: str | None,
        interrupted: bool,
        ended_at: int,
    ) -> None:
        receipts = self.receipts
        wrote = False
        try:
            if receipts is not None:
                with job._lock:
                    timer = job._flush_timer
                    job._flush_timer = None
                    text = "".join(job._buf)
                    truncated = job._buf_truncated
                if timer is not None:
                    timer.cancel()
                if status not in ("done", "failed", "interrupted"):
                    status = "failed"
                try:
                    receipts.finish(
                        job.exec_id,
                        status=status,
                        error=error,
                        interrupted=interrupted,
                        ended_at=ended_at,
                        output=text,
                        truncated=truncated,
                    )
                    wrote = True
                except Exception:
                    job.receipt_degraded = True
        finally:
            _forget_live(job.exec_id)
        if wrote:
            self._prune_after_terminal()

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

    def stop_turn(self, turn_id: str, *, grace_s: float = 5.0) -> list[str]:
        """Stop the running cells a cancelled agent turn launched.

        Stop used to end the turn and leave these running to completion, with
        nothing on screen to show them or stop them. Each one gets SIGINT and,
        if it is still running after ``grace_s``, an exact kill. The ids come
        back at once; the escalation runs on its own thread so a worker that
        ignores SIGINT never holds up the Stop request.
        """
        if not turn_id:
            return []
        with self._lock:
            jobs = [
                job
                for job in self._jobs.values()
                if job.status == "running" and job.turn_id == turn_id
            ]
            for job in jobs:
                job.stop_requested = True
        for job in jobs:
            threading.Thread(
                target=self._stop_job,
                args=(job, grace_s),
                name=f"openai4s-bg-stop-{job.exec_id}",
                daemon=True,
            ).start()
        return [job.exec_id for job in jobs]

    @staticmethod
    def _stop_job(job: _BackgroundJob, grace_s: float) -> None:
        kernel = job._kernel
        if kernel is not None:
            try:
                kernel.interrupt()
            except Exception:  # noqa: BLE001 — escalate to the exact kill
                pass
        thread = job._thread
        if thread is not None:
            thread.join(timeout=max(0.0, grace_s))
        if thread is None or thread.is_alive():
            kernel = job._kernel
            if kernel is not None:
                try:
                    kernel.kill_worker()
                except Exception:  # noqa: BLE001 — worker may already be gone
                    pass

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
