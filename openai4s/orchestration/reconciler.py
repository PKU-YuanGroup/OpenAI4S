"""The reconciler: desired state in, backend calls out (M3a-6).

One periodic, single-threaded pass compares what each workload *wants*
against what its backend *reports*, and takes at most one step per workload
per tick. Deliberately not event-driven: a resource plane's truth is
whatever it says when asked, and a system that only reacts to events is a
system that believes its own last message after the cluster stopped agreeing
with it.

Everything here is written to be run twice. A tick that crashes half way
through must be safe to repeat, because that is the only failure mode a
daemon actually has — so every step is expressed as "given this observation,
what is the next durable fact", never as "continue what I was doing".

Three rules the plan pins, implemented as code rather than as comments:

**INV-8, submission reconciliation.** An `Unknown` result never becomes a
retry. It is recorded, and the *next* tick asks the backend whether anything
carries the token before deciding anything. `find_by_token` answering yes
adopts that handle; answering no is what makes a fresh submission safe.

**INV-3, one active allocation.** A workload gets a new allocation only when
it has no live one. The store's partial unique index is the real enforcement
(a crash between check and insert is exactly when a second one would appear);
this is the readable half.

**The cancel barrier, in the plan's fixed order.** fence new work → cancel
active tasks → drain → cancel the backend allocation → observe terminal →
mark terminal. Written as one method so the order is a thing you can read,
rather than an ordering that emerges from where the calls happen to sit.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from openai4s.orchestration.models import (
    Allocation,
    DesiredState,
    Observation,
    Phase,
    Reason,
    ResourceProfile,
    SubmissionToken,
    Workload,
    WorkloadKind,
    WorkloadSpec,
)
from openai4s.orchestration.ports import (
    AllocationBackend,
    Created,
    Existing,
    Rejected,
    Unknown,
)

#: How often the loop wakes. The plan's default; short enough that a queued
#: job's state feels live, long enough that a thousand workloads do not turn
#: the scheduler into our own denial of service.
DEFAULT_INTERVAL_S = 5.0

#: How many times a SESSION may be recovered before it is left failed.
#: Not unbounded: a node that kills every worker it is handed would
#: otherwise be resubmitted to forever, and an infinite retry against a
#: broken node is indistinguishable from a working system right up until
#: somebody reads the accounting.
DEFAULT_MAX_RECOVERIES = 3


class WorkloadStore(Protocol):
    """What the reconciler needs from persistence.

    A Protocol rather than the Store itself: the reconciler is the piece
    most worth testing without a database, and the operations it needs are
    few enough to name.
    """

    def workloads_needing_attention(self) -> list[Workload]: ...

    def active_allocation(self, workload_id: str) -> Allocation | None: ...

    def create_allocation(self, workload_id: str, epoch: int) -> Allocation: ...

    def save_allocation(self, allocation: Allocation) -> None: ...

    def save_workload(self, workload: Workload) -> None: ...

    def open_recovery_epoch(
        self, allocation: Allocation, workload: Workload
    ) -> None: ...


@dataclass
class TickReport:
    """What one pass did, for tests and for an operator's diagnostics."""

    examined: int = 0
    submitted: int = 0
    adopted: int = 0
    advanced: int = 0
    cancelled: int = 0
    failed: int = 0
    errors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


