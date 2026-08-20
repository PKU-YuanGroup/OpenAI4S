"""The reconciler and the local backend, against an in-memory store.

The store is a Protocol, so these run with no database and no daemon — the
state machine is the thing under test, not persistence.

The cases here are the ones the plan names as invariants, plus the two that
a reconciler gets wrong in practice: treating a backend outage as a state
change, and being unable to survive a tick that dies half way through a
cancel barrier.
"""

from __future__ import annotations

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
    backend = _FakeBackend()
    backend.observations = [Observation(phase=Phase.LOST, reason=Reason.NODE_FAILED)]
    rec = Reconciler(store=repository, backends={"local": backend})

    first = rec.tick()
    assert first.errors
    persisted_allocation = real_store.workloads.active_allocation(workload.id)
    persisted_workload = real_store.workloads.get_workload(workload.id)
    assert persisted_allocation is not None
    assert persisted_allocation.phase is Phase.ACTIVE
    assert persisted_workload.execution_epoch == 0

    repository.fail = False
    backend.observations = [Observation(phase=Phase.LOST, reason=Reason.NODE_FAILED)]
    assert not rec.tick().errors
    assert real_store.workloads.active_allocation(workload.id) is None
    recovered = real_store.workloads.get_workload(workload.id)
    assert recovered.execution_epoch == 1
    assert recovered.phase is Phase.PENDING
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
    backend = _FakeBackend()
    backend.observations = [Observation(phase=Phase.COMPLETED)]
    rec = Reconciler(store=repository, backends={"local": backend})

    assert rec.tick().errors
    assert real_store.workloads.get_workload(workload.id).phase is Phase.ACTIVE
    assert real_store.workloads.active_allocation(workload.id).phase is Phase.ACTIVE

    repository.fail = False
    backend.observations = [Observation(phase=Phase.COMPLETED)]
    assert not rec.tick().errors
    assert real_store.workloads.get_workload(workload.id).phase is Phase.COMPLETED
    assert real_store.workloads.active_allocation(workload.id) is None
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
