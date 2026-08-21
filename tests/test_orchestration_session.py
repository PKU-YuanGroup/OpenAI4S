"""Persistent sessions on a resource plane: readiness, leases, recovery.

M3b-3/4/5. Everything here runs against a real Store and the real
Reconciler with a fake backend, because the claims being made are about
what is *durable* after a step — an in-memory double would assert that the
code did what it was written to do, which was never the question.

The three things worth failing over:

- INV-5 says a session is running only when all four conditions hold. The
  tests that matter are the ones where three hold.
- A lease is renewed by a *user*, never by a healthy worker (M3b-4). The
  test for that is a session whose kernel is perfectly alive expiring
  anyway.
- Recovery is a new epoch and an announcement, never a silent reconnect
  (INV-6/11).
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

import pytest

from openai4s.orchestration.bootstrap import (
    BootstrapAuthority,
    load_or_mint_secret,
    read_credential_file,
)
from openai4s.orchestration.models import (
    DesiredState,
    Observation,
    Phase,
    Reason,
    ResourceProfile,
    WorkloadKind,
)
from openai4s.orchestration.ports import Created
from openai4s.orchestration.reclaimer import LeaseReclaimer
from openai4s.orchestration.reconciler import Reconciler
from openai4s.orchestration.session import (
    CONNECT_ENV,
    AttemptPreparer,
    ComputeSessionManager,
    SessionReadiness,
)
from openai4s.security.permissions import is_owner_only
from openai4s.store import get_store

PROFILE = ResourceProfile(name="gpu-interactive", cpus=4, gpus=1)


# -- doubles ------------------------------------------------------------------


class FakeBackend:
    """A resource plane that records what it was handed and answers what it
    is told to answer. Deliberately not a scheduler: what these tests are
    about is the orchestration above one."""

    name = "fake"

    def __init__(self):
        self.submitted = []
        self.next_phase = Phase.PENDING
        self.next_reason = None
        self.cancelled = []

    def submit(self, *, allocation, spec, profile):
        self.submitted.append((allocation, spec))
        return Created(
            handle=type(allocation.submission_token)  # any wrapped handle
            and _handle(allocation)
        )

    def observe(self, allocation):
        return Observation(phase=self.next_phase, reason=self.next_reason)

    def cancel(self, allocation, *, reason):
        self.cancelled.append((allocation.id, reason))

    def find_by_token(self, token):
        return None

    def diagnostics(self):
        return {}


def _handle(allocation):
    from openai4s.orchestration.models import ExternalHandle

    return ExternalHandle(backend="fake", external_id=f"job-{allocation.epoch}")


class FakeGateway:
    """Registers workers on demand, keyed by (allocation, epoch) exactly as
    the real one is."""

    def __init__(self):
        self.arrivals = {}

    def arrive(self, allocation_id, epoch, registration="reg"):
        self.arrivals.setdefault((allocation_id, int(epoch)), []).append(registration)

    def await_worker(self, allocation_id, epoch, *, timeout_s):
        found = self.await_workers(
            allocation_id, epoch, expected=1, timeout_s=timeout_s
        )
        return found[0] if found else None

    def await_workers(self, allocation_id, epoch, *, expected, timeout_s):
        have = self.arrivals.get((allocation_id, int(epoch))) or []
        if len(have) < expected:
            # The real one hands back the partial set rather than nothing,
            # so a caller can say "3 of 4" instead of "not ready".
            return list(have)
        self.arrivals.pop((allocation_id, int(epoch)), None)
        return list(have)


@pytest.fixture()
def store(tmp_path):
    st = get_store(str(tmp_path / "state.db"))
    try:
        yield st
    finally:
        st.close()


@pytest.fixture()
def manager(store, tmp_path):
    return ComputeSessionManager(
        store=store,
        gateway=FakeGateway(),
        authority=BootstrapAuthority(load_or_mint_secret(tmp_path)),
        workspace_root=tmp_path / "workspaces",
        kernel_factory=lambda registration: _FakeKernel(),
    )


def test_manager_precreates_private_workspace_root_before_any_workload(store, tmp_path):
    root = tmp_path / "cluster-workspaces"
    authority = BootstrapAuthority(load_or_mint_secret(tmp_path))
    assert not root.exists()

    # Prove the mode is explicit rather than an accident of the test runner's
    # ordinary umask: this directory is the mount point bwrap must see before
    # any local kernel starts, and later holds bootstrap credentials.
    previous_umask = os.umask(0)
    try:
        ComputeSessionManager(
            store=store,
            gateway=FakeGateway(),
            authority=authority,
            workspace_root=root,
        )
    finally:
        os.umask(previous_umask)

    assert root.is_dir()
    if os.name == "posix":
        assert is_owner_only(root)


class _FakeKernel:
    def __init__(self):
        self.shutdowns = 0

    def shutdown(self):
        self.shutdowns += 1


# -- INV-5: readiness is a conjunction ---------------------------------------


def test_three_of_four_conditions_is_not_ready():
    """The reason this is a conjunction and not a boolean with a comment."""
    for missing in (
        "allocation_granted",
        "worker_registered",
        "workspace_ready",
        "kernel_ready",
    ):
        conditions = dict.fromkeys(
            (
                "allocation_granted",
                "worker_registered",
                "workspace_ready",
                "kernel_ready",
            ),
            True,
        )
        conditions[missing] = False
        readiness = SessionReadiness(**conditions)
        assert not readiness.ready, f"{missing} was false and it still said ready"
        assert readiness.blocked_on is not None


def test_readiness_names_the_missing_condition_not_just_no():
    """A spinner with no information in it is the thing being avoided."""
    assert SessionReadiness().blocked_on == "allocation"
    assert (
        SessionReadiness(allocation_granted=True, workspace_ready=True).blocked_on
        == "worker"
    )
    assert (
        SessionReadiness(
            allocation_granted=True, workspace_ready=True, worker_registered=True
        ).blocked_on
        == "kernel"
    )


def test_a_granted_allocation_alone_does_not_make_a_session_ready(store, manager):
    """The scheduler saying RUNNING is not the session being usable — the
    exact claim INV-5 exists to forbid."""
    workload = manager.request_session(
        session_id="s1", owner_user_id="u1", profile=PROFILE, backend="fake"
    )
    allocation = store.workloads.create_allocation(workload.id, 0)
    allocation.phase = Phase.ACTIVE
    store.workloads.save_allocation(allocation)

    readiness = manager.readiness("s1")
    assert readiness.allocation_granted and readiness.workspace_ready
    assert not readiness.ready
    assert readiness.blocked_on == "worker"


def test_all_four_conditions_make_it_ready(store, manager):
    workload = manager.request_session(
        session_id="s1", owner_user_id="u1", profile=PROFILE, backend="fake"
    )
    allocation = store.workloads.create_allocation(workload.id, 0)
    allocation.phase = Phase.ACTIVE
    store.workloads.save_allocation(allocation)
    manager._gateway.arrive(allocation.id, 0)

    assert manager.attach_worker("s1", timeout_s=1) is True
    assert manager.readiness("s1").ready


def test_a_worker_from_the_previous_epoch_does_not_satisfy_this_one(store, manager):
    """INV-7 at the rendezvous, not only at the credential."""
    workload = manager.request_session(
        session_id="s1", owner_user_id="u1", profile=PROFILE, backend="fake"
    )
    allocation = store.workloads.create_allocation(workload.id, 0)
    allocation.phase = Phase.ACTIVE
    store.workloads.save_allocation(allocation)
    manager._gateway.arrive(allocation.id, 0)  # a straggler from epoch 0

    workload.execution_epoch = 1
    store.workloads.save_workload(workload)

    assert manager.attach_worker("s1", timeout_s=0.1) is False
    assert not manager.readiness("s1").ready


# -- INV-9: the credential is per attempt and never persisted -----------------


def test_the_submitted_spec_carries_a_path_and_the_stored_one_carries_nothing(
    store, manager, tmp_path
):
    workload = manager.request_session(
        session_id="s1", owner_user_id="u1", profile=PROFILE, backend="fake"
    )
    backend = FakeBackend()
    preparer = AttemptPreparer(
        authority=manager._authority,
        listen_address=lambda: ("0.0.0.0", 8799),
        runtime_dir=manager.runtime_dir,
        advertise_host="daemon.example",
    )
    reconciler = Reconciler(
        store=store.workloads,
        backends={"fake": backend},
        default_backend="fake",
        prepare_attempt=preparer,
    )
    reconciler.tick()

    allocation, submitted = backend.submitted[-1]
    assert submitted.environment[CONNECT_ENV] == "daemon.example:8799"
    credential_path = Path(submitted.environment["OPENAI4S_WORKER_BOOTSTRAP_PATH"])
    assert oct(credential_path.stat().st_mode)[-3:] == "600"

    # the signature is in the file and nowhere else
    credential = read_credential_file(credential_path)
    assert credential.allocation_id == allocation.id
    assert credential.epoch == 0
    for value in submitted.environment.values():
        assert credential.signature not in value

    row = store._conn.execute(
        "SELECT spec_json FROM workloads WHERE id=?", (workload.id,)
    ).fetchone()
    assert credential.signature not in row[0]
    assert "BOOTSTRAP" not in row[0]


def test_each_attempt_gets_its_own_credential(store, manager):
    """A recovery that replayed the lost attempt's identity would be a
    replay in the sense the credential exists to prevent."""
    manager.request_session(
        session_id="s1", owner_user_id="u1", profile=PROFILE, backend="fake"
    )
    backend = FakeBackend()
    preparer = AttemptPreparer(
        authority=manager._authority,
        listen_address=lambda: ("127.0.0.1", 8799),
        runtime_dir=manager.runtime_dir,
    )
    reconciler = Reconciler(
        store=store.workloads,
        backends={"fake": backend},
        default_backend="fake",
        prepare_attempt=preparer,
    )
    reconciler.tick()
    backend.next_phase = Phase.LOST
    backend.next_reason = Reason.NODE_FAILED
    reconciler.tick()  # recovery: epoch 1
    backend.next_phase = Phase.PENDING
    backend.next_reason = None
    reconciler.tick()  # submits the replacement

    assert len(backend.submitted) == 2
    first = read_credential_file(
        Path(backend.submitted[0][1].environment["OPENAI4S_WORKER_BOOTSTRAP_PATH"])
    )
    second = read_credential_file(
        Path(backend.submitted[1][1].environment["OPENAI4S_WORKER_BOOTSTRAP_PATH"])
    )
    assert first.epoch == 0 and second.epoch == 1
    assert first.signature != second.signature
    assert first.nonce != second.nonce


def test_a_batch_workload_is_submitted_exactly_as_written(store, manager):
    """The per-attempt seam must not touch the workload kind that has no
    bootstrap — otherwise M3a's contract quietly changed."""
    from openai4s.orchestration.models import WorkloadSpec

    spec = WorkloadSpec(
        kind=WorkloadKind.BATCH, profile=PROFILE, command=("echo", "hi")
    )
    store.workloads.create_workload(spec=spec, owner_user_id="u1", backend="fake")
    backend = FakeBackend()
    reconciler = Reconciler(
        store=store.workloads,
        backends={"fake": backend},
        default_backend="fake",
        prepare_attempt=AttemptPreparer(
            authority=manager._authority,
            listen_address=lambda: None,  # would raise if consulted
            runtime_dir=manager.runtime_dir,
        ),
    )
    reconciler.tick()
    _, submitted = backend.submitted[-1]
    assert submitted.command == ("echo", "hi")
    assert CONNECT_ENV not in submitted.environment


