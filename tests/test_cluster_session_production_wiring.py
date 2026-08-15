"""The daemon's own wiring for cluster sessions, not a rehearsal of it.

Every earlier cluster test builds the `Kernel` itself
(`tests/test_cluster_session_e2e.py:222`) or constructs its own
`ComputeSessionManager`. Both are useful — they prove the transport, the
credential and the reconciler work — and both are blind to the question
that actually decides whether the feature exists: **does the daemon do
it?** It did not. `attach_worker` had no production caller, no production
`kernel_factory` existed, `touch()` was never called, and
`ensure_reconciler()` was shutting the listener down on every
submission. CI was green throughout.

So these tests go through `SessionRunner` and only `SessionRunner`. They
build nothing the daemon would build for itself. If the wiring is
removed, they fail — which is the property the earlier tests lacked.
"""

from __future__ import annotations

import json
import re

import pytest

from openai4s.orchestration.models import Phase, ResourceProfile
from tests.test_team_auth_routes import (  # noqa: F401  (fixture reuse)
    _fast_pbkdf2,
    _free_port,
    _TeamDaemon,
)

PROFILE = ResourceProfile(name="cpu-interactive", cpus=1)


@pytest.fixture()
def daemon(tmp_path, monkeypatch):
    """A daemon with a worker listener, which is the only configuration in
    which any of this is reachable."""
    monkeypatch.setenv("OPENAI4S_WORKER_LISTEN", f"127.0.0.1:{_free_port()}")
    monkeypatch.setenv("OPENAI4S_RECONCILE_INTERVAL", "0.1")
    node = _TeamDaemon(tmp_path)
    node.seed_user("alice", "fake-pw-a")
    try:
        yield node
    finally:
        node.close()


def _session(daemon, username="alice"):
    """Returns (session_id, project_id) — `_state` needs both."""
    user = daemon.store.team.get_user_by_username(username)
    project = daemon.store.create_project(name="cluster work")
    project_id = (
        project.get("project_id") or project.get("id")
        if isinstance(project, dict)
        else project
    )
    session_id = daemon.runner.create_session(project_id, owner_user_id=user["id"])
    return session_id, project_id


def _request_cluster(daemon, session_id, username="alice"):
    user = daemon.store.team.get_user_by_username(username)
    return daemon.runner.compute_sessions.request_session(
        session_id=session_id,
        owner_user_id=user["id"],
        profile=PROFILE,
        backend="local",
    )


def _grant(daemon, workload_id):
    """Move the allocation to ACTIVE the way a reconciler tick would."""
    allocation = daemon.store.workloads.active_allocation(workload_id)
    if allocation is None:
        allocation = daemon.store.workloads.create_allocation(workload_id, 0)
    allocation.phase = Phase.ACTIVE
    daemon.store.workloads.save_allocation(allocation)
    return allocation


# -- the daemon holds up its end ---------------------------------------------


def test_the_daemon_exposes_a_manager_and_a_listener(daemon):
    assert daemon.runner.compute_sessions is not None
    assert daemon.runner.worker_gateway is not None
    assert daemon.runner.worker_gateway.address is not None
    assert daemon.runner.lease_reclaimer is not None


def test_a_users_execution_renews_the_lease_and_nothing_else_does(daemon):
    """M3b-4's whole point, asserted against the daemon rather than the
    manager: only a cell renews the lease."""
    session_id, project_id = _session(daemon)
    workload = _request_cluster(daemon, session_id)
    before = daemon.store.leases.get(workload.id).last_active_at

    # time passes and the worker is (notionally) healthy; nothing renews
    clock = [before + 60_000]
    daemon.store.leases._clock_ms = lambda: clock[0]
    assert daemon.store.leases.get(workload.id).last_active_at == before

    st = daemon.runner._state(session_id, project_id)
    daemon.runner._touch_compute_lease(st)
    after = daemon.store.leases.get(workload.id).last_active_at
    assert after > before, (
        "a user's execution did not renew the lease; every cluster session "
        "expires on the idle clock regardless of use"
    )


def test_the_cell_boundary_is_what_calls_touch(daemon):
    """Not the helper directly — the path a real execution takes. If the
    call is removed from `_prepare_language`, this fails."""
    import inspect

    from openai4s.server import gateway as gateway_mod

    source = inspect.getsource(gateway_mod.SessionRunner._prepare_language)
    assert (
        "_touch_compute_lease" in source
    ), "the Cell boundary no longer renews the lease"


# -- the kernel a cluster session actually executes in ------------------------


