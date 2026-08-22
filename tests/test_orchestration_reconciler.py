"""The reconciler and the local backend, against an in-memory store.

The store is a Protocol, so these run with no database and no daemon — the
state machine is the thing under test, not persistence.

The cases here are the ones the plan names as invariants, plus the two that
a reconciler gets wrong in practice: treating a backend outage as a state
change, and being unable to survive a tick that dies half way through a
cancel barrier.
"""

from __future__ import annotations

import json
import sys
import time

import pytest

from openai4s.orchestration import (
    Allocation,
    DesiredState,
    ExternalHandle,
    Observation,
    Phase,
    Reason,
    ResourceProfile,
    SubmissionToken,
    Workload,
    WorkloadKind,
    WorkloadSpec,
)
from openai4s.orchestration.local import LocalBackend
from openai4s.orchestration.ports import Created, Existing, Rejected, Unknown
from openai4s.orchestration.reconciler import Reconciler

# -- an in-memory store -------------------------------------------------------


class _Store:
    """Enough persistence to drive the state machine, and INV-3 enforced the
    way the real schema enforces it (one live allocation per workload)."""

    def __init__(self) -> None:
        self.workloads: dict[str, Workload] = {}
        self.allocations: dict[str, Allocation] = {}
        self.saved_allocations = 0

    def add(self, workload: Workload) -> Workload:
        self.workloads[workload.id] = workload
        return workload

    def workloads_needing_attention(self):
        return [w for w in self.workloads.values() if not w.phase.is_terminal]

    def get_workload(self, workload_id: str):
        return self.workloads.get(workload_id)

    def get_allocation(self, allocation_id: str):
        return self.allocations.get(allocation_id)

    def active_allocation(self, workload_id: str):
        live = [
            a
            for a in self.allocations.values()
            if a.workload_id == workload_id and a.phase.is_active_allocation
        ]
        # The real enforcement is a partial unique index; this asserts the
        # same thing so a test can never pass with two live allocations.
        assert len(live) <= 1, f"INV-3 violated: {len(live)} live allocations"
        return live[0] if live else None

    def create_allocation(self, workload_id: str, epoch: int) -> Allocation:
        allocation = Allocation(
            id=Allocation.new_id(),
            workload_id=workload_id,
            epoch=epoch,
            submission_token=SubmissionToken.mint(),
        )
        self.allocations[allocation.id] = allocation
        return allocation

    def save_allocation(self, allocation: Allocation) -> None:
        self.saved_allocations += 1
        self.allocations[allocation.id] = allocation

    def save_workload(self, workload: Workload) -> None:
        self.workloads[workload.id] = workload

    def save_allocation_and_workload(
        self, allocation: Allocation, workload: Workload
    ) -> None:
        self.saved_allocations += 1
        self.allocations[allocation.id] = allocation
        self.workloads[workload.id] = workload

    def open_recovery_epoch(self, allocation: Allocation, workload: Workload) -> None:
        """The `WorkloadStore` Protocol's atomic retire-and-bump.

        The double was missing it, which is why `Reconciler.recover` could
        keep writing the two halves separately -- the fake answered whatever
        the method under test happened to call, so the non-atomic version
        looked fine here. Counted as one save of each, because that is what
        the real repository does in one transaction.
        """
        self.save_allocation_and_workload(allocation, workload)


class _FakeBackend:
    """A backend whose every answer a test can dictate."""

    name = "fake"

    def __init__(self) -> None:
        self.submit_results: list = []
        self.observations: list[Observation] = []
        self.token_lookup: ExternalHandle | None = None
        self.submits = 0
        self.cancels = 0
        self.token_lookups = 0

    def submit(self, *, allocation, spec, profile):
        self.submits += 1
        if self.submit_results:
            return self.submit_results.pop(0)
        return Created(handle=ExternalHandle(backend=self.name, external_id="1"))

    def observe(self, allocation):
        if self.observations:
            return self.observations.pop(0)
        return Observation(phase=Phase.ACTIVE, handle=allocation.handle)

    def cancel(self, allocation, *, reason):
        self.cancels += 1

    def find_by_token(self, token):
        self.token_lookups += 1
        return self.token_lookup

    def diagnostics(self):
        return {"backend": self.name}


class _AcknowledgingFakeBackend(_FakeBackend):
    """Optional receipt-GC capability with observable acknowledgements."""

    def __init__(self) -> None:
        super().__init__()
        self.ack_candidates: list[str] = []
        self.acknowledged: list[str] = []

    def terminal_acknowledgement_candidates(self) -> tuple[str, ...]:
        return tuple(self.ack_candidates)

    def acknowledge_terminal(self, allocation: Allocation) -> None:
        self.ack_candidates.remove(allocation.id)
        self.acknowledged.append(allocation.id)


def _workload(**kwargs) -> Workload:
    spec = WorkloadSpec(
        kind=WorkloadKind.BATCH,
        profile=ResourceProfile(name="cpu-interactive"),
        command=("true",),
    )
    return Workload(id=Workload.new_id(), spec=spec, owner_user_id="user_1", **kwargs)


def _reconciler(store, backend, **kwargs) -> Reconciler:
    return Reconciler(
        store=store, backends={"fake": backend}, default_backend="fake", **kwargs
    )


# -- the ordinary lifecycle ---------------------------------------------------


def test_a_workload_is_submitted_then_advanced_to_terminal():
    store, backend = _Store(), _FakeBackend()
    workload = store.add(_workload())
    rec = _reconciler(store, backend)

    report = rec.tick()
    assert report.submitted == 1
    assert store.workloads[workload.id].phase is Phase.PENDING

    backend.observations = [Observation(phase=Phase.ACTIVE)]
    rec.tick()
    assert store.workloads[workload.id].phase is Phase.ACTIVE

    backend.observations = [Observation(phase=Phase.COMPLETED)]
    rec.tick()
    assert store.workloads[workload.id].phase is Phase.COMPLETED
    # a terminal workload is not examined again
    assert rec.tick().examined == 0


def test_one_submission_per_workload_even_across_many_ticks():
    """INV-3: a live allocation means no new one, however often we tick."""
    store, backend = _Store(), _FakeBackend()
    store.add(_workload())
    rec = _reconciler(store, backend)
    for _ in range(5):
        rec.tick()
    assert backend.submits == 1


def test_a_rejection_fails_the_workload_with_its_reason():
    store, backend = _Store(), _FakeBackend()
    workload = store.add(_workload())
    backend.submit_results = [Rejected(reason=Reason.UNSCHEDULABLE, detail="no nodes")]
    rec = _reconciler(store, backend)

    report = rec.tick()
    assert report.failed == 1
    assert store.workloads[workload.id].phase is Phase.FAILED
    assert store.workloads[workload.id].reason is Reason.UNSCHEDULABLE