# -- INV-6/11: recovery is a new epoch, announced -----------------------------


def test_a_lost_session_recovers_into_a_new_epoch_without_rewriting_history(
    store, manager
):
    workload = manager.request_session(
        session_id="s1", owner_user_id="u1", profile=PROFILE, backend="fake"
    )
    backend = FakeBackend()
    events = []
    lost = []
    reconciler = Reconciler(
        store=store.workloads,
        backends={"fake": backend},
        default_backend="fake",
        prepare_attempt=AttemptPreparer(
            authority=manager._authority,
            listen_address=lambda: ("127.0.0.1", 8799),
            runtime_dir=manager.runtime_dir,
        ),
        on_state_lost=lambda w, a: lost.append((w.id, a.id)),
        on_event=lambda kind, payload: events.append((kind, payload)),
    )
    reconciler.tick()
    first = store.workloads.active_allocation(workload.id)

    backend.next_phase = Phase.LOST
    backend.next_reason = Reason.NODE_FAILED
    reconciler.tick()

    # the dead attempt keeps its own terminal phase and its own reason
    allocations = {a.id: a for a in store.workloads.list_allocations(workload.id)}
    assert allocations[first.id].phase is Phase.LOST
    assert allocations[first.id].reason is Reason.NODE_FAILED

    # the workload is emphatically not terminal; it is on a new epoch
    reloaded = store.workloads.get_workload(workload.id)
    assert reloaded.phase is Phase.PENDING
    assert reloaded.execution_epoch == 1
    assert reloaded.reason is Reason.KERNEL_STATE_LOST
    assert lost == [(workload.id, first.id)]
    assert any(kind == "session_kernel_state_lost" for kind, _ in events)

    # and the next tick places a replacement rather than a second live one
    backend.next_phase = Phase.PENDING
    backend.next_reason = None
    reconciler.tick()
    live = store.workloads.active_allocation(workload.id)
    assert live is not None and live.id != first.id and live.epoch == 1


