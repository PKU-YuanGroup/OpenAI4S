"""FIFO ownership and exact-cancellation contracts for session execution."""

from __future__ import annotations

import itertools
import threading
import time

import pytest

from openai4s.execution import (
    CoordinatorClosed,
    ExecutionCancelled,
    ExecutionOwner,
    SessionExecutionCoordinator,
    TicketState,
    TicketStateError,
)


def _ids():
    values = itertools.count(1)
    return lambda: f"exec-{next(values)}"


def _wait_for(predicate, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        # Test-only observation helper. Coordinator waiters themselves use a
        # Condition and never rely on polling.
        time.sleep(0.001)
    raise AssertionError("condition was not reached before timeout")


def test_fifo_contexts_never_run_two_writers_in_one_session():
    coordinator = SessionExecutionCoordinator(id_factory=_ids())
    release = [threading.Event() for _ in range(3)]
    entered = [threading.Event() for _ in range(3)]
    order: list[int] = []
    active = 0
    maximum_active = 0
    guard = threading.Lock()

    def worker(index: int) -> None:
        nonlocal active, maximum_active
        with coordinator.execution(
            "session-a", owner="agent", owner_id=f"job-{index}"
        ):
            with guard:
                active += 1
                maximum_active = max(maximum_active, active)
                order.append(index)
            entered[index].set()
            assert release[index].wait(2)
            with guard:
                active -= 1

    threads = []
    for index in range(3):
        thread = threading.Thread(target=worker, args=(index,))
        thread.start()
        threads.append(thread)
        if index == 0:
            assert entered[0].wait(1)
        else:
            expected = index
            _wait_for(
                lambda: coordinator.snapshot("session-a")["queued_count"]
                == expected
            )

    assert order == [0]
    snapshot = coordinator.snapshot("session-a")
    assert snapshot["owner"]["owner"] == {"kind": "agent", "id": "job-0"}
    assert [item["queue_position"] for item in snapshot["queue"]] == [1, 2]

    release[0].set()
    assert entered[1].wait(1)
    release[1].set()
    assert entered[2].wait(1)
    release[2].set()
    for thread in threads:
        thread.join(2)
        assert not thread.is_alive()

    assert order == [0, 1, 2]
    assert maximum_active == 1
    assert coordinator.snapshot("session-a")["owner"] is None


def test_queued_owner_can_cancel_itself_without_touching_active_ticket():
    coordinator = SessionExecutionCoordinator(id_factory=_ids())
    active = coordinator.submit("s", owner="agent", owner_id="agent-job")
    queued = coordinator.submit("s", owner="user_repl", owner_id="cell-job")
    outcome: list[type[BaseException]] = []

    def wait() -> None:
        try:
            coordinator.wait_until_running(queued)
        except BaseException as error:  # noqa: BLE001 - asserted below
            outcome.append(type(error))

    waiter = threading.Thread(target=wait)
    waiter.start()
    assert not coordinator.cancel_queued(
        session_id="s",
        execution_id=queued.execution_id,
        owner="user_repl",
        owner_id="somebody-else",
    )
    assert coordinator.cancel_queued(
        session_id="s",
        execution_id=queued.execution_id,
        owner=queued.owner,
        reason="composer cancelled its queued cell",
    )
    waiter.join(1)

    assert outcome == [ExecutionCancelled]
    assert queued.state is TicketState.CANCELLED
    assert queued.cancellation.reason == "composer cancelled its queued cell"
    assert active.state is TicketState.RUNNING
    assert not active.cancellation.is_set()
    coordinator.complete(active)


def test_scoped_interrupt_requires_exact_session_ticket_and_owner():
    coordinator = SessionExecutionCoordinator(id_factory=_ids())
    first = coordinator.submit("s1", owner="agent", owner_id="job-a")
    queued = coordinator.submit("s1", owner="user_repl", owner_id="job-b")
    other_session = coordinator.submit("s2", owner="agent", owner_id="job-a")

    assert not coordinator.request_interrupt(
        session_id="s2",
        execution_id=first.execution_id,
        owner=first.owner,
    )
    assert not coordinator.request_interrupt(
        session_id="s1",
        execution_id=queued.execution_id,
        owner=queued.owner,
    )
    assert not coordinator.request_interrupt(
        session_id="s1",
        execution_id=first.execution_id,
        owner="agent",
        owner_id="wrong-job",
    )
    assert not first.cancellation.is_set()
    assert not queued.cancellation.is_set()
    assert not other_session.cancellation.is_set()

    assert coordinator.request_interrupt(
        session_id="s1",
        execution_id=first.execution_id,
        owner=first.owner,
        reason="user stopped this agent run",
    )
    assert first.cancellation.wait(0.1)
    assert first.cancellation.reason == "user stopped this agent run"
    assert not coordinator.request_interrupt(
        session_id="s1",
        execution_id=first.execution_id,
        owner=first.owner,
    )
    assert not queued.cancellation.is_set()
    assert not other_session.cancellation.is_set()

    # A signalled active action becomes cancelled when it relinquishes owner;
    # the next FIFO ticket is then admitted normally.
    coordinator.complete(first)
    assert first.state is TicketState.CANCELLED
    assert queued.state is TicketState.RUNNING
    coordinator.complete(queued)
    coordinator.complete(other_session)


def test_exception_safe_context_releases_owner_and_admits_next_ticket():
    coordinator = SessionExecutionCoordinator(id_factory=_ids())
    first = coordinator.submit("s", owner="agent", owner_id="one")
    second = coordinator.submit("s", owner="agent", owner_id="two")

    error = ValueError("science failed")
    assert coordinator.fail(first, error)
    assert first.state is TicketState.FAILED
    assert first.error == "ValueError: science failed"
    assert second.state is TicketState.RUNNING
    coordinator.complete(second)

    with pytest.raises(KeyboardInterrupt):
        with coordinator.execution("s", owner="user_repl", owner_id="three"):
            raise KeyboardInterrupt
    assert coordinator.snapshot("s")["owner"] is None


def test_different_sessions_are_admitted_independently():
    coordinator = SessionExecutionCoordinator(id_factory=_ids())
    first = coordinator.submit("session-a", owner="agent", owner_id="a")
    second = coordinator.submit("session-b", owner="agent", owner_id="b")

    assert first.state is TicketState.RUNNING
    assert second.state is TicketState.RUNNING
    assert coordinator.snapshot("session-a")["active_count"] == 1
    assert coordinator.snapshot("session-b")["active_count"] == 1
    coordinator.complete(first)
    assert second.state is TicketState.RUNNING
    coordinator.complete(second)


def test_close_session_wakes_waiters_signals_owner_and_rejects_new_work():
    coordinator = SessionExecutionCoordinator(id_factory=_ids())
    active = coordinator.submit("s", owner="agent", owner_id="active")
    waiting = coordinator.submit("s", owner="user_repl", owner_id="waiting")
    outcome: list[str] = []

    def wait() -> None:
        try:
            coordinator.wait_until_running(waiting)
        except ExecutionCancelled as error:
            outcome.append(str(error))

    waiter = threading.Thread(target=wait)
    waiter.start()
    assert coordinator.close_session("s", reason="frame deleted")
    waiter.join(1)

    assert outcome == ["frame deleted"]
    assert waiting.state is TicketState.CANCELLED
    assert active.cancellation.is_set()
    assert active.state is TicketState.RUNNING
    assert coordinator.snapshot("s")["closed"] is True
    with pytest.raises(CoordinatorClosed, match="frame deleted"):
        coordinator.submit("s", owner="system")

    coordinator.complete(active)
    assert active.state is TicketState.CANCELLED
    assert coordinator.snapshot("s")["owner"] is None


def test_cleanup_removes_idle_state_and_global_close_wakes_all_sessions():
    coordinator = SessionExecutionCoordinator(id_factory=_ids())
    active = coordinator.submit("busy", owner="agent", owner_id="one")
    waiting = coordinator.submit("busy", owner="agent", owner_id="two")
    idle = coordinator.submit("idle", owner="system")
    coordinator.complete(idle)

    assert coordinator.cleanup_session("idle")
    assert "idle" not in coordinator.snapshots()
    # Cleanup is eviction rather than a permanent tombstone: later access can
    # create a fresh queue for the same session id.
    replacement = coordinator.submit("idle", owner="system")
    coordinator.complete(replacement)

    coordinator.close(reason="daemon shutdown")
    assert coordinator.closed
    assert active.cancellation.is_set()
    assert waiting.state is TicketState.CANCELLED
    with pytest.raises(CoordinatorClosed, match="daemon shutdown"):
        coordinator.submit("new", owner="system")
    coordinator.complete(active)


def test_admission_timeout_atomically_cancels_waiting_ticket():
    coordinator = SessionExecutionCoordinator(id_factory=_ids())
    active = coordinator.submit("s", owner="agent", owner_id="active")
    waiting = coordinator.submit("s", owner="agent", owner_id="waiting")

    with pytest.raises(TimeoutError, match=waiting.execution_id):
        coordinator.wait_until_running(waiting, timeout=0.001)

    assert waiting.state is TicketState.CANCELLED
    assert waiting.cancellation.reason == "admission timed out"
    assert coordinator.snapshot("s")["queued_count"] == 0
    coordinator.complete(active)


def test_snapshot_contains_metadata_owner_and_live_fifo_positions():
    coordinator = SessionExecutionCoordinator(id_factory=_ids())
    owner = ExecutionOwner("recovery", "restore-42")
    active = coordinator.submit(
        "root",
        owner=owner,
        branch_id="branch-a",
        language="python",
        generation_id="gen-7",
        resource_keys=("workspace", "kernel:python"),
        metadata={"cause": "daemon_restart"},
    )
    second = coordinator.submit("root", owner="agent", owner_id="job-2")
    third = coordinator.submit("root", owner="user_repl", owner_id="cell-3")

    snapshot = coordinator.snapshot("root")
    assert snapshot["owner"]["execution_id"] == active.execution_id
    assert snapshot["owner"]["queue_position"] == 0
    assert snapshot["owner"]["owner"] == {"kind": "recovery", "id": "restore-42"}
    assert snapshot["owner"]["branch_id"] == "branch-a"
    assert snapshot["owner"]["generation_id"] == "gen-7"
    assert snapshot["owner"]["resource_keys"] == ["workspace", "kernel:python"]
    assert snapshot["owner"]["metadata"] == {"cause": "daemon_restart"}
    assert [item["execution_id"] for item in snapshot["queue"]] == [
        second.execution_id,
        third.execution_id,
    ]
    assert [item["queue_position"] for item in snapshot["queue"]] == [1, 2]

    coordinator.cancel_queued(
        session_id="root",
        execution_id=second.execution_id,
        owner=second.owner,
    )
    assert coordinator.snapshot("root")["queue"][0]["queue_position"] == 1
    coordinator.complete(active)
    coordinator.complete(third)


def test_events_report_fifo_states_owner_changes_and_ignore_sink_failures():
    events: list[dict] = []

    def sink(event: dict) -> None:
        events.append(event)
        if event["type"] == "execution_queue_changed":
            raise RuntimeError("UI disconnected")

    ticks = itertools.count(100)
    coordinator = SessionExecutionCoordinator(
        event_sink=sink,
        clock=lambda: float(next(ticks)),
        id_factory=_ids(),
    )
    first = coordinator.submit("s", owner="agent", owner_id="one")
    second = coordinator.submit("s", owner="user_repl", owner_id="two")
    coordinator.complete(first)
    coordinator.complete(second)

    state_events = [
        event for event in events if event["type"] == "execution_ticket_state"
    ]
    assert [
        event["status"]
        for event in state_events
        if event["execution_id"] == first.execution_id
    ] == ["queued", "running", "completed"]
    assert [
        event["status"]
        for event in state_events
        if event["execution_id"] == second.execution_id
    ] == ["queued", "running", "completed"]

    owner_events = [
        event for event in events if event["type"] == "execution_owner_changed"
    ]
    assert [event["owner"] for event in owner_events] == [
        {"kind": "agent", "id": "one"},
        None,
        {"kind": "user_repl", "id": "two"},
        None,
    ]
    assert coordinator.snapshot("s")["owner"] is None


def test_ticket_from_another_coordinator_cannot_release_owner():
    first = SessionExecutionCoordinator(id_factory=_ids())
    second = SessionExecutionCoordinator(id_factory=_ids())
    ticket = first.submit("s", owner="agent")

    with pytest.raises(TicketStateError, match="different coordinator"):
        second.complete(ticket)
    assert ticket.state is TicketState.RUNNING
    first.complete(ticket)


def test_execution_id_is_not_reused_until_session_cleanup():
    coordinator = SessionExecutionCoordinator(id_factory=_ids())
    ticket = coordinator.submit("s", owner="agent", execution_id="stable-id")
    coordinator.complete(ticket)

    with pytest.raises(ValueError, match="already exists"):
        coordinator.submit("s", owner="agent", execution_id="stable-id")

    assert coordinator.cleanup_session("s")
    replacement = coordinator.submit("s", owner="agent", execution_id="stable-id")
    coordinator.complete(replacement)
