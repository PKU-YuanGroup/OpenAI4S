"""SlurmBackend: the AllocationBackend the control plane never has to know.

Everything scheduler-shaped stops here. The core sees `Phase` and `Reason`;
this module owns the table that turns `TIMEOUT` into
`(FAILED, TIME_LIMIT_EXCEEDED)` and keeps the scheduler's own words in
`diagnostics`, where nothing branches on them.

Two behaviours are load-bearing rather than incidental:

**A lost submission is Unknown, not a failure (INV-8).** When sbatch times
out we do not know whether the job landed. Reporting Rejected would strand a
running job nobody is tracking; retrying blindly would put two jobs on the
cluster. So `submit` returns `Unknown`, and the only correct next step —
asking whether anything carries the token — is `find_by_token`, which the
reconciler must call before it may submit again. `submit` itself begins with
that lookup, so a retry after an Unknown returns `Existing` instead of
double-submitting.

**A job absent from the queue is not finished.** A finished job leaves the
queue, so "squeue says nothing" and "the job is done" are the same
observation from the wrong angle; accounting is what distinguishes them, and
a job absent from both is LOST rather than COMPLETED — because claiming
success for something we cannot find is the one answer that silently loses
science.
"""

from __future__ import annotations

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
    WorkloadKind,
    WorkloadSpec,
)
from openai4s.orchestration.ports import (
    Created,
    Existing,
    Rejected,
    SubmitResult,
    Unknown,
)
from openai4s.orchestration.slurm.broker import (
    SlurmBroker,
    SlurmCommandError,
    SubmitSpec,
)
from openai4s.orchestration.slurm.profiles import ClusterConfig, ClusterProfile

#: Scheduler state -> (phase, reason). The reason is None where the state
#: carries no explanation of its own; a failing state without a reason is
#: still FAILED, which is the point of separating the two columns.
_STATE_MAP: dict[str, tuple[Phase, Reason | None]] = {
    "PENDING": (Phase.PENDING, None),
    "CONFIGURING": (Phase.PENDING, None),
    "REQUEUED": (Phase.PENDING, None),
    "REQUEUE_HOLD": (Phase.PENDING, None),
    "REQUEUE_FED": (Phase.PENDING, None),
    "RESV_DEL_HOLD": (Phase.PENDING, None),
    "EXPEDITING": (Phase.PENDING, None),
    "POWER_UP_NODE": (Phase.PENDING, None),
    "SPECIAL_EXIT": (Phase.PENDING, None),
    "RESIZING": (Phase.ACTIVE, None),
    "RUNNING": (Phase.ACTIVE, None),
    "SUSPENDED": (Phase.ACTIVE, None),
    "COMPLETING": (Phase.ACTIVE, None),
    "COMPLETED": (Phase.COMPLETED, None),
    "CANCELLED": (Phase.CANCELLED, Reason.USER_CANCELLED),
    "FAILED": (Phase.FAILED, None),
    "LAUNCH_FAILED": (Phase.FAILED, Reason.BOOTSTRAP_FAILED),
    "RECONFIG_FAIL": (Phase.FAILED, None),
    "TIMEOUT": (Phase.FAILED, Reason.TIME_LIMIT_EXCEEDED),
    "OUT_OF_MEMORY": (Phase.FAILED, Reason.OUT_OF_MEMORY),
    "NODE_FAIL": (Phase.LOST, Reason.NODE_FAILED),
    "PREEMPTED": (Phase.LOST, Reason.PREEMPTED),
    "BOOT_FAIL": (Phase.FAILED, Reason.BOOTSTRAP_FAILED),
    "DEADLINE": (Phase.FAILED, Reason.TIME_LIMIT_EXCEEDED),
    "REVOKED": (Phase.CANCELLED, Reason.ADMIN_CANCELLED),
}