def test_recovery_tells_the_session_its_kernel_memory_is_gone(store, manager):
    """INV-11. The failure being prevented is a session that reconnects
    silently and keeps answering with results it can no longer reproduce."""
    workload = manager.request_session(
        session_id="s1", owner_user_id="u1", profile=PROFILE, backend="fake"
    )
    allocation = store.workloads.create_allocation(workload.id, 0)
    allocation.phase = Phase.ACTIVE
    store.workloads.save_allocation(allocation)
    manager._gateway.arrive(allocation.id, 0)
    manager.attach_worker("s1", timeout_s=1)
    assert manager.readiness("s1").ready

    manager.note_state_lost(workload.id, epoch=0)

    readiness = manager.readiness("s1")
    assert not readiness.ready
    assert readiness.blocked_on == "worker"
    assert manager.runtime("s1").state_lost_epochs == [0]


def test_recovery_is_bounded(store, manager):
    """A node that kills every worker it is handed must not be resubmitted
    to forever."""
    workload = manager.request_session(
        session_id="s1", owner_user_id="u1", profile=PROFILE, backend="fake"
    )
    backend = FakeBackend()
    reconciler = Reconciler(
        store=store.workloads,
        backends={"fake": backend},
        default_backend="fake",
        max_recoveries=2,
        prepare_attempt=AttemptPreparer(
            authority=manager._authority,
            listen_address=lambda: ("127.0.0.1", 8799),
            runtime_dir=manager.runtime_dir,
        ),
    )
    for _ in range(12):
        backend.next_phase = Phase.PENDING
        backend.next_reason = None
        reconciler.tick()  # submit
        backend.next_phase = Phase.LOST
        backend.next_reason = Reason.NODE_FAILED
        reconciler.tick()  # lose it

    reloaded = store.workloads.get_workload(workload.id)
    assert reloaded.phase is Phase.LOST, "it should have stopped recovering"
    assert reloaded.execution_epoch == 2


