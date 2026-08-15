"""LocalBackend: the default resource plane — this machine (M3a-3).

Same `AllocationBackend` contract as any cluster, so the reconciler, the
routes and the CLI have exactly one code path whether or not a scheduler
exists. That is the point of collecting local execution into a backend
rather than special-casing it: "no cluster configured" becomes a different
*backend*, not a different program.

Two details are worth stating because getting them wrong is invisible until
it matters:

**The token is honoured here too (INV-8).** A local submit is not going to
lose its response to a network partition, but the reconciler cannot know
which backend it is talking to — so `find_by_token` really searches, and a
resubmission with a token already in flight returns `Existing`. A backend
that answered "of course it's new" would make the reconciler's INV-8 path
untested on the only backend every install has.

**A process that vanished is LOST, not COMPLETED.** Same rule as the
cluster: we record an exit status when we reap the child ourselves; a
tracked process that is simply gone (daemon restarted, someone killed it)
has no exit status, and inventing a successful one loses work silently.

Bounded by construction: `MAX_CONCURRENT` refuses rather than queues, and
the refusal is `UNSCHEDULABLE` — the same reason a cluster gives when a job
can never be placed, so callers need no local-only branch.
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openai4s.orchestration.models import (
    Allocation,
    ExternalHandle,
    Observation,
    Phase,
    Reason,
    ResourceProfile,
    SubmissionToken,
    WorkloadSpec,
)
from openai4s.orchestration.ports import Created, Existing, Rejected, SubmitResult

#: How many local allocations may run at once. A workstation is not a
#: cluster; admitting the tenth concurrent job is how a shared login node
#: becomes unusable for everyone.
MAX_CONCURRENT = 4


@dataclass
class _LocalJob:
    """One local process, plus what we will need after it exits."""

    allocation_id: str
    token: str
    process: Any
    started_at: float
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    exit_code: int | None = None
    cancelled: bool = False
    reaped: bool = False
    diagnostics: dict[str, Any] = field(default_factory=dict)


class LocalBackend:
    """Run allocations as child processes on this machine."""

    name = "local"

    def __init__(
        self,
        *,
        log_dir: Path | str | None = None,
        max_concurrent: int = MAX_CONCURRENT,
        clock: Any = time.monotonic,
    ) -> None:
        self._jobs: dict[str, _LocalJob] = {}
        self._lock = threading.RLock()
        self._log_dir = Path(log_dir).expanduser() if log_dir else None
        if self._log_dir is not None:
            self._log_dir.mkdir(parents=True, exist_ok=True)
        self._max_concurrent = max_concurrent
        self._clock = clock

    # --- submission -------------------------------------------------------

    def _live_count(self) -> int:
        return sum(
            1
            for job in self._jobs.values()
            if job.exit_code is None and job.process.poll() is None
        )

    def submit(
        self,
        *,
        allocation: Allocation,
        spec: WorkloadSpec,
        profile: ResourceProfile,
    ) -> SubmitResult:
        token = allocation.submission_token.value
        with self._lock:
            # INV-8 on the backend every install has: a resubmission of a
            # token already in flight is Existing, not a second process.
            for job in self._jobs.values():
                if job.token == token:
                    return Existing(handle=self._handle(job.allocation_id))

            if not spec.command:
                return Rejected(
                    reason=Reason.INVALID_SPEC,
                    detail="a local workload needs a command",
                )
            if self._live_count() >= self._max_concurrent:
                # The same reason a cluster gives for "this can never be
                # placed", so no caller needs a local-only branch.
                return Rejected(
                    reason=Reason.UNSCHEDULABLE,
                    detail=(
                        f"local backend is at capacity "
                        f"({self._max_concurrent} concurrent allocations)"
                    ),
                )

            stdout_path = stderr_path = None
            stdout_handle = stderr_handle = subprocess.DEVNULL
            if self._log_dir is not None:
                stdout_path = self._log_dir / f"{allocation.id}.out"
                stderr_path = self._log_dir / f"{allocation.id}.err"
                stdout_handle = open(stdout_path, "wb")  # noqa: SIM115
                stderr_handle = open(stderr_path, "wb")  # noqa: SIM115

            try:
                process = subprocess.Popen(  # noqa: S603 - argv list, no shell
                    list(spec.command),
                    cwd=spec.workdir or None,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    stdin=subprocess.DEVNULL,
                    # Its own process group, so cancelling kills the whole
                    # tree rather than the parent that spawned the real work.
                    start_new_session=True,
                    env=self._child_env(spec),
                    shell=False,
                )
            except (OSError, ValueError) as exc:
                for handle in (stdout_handle, stderr_handle):
                    if handle not in (subprocess.DEVNULL, None):
                        handle.close()
                return Rejected(reason=Reason.BOOTSTRAP_FAILED, detail=str(exc))
            finally:
                # The child holds its own duplicated descriptors.
                for handle in (stdout_handle, stderr_handle):
                    if handle not in (subprocess.DEVNULL, None):
                        try:
                            handle.close()
                        except OSError:
                            pass

            self._jobs[allocation.id] = _LocalJob(
                allocation_id=allocation.id,
                token=token,
                process=process,
                started_at=self._clock(),
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                diagnostics={"pid": process.pid, "command": shlex.join(spec.command)},
            )
        return Created(
            handle=self._handle(allocation.id), diagnostics={"pid": process.pid}
        )

    def _child_env(self, spec: WorkloadSpec) -> dict[str, str]:
        """A named environment, never the daemon's.

        The daemon's environment holds API keys; inheriting it by default
        would put them in every batch job's `/proc/<pid>/environ`.
        """
        base = {
            key: os.environ[key]
            for key in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
            if key in os.environ
        }
        base.update(spec.environment)
        return base

    # --- observation ------------------------------------------------------

    def observe(self, allocation: Allocation) -> Observation:
        with self._lock:
            job = self._jobs.get(allocation.id)
            if job is None:
                # Tracked by the caller, unknown to us: the daemon restarted,
                # so the child is gone with no exit status to report. LOST,
                # never COMPLETED — see the module docstring.
                if allocation.handle is not None:
                    return Observation(
                        phase=Phase.LOST,
                        reason=Reason.WORKER_LOST,
                        handle=allocation.handle,
                        diagnostics={"note": "no local record; daemon restarted?"},
                    )
                return Observation(phase=Phase.SUBMITTING)

            code = job.process.poll()
            if code is None:
                return Observation(
                    phase=Phase.ACTIVE,
                    handle=self._handle(job.allocation_id),
                    diagnostics=dict(job.diagnostics),
                )
            job.exit_code = code
            job.reaped = True
            diagnostics = dict(job.diagnostics, exit_code=code)
            if job.cancelled:
                return Observation(
                    phase=Phase.CANCELLED,
                    reason=Reason.USER_CANCELLED,
                    handle=self._handle(job.allocation_id),
                    diagnostics=diagnostics,
                )
            if code == 0:
                return Observation(
                    phase=Phase.COMPLETED,
                    handle=self._handle(job.allocation_id),
                    diagnostics=diagnostics,
                )
            # A negative code is a signal. SIGKILL after an OOM is the one
            # worth naming, because "killed" and "failed" send an operator
            # looking in different places.
            reason = Reason.OUT_OF_MEMORY if code == -signal.SIGKILL else None
            return Observation(
                phase=Phase.FAILED,
                reason=reason,
                handle=self._handle(job.allocation_id),
                diagnostics=diagnostics,
            )

    # --- lifecycle --------------------------------------------------------

    def cancel(self, allocation: Allocation, *, reason: Reason) -> None:
        """Idempotent: cancelling something already gone is success."""
        with self._lock:
            job = self._jobs.get(allocation.id)
            if job is None:
                return
            job.cancelled = True
            if job.process.poll() is not None:
                return
            try:
                # The whole group: the command may have spawned the real work.
                os.killpg(os.getpgid(job.process.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    job.process.terminate()
                except OSError:
                    return

    def find_by_token(self, token: SubmissionToken) -> ExternalHandle | None:
        with self._lock:
            for job in self._jobs.values():
                if job.token == token.value:
                    return self._handle(job.allocation_id)
        return None

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            return {
                "backend": self.name,
                "available": True,
                "running": self._live_count(),
                "max_concurrent": self._max_concurrent,
                "tracked": len(self._jobs),
            }

    def log_paths(self, allocation_id: str) -> tuple[Path | None, Path | None]:
        """Where this allocation's output went, for the log-tail route."""
        with self._lock:
            job = self._jobs.get(allocation_id)
            return (job.stdout_path, job.stderr_path) if job else (None, None)

    def close(self) -> None:
        """Terminate anything still running. The daemon owns these children,
        so leaving them behind on shutdown orphans real compute."""
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            if job.process.poll() is None:
                try:
                    os.killpg(os.getpgid(job.process.pid), signal.SIGTERM)
                except OSError:
                    pass

    # --- helpers ----------------------------------------------------------

    def _handle(self, allocation_id: str) -> ExternalHandle:
        with self._lock:
            job = self._jobs.get(allocation_id)
            external = str(job.process.pid) if job else allocation_id
        return ExternalHandle(backend=self.name, external_id=external)


__all__ = ["MAX_CONCURRENT", "LocalBackend"]
