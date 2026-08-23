"""Distributed work inside an allocation, and gang readiness (M4-2/3).

Two invariants, and both are about a system that would otherwise be
plausibly wrong rather than obviously broken.

**INV-4.** A distributed task runs as a step *inside* the resource the
workload already holds. `srun` without `--jobid` allocates — a one-flag
difference between the correct behaviour and turning one interactive
session into two jobs, one of which nobody is watching and both of which
are billed. So the argv is asserted directly, and the no-handle case is
asserted to *refuse* rather than fall back to submitting.

**Gang readiness (M4-3).** A multi-node session is ready when every rank
has registered, not when the first one has. A run started against a job
whose other nodes are still being placed fails inside the user's
computation, where it looks like their bug.
"""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest

from openai4s.orchestration.bootstrap import BootstrapAuthority, load_or_mint_secret
from openai4s.orchestration.models import (
    Allocation,
    ExternalHandle,
    Phase,
    Reason,
    ResourceProfile,
    SubmissionToken,
    TaskSpec,
)
from openai4s.orchestration.ports import TaskRunner
from openai4s.orchestration.session import ComputeSessionManager, SessionReadiness
from openai4s.orchestration.slurm.backend import SlurmBackend
from openai4s.orchestration.slurm.broker import SlurmBroker, StepSpec
from openai4s.orchestration.worker_gateway import Registration, WorkerGateway
from openai4s.store import get_store

PROFILE = ResourceProfile(name="gpu-multi", cpus=8, gpus=4, nodes=2)


def _allocation(job_id: str | None = "4242") -> Allocation:
    allocation = Allocation(
        id="alloc_1",
        workload_id="wl_1",
        epoch=0,
        submission_token=SubmissionToken.mint(),
    )
    if job_id:
        allocation.handle = ExternalHandle(backend="slurm", external_id=job_id)
    return allocation


# -- INV-4: a step, never a new allocation ------------------------------------


def test_the_step_names_the_job_it_runs_inside():
    """The `--jobid` is the invariant. Without it `srun` allocates."""
    broker = SlurmBroker()
    argv = broker.build_step_argv(
        "4242",
        StepSpec(command=("python", "train.py"), tasks=8, nodes=2, cpus_per_task=4),
    )
    assert argv[0] == "srun"
    assert "--jobid=4242" in argv
    assert "--ntasks=8" in argv and "--nodes=2" in argv
    assert "--cpus-per-task=4" in argv
    assert "--export=NONE" in argv
    assert argv[argv.index("--") + 1 :] == ["python", "train.py"]


def test_a_workload_holding_nothing_is_refused_rather_than_given_a_new_job():
    """The tempting fallback is the exact behaviour INV-4 forbids."""
    backend = SlurmBackend(broker=SlurmBroker())
    with pytest.raises(RuntimeError, match="INV-4"):
        backend.run_task(_allocation(job_id=None), TaskSpec(command=("hostname",)))


def test_the_step_is_run_through_the_real_argv(monkeypatch):
    seen = {}

    def fake_runner(command, **kwargs):
        seen["command"] = list(command)
        seen["timeout"] = kwargs.get("timeout")

        class _Done:
            returncode = 0
            stdout = "node-01\nnode-02\n"
            stderr = ""

        return _Done()

    backend = SlurmBackend(broker=SlurmBroker(runner=fake_runner))
    result = backend.run_task(
        _allocation(), TaskSpec(command=("hostname",), tasks=2, nodes=2)
    )
    assert seen["command"][0] == "srun"
    assert "--jobid=4242" in seen["command"]
    assert result.output.split() == ["node-01", "node-02"]
    assert result.handle.allocation_id == "alloc_1"
    assert result.handle.tasks == 2
    assert seen["timeout"] is None


def test_a_slurm_backend_is_a_task_runner():
    """The Protocol is how a caller asks rather than assuming; a backend
    that cannot run steps must be able to say so."""
    assert isinstance(SlurmBackend(broker=SlurmBroker()), TaskRunner)


def test_a_step_refuses_a_credential_shaped_environment_name():
    """INV-9 does not stop applying because the unit got smaller: a step's
    environment is as readable as a job's."""
    with pytest.raises(ValueError, match="INV-9"):
        StepSpec(command=("x",), environment={"OPENAI4S_API_TOKEN": "shh"})


