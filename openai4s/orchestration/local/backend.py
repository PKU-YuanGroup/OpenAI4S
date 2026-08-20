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

from openai4s.execution.process_group import group_alive, stop_process_group
from openai4s.kernel.environment import name_can_carry_a_secret
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
    diagnostics: dict[str, Any] = field(default_factory=dict)
    #: The child's process group, read at *spawn*. `os.getpgid(pid)` fails once
    #: the leader has been reaped -- which `observe()` does on every tick --
    #: and that is exactly when the surviving group most needs signalling.
    pgid: int | None = None


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
        live = 0
        for job in self._jobs.values():
            code, whole_group_alive = self._poll_job(job)
            if code is None or whole_group_alive:
                live += 1
        return live

    @staticmethod
    def _poll_job(job: _LocalJob) -> tuple[int | None, bool]:
        """Return leader status/group liveness and freeze a terminal job.

        A saved pgid is needed only for the short leader-exited/child-alive
        window. Keeping it after the group is confirmed empty lets OS pgid
        reuse resurrect a completed allocation or makes a later cancel/close
        signal an unrelated process group. Once terminal, cache the exit code
        and discard the pgid permanently.
        """

        if job.exit_code is not None:
            return job.exit_code, False
        code = job.process.poll()
        if code is None:
            return None, True
        whole_group_alive = group_alive(job.pgid)
        if not whole_group_alive:
            job.exit_code = code
            job.pgid = None
        return code, whole_group_alive

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

            # ``start_new_session=True`` makes the child a POSIX session
            # leader, so its process group id is its pid by definition.  Do
            # not ask the kernel for it after spawn: a wrapper may already
            # have exited while a descendant remains in that group, and
            # ``getpgid`` then loses the only handle capable of stopping the
            # surviving work.
            job_pgid: int | None = process.pid if os.name == "posix" else None
            self._jobs[allocation.id] = _LocalJob(
                allocation_id=allocation.id,
                pgid=job_pgid,
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
        # The spec's environment is caller-supplied -- `POST /orchestration/jobs`
        # passes `body["environment"]` straight through, and `WorkloadSpec`
        # validates only `command`. Taking it verbatim let a submission set
        # `LD_PRELOAD` or `PYTHONSTARTUP` on a child of the daemon, and put a
        # credential-shaped value into `/proc/<pid>/environ` where every other
        # process of the same uid can read it. The scheduler sibling has
        # refused exactly this since it was written; two backends disagreeing
        # about the same field is the drift, not the rule.
        for key, value in spec.environment.items():
            if name_can_carry_a_secret(str(key)):
                raise ValueError(
                    f"refusing to put {key!r} in a job environment "
                    f"(INV-9: pass a path to a 0600 file instead)"
                )
            base[str(key)] = str(value)
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

            code, whole_group_alive = self._poll_job(job)
            if code is None or whole_group_alive:
                diagnostics = dict(job.diagnostics)
                if code is not None:
                    # The process we spawned was only the group leader. A
                    # wrapper may exit after starting the real work, and that
                    # work is still the allocation until the whole group is
                    # empty. Publishing COMPLETED here made the reconciler
                    # release capacity while descendants kept consuming it.
                    diagnostics["leader_exit_code"] = code
                return Observation(
                    phase=Phase.ACTIVE,
                    handle=self._handle(job.allocation_id),
                    diagnostics=diagnostics,
                )
            assert code is not None
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
            code, whole_group_alive = self._poll_job(job)
            if code is not None and not whole_group_alive:
                return
            job.cancelled = True
            # `stop_process_group`, not a bare `killpg(SIGTERM)`: it escalates
            # to SIGKILL and then *confirms* the group is gone. A TERM that the
            # work ignores used to return here as success, so the cancel
            # barrier concluded "released" for a job still holding its CPUs --
            # the one outcome the barrier exists to prevent. The shared helper
            # is also where the escalation ladder is tuned, so this path gets
            # future fixes instead of missing them.
            stopped, detail = stop_process_group(job.process, job.pgid)
            job.diagnostics["cancel"] = {"stopped": stopped, "detail": detail}
            if stopped:
                code = job.process.poll()
                if code is not None:
                    job.exit_code = code
                    job.pgid = None

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
            with self._lock:
                code, whole_group_alive = self._poll_job(job)
            if code is not None and not whole_group_alive:
                continue
            # Same stopper as `cancel`: shutdown is the other place a group
            # that ignores TERM turns into orphaned compute. The helper also
            # handles a reaped leader with surviving descendants, which a
            # `poll()` guard would skip.
            stopped, _detail = stop_process_group(job.process, job.pgid)
            if stopped:
                with self._lock:
                    code = job.process.poll()
                    if code is not None:
                        job.exit_code = code
                        job.pgid = None

    # --- helpers ----------------------------------------------------------

    def _handle(self, allocation_id: str) -> ExternalHandle:
        with self._lock:
            job = self._jobs.get(allocation_id)
            external = str(job.process.pid) if job else allocation_id
        return ExternalHandle(backend=self.name, external_id=external)


__all__ = ["MAX_CONCURRENT", "LocalBackend"]