# -- INV-8 --------------------------------------------------------------------


def test_unknown_submission_is_never_retried_blindly():
    """The defect the whole mechanism exists to prevent."""
    store, backend = _Store(), _FakeBackend()
    workload = store.add(_workload())
    backend.submit_results = [Unknown(token=SubmissionToken.mint(), detail="timeout")]
    rec = _reconciler(store, backend)

    rec.tick()
    assert backend.submits == 1
    assert store.workloads[workload.id].phase is not Phase.FAILED

    # the next tick must ASK before doing anything
    backend.token_lookup = ExternalHandle(backend="fake", external_id="7")
    report = rec.tick()
    assert backend.token_lookups == 1
    assert report.adopted == 1
    assert backend.submits == 1, "it must adopt, not submit again"
    allocation = store.active_allocation(workload.id)
    assert allocation.handle.external_id == "7"
    assert allocation.phase is Phase.PENDING


def test_unknown_then_nothing_found_submits_once_more():
    """Asking is what makes the fresh submission safe."""
    store, backend = _Store(), _FakeBackend()
    store.add(_workload())
    backend.submit_results = [Unknown(token=SubmissionToken.mint())]
    rec = _reconciler(store, backend)
    rec.tick()

    backend.token_lookup = None  # nothing carries the token: it never landed
    report = rec.tick()
    assert backend.token_lookups == 1
    assert backend.submits == 2
    assert report.submitted == 1


def test_repeated_unknowns_never_accumulate_submissions():
    store, backend = _Store(), _FakeBackend()
    store.add(_workload())
    backend.submit_results = [
        Unknown(token=SubmissionToken.mint()),
        Unknown(token=SubmissionToken.mint()),
        Unknown(token=SubmissionToken.mint()),
    ]
    rec = _reconciler(store, backend)
    for _ in range(3):
        rec.tick()
    # one per tick at most, each preceded by a lookup after the first
    assert backend.submits == 3
    assert backend.token_lookups == 2


# -- outages are not state changes -------------------------------------------


def test_backend_unavailable_does_not_move_the_phase():
    store, backend = _Store(), _FakeBackend()
    workload = store.add(_workload())
    rec = _reconciler(store, backend)
    rec.tick()
    before = store.workloads[workload.id].phase

    backend.observations = [
        Observation(phase=Phase.LOST, reason=Reason.BACKEND_UNAVAILABLE)
    ]
    report = rec.tick()
    assert store.workloads[workload.id].phase is before
    assert report.advanced == 0


# -- the cancel barrier -------------------------------------------------------


def test_cancel_barrier_runs_in_order_and_reaches_terminal():
    store, backend = _Store(), _FakeBackend()
    workload = store.add(_workload())
    rec = _reconciler(store, backend)
    rec.tick()

    workload.desired_state = DesiredState.STOPPED
    backend.observations = [
        Observation(phase=Phase.CANCELLED, reason=Reason.USER_CANCELLED)
    ]
    report = rec.tick()
    assert backend.cancels == 1
    assert report.cancelled == 1
    assert store.workloads[workload.id].phase is Phase.CANCELLED
    assert store.workloads[workload.id].reason is Reason.USER_CANCELLED


def test_cancel_barrier_is_reentrant_when_the_backend_lags():
    """A backend that has not caught up leaves the barrier unfinished, and
    the next tick must be able to walk it again — the failure mode a
    non-idempotent barrier has is a permanently stranded workload."""
    store, backend = _Store(), _FakeBackend()
    workload = store.add(_workload())
    rec = _reconciler(store, backend)
    rec.tick()

    workload.desired_state = DesiredState.STOPPED
    backend.observations = [Observation(phase=Phase.ACTIVE)]  # not gone yet
    rec.tick()
    assert store.workloads[workload.id].phase is Phase.RELEASING
    assert backend.cancels == 1

    backend.observations = [Observation(phase=Phase.CANCELLED)]
    rec.tick()
    assert store.workloads[workload.id].phase is Phase.CANCELLED
    assert backend.cancels == 2, "cancel is idempotent and may be repeated"


def test_a_releasing_allocation_still_counts_as_active():
    """INV-3 covers teardown too: an allocation being released still holds a
    real job, so a new submission must not start beside it — and the cancel
    barrier must still be able to find it on its second pass."""
    store, backend = _Store(), _FakeBackend()
    workload = store.add(_workload())
    rec = _reconciler(store, backend)
    rec.tick()

    workload.desired_state = DesiredState.STOPPED
    backend.observations = [Observation(phase=Phase.ACTIVE)]  # backend lags
    rec.tick()

    allocation = store.active_allocation(workload.id)
    assert allocation is not None, "a releasing allocation must remain findable"
    assert allocation.phase is Phase.RELEASING
    assert Phase.RELEASING.is_active_allocation is True
    assert backend.submits == 1, "no new allocation may start during teardown"


def test_cancelling_an_allocation_that_was_never_placed_terminates():
    """The hang this found: an allocation row exists but no submission ever
    returned a handle. A backend asked about an allocation it has never seen
    answers SUBMITTING — not terminal — so the barrier would re-enter every
    tick and never finish, leaving the workload stuck in RELEASING forever."""
    store, backend = _Store(), _FakeBackend()
    workload = store.add(_workload())
    allocation = store.create_allocation(workload.id, 0)
    assert allocation.handle is None
    workload.desired_state = DesiredState.STOPPED
    rec = _reconciler(store, backend)

    report = rec.tick()
    assert store.workloads[workload.id].phase is Phase.CANCELLED
    assert report.cancelled == 1
    assert backend.submits == 0


def test_cancelling_an_unknown_submission_asks_before_concluding():
    """The one case where 'no handle' does NOT mean 'nothing was placed'
    (INV-8): the submission may have landed and simply not told us. Marking
    it cancelled without asking would leave a real job running unattended."""
    store, backend = _Store(), _FakeBackend()
    workload = store.add(_workload())
    allocation = store.create_allocation(workload.id, 0)
    allocation.reason = Reason.BACKEND_SUBMISSION_UNKNOWN
    store.save_allocation(allocation)
    workload.desired_state = DesiredState.STOPPED

    backend.token_lookup = ExternalHandle(backend="fake", external_id="42")
    backend.observations = [Observation(phase=Phase.CANCELLED)]
    rec = _reconciler(store, backend)
    rec.tick()

    assert backend.token_lookups == 1, "it must ask before concluding"
    assert backend.cancels == 1, "the job it found must actually be cancelled"
    assert store.workloads[workload.id].phase is Phase.CANCELLED