class _FakeTransport:
    """A worker that answers the frame protocol, so `_spawn_kernel` can run.

    Deliberately a real peer rather than a sink: the first version of this
    file recorded writes and returned "" for reads, which meant the test had
    to call the resolver directly instead of going through the production
    spawn — and a mutation that removed the production call site left it
    green. That is the exact failure mode this whole file exists to close,
    reproduced once on the way to closing it. The real transport is proven
    against a real worker in tests/test_worker_tcp_transport.py; what is
    asserted here is which transport the daemon reaches for.
    """

    def __init__(self):
        self.sent = []
        self.process = None
        self.stderr_tail = None
        self.closed = False
        self._pending = []

    def write_line(self, line):
        self.sent.append(line)
        try:
            frame = json.loads(line)
        except Exception:  # noqa: BLE001
            return
        kind = frame.get("type")
        if kind == "shutdown":
            self.closed = True
            return
        # The daemon's bootstrap probes the fresh kernel by printing a
        # one-shot marker followed by JSON, so a peer that answers nothing
        # fails the spawn. Echo the marker the daemon just sent -- that is
        # what a real worker running that code would print.
        stdout = ""
        code = str(frame.get("code") or "")
        found = re.search(r"__OPENAI4S_[A-Z_]+_[0-9a-f]{32}__", code)
        if found:
            marker = found.group(0)
            payload = "[]" if "SYMBOLS" in marker else "{}"
            stdout = marker + payload + "\n"
        self._pending.append(
            json.dumps(
                {
                    "type": "response",
                    "id": frame.get("id"),
                    "stdout": stdout,
                    "stderr": "",
                    "error": None,
                    "interrupted": False,
                    "trace": {},
                    "guards": {},
                    "usage": {},
                    "cwd": "/tmp",
                }
            )
            + "\n"
        )

    def read_line(self):
        return self._pending.pop(0) if self._pending else ""

    def alive(self):
        return not self.closed

    def interrupt(self):
        return False

    def kill(self):
        self.closed = True

    def close(self, *, graceful=True):
        self.closed = True


class _Registration:
    def __init__(self, transport):
        self.transport = transport
        self.rank = 0


def test_a_cluster_session_gets_a_kernel_over_its_workers_transport(daemon):
    """The defect this exists for: with no production wiring, a session
    that asked for a cluster ran its cells on the daemon's own machine
    while the cluster job sat there holding a GPU."""
    session_id, project_id = _session(daemon)
    workload = _request_cluster(daemon, session_id)
    allocation = _grant(daemon, workload.id)

    transport = _FakeTransport()
    manager = daemon.runner.compute_sessions
    # the worker dials in for this exact attempt
    manager._gateway._arrived[(allocation.id, 0)] = [_Registration(transport)]

    st = daemon.runner._state(session_id, project_id)
    # Through the production spawn, not through the resolver it calls: the
    # question is whether the daemon routes the session, and a test that
    # calls the resolver itself answers a different one.
    daemon.runner._spawn_kernel(st)

    kernel = st.kernels.lease("python").kernel
    assert kernel._transport is transport, (
        "the session's kernel is not on its worker's socket -- cells would run "
        "on the daemon while the cluster job holds a GPU"
    )
    assert manager.runtime(session_id).kernel_ready is True
    assert manager.readiness(session_id).ready is True


def test_a_session_that_never_asked_for_a_cluster_stays_local(daemon):
    """INV-1's shape here: the resolver must answer None for every session
    that is not on a cluster, on a daemon that has a listener."""
    session_id, project_id = _session(daemon)
    st = daemon.runner._state(session_id, project_id)
    disp = daemon.runner._ensure_runtime(st)
    assert daemon.runner._remote_kernel_factory(st, disp) is None


def test_a_granted_allocation_whose_worker_never_arrives_stays_local(daemon):
    """A queue wait is not an error and must not block a cell: the session
    runs locally for this attempt and is asked for again on the next."""
    session_id, project_id = _session(daemon)
    workload = _request_cluster(daemon, session_id)
    _grant(daemon, workload.id)

    st = daemon.runner._state(session_id, project_id)
    disp = daemon.runner._ensure_runtime(st)
    import openai4s.server.gateway as gateway_mod

    original = gateway_mod._REMOTE_ATTACH_TIMEOUT_S
    gateway_mod._REMOTE_ATTACH_TIMEOUT_S = 0.1
    try:
        assert daemon.runner._remote_kernel_factory(st, disp) is None
    finally:
        gateway_mod._REMOTE_ATTACH_TIMEOUT_S = original
    assert not daemon.runner.compute_sessions.readiness(session_id).ready


def test_a_recovery_does_not_reuse_the_dead_workers_kernel(daemon):
    """The lease key carries the epoch, so a new attempt is a new kernel
    rather than a reused lease pointing at a socket whose far end is gone."""
    session_id, project_id = _session(daemon)
    workload = _request_cluster(daemon, session_id)
    allocation = _grant(daemon, workload.id)
    manager = daemon.runner.compute_sessions
    manager._gateway._arrived[(allocation.id, 0)] = [_Registration(_FakeTransport())]

    st = daemon.runner._state(session_id, project_id)
    disp = daemon.runner._ensure_runtime(st)
    _, first_key = daemon.runner._remote_kernel_factory(st, disp)

    # the node dies and the reconciler moves the workload to a new epoch
    manager.note_state_lost(workload.id, epoch=0)
    workload.execution_epoch = 1
    daemon.store.workloads.save_workload(workload)
    allocation.phase = Phase.LOST
    daemon.store.workloads.save_allocation(allocation)
    second = daemon.store.workloads.create_allocation(workload.id, 1)
    second.phase = Phase.ACTIVE
    daemon.store.workloads.save_allocation(second)
    manager._gateway._arrived[(second.id, 1)] = [_Registration(_FakeTransport())]

    _, second_key = daemon.runner._remote_kernel_factory(st, disp)
    assert first_key != second_key, "a recovered session would reuse the dead kernel"