def test_a_session_being_cancelled_is_not_recovered(store, manager):
    """Losing a node while being torn down is not a recovery — the cancel
    barrier owns that workload, and resurrecting it would resubmit work the
    user asked to stop."""
    workload = manager.request_session(
        session_id="s1", owner_user_id="u1", profile=PROFILE, backend="fake"
    )
    backend = FakeBackend()
    reconciler = Reconciler(
        store=store.workloads,
        backends={"fake": backend},
        default_backend="fake",
        prepare_attempt=AttemptPreparer(
            authority=manager._authority,
            listen_address=lambda: ("127.0.0.1", 8799),
            runtime_dir=manager.runtime_dir,
        ),
    )
    reconciler.tick()
    store.workloads.request_stop(workload.id, reason=Reason.USER_CANCELLED)
    backend.next_phase = Phase.LOST
    backend.next_reason = Reason.NODE_FAILED
    reconciler.tick()
    reconciler.tick()

    reloaded = store.workloads.get_workload(workload.id)
    assert reloaded.phase.is_terminal
    assert reloaded.execution_epoch == 0
    assert reloaded.desired_state is DesiredState.STOPPED


# -- M3b-4: leases -----------------------------------------------------------


class _Clock:
    """A clock the test moves. Started near a real epoch millisecond so a
    row written by the Store's own clock and a deadline computed by this
    one are comparable — a fake starting at zero silently makes every
    lease look freshly created, and the sweep then correctly finds nothing
    to do, which reads as a passing test of the wrong thing."""

    def __init__(self, start_ms=1_700_000_000_000):
        self.now = start_ms

    def __call__(self):
        return self.now

    def advance_s(self, seconds):
        self.now += int(seconds * 1000)