def test_cancelling_a_workload_with_no_allocation_is_immediate():
    store, backend = _Store(), _FakeBackend()
    workload = store.add(_workload(desired_state=DesiredState.STOPPED))
    rec = _reconciler(store, backend)
    report = rec.tick()
    assert store.workloads[workload.id].phase is Phase.CANCELLED
    assert report.cancelled == 1
    assert backend.submits == 0, "a stopped workload must never be submitted"


# -- recovery -----------------------------------------------------------------


def test_recovery_is_a_new_epoch_not_a_rewrite():
    """INV-6/INV-7: history stands, the epoch advances.

    Driven through `tick()`, because there used to be two recoveries and this
    test drove the one nothing called. The public `recover()` wrote the dead
    allocation and the epoch bump as two commits -- the split the comment in
    `_recover_session` exists to forbid, since a crash between them strands
    the workload on an epoch whose allocation already exists -- so the method
    under test could not fail in the way production could. There is one
    recovery now, and this is it.
    """
    store, backend = _Store(), _FakeBackend()
    # A SESSION: `_recover_session` refuses a BATCH by design, so a batch
    # workload would have exercised nothing.
    workload = store.add(
        Workload(
            id=Workload.new_id(),
            spec=WorkloadSpec(
                kind=WorkloadKind.SESSION,
                profile=ResourceProfile(name="cpu-interactive"),
            ),
            owner_user_id="user_1",
        )
    )
    rec = _reconciler(store, backend)
    rec.tick()
    first = store.active_allocation(workload.id)
    assert first.epoch == 0

    # The node it was placed on goes away.
    backend.observations.append(
        Observation(phase=Phase.LOST, reason=Reason.NODE_FAILED)
    )
    rec.tick()
    assert store.allocations[first.id].phase is Phase.LOST
    assert store.allocations[first.id].reason is Reason.NODE_FAILED
    assert store.workloads[workload.id].execution_epoch == 1
    assert not store.workloads[
        workload.id
    ].phase.is_terminal, "a recovered session is emphatically not terminal"

    rec.tick()
    second = store.active_allocation(workload.id)
    assert second.id != first.id
    assert second.epoch == 1
    assert second.submission_token != first.submission_token


def test_recovery_never_commits_the_dead_allocation_before_the_epoch_bump(tmp_path):
    """A crash before the atomic pair must leave a retryable old attempt."""
    from openai4s.config import Config
    from openai4s.store import get_store

    real_store = get_store(Config(data_dir=tmp_path).db_path)
    workload = real_store.workloads.create_workload(
        spec=WorkloadSpec(
            kind=WorkloadKind.SESSION,
            profile=ResourceProfile(name="cpu-interactive"),
        ),
        owner_user_id="u1",
    )
    workload.phase = Phase.ACTIVE
    real_store.workloads.save_workload(workload)
    allocation = real_store.workloads.create_allocation(workload.id, 0)
    allocation.phase = Phase.ACTIVE
    allocation.handle = ExternalHandle(backend="fake", external_id="1")
    real_store.workloads.save_allocation(allocation)

    class _FailFirstRecoveryCommit:
        def __init__(self, delegate):
            self.delegate = delegate
            self.fail = True

        def __getattr__(self, name):
            return getattr(self.delegate, name)

        def open_recovery_epoch(self, dead, recovering):
            if self.fail:
                raise RuntimeError("crash before atomic recovery commit")
            return self.delegate.open_recovery_epoch(dead, recovering)

    repository = _FailFirstRecoveryCommit(real_store.workloads)
    backend = _AcknowledgingFakeBackend()
    backend.ack_candidates = [allocation.id]
    backend.observations = [Observation(phase=Phase.LOST, reason=Reason.NODE_FAILED)]
    rec = Reconciler(store=repository, backends={"local": backend})

    first = rec.tick()
    assert first.errors
    persisted_allocation = real_store.workloads.active_allocation(workload.id)
    persisted_workload = real_store.workloads.get_workload(workload.id)
    assert persisted_allocation is not None
    assert persisted_allocation.phase is Phase.ACTIVE
    assert persisted_workload.execution_epoch == 0
    assert backend.acknowledged == []

    repository.fail = False
    backend.observations = [Observation(phase=Phase.LOST, reason=Reason.NODE_FAILED)]
    assert not rec.tick().errors
    assert real_store.workloads.active_allocation(workload.id) is None
    recovered = real_store.workloads.get_workload(workload.id)
    assert recovered.execution_epoch == 1
    assert recovered.phase is Phase.PENDING
    assert backend.acknowledged == [allocation.id]
    real_store.close()


def test_a_batch_terminal_transition_is_one_database_commit(tmp_path):
    """A failed pair leaves both rows active and the next tick retryable."""
    from openai4s.config import Config
    from openai4s.store import get_store

    real_store = get_store(Config(data_dir=tmp_path).db_path)
    workload = real_store.workloads.create_workload(
        spec=WorkloadSpec(
            kind=WorkloadKind.BATCH,
            profile=ResourceProfile(name="cpu"),
            command=("true",),
        ),
        owner_user_id="u1",
    )
    workload.phase = Phase.ACTIVE
    real_store.workloads.save_workload(workload)
    allocation = real_store.workloads.create_allocation(workload.id, 0)
    allocation.phase = Phase.ACTIVE
    allocation.handle = ExternalHandle(backend="fake", external_id="1")
    real_store.workloads.save_allocation(allocation)

    class _FailFirstPair:
        def __init__(self, delegate):
            self.delegate = delegate
            self.fail = True

        def __getattr__(self, name):
            return getattr(self.delegate, name)

        def save_allocation_and_workload(self, observed, parent):
            if self.fail:
                raise RuntimeError("crash before atomic terminal commit")
            return self.delegate.save_allocation_and_workload(observed, parent)

    repository = _FailFirstPair(real_store.workloads)
    backend = _AcknowledgingFakeBackend()
    backend.ack_candidates = [allocation.id]
    backend.observations = [Observation(phase=Phase.COMPLETED)]
    rec = Reconciler(store=repository, backends={"local": backend})

    assert rec.tick().errors
    assert real_store.workloads.get_workload(workload.id).phase is Phase.ACTIVE
    assert real_store.workloads.active_allocation(workload.id).phase is Phase.ACTIVE
    assert backend.acknowledged == []

    repository.fail = False
    backend.observations = [Observation(phase=Phase.COMPLETED)]
    assert not rec.tick().errors
    assert real_store.workloads.get_workload(workload.id).phase is Phase.COMPLETED
    assert real_store.workloads.active_allocation(workload.id) is None
    assert backend.acknowledged == [allocation.id]
    real_store.close()