def test_a_step_refuses_an_unsafe_job_id():
    broker = SlurmBroker()
    with pytest.raises(ValueError, match="unsafe job id"):
        broker.build_step_argv("4242; rm -rf /", StepSpec(command=("x",)))


def test_a_slurm_worker_selects_the_credential_for_its_rank(monkeypatch):
    from openai4s.kernel import worker as worker_mod

    monkeypatch.setenv(
        "OPENAI4S_WORKER_BOOTSTRAP_PATH_TEMPLATE", "/shared/bootstrap-r{rank}.json"
    )
    monkeypatch.setenv("OPENAI4S_WORKER_RANK_ENV", "SLURM_PROCID")
    monkeypatch.setenv("SLURM_PROCID", "3")

    assert worker_mod._remote_credential_path() == "/shared/bootstrap-r3.json"


# -- M4-3: gang readiness -----------------------------------------------------


@pytest.fixture()
def gateway(tmp_path):
    authority = BootstrapAuthority(load_or_mint_secret(tmp_path))
    node = WorkerGateway(authority, bind=("127.0.0.1", 0))
    node.start()
    try:
        yield node, authority
    finally:
        node.stop()


def _dial(gateway, credential):
    host, port = gateway.address
    sock = socket.create_connection((host, port), timeout=10)
    sock.sendall((credential.to_json() + "\n").encode())
    sock.settimeout(10)
    data = sock.recv(4096)
    assert json.loads(data.decode().split("\n", 1)[0])["ok"] is True
    return sock


def test_every_rank_is_kept_not_just_the_last_one(gateway):
    """Keyed by (allocation, epoch) alone, rank 1 silently replaced rank 0
    and a two-node session looked like a one-node session that worked."""
    node, authority = gateway
    sockets = [
        _dial(node, authority.issue(allocation_id="alloc_1", epoch=0, rank=rank))
        for rank in (0, 1, 2)
    ]
    try:
        arrivals = node.await_workers("alloc_1", 0, expected=3, timeout_s=5)
        assert sorted(r.rank for r in arrivals) == [0, 1, 2]
    finally:
        for sock in sockets:
            sock.close()


def test_waiting_for_a_gang_returns_the_partial_set_on_timeout(gateway):
    """ "3 of 4" is a diagnosis; "not ready" is a spinner."""
    node, authority = gateway
    sock = _dial(node, authority.issue(allocation_id="alloc_1", epoch=0, rank=0))
    try:
        arrivals = node.await_workers("alloc_1", 0, expected=4, timeout_s=0.5)
        assert len(arrivals) == 1
    finally:
        sock.close()


def test_a_timed_out_wait_keeps_the_partial_set_for_the_next_one(gateway):
    """The bug this closes: the timeout path used to `pop`, so the ranks that
    had already arrived were destroyed — and `attach_worker` assigns from
    what it is handed. With a 5s attach timeout and ranks arriving further
    apart than that, attempt one took rank 0 and dropped it, attempt two saw
    only rank 1, and the gang could never complete while rank 0's socket sat
    orphaned with nothing holding a reference to close it.

    A retry has to be additive, which is the only way "3 of 4, waiting for
    the rest" can ever become "4 of 4"."""
    node, authority = gateway
    sockets = [_dial(node, authority.issue(allocation_id="alloc_1", epoch=0, rank=0))]
    try:
        first = node.await_workers("alloc_1", 0, expected=2, timeout_s=0.3)
        assert [r.rank for r in first] == [0]

        sockets.append(
            _dial(node, authority.issue(allocation_id="alloc_1", epoch=0, rank=1))
        )
        second = node.await_workers("alloc_1", 0, expected=2, timeout_s=5)
        assert sorted(r.rank for r in second) == [
            0,
            1,
        ], "the rank from the first, timed-out wait was dropped"
    finally:
        for sock in sockets:
            sock.close()


def test_an_unawaited_registration_is_reaped(gateway, monkeypatch):
    """`_arrived` was pruned only by `await_workers`, and only for the key it
    was called with — so a straggler from a superseded epoch, or a worker
    whose session was released while its job queued, held a live transport,
    its two makefile wrappers and the accepted socket for the daemon's
    lifetime. One leaked fd triple per straggler, ending in EMFILE."""
    import openai4s.orchestration.worker_gateway as wg

    monkeypatch.setattr(wg, "ORPHAN_REGISTRATION_TTL_S", 0.05)
    node, authority = gateway
    sock = _dial(node, authority.issue(allocation_id="alloc_orphan", epoch=0, rank=0))
    try:
        # The ack is sent before the registration is parked, so first wait for
        # the server-side handoff. Then do *nothing*: the final orphan must age
        # out even when no later worker arrives and nobody calls await_workers.
        deadline = time.monotonic() + 2
        while node.accepted < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert node.accepted == 1
        deadline = time.monotonic() + 2
        while node.reaped < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert node.reaped >= 1
        assert ("alloc_orphan", 0) not in node._arrived
    finally:
        sock.close()