@pytest.fixture()
def lease_clock(store):
    """Both ends of the deadline read the same clock."""
    clock = _Clock()
    store.leases._clock_ms = clock
    return clock


def test_a_healthy_worker_does_not_keep_a_session_alive(store, manager, lease_clock):
    """The load-bearing subtlety of M3b-4, stated as a test: a session
    whose kernel is perfectly alive still expires if nobody used it."""
    workload = manager.request_session(
        session_id="s1", owner_user_id="u1", profile=PROFILE, backend="fake"
    )
    store.leases.open_lease(workload.id, idle_ttl_s=3600, max_lifetime_s=48 * 3600)

    clock = lease_clock
    reclaimer = LeaseReclaimer(
        leases=store.leases, workloads=store.workloads, clock_ms=clock
    )
    # a worker attaches, stays connected, and heartbeats — none of which is
    # a user, and none of which may touch the lease
    allocation = store.workloads.create_allocation(workload.id, 0)
    allocation.phase = Phase.ACTIVE
    store.workloads.save_allocation(allocation)
    manager._gateway.arrive(allocation.id, 0)
    manager.attach_worker("s1", timeout_s=1)

    clock.advance_s(3601)
    report = reclaimer.sweep()

    assert report.expired == 1 and report.stopped == 1
    reloaded = store.workloads.get_workload(workload.id)
    assert reloaded.desired_state is DesiredState.STOPPED
    assert reloaded.reason is Reason.SESSION_IDLE_TIMEOUT


def test_a_user_executing_something_renews_the_lease(store, manager, lease_clock):
    workload = manager.request_session(
        session_id="s1", owner_user_id="u1", profile=PROFILE, backend="fake"
    )
    clock = lease_clock
    store.leases.open_lease(workload.id, idle_ttl_s=3600, max_lifetime_s=48 * 3600)
    reclaimer = LeaseReclaimer(
        leases=store.leases, workloads=store.workloads, clock_ms=clock
    )

    clock.advance_s(3000)
    assert manager.touch("s1") is True
    clock.advance_s(3000)  # 6000s in total, but only 3000 since the user

    assert reclaimer.sweep().expired == 0
    assert (
        store.workloads.get_workload(workload.id).desired_state is DesiredState.RUNNING
    )


def test_a_touch_after_the_sweep_snapshot_wins_the_expiry_cas(
    store, manager, lease_clock, monkeypatch
):
    """Renewal and reclamation are one race, not two independent writes.

    The old sweep decided from a stale ``live_leases`` object and then wrote
    STOPPED plus released the lease even when ``touch`` had already returned
    True. Interpose the touch at the repository CAS boundary to make that
    ordering deterministic.
    """
    workload = manager.request_session(
        session_id="s1", owner_user_id="u1", profile=PROFILE, backend="fake"
    )
    clock = lease_clock
    store.leases.open_lease(workload.id, idle_ttl_s=60, max_lifetime_s=7200)
    reclaimer = LeaseReclaimer(
        leases=store.leases, workloads=store.workloads, clock_ms=clock
    )
    clock.advance_s(61)

    expire = store.leases.expire_if_unchanged

    def touch_then_expire(lease, *, now_ms, reason):
        assert store.leases.touch(lease.workload_id) is True
        return expire(lease, now_ms=now_ms, reason=reason)

    monkeypatch.setattr(store.leases, "expire_if_unchanged", touch_then_expire)
    report = reclaimer.sweep()

    assert report.expired == 0 and report.stopped == 0
    assert store.leases.get(workload.id).released_at is None
    assert (
        store.workloads.get_workload(workload.id).desired_state is DesiredState.RUNNING
    )