# -- resilience ---------------------------------------------------------------


def test_one_broken_workload_does_not_stop_the_others():
    class _Exploding(_FakeBackend):
        def submit(self, *, allocation, spec, profile):
            if allocation.workload_id == boom.id:
                raise RuntimeError("backend on fire")
            return super().submit(allocation=allocation, spec=spec, profile=profile)

    store = _Store()
    backend = _Exploding()
    boom = store.add(_workload())
    fine = store.add(_workload())
    rec = _reconciler(store, backend)

    report = rec.tick()
    assert report.errors and boom.id in report.errors[0]
    assert store.workloads[fine.id].phase is Phase.PENDING


def test_the_loop_survives_a_tick_that_raises(monkeypatch):
    store, backend = _Store(), _FakeBackend()
    rec = _reconciler(store, backend, interval_s=0.01)
    calls = {"n": 0}

    def _boom():
        calls["n"] += 1
        raise RuntimeError("nope")

    monkeypatch.setattr(rec, "tick", _boom)
    rec.start()
    deadline = time.monotonic() + 2.0
    while calls["n"] < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    rec.stop()
    assert calls["n"] >= 2, "the loop stopped after the first failure"


# -- the local backend --------------------------------------------------------


@pytest.fixture()
def local_backend(tmp_path):
    backend = LocalBackend(log_dir=tmp_path / "logs")
    try:
        yield backend
    finally:
        backend.close()


def _local_workload(command) -> Workload:
    return Workload(
        id=Workload.new_id(),
        spec=WorkloadSpec(
            kind=WorkloadKind.BATCH,
            profile=ResourceProfile(name="cpu-interactive"),
            command=tuple(command),
        ),
        owner_user_id="user_1",
    )


def _drive_to_terminal(rec, store, workload, *, timeout_s=10.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        rec.tick()
        if store.workloads[workload.id].phase.is_terminal:
            return store.workloads[workload.id].phase
        time.sleep(0.05)
    raise AssertionError(
        f"never reached terminal; phase={store.workloads[workload.id].phase}"
    )


def _wait_local_terminal(backend, allocation, *, timeout_s=10.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        observed = backend.observe(allocation)
        if observed.phase.is_terminal:
            return observed
        time.sleep(0.01)
    raise AssertionError("local allocation never reached a terminal phase")


def test_local_backend_runs_a_real_process_to_completion(local_backend):
    store = _Store()
    workload = store.add(_local_workload([sys.executable, "-c", "print('hi')"]))
    rec = Reconciler(
        store=store, backends={"local": local_backend}, default_backend="local"
    )
    assert _drive_to_terminal(rec, store, workload) is Phase.COMPLETED


def test_local_backend_reports_a_nonzero_exit_as_failure(local_backend):
    store = _Store()
    workload = store.add(_local_workload([sys.executable, "-c", "raise SystemExit(3)"]))
    rec = Reconciler(
        store=store, backends={"local": local_backend}, default_backend="local"
    )
    assert _drive_to_terminal(rec, store, workload) is Phase.FAILED


def test_local_supervisor_preserves_a_sigkill_exit(local_backend):
    allocation = Allocation(
        id=Allocation.new_id(),
        workload_id="wl_sigkill",
        epoch=0,
        submission_token=SubmissionToken.mint(),
    )
    spec = WorkloadSpec(
        kind=WorkloadKind.BATCH,
        profile=ResourceProfile(name="cpu"),
        command=(
            sys.executable,
            "-c",
            "import os,signal; os.kill(os.getpid(), signal.SIGKILL)",
        ),
    )
    created = local_backend.submit(
        allocation=allocation, spec=spec, profile=spec.profile
    )
    assert isinstance(created, Created)
    deadline = time.monotonic() + 10
    while True:
        observed = local_backend.observe(allocation)
        if observed.phase.is_terminal:
            break
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert observed.phase is Phase.FAILED
    assert observed.reason is Reason.OUT_OF_MEMORY


def test_local_backend_cancels_a_running_process(local_backend):
    store = _Store()
    workload = store.add(
        _local_workload([sys.executable, "-c", "import time; time.sleep(60)"])
    )
    rec = Reconciler(
        store=store, backends={"local": local_backend}, default_backend="local"
    )
    rec.tick()
    rec.tick()
    assert store.workloads[workload.id].phase in (Phase.PENDING, Phase.ACTIVE)

    workload.desired_state = DesiredState.STOPPED
    assert _drive_to_terminal(rec, store, workload) is Phase.CANCELLED


def test_local_backend_cancels_descendants_after_the_leader_exits(local_backend):
    """A short-lived wrapper is not the allocation when its child survives."""
    from openai4s.execution.process_group import group_alive

    allocation = Allocation(
        id=Allocation.new_id(),
        workload_id="wl_descendant",
        epoch=0,
        submission_token=SubmissionToken.mint(),
    )
    # The wrapper launches the actual work into its inherited process group
    # and exits cleanly. This is the exact state where a leader-only poll used
    # to publish COMPLETED and make cancel return without signalling anything.
    code = (
        "import subprocess,sys; "
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])"
    )
    spec = WorkloadSpec(
        kind=WorkloadKind.BATCH,
        profile=ResourceProfile(name="cpu"),
        command=(sys.executable, "-c", code),
    )
    created = local_backend.submit(
        allocation=allocation, spec=spec, profile=spec.profile
    )
    assert isinstance(created, Created)
    job = local_backend._jobs[allocation.id]
    deadline = time.monotonic() + 5
    while job.process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert job.process.poll() == 0
    assert group_alive(job.pgid), "the child did not survive its wrapper"
    assert local_backend.observe(allocation).phase is Phase.ACTIVE

    local_backend.cancel(allocation, reason=Reason.USER_CANCELLED)

    assert not group_alive(job.pgid)
    assert local_backend.observe(allocation).phase is Phase.CANCELLED