def test_a_live_bound_session_registration_survives_orphan_housekeeping(
    tmp_path, monkeypatch
):
    """Worker readiness may precede the first Cell by ordinary user think-time.

    The live lease/allocation owns that parked socket, so the orphan TTL must
    only reap it after release, recovery or another durable fence makes the
    attempt unexpected.
    """
    import openai4s.orchestration.worker_gateway as wg

    monkeypatch.setattr(wg, "ORPHAN_REGISTRATION_TTL_S", 0.05)
    store = get_store(str(tmp_path / "state.db"))
    authority = BootstrapAuthority(load_or_mint_secret(tmp_path))
    node = WorkerGateway(authority, bind=("127.0.0.1", 0))
    node.start()
    sock = None
    try:
        manager = ComputeSessionManager(
            store=store,
            gateway=node,
            authority=authority,
            workspace_root=tmp_path / "ws",
            session_credentials_isolated=lambda backend: backend == "fake",
        )
        workload = manager.request_session(
            session_id="s1",
            owner_user_id="u1",
            profile=ResourceProfile(name="single"),
            backend="fake",
        )
        allocation = store.workloads.create_allocation(workload.id, 0)
        allocation.phase = Phase.ACTIVE
        store.workloads.save_allocation(allocation)

        sock = _dial(
            node,
            authority.issue(allocation_id=allocation.id, epoch=0, rank=0),
        )
        deadline = time.monotonic() + 2
        while node.accepted < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert node.accepted == 1
        # The accept-loop housekeeping tick is 0.5s, well past this test TTL.
        time.sleep(0.7)
        assert node.reaped == 0
        assert store.workloads.session_registration_expected(allocation.id, 0)

        assert manager.release("s1", reason=Reason.USER_CANCELLED)
        assert not store.workloads.session_registration_expected(allocation.id, 0)
        deadline = time.monotonic() + 2
        while node.reaped < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert node.reaped == 1
        assert (allocation.id, 0) not in node._arrived
    finally:
        if sock is not None:
            sock.close()
        node.stop()
        store.close()


def test_a_wait_timeout_sweeps_an_orphan_that_arrived_after_the_first_check(
    gateway, monkeypatch
):
    """An arrival can be parked while an unrelated wait is sleeping. Its
    timeout is gateway activity too, and must sweep what the first check could
    not have seen."""
    import openai4s.orchestration.worker_gateway as wg

    monkeypatch.setattr(wg, "ORPHAN_REGISTRATION_TTL_S", 0.0)
    _, authority = gateway
    # No accept thread: this test isolates the timeout branch deterministically
    # rather than letting the listener's periodic sweep win the race first.
    node = WorkerGateway(authority, bind=("127.0.0.1", 0))

    class _Transport:
        closed = False

        def close(self, *, graceful=True):
            self.closed = True

    transport = _Transport()
    orphan = Registration(
        allocation_id="alloc_orphan",
        epoch=0,
        rank=0,
        transport=transport,
        peer="test",
    )
    real_reap = node._reap_locked
    planted = False

    def reap_then_plant_once():
        nonlocal planted
        dropped = real_reap()
        if not planted:
            # `_reap_locked` is called with the gateway lock held. Plant after
            # the initial sweep to reproduce an arrival during waiter.sleep.
            node._arrived[("alloc_orphan", 0)] = [orphan]
            planted = True
        return dropped

    monkeypatch.setattr(node, "_reap_locked", reap_then_plant_once)
    node.await_workers("alloc_other", 0, expected=1, timeout_s=0.0)

    assert transport.closed is True
    assert ("alloc_orphan", 0) not in node._arrived