class Reconciler:
    """Drive workloads toward their desired state, one step per tick."""

    def __init__(
        self,
        *,
        store: WorkloadStore,
        backends: dict[str, AllocationBackend],
        default_backend: str = "local",
        profile_for: Callable[[Workload], ResourceProfile] | None = None,
        prepare_attempt: Callable[[Workload, Allocation], WorkloadSpec] | None = None,
        max_recoveries: int = DEFAULT_MAX_RECOVERIES,
        on_state_lost: Callable[[Workload, Allocation], None] | None = None,
        interval_s: float = DEFAULT_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
        on_event: Callable[[str, dict], None] | None = None,
    ) -> None:
        self._store = store
        self._backends = backends
        self._default_backend = default_backend
        self._profile_for = profile_for or (lambda w: w.spec.profile)
        # What is actually submitted for one attempt, derived from the
        # durable spec. The default is the identity, so a BATCH workload —
        # and every existing caller — is unchanged. A SESSION needs this
        # seam because its bootstrap credential is bound to (allocation,
        # epoch) and therefore cannot exist until an attempt does.
        self._prepare_attempt = prepare_attempt or (lambda w, a: w.spec)
        self._max_recoveries = max_recoveries
        self._on_state_lost = on_state_lost
        self._interval_s = interval_s
        self._clock = clock
        self._on_event = on_event or (lambda kind, payload: None)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        #: Observable liveness. "Is there a thread object" is not the same
        #: question as "is this loop running", and answering the first one
        #: while meaning the second sent one investigation down a dead end.
        self.ticks = 0
        self.last_error: str | None = None
        self.exited_reason: str | None = None

    # --- the loop ---------------------------------------------------------

    def running(self) -> bool:
        """Is the loop actually ticking? Not 'does a thread object exist'."""
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        # `running()` rather than `_thread is not None`: a loop that exited on
        # its own leaves the attribute set, and treating that as "already
        # started" means it can never be restarted — the daemon goes quiet
        # and every later submission waits forever for a tick that will not
        # come.
        if self.running():
            return
        self._thread = None
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="orchestration-reconciler", daemon=True
        )
        self._thread.start()

    def stop(self, *, timeout_s: float = 5.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=timeout_s)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.ticks += 1
                self.tick()
            except Exception as exc:  # noqa: BLE001 — a tick must never kill the loop
                self.last_error = f"{type(exc).__name__}: {exc}"
                if _store_is_gone(exc):
                    # Not a transient error to log forever: the database this
                    # loop exists to reconcile has been closed, so the world
                    # it was built for is over. A daemon shutdown that races
                    # the thread otherwise produces an endless stream of
                    # "closed database" lines and keeps a thread alive past
                    # the process state it depends on.
                    self.exited_reason = "store closed"
                    self._stop.set()
                    return
                self._emit("reconcile_error", {"error": str(exc)})
            self._stop.wait(self._interval_s)
        self.exited_reason = self.exited_reason or "stopped"

    # --- one pass ---------------------------------------------------------

    def tick(self) -> TickReport:
        report = TickReport()
        for workload in self._store.workloads_needing_attention():
            report.examined += 1
            try:
                self._reconcile_one(workload, report)
            except Exception as exc:  # noqa: BLE001
                # One bad workload must not stop the other 99.
                report.errors.append(f"{workload.id}: {exc}")
                # Also recorded on the loop itself: a per-workload failure was
                # invisible to `last_error`, which only saw exceptions that
                # escaped `tick()` — so a workload failing identically on
                # every pass looked exactly like a healthy loop.
                self.last_error = f"{workload.id}: {type(exc).__name__}: {exc}"
                self._emit(
                    "reconcile_error", {"workload_id": workload.id, "error": str(exc)}
                )
        return report

    def _backend_for(self, workload: Workload) -> AllocationBackend:
        name = getattr(workload, "backend", None) or self._default_backend
        try:
            return self._backends[name]
        except KeyError:
            raise LookupError(f"no backend named {name!r}") from None

    def _reconcile_one(self, workload: Workload, report: TickReport) -> None:
        if workload.phase.is_terminal:
            return
        backend = self._backend_for(workload)
        allocation = self._store.active_allocation(workload.id)

        if workload.desired_state is DesiredState.STOPPED:
            self._cancel_barrier(
                workload,
                allocation,
                backend,
                reason=workload.reason or Reason.USER_CANCELLED,
                report=report,
            )
            return

        if allocation is None:
            # INV-3: only when nothing is live. The store's partial unique
            # index is what makes this true under a crash; this is the
            # readable half of the same rule.
            self._submit_new(workload, backend, report)
            return

        if allocation.phase is Phase.SUBMITTING and allocation.handle is None:
            # INV-8: last tick's submission may or may not have landed. Ask
            # before doing anything else — this is the only path allowed to
            # decide that a fresh submission is safe.
            #
            # The predicate is the durable *state*, not the reason code. "In
            # SUBMITTING with no handle" IS the definition of a submission
            # whose outcome we never recorded; BACKEND_SUBMISSION_UNKNOWN is
            # only the subset where the backend was still there to tell us.
            # Keying on the reason left the commonest window open: the row is
            # persisted before `backend.submit` is called, so a process that
            # dies between a successful submit and `save_allocation` leaves a
            # handle-less row with no reason at all — and `observe` on a
            # handle-less allocation answers SUBMITTING forever, so it never
            # advanced and the cancel barrier concluded "nothing was placed"
            # while the job kept its GPU.
            #
            # A row that genuinely never reached the backend answers None to
            # `find_by_token` and is submitted — the same outcome, one query
            # later.
            self._reconcile_unknown(workload, allocation, backend, report)
            return

        self._advance(workload, allocation, backend, report)

    # --- steps ------------------------------------------------------------

    def _submit_new(
        self, workload: Workload, backend: AllocationBackend, report: TickReport
    ) -> None:
        allocation = self._store.create_allocation(
            workload.id, workload.execution_epoch
        )
        result = backend.submit(
            allocation=allocation,
            spec=self._prepare_attempt(workload, allocation),
            profile=self._profile_for(workload),
        )
        if isinstance(result, (Created, Existing)):
            allocation.handle = result.handle
            allocation.phase = Phase.PENDING
            allocation.reason = None
            allocation.diagnostics = dict(result.diagnostics)
            self._store.save_allocation(allocation)
            workload.phase = Phase.PENDING
            self._store.save_workload(workload)
            report.submitted += 1
            self._emit(
                "allocation_submitted",
                {
                    "workload_id": workload.id,
                    "allocation_id": allocation.id,
                    "existing": isinstance(result, Existing),
                },
            )
            return
        if isinstance(result, Rejected):
            allocation.phase = Phase.FAILED
            allocation.reason = result.reason
            allocation.diagnostics = dict(result.diagnostics, detail=result.detail)
            self._store.save_allocation(allocation)
            self._fail(workload, result.reason, report)
            return
        # Unknown: record it and stop. The next tick reconciles by token —
        # retrying here is exactly the double-submission INV-8 exists to
        # prevent.
        allocation.phase = Phase.SUBMITTING
        allocation.reason = Reason.BACKEND_SUBMISSION_UNKNOWN
        allocation.diagnostics = dict(result.diagnostics, detail=result.detail)
        self._store.save_allocation(allocation)
        self._emit(
            "allocation_submission_unknown",
            {"workload_id": workload.id, "allocation_id": allocation.id},
        )

    def _reconcile_unknown(
        self,
        workload: Workload,
        allocation: Allocation,
        backend: AllocationBackend,
        report: TickReport,
    ) -> None:
        handle = backend.find_by_token(allocation.submission_token)
        if handle is not None:
            # It landed after all. Adopt it rather than submitting again.
            allocation.handle = handle
            allocation.phase = Phase.PENDING
            allocation.reason = None
            self._store.save_allocation(allocation)
            workload.phase = Phase.PENDING
            self._store.save_workload(workload)
            report.adopted += 1
            self._emit(
                "allocation_adopted",
                {"workload_id": workload.id, "allocation_id": allocation.id},
            )
            return
        # Nothing carries the token, so nothing landed: a fresh submission is
        # safe *because* we asked, not because we assumed.
        result = backend.submit(
            allocation=allocation,
            spec=self._prepare_attempt(workload, allocation),
            profile=self._profile_for(workload),
        )
        if isinstance(result, (Created, Existing)):
            allocation.handle = result.handle
            allocation.phase = Phase.PENDING
            allocation.reason = None
            self._store.save_allocation(allocation)
            workload.phase = Phase.PENDING
            self._store.save_workload(workload)
            report.submitted += 1
        elif isinstance(result, Rejected):
            allocation.phase = Phase.FAILED
            allocation.reason = result.reason
            self._store.save_allocation(allocation)
            self._fail(workload, result.reason, report)
        # still Unknown -> leave it; the next tick asks again.

    def _advance(
        self,
        workload: Workload,
        allocation: Allocation,
        backend: AllocationBackend,
        report: TickReport,
    ) -> None:
        observed: Observation = backend.observe(allocation)
        if observed.reason is Reason.BACKEND_UNAVAILABLE:
            # An outage is not a state change. Recording one would turn a
            # scheduler restart into a wave of dead workloads.
            self._emit(
                "backend_unavailable",
                {"workload_id": workload.id, "allocation_id": allocation.id},
            )
            return

        changed = observed.phase is not allocation.phase
        allocation.phase = observed.phase
        allocation.reason = observed.reason
        if observed.handle is not None:
            allocation.handle = observed.handle
        allocation.diagnostics = dict(observed.diagnostics)
        self._store.save_allocation(allocation)

        if observed.phase.is_terminal:
            if self._recover_session(workload, allocation, observed, report):
                return
            workload.phase = observed.phase
            workload.reason = observed.reason
            self._store.save_workload(workload)
            report.advanced += 1
            if observed.phase is not Phase.COMPLETED:
                report.failed += 1
            self._emit(
                "workload_terminal",
                {
                    "workload_id": workload.id,
                    "phase": observed.phase.value,
                    "reason": observed.reason.value if observed.reason else None,
                },
            )
            return

        if changed:
            workload.phase = observed.phase
            self._store.save_workload(workload)
            report.advanced += 1
            self._emit(
                "workload_phase",
                {"workload_id": workload.id, "phase": observed.phase.value},
            )

    # --- recovery ---------------------------------------------------------

    def _recover_session(
        self,
        workload: Workload,
        allocation: Allocation,
        observed: Observation,
        report: TickReport,
    ) -> bool:
        """An interactive session lost its resource but is still wanted.

        M3b-5, and it is two invariants at once. INV-6: the allocation that
        died stays dead, with its own terminal phase and its own reason —
        recovery is a *new epoch*, never a rewrite of what happened. INV-11:
        the kernel's memory went with it, and the user is told so, because
        a session that silently reconnects to a fresh interpreter and keeps
        answering produces results indistinguishable from the ones it can
        no longer reproduce.

        Returns True when this observation was handled as a recovery, in
        which case the workload is emphatically *not* terminal.
        """
        if workload.spec.kind is not WorkloadKind.SESSION:
            return False
        if workload.desired_state is not DesiredState.RUNNING:
            # A session being torn down that also lost its node has not
            # been "recovered" into anything; the cancel barrier owns it.
            return False
        if workload.execution_epoch >= self._max_recoveries:
            self._emit(
                "session_recovery_exhausted",
                {
                    "workload_id": workload.id,
                    "epoch": workload.execution_epoch,
                    "limit": self._max_recoveries,
                },
            )
            return False

        # Retiring the dead allocation and starting the next epoch is ONE
        # durable fact. Written separately, the window between them strands
        # the workload permanently: the allocation goes terminal, the
        # process dies, the workload comes back still on the old epoch, and
        # the next tick's `create_allocation` hits UNIQUE (workload_id,
        # epoch) -- every tick after it identically, with no state left that
        # can move. Swapping the order only moves the window: bump first and
        # the old allocation is still in the active set, so INV-3's index
        # refuses the replacement instead.
        allocation.phase = observed.phase
        allocation.reason = observed.reason or Reason.WORKER_LOST
        allocation.diagnostics = dict(observed.diagnostics)

        lost_epoch = workload.execution_epoch
        workload.execution_epoch = lost_epoch + 1
        workload.phase = Phase.PENDING
        workload.reason = Reason.KERNEL_STATE_LOST
        self._store.open_recovery_epoch(allocation, workload)
        report.advanced += 1
        if self._on_state_lost is not None:
            try:
                self._on_state_lost(workload, allocation)
            except Exception as exc:  # noqa: BLE001 - a listener must not
                # strand the workload it was told about.
                self.last_error = f"{workload.id}: on_state_lost: {exc}"
        self._emit(
            "session_kernel_state_lost",
            {
                "workload_id": workload.id,
                "allocation_id": allocation.id,
                "lost_epoch": lost_epoch,
                "epoch": workload.execution_epoch,
                "reason": (observed.reason.value if observed.reason else None),
            },
        )
        return True

    # --- the cancel barrier -----------------------------------------------

    def _cancel_barrier(
        self,
        workload: Workload,
        allocation: Allocation | None,
        backend: AllocationBackend,
        *,
        reason: Reason,
        report: TickReport,
    ) -> None:
        """The plan's §2 order, in one place so it can be read as an order.

        fence new work → cancel active tasks → drain → cancel the backend
        allocation → observe the terminal state → mark terminal.

        Each step is idempotent, because a barrier that cannot be re-entered
        is a barrier that strands a workload the first time a tick dies in
        the middle of it.
        """
        # 1. fence: mark the workload releasing so nothing new is admitted.
        if workload.phase is not Phase.RELEASING:
            workload.phase = Phase.RELEASING
            workload.reason = reason
            self._store.save_workload(workload)
            self._emit("workload_releasing", {"workload_id": workload.id})

        if allocation is None:
            # 6. nothing to release: terminal immediately.
            workload.phase = Phase.CANCELLED
            workload.reason = reason
            self._store.save_workload(workload)
            report.cancelled += 1
            return

        # 2-3. cancel active tasks and drain. The task plane is the kernel's
        # (M3b wires it); with no session runtime attached this is a no-op
        # rather than a lie about having drained something.
        self._emit(
            "allocation_draining",
            {"workload_id": workload.id, "allocation_id": allocation.id},
        )

        if allocation.handle is None:
            # Nothing was ever placed on a resource plane — the allocation row
            # exists but no submission returned a handle. There is nothing to
            # release, and waiting for a terminal observation would wait
            # forever: a backend asked about an allocation it has never seen
            # answers SUBMITTING, which is not terminal, so every tick would
            # re-enter this barrier and none would finish it.
            #
            # The one case that is NOT "nothing was placed" is a submission
            # whose outcome we never learned (INV-8). Ask before concluding —
            # otherwise a cancel could mark a workload finished while its job
            # runs on unattended.
            # Asked for every handle-less allocation, not only the ones
            # whose reason names it: the same crash window applies here, and
            # concluding "nothing was placed" about a job that is running is
            # how a cancel leaves a GPU held by something nobody is watching.
            found = None
            try:
                found = backend.find_by_token(allocation.submission_token)
            except Exception as exc:  # noqa: BLE001
                # Undecidable. Leave the barrier un-concluded rather than
                # mark a workload cancelled on a question we could not ask;
                # the next tick re-enters, which is what every step here is
                # written to tolerate.
                self._emit(
                    "cancel_reconcile_failed",
                    {"workload_id": workload.id, "error": str(exc)},
                )
                return
            if found is not None:
                allocation.handle = found
                self._store.save_allocation(allocation)
                self._emit(
                    "allocation_adopted",
                    {
                        "workload_id": workload.id,
                        "allocation_id": allocation.id,
                        "during": "cancel",
                    },
                )
            if allocation.handle is None:
                allocation.phase = Phase.CANCELLED
                allocation.reason = reason
                self._store.save_allocation(allocation)
                workload.phase = Phase.CANCELLED
                workload.reason = reason
                self._store.save_workload(workload)
                report.cancelled += 1
                self._emit(
                    "workload_terminal",
                    {
                        "workload_id": workload.id,
                        "phase": Phase.CANCELLED.value,
                        "reason": reason.value,
                    },
                )
                return

        # 4. release the resource.
        if allocation.phase is not Phase.RELEASING:
            allocation.phase = Phase.RELEASING
            allocation.reason = reason
            self._store.save_allocation(allocation)
        backend.cancel(allocation, reason=reason)

        # 5. observe. A backend that has not caught up yet leaves this tick
        # short of terminal, and the next tick re-enters the barrier — which
        # is why every step above tolerates being repeated.
        observed = backend.observe(allocation)
        if not observed.phase.is_terminal:
            return

        # 6. mark terminal, once the resource plane agrees it is gone.
        #
        # The two rows record two different facts, and conflating them
        # loses the one that matters. The *allocation* records what the
        # resource plane says became of the attempt — if it was preempted a
        # second before our cancel reached it, that is its history and it
        # is worth keeping. The *workload* records why we ended it, which
        # only we know: a scheduler asked about a job it cancelled on our
        # instruction can say "cancelled" and nothing more. Letting its
        # answer win here made an idle-reclaimed session, an
        # admin-cancelled one and a user-cancelled one all report
        # USER_CANCELLED — telling a user they cancelled a session the
        # system took back, and telling an operator auditing released GPUs
        # something that was simply not true.
        allocation.phase = observed.phase
        allocation.reason = observed.reason or reason
        allocation.diagnostics = dict(observed.diagnostics)
        self._store.save_allocation(allocation)
        workload.phase = (
            Phase.CANCELLED if observed.phase is Phase.CANCELLED else observed.phase
        )
        workload.reason = reason
        self._store.save_workload(workload)
        report.cancelled += 1
        self._emit(
            "workload_terminal",
            {
                "workload_id": workload.id,
                "phase": workload.phase.value,
                "reason": workload.reason.value if workload.reason else None,
            },
        )

    # --- recovery ---------------------------------------------------------

    def recover(self, workload: Workload, *, reason: Reason) -> Allocation | None:
        """Start a new epoch after a lost allocation (INV-6/INV-7).

        History is not rewritten: the old allocation keeps its terminal
        phase, the workload's epoch increments, and the next tick submits
        under a *new* token. An old epoch's callbacks are refused by whoever
        holds the epoch fence, which is why this returns the new allocation
        rather than mutating the old one.
        """
        current = self._store.active_allocation(workload.id)
        if current is not None:
            current.phase = Phase.LOST
            current.reason = reason
            self._store.save_allocation(current)
        workload.execution_epoch += 1
        workload.phase = Phase.PENDING
        workload.reason = reason
        self._store.save_workload(workload)
        self._emit(
            "workload_recovered",
            {"workload_id": workload.id, "epoch": workload.execution_epoch},
        )
        return None

    # --- helpers ----------------------------------------------------------

    def _fail(self, workload: Workload, reason: Reason, report: TickReport) -> None:
        workload.phase = Phase.FAILED
        workload.reason = reason
        self._store.save_workload(workload)
        report.failed += 1
        self._emit(
            "workload_terminal",
            {
                "workload_id": workload.id,
                "phase": Phase.FAILED.value,
                "reason": reason.value,
            },
        )

    def _emit(self, kind: str, payload: dict) -> None:
        try:
            self._on_event(kind, payload)
        except Exception:  # noqa: BLE001 — a listener must not break the loop
            pass


def _store_is_gone(exc: BaseException) -> bool:
    """Is this the database having been closed under us?

    Matched on the message because sqlite3 raises a plain ProgrammingError
    for it; narrow enough that a genuine programming error still surfaces as
    one rather than quietly stopping the loop.
    """
    return "closed database" in str(exc).lower()


def new_token() -> SubmissionToken:
    """Re-exported so a store implementation need not import models directly."""
    return SubmissionToken.mint()


__all__ = [
    "DEFAULT_INTERVAL_S",
    "Reconciler",
    "TickReport",
    "WorkloadStore",
    "new_token",
]