def test_the_maximum_lifetime_cannot_be_renewed_past(store, manager, lease_clock):
    """And it is reported as itself: 'come back later' is the wrong thing
    to tell somebody whose session cannot come back."""
    workload = manager.request_session(
        session_id="s1", owner_user_id="u1", profile=PROFILE, backend="fake"
    )
    clock = lease_clock
    store.leases.open_lease(workload.id, idle_ttl_s=3600, max_lifetime_s=7200)
    reclaimer = LeaseReclaimer(
        leases=store.leases, workloads=store.workloads, clock_ms=clock
    )

    for _ in range(4):  # a user busily working the whole time
        clock.advance_s(1800)
        manager.touch("s1")
        reclaimer.sweep()

    reloaded = store.workloads.get_workload(workload.id)
    assert reloaded.desired_state is DesiredState.STOPPED
    assert reloaded.reason is Reason.SESSION_MAX_LIFETIME_EXCEEDED


def test_a_touch_racing_the_maximum_lifetime_cannot_renew_it(
    store, manager, lease_clock, monkeypatch
):
    """The maximum lifetime is a hard bound even at the CAS boundary."""
    workload = manager.request_session(
        session_id="s1", owner_user_id="u1", profile=PROFILE, backend="fake"
    )
    clock = lease_clock
    store.leases.open_lease(workload.id, idle_ttl_s=3600, max_lifetime_s=60)
    reclaimer = LeaseReclaimer(
        leases=store.leases, workloads=store.workloads, clock_ms=clock
    )
    clock.advance_s(61)

    expire = store.leases.expire_if_unchanged

    def touch_then_expire(lease, *, now_ms, reason):
        assert reason is Reason.SESSION_MAX_LIFETIME_EXCEEDED
        assert store.leases.touch(lease.workload_id) is True
        return expire(lease, now_ms=now_ms, reason=reason)

    monkeypatch.setattr(store.leases, "expire_if_unchanged", touch_then_expire)
    report = reclaimer.sweep()

    assert report.expired == 1 and report.stopped == 1
    assert store.leases.get(workload.id).released_at is not None
    reloaded = store.workloads.get_workload(workload.id)
    assert reloaded.desired_state is DesiredState.STOPPED
    assert reloaded.reason is Reason.SESSION_MAX_LIFETIME_EXCEEDED


def test_an_expired_lease_is_swept_once(store, manager, lease_clock):
    workload = manager.request_session(
        session_id="s1", owner_user_id="u1", profile=PROFILE, backend="fake"
    )
    clock = lease_clock
    store.leases.open_lease(workload.id, idle_ttl_s=60, max_lifetime_s=7200)
    reclaimer = LeaseReclaimer(
        leases=store.leases, workloads=store.workloads, clock_ms=clock
    )
    clock.advance_s(61)
    assert reclaimer.sweep().expired == 1
    assert reclaimer.sweep().examined == 0
    assert store.leases.touch(workload.id) is False


def test_the_reclaimer_reports_whether_it_is_actually_sweeping(store):
    """'Is there a thread object' is not the same question as 'is this
    loop running' — answering the first while meaning the second sent one
    investigation down a dead end already."""
    reclaimer = LeaseReclaimer(
        leases=store.leases, workloads=store.workloads, sweep_s=0.01
    )
    assert reclaimer.running() is False
    reclaimer.start()
    try:
        assert reclaimer.running() is True
    finally:
        reclaimer.stop()
    assert reclaimer.running() is False
    reclaimer.start()  # a stopped loop must be restartable
    try:
        assert reclaimer.running() is True
    finally:
        reclaimer.stop()


# -- bindings ----------------------------------------------------------------


def test_a_session_keeps_one_workload_across_a_recovery(store, manager):
    """Recovery is a new epoch on the same workload; a binding that
    refused to be rewritten would make it look like a new session."""
    workload = manager.request_session(
        session_id="s1", owner_user_id="u1", profile=PROFILE, backend="fake"
    )
    store.leases.bind_session("s1", workload.id)  # idempotent re-bind
    assert store.leases.workload_for_session("s1") == workload.id
    assert store.leases.session_for_workload(workload.id) == "s1"


def test_asking_twice_for_a_session_returns_the_same_workload(store, manager):
    first = manager.request_session(
        session_id="s1", owner_user_id="u1", profile=PROFILE, backend="fake"
    )
    second = manager.request_session(
        session_id="s1", owner_user_id="u1", profile=PROFILE, backend="fake"
    )
    assert first.id == second.id


