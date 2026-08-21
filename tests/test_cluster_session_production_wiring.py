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
import threading
import time
from pathlib import Path

import pytest

from openai4s.orchestration.models import (
    Phase,
    ResourceProfile,
    WorkloadKind,
    WorkloadSpec,
)
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


def _wait_for(predicate, *, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


# -- the daemon holds up its end ---------------------------------------------


def test_the_daemon_exposes_a_manager_and_a_listener(daemon):
    assert daemon.runner.compute_sessions is not None
    assert daemon.runner.worker_gateway is not None
    assert daemon.runner.worker_gateway.address is not None
    assert daemon.runner.lease_reclaimer is not None


def test_terminal_cleanup_does_not_block_the_orchestration_callback(daemon):
    """A long Cell in one session cannot stall reconciliation for another."""

    first_session, first_project = _session(daemon)
    second_session, second_project = _session(daemon)
    first_workload = _request_cluster(daemon, first_session)
    second_workload = _request_cluster(daemon, second_session)
    first_state = daemon.runner._state(first_session, first_project)
    daemon.runner._state(second_session, second_project)

    entered = threading.Event()
    release_cell = threading.Event()

    def hold_session_barrier():
        with daemon.runner._session_execution(
            first_state,
            owner="user_repl",
            owner_id="long-cell",
            language="python",
            reason="test long cell",
        ):
            entered.set()
            release_cell.wait(5.0)

    holder = threading.Thread(target=hold_session_barrier, daemon=True)
    holder.start()
    assert entered.wait(1.0)

    started = time.monotonic()
    daemon.runner._on_orchestration_event(
        "workload_terminal",
        {"workload_id": first_workload.id, "reason": "WORKER_LOST"},
    )
    daemon.runner._on_orchestration_event(
        "workload_terminal",
        {"workload_id": second_workload.id, "reason": "WORKER_LOST"},
    )
    callback_elapsed = time.monotonic() - started

    try:
        assert callback_elapsed < 0.2
        assert _wait_for(
            lambda: daemon.store.leases.workload_for_session(second_session) is None
        ), "the first session's FIFO stalled cleanup of a second workload"
        assert (
            daemon.store.leases.workload_for_session(first_session) == first_workload.id
        )
    finally:
        release_cell.set()
        holder.join(2.0)
    assert not holder.is_alive()
    assert _wait_for(
        lambda: daemon.store.leases.workload_for_session(first_session) is None
    )


def test_cleanup_admission_timeout_prevents_worker_pool_head_of_line(daemon):
    """Four occupied FIFOs cannot monopolize all cleanup workers forever."""

    sessions = []
    workloads = []
    states = []
    for _index in range(5):
        session_id, project_id = _session(daemon)
        sessions.append(session_id)
        workloads.append(_request_cluster(daemon, session_id))
        states.append(daemon.runner._state(session_id, project_id))

    entered = [threading.Event() for _index in range(4)]
    release_cells = threading.Event()
    holders = []

    def hold(index):
        with daemon.runner._session_execution(
            states[index],
            owner="user_repl",
            owner_id=f"blocked-cell-{index}",
            language="python",
            reason="test occupied FIFO",
        ):
            entered[index].set()
            release_cells.wait(5.0)

    for index in range(4):
        holder = threading.Thread(target=hold, args=(index,), daemon=True)
        holders.append(holder)
        holder.start()
    assert all(event.wait(1.0) for event in entered)

    for workload in workloads[:4]:
        daemon.runner._on_orchestration_event(
            "workload_terminal",
            {"workload_id": workload.id, "reason": "WORKER_LOST"},
        )
    assert _wait_for(
        lambda: sum(
            bool(task.get("running"))
            for task in daemon.runner._orchestration_cleanup_tasks.values()
        )
        == 4
    )

    daemon.runner._on_orchestration_event(
        "workload_terminal",
        {"workload_id": workloads[4].id, "reason": "WORKER_LOST"},
    )
    try:
        assert _wait_for(
            lambda: daemon.store.leases.workload_for_session(sessions[4]) is None,
            timeout=1.5,
        ), "four queued lifecycle cleanups starved a fifth free session"
        assert all(
            daemon.store.leases.workload_for_session(session_id) == workload.id
            for session_id, workload in zip(sessions[:4], workloads[:4])
        )
    finally:
        release_cells.set()
        for holder in holders:
            holder.join(2.0)
    assert all(not holder.is_alive() for holder in holders)
    assert _wait_for(
        lambda: all(
            daemon.store.leases.workload_for_session(session_id) is None
            for session_id in sessions[:4]
        )
    )


def test_cleanup_deadline_covers_stop_admission_before_fifo_submit(daemon):
    """Four Stops cannot pin workers before lifecycle tickets even exist."""

    sessions = []
    workloads = []
    states = []
    for _index in range(5):
        session_id, project_id = _session(daemon)
        sessions.append(session_id)
        workloads.append(_request_cluster(daemon, session_id))
        states.append(daemon.runner._state(session_id, project_id))

    for state in states[:4]:
        state.stop_finished.clear()
        state.stop_requested.set()

    try:
        for workload in workloads[:4]:
            daemon.runner._on_orchestration_event(
                "workload_terminal",
                {"workload_id": workload.id, "reason": "WORKER_LOST"},
            )
        assert _wait_for(
            lambda: sum(
                bool(task.get("running"))
                for task in daemon.runner._orchestration_cleanup_tasks.values()
            )
            == 4
        )

        daemon.runner._on_orchestration_event(
            "workload_terminal",
            {"workload_id": workloads[4].id, "reason": "WORKER_LOST"},
        )
        assert _wait_for(
            lambda: daemon.store.leases.workload_for_session(sessions[4]) is None,
            timeout=1.5,
        ), "Stop pre-admission waits starved a fifth free session"
        for session_id in sessions[:4]:
            snapshot = daemon.runner.executions.snapshot(session_id)
            assert snapshot.get("owner") is None
            assert snapshot.get("queue") == []
    finally:
        for state in states[:4]:
            state.stop_requested.clear()
            state.stop_finished.set()

    assert _wait_for(
        lambda: all(
            daemon.store.leases.workload_for_session(session_id) is None
            for session_id in sessions[:4]
        )
    )


def test_cleanup_deadline_covers_legacy_turn_barrier_after_admission(daemon):
    """Legacy review-style holders cannot pin four admitted cleanup tickets."""

    sessions = []
    workloads = []
    states = []
    for _index in range(5):
        session_id, project_id = _session(daemon)
        sessions.append(session_id)
        workloads.append(_request_cluster(daemon, session_id))
        states.append(daemon.runner._state(session_id, project_id))

    entered = [threading.Event() for _index in range(4)]
    release_barriers = threading.Event()
    holders = []

    def hold_legacy_barrier(index):
        # Reviewer still uses this compatibility barrier without reserving a
        # coordinator ticket. That is the exact seam this regression pins.
        with states[index].execution_barrier():
            entered[index].set()
            release_barriers.wait(5.0)

    for index in range(4):
        holder = threading.Thread(
            target=hold_legacy_barrier, args=(index,), daemon=True
        )
        holders.append(holder)
        holder.start()
    assert all(event.wait(1.0) for event in entered)

    try:
        for workload in workloads[:4]:
            daemon.runner._on_orchestration_event(
                "workload_terminal",
                {"workload_id": workload.id, "reason": "WORKER_LOST"},
            )
        assert _wait_for(
            lambda: sum(
                bool(task.get("running"))
                for task in daemon.runner._orchestration_cleanup_tasks.values()
            )
            == 4
        )

        daemon.runner._on_orchestration_event(
            "workload_terminal",
            {"workload_id": workloads[4].id, "reason": "WORKER_LOST"},
        )
        assert _wait_for(
            lambda: daemon.store.leases.workload_for_session(sessions[4]) is None,
            timeout=1.5,
        ), "legacy turn locks starved a fifth cleanup after FIFO admission"
    finally:
        release_barriers.set()
        for holder in holders:
            holder.join(2.0)

    assert all(not holder.is_alive() for holder in holders)
    assert _wait_for(
        lambda: all(
            daemon.store.leases.workload_for_session(session_id) is None
            for session_id in sessions[:4]
        )
    )


def test_terminal_cleanup_retries_a_temporary_admission_failure(daemon, monkeypatch):
    """A queue-full/error return from emit cannot permanently lose cleanup."""

    from openai4s.execution import QueueDepthExceeded

    session_id, _project_id = _session(daemon)
    workload = _request_cluster(daemon, session_id)
    original = daemon.runner.release_session_compute
    attempts = []

    def flaky_release(*args, **kwargs):
        attempts.append(time.monotonic())
        if len(attempts) == 1:
            raise QueueDepthExceeded("temporary full session lifecycle queue")
        return original(*args, **kwargs)

    monkeypatch.setattr(daemon.runner, "release_session_compute", flaky_release)
    daemon.runner._on_orchestration_event(
        "lease_expired",
        {"workload_id": workload.id, "reason": "SESSION_IDLE_TIMEOUT"},
    )

    assert _wait_for(lambda: len(attempts) >= 2)
    assert _wait_for(
        lambda: daemon.store.leases.workload_for_session(session_id) is None
    )


def test_delayed_w1_cleanup_cannot_cancel_or_erase_rebound_w2(daemon, monkeypatch):
    """The event ABA fence covers both execution cancellation and task removal."""

    from openai4s.orchestration.models import Reason

    session_id, project_id = _session(daemon)
    state = daemon.runner._state(session_id, project_id)
    first = _request_cluster(daemon, session_id)
    original_release = daemon.runner.release_session_compute
    first_entered = threading.Event()
    release_first = threading.Event()

    def delay_first(*args, **kwargs):
        if kwargs.get("expected_workload_id") == first.id:
            first_entered.set()
            assert release_first.wait(2.0)
        return original_release(*args, **kwargs)

    monkeypatch.setattr(daemon.runner, "release_session_compute", delay_first)
    daemon.runner._on_orchestration_event(
        "workload_terminal",
        {"workload_id": first.id, "reason": "WORKER_LOST"},
    )
    assert first_entered.wait(1.0)

    # A legitimate replacement wins while W1 cleanup is delayed.
    assert daemon.runner.compute_sessions.release(
        session_id,
        reason=Reason.WORKER_LOST,
        expected_workload_id=first.id,
    )
    second = _request_cluster(daemon, session_id)

    cell_entered = threading.Event()
    finish_cell = threading.Event()

    def hold_second_cell():
        with daemon.runner._session_execution(
            state,
            owner="user_repl",
            owner_id="w2-cell",
            language="python",
            reason="test W2 cell",
        ):
            cell_entered.set()
            finish_cell.wait(5.0)

    holder = threading.Thread(target=hold_second_cell, daemon=True)
    holder.start()
    assert cell_entered.wait(1.0)
    daemon.runner._on_orchestration_event(
        "workload_terminal",
        {"workload_id": second.id, "reason": "WORKER_LOST"},
    )
    release_first.set()

    try:
        assert _wait_for(
            lambda: bool(
                daemon.runner._orchestration_cleanup_tasks.get(session_id, {}).get(
                    "running"
                )
            )
        )
        assert not state.cancel.is_set(), "stale W1 cleanup cancelled W2's Cell"
        assert daemon.store.leases.workload_for_session(session_id) == second.id
    finally:
        finish_cell.set()
        holder.join(2.0)
    assert not holder.is_alive()
    assert _wait_for(
        lambda: daemon.store.leases.workload_for_session(session_id) is None
    ), "W1 completion erased W2's pending cleanup task"


def test_startup_restores_stale_durable_session_cleanup(tmp_path, monkeypatch):
    """A terminal event lost to process death is replayed from durable state."""

    from openai4s.config import Config
    from openai4s.orchestration.models import DesiredState, Reason
    from openai4s.store import get_store

    cfg = Config(data_dir=tmp_path)
    cfg.ensure_dirs()
    store = get_store(cfg.db_path)
    workload = store.workloads.create_session_workload(
        session_id="stale-session",
        spec=WorkloadSpec(
            kind=WorkloadKind.SESSION,
            profile=PROFILE,
            command=(),
        ),
        owner_user_id="u1",
        project_id=None,
        backend="local",
        idle_ttl_s=3600,
        max_lifetime_s=7200,
    )
    store.workloads.request_stop(workload.id, reason=Reason.WORKER_LOST)
    assert (
        store.workloads.get_workload(workload.id).desired_state is DesiredState.STOPPED
    )
    store.close()

    monkeypatch.setenv("OPENAI4S_WORKER_LISTEN", f"127.0.0.1:{_free_port()}")
    node = _TeamDaemon(tmp_path)
    try:
        assert _wait_for(
            lambda: node.store.leases.workload_for_session("stale-session") is None
        )
    finally:
        node.close()


def test_blocked_terminal_cleanup_cannot_hang_daemon_close(daemon, monkeypatch):
    """Cleanup workers are daemon-owned and shutdown never joins a stuck one."""

    session_id, _project_id = _session(daemon)
    workload = _request_cluster(daemon, session_id)
    entered = threading.Event()
    unblock = threading.Event()

    def blocked_release(*_args, **_kwargs):
        entered.set()
        unblock.wait(5.0)
        return False

    monkeypatch.setattr(daemon.runner, "release_session_compute", blocked_release)
    daemon.runner._on_orchestration_event(
        "workload_terminal",
        {"workload_id": workload.id, "reason": "WORKER_LOST"},
    )
    assert entered.wait(1.0)
    assert daemon.runner._orchestration_cleanup_threads
    assert all(thread.daemon for thread in daemon.runner._orchestration_cleanup_threads)

    closed = threading.Event()

    def close_runner():
        daemon.runner.close()
        closed.set()

    closer = threading.Thread(target=close_runner, daemon=True)
    closer.start()
    returned_without_cleanup = closed.wait(1.0)
    unblock.set()
    closer.join(2.0)
    assert returned_without_cleanup, "close waited for a blocked cleanup worker"
    assert not closer.is_alive()


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
        if kind == "initialize":
            self._pending.append(
                json.dumps({"type": "initialized", "id": frame.get("id")}) + "\n"
            )
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
    initialization = json.loads(transport.sent[0])
    assert initialization["type"] == "initialize"
    assert len(initialization["skill_attestation_key"]) == 64
    assert manager.runtime(session_id).kernel_ready is True
    assert manager.readiness(session_id).ready is True


def test_a_remote_candidate_is_not_published_before_generation_commit(
    daemon, monkeypatch
):
    """Compute readiness and supervisor ownership are one commit boundary.

    Publishing ``runtime.kernel`` from the candidate factory made a failed
    generation write leave readiness pointing at the dead candidate while the
    supervisor correctly kept its prior slot.
    """
    session_id, project_id = _session(daemon)
    workload = _request_cluster(daemon, session_id)
    allocation = _grant(daemon, workload.id)
    transport = _FakeTransport()
    manager = daemon.runner.compute_sessions
    manager._gateway._arrived[(allocation.id, 0)] = [_Registration(transport)]
    st = daemon.runner._state(session_id, project_id)

    def fail_generation(*_args, **_kwargs):
        raise RuntimeError("generation store unavailable")

    monkeypatch.setattr(st.kernels, "_begin_generation", fail_generation)
    with pytest.raises(RuntimeError, match="generation store unavailable"):
        daemon.runner._spawn_kernel(st)

    runtime = manager.runtime(session_id)
    assert st.kernels.lease("python") is None
    assert runtime is None
    assert daemon.store.leases.workload_for_session(session_id) is None
    stopped = daemon.store.workloads.get_workload(workload.id)
    assert stopped.desired_state.value == "STOPPED"
    assert manager.readiness(session_id).ready is False
    assert transport.closed is True


def test_a_session_that_never_asked_for_a_cluster_stays_local(daemon):
    """INV-1's shape here: the resolver must answer None for every session
    that is not on a cluster, on a daemon that has a listener."""
    session_id, project_id = _session(daemon)
    st = daemon.runner._state(session_id, project_id)
    disp = daemon.runner._ensure_runtime(st)
    assert daemon.runner._remote_kernel_factory(st, disp) is None


def test_a_bound_session_refuses_local_execution_until_its_worker_arrives(daemon):
    """A placement request fixes the execution plane before any Cell runs.

    Running locally while queued and switching to remote later silently loses
    the local namespace and splits workspace files across two directories.
    """
    session_id, project_id = _session(daemon)
    workload = _request_cluster(daemon, session_id)
    allocation = _grant(daemon, workload.id)

    st = daemon.runner._state(session_id, project_id)
    local = st.local_workspace
    disp = daemon.runner._ensure_runtime(st)
    import openai4s.server.gateway as gateway_mod

    original = gateway_mod._REMOTE_ATTACH_TIMEOUT_S
    gateway_mod._REMOTE_ATTACH_TIMEOUT_S = 0.1
    try:
        with pytest.raises(RuntimeError, match="has not registered yet"):
            daemon.runner._ensure_kernel(st)
    finally:
        gateway_mod._REMOTE_ATTACH_TIMEOUT_S = original

    assert st.kernels.lease("python") is None
    assert st.workspace == local
    assert disp.workspace_path == local.resolve()
    assert not daemon.runner.compute_sessions.readiness(session_id).ready

    # The next ordinary ensure retries placement once rank 0 arrives, with no
    # local namespace having been created and then silently discarded.
    transport = _FakeTransport()
    daemon.runner.compute_sessions._gateway._arrived[(allocation.id, 0)] = [
        _Registration(transport)
    ]
    daemon.runner._ensure_kernel(st)

    remote = st.kernels.lease("python")
    assert remote is not None and remote.kernel._transport is transport
    assert st.workspace == daemon.runner.compute_sessions.workspace_for(workload.id)
    assert disp.workspace_path == st.workspace.resolve()


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


def test_the_session_workspace_follows_the_placement(daemon):
    """Where the cells run and where the daemon looks have to be one directory.

    The remote kernel was built with the workload's directory as its cwd while
    artifact capture, the Host dispatcher's file tools and the R kernel stayed
    on `agent-workspaces/<root_frame_id>`. So a cluster cell wrote `result.csv`
    into one directory and `capture` diffed another: empty figures, empty
    files_written, no Artifact row -- and no error to say so. Asserted through
    the production spawn: the durable workload binding is a request, while the
    attached worker selected there is the execution plane.
    """
    session_id, project_id = _session(daemon)
    st = daemon.runner._state(session_id, project_id)
    manager = daemon.runner.compute_sessions

    local = st.workspace
    daemon.runner._ensure_runtime(st)
    assert st.workspace == local, "a session with no placement must not move"

    workload = _request_cluster(daemon, session_id)
    placed = manager.workspace_for(workload.id)
    assert placed != local

    # Requesting a worker does not mean one exists. A tools-only turn (or a
    # local fallback while the scheduler is queued) must stay on the local
    # workspace and retain the sandbox deny over the cluster credential tree.
    disp = daemon.runner._ensure_runtime(st)
    assert st.workspace == local
    assert disp.workspace_path == local.resolve()

    allocation = _grant(daemon, workload.id)
    transport = _FakeTransport()
    manager._gateway._arrived[(allocation.id, 0)] = [_Registration(transport)]
    daemon.runner._ensure_kernel(st)
    assert st.workspace == placed, (
        "the session still points at its local workspace while its cells run "
        "on the workload's -- artifact capture would diff a directory nothing "
        "wrote to"
    )
    assert (
        disp.workspace_path == placed.resolve()
    ), "host.write_file would land where the remote cell cannot see it"

    # And back again: a released placement must not leave the session pointed
    # at a workload that no longer exists.
    from openai4s.orchestration.models import Reason

    daemon.runner.release_session_compute(session_id, reason=Reason.USER_CANCELLED)
    # No Python spawn is needed to restore the control plane. A tools-only
    # turn immediately after release must already use the local workspace.
    assert st.kernels.lease("python") is None
    daemon.runner._ensure_runtime(st)
    assert st.workspace == local
    assert disp.workspace_path == local.resolve()


def test_r_is_refused_on_a_cluster_session(daemon):
    """`spawn_r_kernel` starts a child of the daemon, so an ```r cell on a
    placed session ran on the head node with none of the allocated resources
    -- and worked, which is what made it silent. `host.exec_background`
    already refuses for the same reason; this is the other half."""
    session_id, project_id = _session(daemon)
    st = daemon.runner._state(session_id, project_id)

    _request_cluster(daemon, session_id)
    refusal = daemon.runner._ensure_r_kernel(st)
    assert refusal is not None and "cluster session" in refusal, refusal
    assert st.kernels.lease("r") is None, "an R worker was started anyway"


def test_a_backend_that_will_not_answer_is_unknown_not_absent(daemon):
    """INV-8 inverted: `submit` asked whether anything already carries this
    token, caught the scheduler being unreachable, and read the silence as
    "nothing does" -- then submitted. `find_by_comment` raises on a timeout
    precisely because absence of an answer is not an answer of absence."""
    from openai4s.orchestration.ports import Unknown
    from openai4s.orchestration.slurm.backend import SlurmBackend
    from openai4s.orchestration.slurm.broker import SlurmCommandError

    class _MuteBroker:
        submitted: list = []

        def find_by_comment(self, comment, *, job_name):
            raise SlurmCommandError(
                "squeue timed out", command=("squeue",), timed_out=True
            )

        def submit(self, spec):  # pragma: no cover - must not be reached
            _MuteBroker.submitted.append(spec)
            return "12345"

    cluster = daemon.runner.cluster_config
    backend = SlurmBackend(cluster=cluster, log_dir="/tmp", broker=_MuteBroker())
    workload = daemon.store.workloads.create_workload(
        spec=WorkloadSpec(
            kind=WorkloadKind.BATCH,
            profile=ResourceProfile(name="cpu"),
            command=("true",),
        ),
        owner_user_id="u",
        backend="cluster",
    )
    allocation = daemon.store.workloads.create_allocation(workload.id, 0)

    result = backend.submit(
        allocation=allocation,
        spec=workload.spec,
        profile=ResourceProfile(name="cpu"),
    )
    assert isinstance(result, Unknown), result
    assert not _MuteBroker.submitted, "a second job was submitted on a silence"