#: Queue reasons that mean "this will never schedule". Everything else is
#: ordinary waiting, and treating waiting as failure is how a busy cluster
#: looks like a broken one.
_UNSCHEDULABLE_REASONS = frozenset(
    {
        "PartitionConfig",
        "PartitionNodeLimit",
        "PartitionTimeLimit",
        "QOSMaxWallDurationPerJobLimit",
        "BadConstraints",
        "InvalidQOS",
        "InvalidAccount",
    }
)


class SlurmBackend:
    """An `AllocationBackend` over a real scheduler, via `SlurmBroker`."""

    name = "slurm"

    def __init__(
        self,
        *,
        broker: SlurmBroker | None = None,
        cluster: ClusterConfig | None = None,
        log_dir: str | None = None,
    ) -> None:
        self._broker = broker or SlurmBroker()
        self._cluster = cluster or ClusterConfig()
        self._log_dir = log_dir

    # --- submission -------------------------------------------------------

    def _cluster_profile(self, profile: ResourceProfile) -> ClusterProfile:
        """The site mapping for this profile, or a bare one.

        A profile the operator has not configured still submits — with no
        partition or QoS, letting the scheduler's own defaults apply —
        rather than failing. Refusing here would make an unconfigured
        cluster.toml an outage instead of a default.
        """
        try:
            return self._cluster.profile(profile.name)
        except Exception:  # noqa: BLE001 - unknown profile is not fatal
            return ClusterProfile(name=profile.name, resources=profile)

    def _submit_spec(
        self,
        allocation: Allocation,
        spec: WorkloadSpec,
        profile: ResourceProfile,
    ) -> SubmitSpec:
        site = self._cluster_profile(profile)
        script_lines = ["#!/bin/sh", "set -eu"]
        if spec.workdir:
            script_lines.append(f"cd {_sh_quote(spec.workdir)}")
        command = tuple(spec.command)
        environment = dict(spec.environment)
        if spec.kind is WorkloadKind.SESSION and int(profile.nodes) > 1:
            # A batch script itself runs once, on its first node. Gang
            # readiness needs one worker per node, and srun is the scheduler's
            # in-allocation launcher that also supplies a distinct SLURM_PROCID
            # to each task. The worker resolves its credential template from
            # that variable; no credential value enters the job environment.
            from openai4s.orchestration.session import RANK_ENV_NAME_ENV

            environment[RANK_ENV_NAME_ENV] = "SLURM_PROCID"
            command = (
                "srun",
                f"--nodes={int(profile.nodes)}",
                f"--ntasks={int(profile.nodes)}",
                "--ntasks-per-node=1",
                f"--cpus-per-task={int(profile.cpus)}",
                "--kill-on-bad-exit=1",
                "--",
                *command,
            )
        script_lines.append(" ".join(_sh_quote(part) for part in command))
        return SubmitSpec(
            # The workload id, not the user's text: a job name reaches the
            # scheduler's own logs and every operator's squeue output. Keep the
            # idempotency token in JobName too: Comment is persisted by sacct
            # only when a site enables AccountingStoreFlags=job_comment, while
            # JobName is an ordinary accounting field. Prefix/workload slices
            # keep the full token within Slurm's common 128-byte name limit.
            job_name=self._job_name_for_token(allocation.submission_token),
            comment=allocation.submission_token.value,
            script="\n".join(script_lines) + "\n",
            cpus=profile.cpus,
            memory_mb=profile.memory_mb,
            gpus=profile.gpus,
            walltime_s=profile.walltime_s,
            nodes=profile.nodes,
            partition=site.partition,
            qos=site.qos,
            workdir=spec.workdir,
            stdout_path=(
                f"{self._log_dir}/{allocation.id}.out" if self._log_dir else None
            ),
            stderr_path=(
                f"{self._log_dir}/{allocation.id}.err" if self._log_dir else None
            ),
            environment=environment,
        )

    def submit(
        self,
        *,
        allocation: Allocation,
        spec: WorkloadSpec,
        profile: ResourceProfile,
    ) -> SubmitResult:
        # INV-8, first half: before submitting, ask whether this token is
        # already out there. A retry after an Unknown lands here and gets
        # Existing rather than a second job.
        try:
            already = self._broker.find_by_comment(
                allocation.submission_token.value,
                job_name=self._job_name_for_token(allocation.submission_token),
            )
        except SlurmCommandError as exc:
            # An unanswerable lookup is `Unknown`, not "nothing carries it".
            # Swallowing the error to None and carrying on inverted the whole
            # invariant: `find_by_comment` raises on a timeout or an
            # unreachable controller precisely because absence of an answer is
            # not an answer of absence -- and the next statement submitted a
            # second job for a token that may well already have one. The
            # reconciler's `_reconcile_unknown` is what is allowed to decide a
            # fresh submission is safe, and only after the backend has
            # actually answered.
            return Unknown(
                token=allocation.submission_token,
                detail=str(exc),
                diagnostics={"command": list(exc.command), "phase": "find_by_token"},
            )
        if already:
            return Existing(handle=self._handle(already))

        try:
            submit_spec = self._submit_spec(allocation, spec, profile)
        except ValueError as exc:
            return Rejected(reason=Reason.INVALID_SPEC, detail=str(exc))

        try:
            job_id = self._broker.submit(submit_spec)
        except SlurmCommandError as exc:
            if exc.timed_out:
                # INV-8, second half: we genuinely do not know. Not a
                # failure, not a retry — a reconciliation instruction.
                return Unknown(
                    token=allocation.submission_token,
                    detail=str(exc),
                    diagnostics={"command": list(exc.command)},
                )
            return Rejected(
                reason=Reason.BACKEND_REJECTED,
                detail=str(exc),
                diagnostics={"stderr": exc.stderr, "returncode": exc.returncode},
            )
        return Created(handle=self._handle(job_id))

    # --- tasks inside an allocation (M4-2) --------------------------------

    def run_task(self, allocation: Any, spec: Any) -> Any:
        """Run a step inside the resource this workload already holds.

        INV-4, enforced rather than documented: with no live handle there
        is nothing to run *inside*, and the tempting fallback — submit one
        — is the exact behaviour the invariant forbids. An interactive task
        that quietly allocates turns one session into two jobs, one of
        which nobody is watching and both of which are billed. So this
        refuses and says why.
        """
        from openai4s.orchestration.models import TaskHandle, TaskResult
        from openai4s.orchestration.slurm.broker import StepSpec

        handle = getattr(allocation, "handle", None)
        job_id = handle.external_id if handle else None
        if not job_id:
            raise RuntimeError(
                "this workload holds no granted resource, so there is nothing "
                "to run inside; a task must never allocate one of its own "
                "(INV-4)"
            )
        step = StepSpec(
            command=tuple(spec.command),
            tasks=spec.tasks,
            nodes=spec.nodes,
            cpus_per_task=spec.cpus_per_task,
            workdir=spec.workdir,
            environment=dict(spec.environment),
        )
        output = self._broker.run_step(job_id, step)
        return TaskResult(
            handle=TaskHandle(
                allocation_id=allocation.id,
                step_id=f"{job_id}.step",
                tasks=spec.tasks,
            ),
            output=output,
        )

    # --- observation ------------------------------------------------------

    def observe(self, allocation: Allocation) -> Observation:
        job_id = allocation.handle.external_id if allocation.handle else None
        if not job_id:
            return Observation(
                phase=Phase.SUBMITTING,
                diagnostics={"note": "no external handle yet"},
            )
        try:
            queued = self._broker.queue_status(job_id)
        except SlurmCommandError as exc:
            # A scheduler we cannot reach is not a job that failed. Reporting
            # a phase here would turn an outage into a wave of dead sessions.
            return Observation(
                phase=allocation.phase,
                reason=Reason.BACKEND_UNAVAILABLE,
                handle=allocation.handle,
                diagnostics={"error": str(exc), "timed_out": exc.timed_out},
            )
        if queued is not None:
            return self._observation_from(queued, allocation)

        try:
            finished = self._broker.accounting_status(job_id)
        except SlurmCommandError as exc:
            return Observation(
                phase=allocation.phase,
                reason=Reason.BACKEND_UNAVAILABLE,
                handle=allocation.handle,
                diagnostics={"error": str(exc), "timed_out": exc.timed_out},
            )
        if finished is not None:
            return self._observation_from(finished, allocation)

        # In neither the queue nor accounting. LOST, not COMPLETED: claiming
        # success for work we cannot find is the one answer that silently
        # loses science.
        return Observation(
            phase=Phase.LOST,
            reason=Reason.WORKER_LOST,
            handle=allocation.handle,
            diagnostics={"note": "job in neither queue nor accounting"},
        )

    def _observation_from(self, status: Any, allocation: Allocation) -> Observation:
        phase, reason = _STATE_MAP.get(status.state, (Phase.ACTIVE, None))
        if phase is Phase.PENDING and status.reason in _UNSCHEDULABLE_REASONS:
            phase, reason = Phase.FAILED, Reason.UNSCHEDULABLE
        # No branch on `status.exit_code` here: an exit code is the detail an
        # operator wants and it is already carried in `diagnostics`, but it
        # does not change the *reason* -- a nonzero exit is a failure of the
        # work, which has no more specific cause than "it failed". The
        # condition used to be spelled out and then do nothing.
        return Observation(
            phase=phase,
            reason=reason,
            handle=allocation.handle,
            # The scheduler's own words, kept where no decision reads them.
            diagnostics={
                "state": status.state,
                "reason": status.reason,
                "exit_code": status.exit_code,
            },
        )

    # --- lifecycle --------------------------------------------------------

    def cancel(self, allocation: Allocation, *, reason: Reason) -> None:
        if allocation.handle is None:
            return
        self._broker.cancel(allocation.handle.external_id)

    def find_by_token(self, token: SubmissionToken) -> ExternalHandle | None:
        found = self._broker.find_by_comment(
            token.value, job_name=self._job_name_for_token(token)
        )
        return self._handle(found) if found else None

    def _job_name_for_token(self, token: SubmissionToken) -> str:
        """A durable, scheduler-indexable idempotency key.

        Accounting persists JobName at ordinary sites while Comment requires
        an optional Slurm setting.  The name is derived only from the token so
        reconciliation can reconstruct the exact server-side filter after a
        daemon restart without loading every historical accounting row.
        """
        prefix = self._cluster.job_name_prefix or "openai4s"
        return f"{prefix[:64]}-{token.value}"

    def log_paths(self, allocation_id: str) -> tuple[Path | None, Path | None]:
        """Where this allocation's output went, for the log-tail route.

        The route discovers this by `getattr(backend, "log_paths", None)`,
        and only `LocalBackend` had it -- so every *cluster* job's logs route
        answered `{"stdout": "", "stderr": ""}` with a valid allocation id,
        which is indistinguishable from "the job has not printed anything
        yet". The files existed the whole time: `_submit_spec` names them
        `<log_dir>/<allocation_id>.out|.err` and hands those paths to the
        scheduler.

        Derived from the id rather than remembered, because the daemon may
        have restarted since the job was submitted -- and a log tail that
        only worked for jobs submitted by this process would be the same
        empty answer for the same invisible reason.
        """
        if not self._log_dir:
            return (None, None)
        base = Path(self._log_dir)
        return (base / f"{allocation_id}.out", base / f"{allocation_id}.err")

    def diagnostics(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "available": self._broker.available(),
            "cluster": self._cluster.name,
            "profiles": sorted(self._cluster.profiles),
        }

    # --- helpers ----------------------------------------------------------

    def _handle(self, job_id: str) -> ExternalHandle:
        return ExternalHandle(
            backend=self.name,
            external_id=str(job_id),
            namespace=self._cluster.name or None,
        )


def _sh_quote(value: str) -> str:
    """POSIX single-quote quoting for the generated script.

    The submission itself never goes through a shell — argv is a list — but
    the *script* is shell text by definition, so the one place a value can
    become a command is here.
    """
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


__all__ = ["SlurmBackend"]