def test_local_backend_honours_the_token_like_a_cluster_does(local_backend):
    """INV-8 on the backend every install has, so the reconciler's hardest
    path is exercised without a cluster."""
    allocation = Allocation(
        id=Allocation.new_id(),
        workload_id="wl_1",
        epoch=0,
        submission_token=SubmissionToken.mint(),
    )
    spec = WorkloadSpec(
        kind=WorkloadKind.BATCH,
        profile=ResourceProfile(name="cpu"),
        command=(sys.executable, "-c", "import time; time.sleep(5)"),
    )
    first = local_backend.submit(allocation=allocation, spec=spec, profile=spec.profile)
    assert isinstance(first, Created)
    second = local_backend.submit(
        allocation=allocation, spec=spec, profile=spec.profile
    )
    assert isinstance(second, Existing), "a repeated token must not fork a process"
    assert local_backend.find_by_token(allocation.submission_token) is not None
    assert local_backend.find_by_token(SubmissionToken.mint()) is None


def test_local_backend_refuses_beyond_its_concurrency_bound(tmp_path):
    backend = LocalBackend(log_dir=tmp_path / "logs", max_concurrent=1)
    spec = WorkloadSpec(
        kind=WorkloadKind.BATCH,
        profile=ResourceProfile(name="cpu"),
        command=(sys.executable, "-c", "import time; time.sleep(5)"),
    )
    try:
        first = backend.submit(
            allocation=Allocation(
                id=Allocation.new_id(),
                workload_id="a",
                epoch=0,
                submission_token=SubmissionToken.mint(),
            ),
            spec=spec,
            profile=spec.profile,
        )
        assert isinstance(first, Created)
        second = backend.submit(
            allocation=Allocation(
                id=Allocation.new_id(),
                workload_id="b",
                epoch=0,
                submission_token=SubmissionToken.mint(),
            ),
            spec=spec,
            profile=spec.profile,
        )
        assert isinstance(second, Rejected)
        # the same reason a cluster gives, so callers need no local branch
        assert second.reason is Reason.UNSCHEDULABLE
    finally:
        backend.close()


def test_local_backend_does_not_leak_the_daemon_environment(local_backend, tmp_path):
    """The daemon's environment holds API keys; a batch job must not inherit
    them just by existing."""
    out = tmp_path / "env.txt"
    import os

    os.environ["OPENAI4S_TEST_FAKE_SECRET"] = "must-not-propagate"
    try:
        allocation = Allocation(
            id=Allocation.new_id(),
            workload_id="wl_env",
            epoch=0,
            submission_token=SubmissionToken.mint(),
        )
        spec = WorkloadSpec(
            kind=WorkloadKind.BATCH,
            profile=ResourceProfile(name="cpu"),
            command=(
                sys.executable,
                "-c",
                f"import os;open({str(out)!r},'w').write("
                f"os.environ.get('OPENAI4S_TEST_FAKE_SECRET','ABSENT'))",
            ),
        )
        local_backend.submit(allocation=allocation, spec=spec, profile=spec.profile)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            observed = local_backend.observe(allocation)
            if observed.phase.is_terminal:
                break
            time.sleep(0.05)
        assert out.read_text() == "ABSENT"
    finally:
        os.environ.pop("OPENAI4S_TEST_FAKE_SECRET", None)


def test_restart_adopts_a_live_local_receipt_instead_of_resubmitting(tmp_path):
    """A live PID is not enough; the inherited launch lock proves its identity."""
    from openai4s.execution.process_group import group_alive

    log_dir = tmp_path / "logs"
    first = LocalBackend(log_dir=log_dir)
    second = None
    allocation = Allocation(
        id=Allocation.new_id(),
        workload_id="wl_restart_live",
        epoch=0,
        submission_token=SubmissionToken.mint(),
    )
    spec = WorkloadSpec(
        kind=WorkloadKind.BATCH,
        profile=ResourceProfile(name="cpu"),
        command=(sys.executable, "-c", "import time; time.sleep(60)"),
    )
    try:
        created = first.submit(allocation=allocation, spec=spec, profile=spec.profile)
        assert isinstance(created, Created)
        original_pid = int(created.handle.external_id)

        second = LocalBackend(log_dir=log_dir)
        found = second.find_by_token(allocation.submission_token)
        repeated = second.submit(allocation=allocation, spec=spec, profile=spec.profile)

        assert found is not None and int(found.external_id) == original_pid
        assert isinstance(repeated, Existing)
        assert int(repeated.handle.external_id) == original_pid
        assert group_alive(original_pid), "the adopted process disappeared"
    finally:
        if second is not None:
            second.cancel(allocation, reason=Reason.USER_CANCELLED)
            second.close()
        first.close()


def test_spawn_before_arm_is_stopped_before_retry_and_runs_user_code_once(
    tmp_path, monkeypatch
):
    """Crash after Popen but before its receipt is armed cannot overlap a retry."""
    log_dir = tmp_path / "logs"
    marker = tmp_path / "ran.txt"
    first = LocalBackend(log_dir=log_dir)
    original_write = first._write_receipt

    def crash_after_prepared(path, receipt):
        original_write(path, receipt)
        if receipt.get("state") == "prepared":
            raise SystemExit("simulated daemon death before arm")

    monkeypatch.setattr(first, "_write_receipt", crash_after_prepared)
    allocation = Allocation(
        id=Allocation.new_id(),
        workload_id="wl_prearm_crash",
        epoch=0,
        submission_token=SubmissionToken.mint(),
    )
    code = f"from pathlib import Path; Path({str(marker)!r}).open('a').write('ran\\n')"
    spec = WorkloadSpec(
        kind=WorkloadKind.BATCH,
        profile=ResourceProfile(name="cpu"),
        command=(sys.executable, "-c", code),
    )

    with pytest.raises(SystemExit, match="before arm"):
        first.submit(allocation=allocation, spec=spec, profile=spec.profile)

    restarted = LocalBackend(log_dir=log_dir)
    try:
        # Recovery either observed EOF or conclusively stopped the gated
        # wrapper before removing its prepared receipt. Only now is retry safe.
        assert restarted.find_by_token(allocation.submission_token) is None
        created = restarted.submit(
            allocation=allocation, spec=spec, profile=spec.profile
        )
        assert isinstance(created, Created)
        deadline = time.monotonic() + 10
        while restarted.observe(allocation).phase is Phase.ACTIVE:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert marker.read_text(encoding="utf-8").splitlines() == ["ran"]
    finally:
        restarted.close()
        first.close()