def test_a_late_rank_completes_the_gang(gateway):
    """The wait must not conclude on the first arrival — it re-checks."""
    node, authority = gateway
    first = _dial(node, authority.issue(allocation_id="alloc_1", epoch=0, rank=0))
    late = []

    def arrive_later():
        import time

        time.sleep(0.2)
        late.append(
            _dial(node, authority.issue(allocation_id="alloc_1", epoch=0, rank=1))
        )

    thread = threading.Thread(target=arrive_later, daemon=True)
    thread.start()
    try:
        arrivals = node.await_workers("alloc_1", 0, expected=2, timeout_s=10)
        assert len(arrivals) == 2
    finally:
        thread.join(timeout=5)
        first.close()
        for sock in late:
            sock.close()


def test_a_multi_node_session_is_not_ready_on_one_rank(tmp_path):
    """The whole point of M4-3, at the level a user sees."""
    store = get_store(str(tmp_path / "state.db"))
    try:
        authority = BootstrapAuthority(load_or_mint_secret(tmp_path))

        class _PartialGateway:
            def await_workers(self, allocation_id, epoch, *, expected, timeout_s):
                return ["rank0"]  # only one node ever shows up

            def await_worker(self, allocation_id, epoch, *, timeout_s):
                return "rank0"

        manager = ComputeSessionManager(
            store=store,
            gateway=_PartialGateway(),
            authority=authority,
            workspace_root=tmp_path / "ws",
            kernel_factory=lambda registration: object(),
            session_credentials_isolated=lambda backend: backend == "fake",
        )
        workload = manager.request_session(
            session_id="s1",
            owner_user_id="u1",
            profile=PROFILE,  # two nodes
            backend="fake",
        )
        from openai4s.orchestration.models import Phase

        allocation = store.workloads.create_allocation(workload.id, 0)
        allocation.phase = Phase.ACTIVE
        store.workloads.save_allocation(allocation)

        assert manager.attach_worker("s1", timeout_s=0.1) is False
        readiness = manager.readiness("s1")
        assert readiness.workers_expected == 2
        assert readiness.workers_registered == 1
        assert not readiness.ready
        assert readiness.blocked_on == "worker"
        # and the partial set is kept, so those workers can still be released
        assert manager.runtime("s1").registrations == ["rank0"]
    finally:
        store.close()


def test_a_reaped_partial_rank_cannot_satisfy_later_gang_readiness(tmp_path):
    """Gateway and manager share a transport while a partial gang is parked.

    If the orphan TTL closes rank 0 before rank 1 arrives, the manager must
    drop that stale registration rather than count two ranks and build the
    driver Kernel over rank 0's closed socket.
    """
    store = get_store(str(tmp_path / "state.db"))
    try:
        authority = BootstrapAuthority(load_or_mint_secret(tmp_path))

        class _Transport:
            def __init__(self):
                self.live = True

            def alive(self):
                return self.live

        class _Rank:
            def __init__(self, rank):
                self.rank = rank
                self.transport = _Transport()

        rank0 = _Rank(0)
        rank1 = _Rank(1)
        rounds = [[rank0], [rank1]]

        class _Gateway:
            def await_workers(self, allocation_id, epoch, *, expected, timeout_s):
                return rounds.pop(0)

        built = []
        manager = ComputeSessionManager(
            store=store,
            gateway=_Gateway(),
            authority=authority,
            workspace_root=tmp_path / "ws",
            kernel_factory=lambda registration: built.append(registration),
            session_credentials_isolated=lambda backend: backend == "fake",
        )
        workload = manager.request_session(
            session_id="s1",
            owner_user_id="u1",
            profile=PROFILE,
            backend="fake",
        )
        allocation = store.workloads.create_allocation(workload.id, 0)
        allocation.phase = Phase.ACTIVE
        store.workloads.save_allocation(allocation)

        assert manager.attach_worker("s1", timeout_s=0) is False
        rank0.transport.live = False  # the gateway's TTL reaper closed it
        assert manager.attach_worker("s1", timeout_s=0) is False

        readiness = manager.readiness("s1")
        assert readiness.workers_registered == 1
        assert not readiness.ready
        assert manager.runtime("s1").registrations == [rank1]
        assert built == []
    finally:
        store.close()


def test_a_single_node_session_does_not_have_to_count(tmp_path):
    """Gang is a refinement of the worker condition, not a second one: the
    common case must not need a number nobody has."""
    assert SessionReadiness(
        allocation_granted=True,
        worker_registered=True,
        workspace_ready=True,
        kernel_ready=True,
    ).ready