def test_a_terminal_binding_is_atomically_replaced_by_a_new_workload(store, manager):
    first = manager.request_session(
        session_id="s1", owner_user_id="u1", profile=PROFILE, backend="fake"
    )
    first.phase = Phase.COMPLETED
    store.workloads.save_workload(first)

    second = manager.request_session(
        session_id="s1", owner_user_id="u1", profile=PROFILE, backend="fake"
    )

    assert second.id != first.id
    assert store.leases.workload_for_session("s1") == second.id
    assert store.leases.get(second.id).released_at is None


def test_session_workload_binding_and_lease_roll_back_together(store, manager):
    store._conn.execute(
        "CREATE TRIGGER reject_session_lease BEFORE INSERT ON leases "
        "BEGIN SELECT RAISE(ABORT, 'injected lease failure'); END"
    )
    store._conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="injected lease failure"):
        manager.request_session(
            session_id="s1", owner_user_id="u1", profile=PROFILE, backend="fake"
        )

    assert store.workloads.list_workloads() == []
    assert store.leases.workload_for_session("s1") is None


def test_concurrent_session_requests_publish_only_one_workload(
    store, manager, monkeypatch
):
    real_create = store.workloads.create_session_workload
    first_entered = threading.Event()
    release_first = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def delayed_create(**kwargs):
        nonlocal calls
        with calls_lock:
            calls += 1
            ordinal = calls
        if ordinal == 1:
            first_entered.set()
            assert release_first.wait(2)
        return real_create(**kwargs)

    monkeypatch.setattr(store.workloads, "create_session_workload", delayed_create)
    results = []

    def request():
        results.append(
            manager.request_session(
                session_id="s1", owner_user_id="u1", profile=PROFILE, backend="fake"
            )
        )

    first = threading.Thread(target=request)
    second = threading.Thread(target=request)
    first.start()
    assert first_entered.wait(2)
    second.start()
    release_first.set()
    first.join(2)
    second.join(2)

    assert not first.is_alive() and not second.is_alive()
    assert len({workload.id for workload in results}) == 1
    assert len(store.workloads.list_workloads()) == 1
    assert calls == 1


def test_releasing_a_session_asks_for_a_stop_and_ends_the_lease(store, manager):
    workload = manager.request_session(
        session_id="s1", owner_user_id="u1", profile=PROFILE, backend="fake"
    )
    assert manager.release("s1", reason=Reason.USER_CANCELLED) is True
    reloaded = store.workloads.get_workload(workload.id)
    assert reloaded.desired_state is DesiredState.STOPPED
    assert store.leases.get(workload.id).released_at is not None
    assert manager.touch("s1") is False


def test_session_cleanup_candidates_are_complete_durable_intents(store, manager):
    active = manager.request_session(
        session_id="active", owner_user_id="u1", profile=PROFILE, backend="fake"
    )
    stopped = manager.request_session(
        session_id="stopped", owner_user_id="u1", profile=PROFILE, backend="fake"
    )
    released = manager.request_session(
        session_id="released", owner_user_id="u1", profile=PROFILE, backend="fake"
    )
    missing = manager.request_session(
        session_id="missing", owner_user_id="u1", profile=PROFILE, backend="fake"
    )
    terminal = manager.request_session(
        session_id="terminal", owner_user_id="u1", profile=PROFILE, backend="fake"
    )

    store.workloads.request_stop(stopped.id, reason=Reason.USER_CANCELLED)
    store.leases.release(released.id)
    with store._lock:
        store._conn.execute("DELETE FROM leases WHERE workload_id=?", (missing.id,))
        store._conn.commit()
    terminal.phase = Phase.FAILED
    terminal.reason = Reason.WORKER_LOST
    store.workloads.save_workload(terminal)

    candidates = {
        session_id: (workload_id, reason)
        for session_id, workload_id, reason in (
            store.workloads.session_cleanup_candidates()
        )
    }
    assert "active" not in candidates
    assert candidates["stopped"] == (stopped.id, Reason.USER_CANCELLED)
    assert candidates["released"][0] == released.id
    assert candidates["missing"][0] == missing.id
    assert candidates["terminal"] == (terminal.id, Reason.WORKER_LOST)
    assert active.id not in {
        workload_id for workload_id, _reason in candidates.values()
    }