def test_crash_after_running_receipt_before_handle_adopts_the_same_process(
    tmp_path, monkeypatch
):
    """The reconciler may lose the handle response after user code was armed."""
    log_dir = tmp_path / "logs"
    first = LocalBackend(log_dir=log_dir)
    original_wait = first._wait_for_running_receipt

    def crash_before_handle(path, process):
        receipt = original_wait(path, process)
        assert receipt is not None and receipt["state"] == "running"
        raise SystemExit("simulated daemon death before handle persistence")

    monkeypatch.setattr(first, "_wait_for_running_receipt", crash_before_handle)
    allocation = Allocation(
        id=Allocation.new_id(),
        workload_id="wl_postarm_crash",
        epoch=0,
        submission_token=SubmissionToken.mint(),
    )
    spec = WorkloadSpec(
        kind=WorkloadKind.BATCH,
        profile=ResourceProfile(name="cpu"),
        command=(sys.executable, "-c", "import time; time.sleep(60)"),
    )

    with pytest.raises(SystemExit, match="handle persistence"):
        first.submit(allocation=allocation, spec=spec, profile=spec.profile)

    restarted = LocalBackend(log_dir=log_dir)
    try:
        found = restarted.find_by_token(allocation.submission_token)
        assert found is not None
        repeated = restarted.submit(
            allocation=allocation, spec=spec, profile=spec.profile
        )
        assert isinstance(repeated, Existing)
        assert repeated.handle.external_id == found.external_id
    finally:
        restarted.cancel(allocation, reason=Reason.USER_CANCELLED)
        restarted.close()
        first.close()


def test_restart_never_trusts_a_live_numeric_group_without_launch_identity(tmp_path):
    """Identity loss after adoption dynamically revokes signalling authority."""
    from openai4s.execution.process_group import group_alive
    from openai4s.orchestration.local import backend as local_backend_mod

    log_dir = tmp_path / "logs"
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    descendant_ready = tmp_path / "descendant-ready"
    first = LocalBackend(log_dir=log_dir)
    allocation = Allocation(
        id=Allocation.new_id(),
        workload_id="wl_identity_closed",
        epoch=0,
        submission_token=SubmissionToken.mint(),
    )
    code = (
        "import os,time\n"
        f"open({str(ready)!r},'w').write('ready')\n"
        f"while not os.path.exists({str(release)!r}): time.sleep(0.01)\n"
        "if os.fork() == 0:\n"
        f" open({str(descendant_ready)!r},'w').write('ready')\n"
        " time.sleep(60)\n"
        " os._exit(0)\n"
        "os._exit(0)\n"
    )
    spec = WorkloadSpec(
        kind=WorkloadKind.BATCH,
        profile=ResourceProfile(name="cpu"),
        command=(sys.executable, "-c", code),
    )
    created = first.submit(allocation=allocation, spec=spec, profile=spec.profile)
    assert isinstance(created, Created)
    allocation.handle = created.handle
    deadline = time.monotonic() + 10
    while not ready.exists():
        assert time.monotonic() < deadline
        time.sleep(0.01)

    restarted = LocalBackend(log_dir=log_dir)
    try:
        job = restarted._jobs[allocation.id]
        assert job.identity_verified is True
        release.write_text("go", encoding="utf-8")
        deadline = time.monotonic() + 10
        while not descendant_ready.exists() or local_backend_mod._identity_is_held(
            job.identity_path
        ):
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert group_alive(int(created.handle.external_id))

        assert restarted.observe(allocation).phase is Phase.ACTIVE
        assert job.identity_verified is False
        assert job.pgid is None
        repeated = restarted.submit(
            allocation=allocation, spec=spec, profile=spec.profile
        )
        assert isinstance(repeated, Existing)
        assert repeated.handle.external_id == created.handle.external_id
        restarted.cancel(allocation, reason=Reason.USER_CANCELLED)
        assert job.diagnostics["cancel"]["stopped"] is False
        assert restarted.observe(allocation).phase is Phase.ACTIVE
    finally:
        # Only the original Popen is still authorized to signal this group.
        first.close()
        restarted.close()


def test_a_fast_terminal_job_remains_discoverable_by_token_after_restart(tmp_path):
    """Receipt identity survives the process, not merely the other way around."""
    log_dir = tmp_path / "logs"
    first = LocalBackend(log_dir=log_dir)
    allocation = Allocation(
        id=Allocation.new_id(),
        workload_id="wl_fast_terminal",
        epoch=0,
        submission_token=SubmissionToken.mint(),
    )
    spec = WorkloadSpec(
        kind=WorkloadKind.BATCH,
        profile=ResourceProfile(name="cpu"),
        command=(sys.executable, "-c", "pass"),
    )
    created = first.submit(allocation=allocation, spec=spec, profile=spec.profile)
    assert isinstance(created, Created)
    allocation.handle = created.handle
    deadline = time.monotonic() + 10
    while not first.observe(allocation).phase.is_terminal:
        assert time.monotonic() < deadline
        time.sleep(0.01)

    restarted = LocalBackend(log_dir=log_dir)
    try:
        found = restarted.find_by_token(allocation.submission_token)
        repeated = restarted.submit(
            allocation=allocation, spec=spec, profile=spec.profile
        )
        assert found is not None
        assert found.external_id == created.handle.external_id
        assert isinstance(repeated, Existing)
        assert repeated.handle.external_id == created.handle.external_id
        assert restarted.observe(allocation).phase is Phase.LOST
        assert list((log_dir / ".local-job-receipts").glob("*.json"))
    finally:
        restarted.close()
        first.close()


