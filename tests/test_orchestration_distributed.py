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

import pytest

from openai4s.orchestration.bootstrap import BootstrapAuthority, load_or_mint_secret
from openai4s.orchestration.models import (
    Allocation,
    ExternalHandle,
    ResourceProfile,
    SubmissionToken,
    TaskSpec,
)
from openai4s.orchestration.ports import TaskRunner
from openai4s.orchestration.session import ComputeSessionManager, SessionReadiness
from openai4s.orchestration.slurm.backend import SlurmBackend
from openai4s.orchestration.slurm.broker import SlurmBroker, StepSpec
from openai4s.orchestration.worker_gateway import WorkerGateway
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


def test_a_single_node_session_does_not_have_to_count(tmp_path):
    """Gang is a refinement of the worker condition, not a second one: the
    common case must not need a number nobody has."""
    assert SessionReadiness(
        allocation_granted=True,
        worker_registered=True,
        workspace_ready=True,
        kernel_ready=True,
    ).ready