def test_session_release_rolls_back_stop_lease_and_binding_together(store, manager):
    workload = manager.request_session(
        session_id="s1", owner_user_id="u1", profile=PROFILE, backend="fake"
    )
    store._conn.execute(
        "CREATE TRIGGER reject_session_unbind BEFORE DELETE ON session_workloads "
        "BEGIN SELECT RAISE(ABORT, 'injected unbind failure'); END"
    )
    store._conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="injected unbind failure"):
        manager.release("s1", reason=Reason.USER_CANCELLED)

    reloaded = store.workloads.get_workload(workload.id)
    assert reloaded.desired_state is DesiredState.RUNNING
    assert store.leases.get(workload.id).released_at is None
    assert store.leases.workload_for_session("s1") == workload.id


def test_the_reason_a_session_was_reclaimed_survives_the_backend(store, manager):
    """A resource plane asked about a job it cancelled on our instruction
    can only say "cancelled". Letting that answer win made every teardown
    report USER_CANCELLED — telling a user they cancelled a session the
    system took back, and telling an operator auditing released GPUs
    something that was simply not true."""
    workload = manager.request_session(
        session_id="s1", owner_user_id="u1", profile=PROFILE, backend="fake"
    )
    backend = FakeBackend()
    reconciler = Reconciler(
        store=store.workloads,
        backends={"fake": backend},
        default_backend="fake",
        prepare_attempt=AttemptPreparer(
            authority=manager._authority,
            listen_address=lambda: ("127.0.0.1", 8799),
            runtime_dir=manager.runtime_dir,
        ),
    )
    reconciler.tick()
    store.workloads.request_stop(workload.id, reason=Reason.SESSION_IDLE_TIMEOUT)

    # what the plane says about the attempt: it was cancelled, and that is
    # all it can know
    backend.next_phase = Phase.CANCELLED
    backend.next_reason = Reason.USER_CANCELLED
    for _ in range(3):
        reconciler.tick()

    reloaded = store.workloads.get_workload(workload.id)
    assert reloaded.phase is Phase.CANCELLED
    assert reloaded.reason is Reason.SESSION_IDLE_TIMEOUT

    # and the attempt keeps the plane's own account of what became of it
    allocation = store.workloads.list_allocations(workload.id)[-1]
    assert allocation.reason is Reason.USER_CANCELLED


# -- M4-6: a declared strategy this version cannot honour ---------------------


def test_checkpoint_recovery_is_refused_rather_than_approximated(store, manager):
    """The refusal is the feature. A field that silently means
    WORKSPACE_ONLY tells a user who selected "checkpoint" and received a
    fresh interpreter something untrue about results they may publish."""
    from openai4s.orchestration.models import (
        RecoveryStrategy,
        UnsupportedRecoveryStrategy,
    )

    with pytest.raises(UnsupportedRecoveryStrategy, match="not supported yet"):
        manager.request_session(
            session_id="s1",
            owner_user_id="u1",
            profile=PROFILE,
            backend="fake",
            recovery=RecoveryStrategy.CHECKPOINT,
        )
    # and nothing durable was created for a request that was not honoured
    assert store.leases.workload_for_session("s1") is None
    assert store.workloads.list_workloads() == []


def test_an_unknown_strategy_is_refused_the_same_way(store, manager):
    from openai4s.orchestration.models import UnsupportedRecoveryStrategy

    with pytest.raises(UnsupportedRecoveryStrategy):
        manager.request_session(
            session_id="s1",
            owner_user_id="u1",
            profile=PROFILE,
            backend="fake",
            recovery="MAGIC",
        )


def test_the_supported_strategy_is_the_default(store, manager):
    """A caller that names nothing gets what this version actually does."""
    workload = manager.request_session(
        session_id="s1", owner_user_id="u1", profile=PROFILE, backend="fake"
    )
    assert workload is not None
    from openai4s.orchestration.models import RecoveryStrategy

    assert RecoveryStrategy.WORKSPACE_ONLY.supported
    assert not RecoveryStrategy.CHECKPOINT.supported