def test_many_fast_terminal_receipts_are_reclaimed_after_durable_restart(tmp_path):
    """Committed terminal rows bound receipts, sidecars, and restart `_jobs`."""
    from openai4s.config import Config
    from openai4s.store import get_store

    store = get_store(Config(data_dir=tmp_path / "data").db_path)
    log_dir = tmp_path / "logs"
    first = LocalBackend(log_dir=log_dir, max_concurrent=2)
    restarted = None
    allocations = []
    try:
        for index in range(12):
            workload = store.workloads.create_workload(
                spec=WorkloadSpec(
                    kind=WorkloadKind.BATCH,
                    profile=ResourceProfile(name="cpu"),
                    command=(sys.executable, "-c", f"print('done-{index}')"),
                ),
                owner_user_id="u1",
            )
            allocation = store.workloads.create_allocation(workload.id, 0)
            created = first.submit(
                allocation=allocation,
                spec=workload.spec,
                profile=workload.spec.profile,
            )
            assert isinstance(created, Created)
            allocation.handle = created.handle
            allocation.phase = Phase.PENDING
            store.workloads.save_allocation_and_workload(allocation, workload)

            observed = _wait_local_terminal(first, allocation)
            allocation.phase = observed.phase
            allocation.reason = observed.reason
            allocation.diagnostics = dict(observed.diagnostics)
            workload.phase = observed.phase
            workload.reason = observed.reason
            store.workloads.save_allocation_and_workload(allocation, workload)
            allocations.append(allocation)

        receipt_dir = log_dir / ".local-job-receipts"
        assert first.diagnostics()["tracked"] == len(allocations)
        assert len(list(receipt_dir.glob("*.json"))) == len(allocations)

        # Simulate a daemon dying after the terminal pair commits but before
        # the optional acknowledgement runs. Recovery first adopts all facts;
        # the reconciler constructor then reloads the terminal pairs and GCs.
        first.close()
        restarted = LocalBackend(log_dir=log_dir)
        assert restarted.diagnostics()["tracked"] == len(allocations)
        Reconciler(
            store=store.workloads,
            backends={"local": restarted},
            default_backend="local",
        )

        assert restarted.diagnostics()["tracked"] == 0
        assert list(receipt_dir.iterdir()) == []
        for allocation in allocations:
            # A repeated acknowledgement is a no-op, while deterministic log
            # lookup remains available after the process record is evicted.
            restarted.acknowledge_terminal(allocation)
            stdout_path, stderr_path = restarted.log_paths(allocation.id)
            assert stdout_path is not None and stdout_path.exists()
            assert stderr_path is not None and stderr_path.exists()
    finally:
        if restarted is not None:
            restarted.close()
        first.close()
        store.close()


def test_uncommitted_fast_terminal_receipt_survives_restart_until_pair_commit(
    tmp_path,
):
    """A terminal observation is not permission to erase INV-8 recovery state."""
    from openai4s.config import Config
    from openai4s.store import get_store

    store = get_store(Config(data_dir=tmp_path / "data").db_path)
    log_dir = tmp_path / "logs"
    first = LocalBackend(log_dir=log_dir)
    restarted = None
    try:
        workload = store.workloads.create_workload(
            spec=WorkloadSpec(
                kind=WorkloadKind.BATCH,
                profile=ResourceProfile(name="cpu"),
                command=(sys.executable, "-c", "pass"),
            ),
            owner_user_id="u1",
        )
        allocation = store.workloads.create_allocation(workload.id, 0)
        created = first.submit(
            allocation=allocation,
            spec=workload.spec,
            profile=workload.spec.profile,
        )
        assert isinstance(created, Created)
        allocation.handle = created.handle
        allocation.phase = Phase.PENDING
        store.workloads.save_allocation_and_workload(allocation, workload)
        assert _wait_local_terminal(first, allocation).phase.is_terminal

        # Crash before the terminal observation is committed: both durable
        # rows still say PENDING, so construction must retain token lookup.
        first.close()
        restarted = LocalBackend(log_dir=log_dir)
        reconciler = Reconciler(
            store=store.workloads,
            backends={"local": restarted},
            default_backend="local",
        )
        assert restarted.find_by_token(allocation.submission_token) is not None
        assert list((log_dir / ".local-job-receipts").glob("*.json"))

        # The adopted process is now observed LOST. That paired terminal write
        # happens before the end-of-tick acknowledgement and makes GC safe.
        assert not reconciler.tick().errors
        assert store.workloads.get_workload(workload.id).phase is Phase.LOST
        assert restarted.find_by_token(allocation.submission_token) is None
        assert list((log_dir / ".local-job-receipts").iterdir()) == []
    finally:
        if restarted is not None:
            restarted.close()
        first.close()
        store.close()


def test_terminal_acknowledgement_refuses_a_still_live_local_group(tmp_path):
    """Even an inconsistent terminal database row cannot kill token safety."""
    log_dir = tmp_path / "logs"
    backend = LocalBackend(log_dir=log_dir)
    allocation = Allocation(
        id=Allocation.new_id(),
        workload_id="wl_inconsistent_terminal",
        epoch=0,
        submission_token=SubmissionToken.mint(),
        phase=Phase.COMPLETED,
    )
    spec = WorkloadSpec(
        kind=WorkloadKind.BATCH,
        profile=ResourceProfile(name="cpu"),
        command=(sys.executable, "-c", "import time; time.sleep(60)"),
    )
    try:
        assert isinstance(
            backend.submit(allocation=allocation, spec=spec, profile=spec.profile),
            Created,
        )
        with pytest.raises(RuntimeError, match="live process"):
            backend.acknowledge_terminal(allocation)
        assert allocation.id in backend._jobs
        assert backend.find_by_token(allocation.submission_token) is not None
        assert list((log_dir / ".local-job-receipts").glob("*.json"))
    finally:
        backend.close()


def test_restart_finishes_a_crash_after_terminal_ack_marker(tmp_path, monkeypatch):
    """The `.acked` rename is a durable cleanup outbox, not a new leak."""
    log_dir = tmp_path / "logs"
    first = LocalBackend(log_dir=log_dir)
    allocation = Allocation(
        id=Allocation.new_id(),
        workload_id="wl_ack_crash",
        epoch=0,
        submission_token=SubmissionToken.mint(),
    )
    spec = WorkloadSpec(
        kind=WorkloadKind.BATCH,
        profile=ResourceProfile(name="cpu"),
        command=(sys.executable, "-c", "pass"),
    )
    restarted = None
    try:
        created = first.submit(allocation=allocation, spec=spec, profile=spec.profile)
        assert isinstance(created, Created)
        allocation.handle = created.handle
        observed = _wait_local_terminal(first, allocation)
        allocation.phase = observed.phase
        allocation.reason = observed.reason

        receipt_path = first._receipt_path(allocation.id)

        def crash_after_ack_rename(ack_path):
            assert ack_path.exists()
            assert not receipt_path.exists()
            raise SystemExit("simulated crash during terminal receipt GC")

        monkeypatch.setattr(first, "_complete_acknowledgement", crash_after_ack_rename)
        with pytest.raises(SystemExit, match="receipt GC"):
            first.acknowledge_terminal(allocation)
        assert receipt_path.with_suffix(".acked").exists()

        # Construction can trust the marker because only the post-commit
        # acknowledgement path creates it, and completes every remaining unlink.
        restarted = LocalBackend(log_dir=log_dir)
        assert restarted.diagnostics()["tracked"] == 0
        assert list((log_dir / ".local-job-receipts").iterdir()) == []
    finally:
        if restarted is not None:
            restarted.close()
        first.close()


def test_an_armed_but_unconfirmed_launch_keeps_the_known_process(tmp_path, monkeypatch):
    backend = LocalBackend(log_dir=tmp_path / "logs")
    allocation = Allocation(
        id=Allocation.new_id(),
        workload_id="wl_arm_reply_lost",
        epoch=0,
        submission_token=SubmissionToken.mint(),
    )
    spec = WorkloadSpec(
        kind=WorkloadKind.BATCH,
        profile=ResourceProfile(name="cpu"),
        command=(sys.executable, "-c", "import time; time.sleep(60)"),
    )
    original_wait = backend._wait_for_running_receipt

    def lose_confirmation(path, process):
        assert original_wait(path, process) is not None
        return None

    monkeypatch.setattr(backend, "_wait_for_running_receipt", lose_confirmation)
    try:
        result = backend.submit(allocation=allocation, spec=spec, profile=spec.profile)
        assert isinstance(result, Created)
        found = backend.find_by_token(allocation.submission_token)
        assert found is not None
        assert found.external_id == result.handle.external_id
        assert backend.observe(allocation).phase is Phase.ACTIVE
    finally:
        backend.close()


def test_a_corrupt_post_arm_receipt_returns_unknown_with_its_token(
    tmp_path, monkeypatch
):
    backend = LocalBackend(log_dir=tmp_path / "logs")
    allocation = Allocation(
        id=Allocation.new_id(),
        workload_id="wl_arm_receipt_corrupt",
        epoch=0,
        submission_token=SubmissionToken.mint(),
    )
    spec = WorkloadSpec(
        kind=WorkloadKind.BATCH,
        profile=ResourceProfile(name="cpu"),
        command=(sys.executable, "-c", "import time; time.sleep(60)"),
    )
    original_wait = backend._wait_for_running_receipt

    def corrupt_confirmation(path, process):
        assert original_wait(path, process) is not None
        path.write_text("{broken", encoding="utf-8")
        return None

    monkeypatch.setattr(backend, "_wait_for_running_receipt", corrupt_confirmation)
    try:
        result = backend.submit(allocation=allocation, spec=spec, profile=spec.profile)
        assert isinstance(result, Unknown)
        assert result.token == allocation.submission_token
        assert backend.find_by_token(allocation.submission_token) is not None
    finally:
        backend.close()


def test_a_corrupt_receipt_makes_token_absence_unknown(tmp_path):
    receipt_dir = tmp_path / "logs" / ".local-job-receipts"
    receipt_dir.mkdir(parents=True)
    (receipt_dir / "corrupt.json").write_text("{not-json", encoding="utf-8")
    backend = LocalBackend(log_dir=tmp_path / "logs")
    allocation = Allocation(
        id=Allocation.new_id(),
        workload_id="wl_corrupt_receipt",
        epoch=0,
        submission_token=SubmissionToken.mint(),
    )
    spec = WorkloadSpec(
        kind=WorkloadKind.BATCH,
        profile=ResourceProfile(name="cpu"),
        command=(sys.executable, "-c", "pass"),
    )

    result = backend.submit(allocation=allocation, spec=spec, profile=spec.profile)

    assert isinstance(result, Unknown)
    assert result.token == allocation.submission_token
    with pytest.raises(RuntimeError, match="token absence is unknown"):
        backend.find_by_token(allocation.submission_token)


def test_a_running_receipt_cannot_authorize_a_different_process_group(tmp_path):
    """A held identity lock does not make a corrupt numeric PGID safe to signal."""
    log_dir = tmp_path / "logs"
    first = LocalBackend(log_dir=log_dir)
    allocation = Allocation(
        id=Allocation.new_id(),
        workload_id="wl_corrupt_pgid",
        epoch=0,
        submission_token=SubmissionToken.mint(),
    )
    spec = WorkloadSpec(
        kind=WorkloadKind.BATCH,
        profile=ResourceProfile(name="cpu"),
        command=(sys.executable, "-c", "import time; time.sleep(60)"),
    )
    created = first.submit(allocation=allocation, spec=spec, profile=spec.profile)
    assert isinstance(created, Created)
    receipt_path = next((log_dir / ".local-job-receipts").glob("*.json"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["pgid"] = int(receipt["pid"]) + 1
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    restarted = LocalBackend(log_dir=log_dir)
    try:
        assert restarted.diagnostics()["receipt_error"] is not None
        assert allocation.id not in restarted._jobs
        with pytest.raises(RuntimeError, match="token absence is unknown"):
            restarted.find_by_token(allocation.submission_token)
    finally:
        first.close()
        restarted.close()


def test_an_untracked_allocation_is_lost_not_completed(local_backend):
    """A daemon restart loses the child; inventing a successful exit for it
    would silently lose the work."""
    allocation = Allocation(
        id=Allocation.new_id(),
        workload_id="wl_gone",
        epoch=0,
        submission_token=SubmissionToken.mint(),
        handle=ExternalHandle(backend="local", external_id="999999"),
    )
    observed = local_backend.observe(allocation)
    assert observed.phase is Phase.LOST
    assert observed.reason is Reason.WORKER_LOST


# -- intent is not the reconciler's to write ---------------------------------


def test_a_reconciler_save_cannot_overwrite_a_users_cancel(tmp_path):
    """The lost update that dropped cancels in the full suite.

    A tick loads a workload, the user cancels while it is mid-pass, and the
    tick then saves. If that save writes `desired_state` from its own stale
    copy, the cancel is gone — the job keeps running and nothing records
    that the request was overwritten. Driven against the real repository,
    because the defect is in what the UPDATE names.
    """
    from openai4s.config import Config
    from openai4s.orchestration.models import DesiredState, Phase, Reason
    from openai4s.store import get_store

    store = get_store(Config(data_dir=tmp_path).db_path)
    spec = WorkloadSpec(
        kind=WorkloadKind.BATCH,
        profile=ResourceProfile(name="cpu"),
        command=("true",),
    )
    workload = store.workloads.create_workload(spec=spec, owner_user_id="u1")

    # a tick loads it (desired_state is RUNNING at this instant)
    in_flight = store.workloads.get_workload(workload.id)
    assert in_flight.desired_state is DesiredState.RUNNING

    # the user cancels while that pass is still running
    assert store.workloads.request_stop(workload.id, reason=Reason.USER_CANCELLED)

    # the tick finishes and writes what it observed
    in_flight.phase = Phase.ACTIVE
    store.workloads.save_workload(in_flight)

    # the cancel must have survived
    after = store.workloads.get_workload(workload.id)
    assert (
        after.desired_state is DesiredState.STOPPED
    ), "the reconciler overwrote the user's cancel with its own stale copy"
    assert after.phase is Phase.ACTIVE, "observed state should still be saved"
    assert after.reason is Reason.USER_CANCELLED
