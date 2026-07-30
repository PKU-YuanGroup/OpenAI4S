"""Whether Stop can stop a session that has work waiting.

`stop_kernel` cancelled the running execution and then submitted its own
lifecycle ticket to the same FIFO — so anything already queued ran first. That
is not merely slow: a turn admitted after `stop_requested` is set blocks on
`stop_finished` the moment it submits anything, and `stop_finished` is what
Stop sets when it completes. Stop waits for the queue; the queue waits for
Stop.

Measured before the fix, with a running turn that cancelled correctly and three
items queued behind it: no return after 40 seconds. With nothing queued it
returned in 0.1s, which is why this went unnoticed — the tests stopped idle
sessions.

`cancel_queued` already existed on the coordinator. It is deliberately exact —
one ticket, matched on id and owner — because a queued cancellation must never
disturb a sibling. Right for a user cancelling one item, wrong for "stop this
session's kernel", where everything waiting is waiting for a kernel that is
going away. The lifecycle drain is the missing caller, not a missing mechanism.
"""

from __future__ import annotations

import threading
import time

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


@pytest.fixture
def session(tmp_path):
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=1,
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    store = runner.store
    store.create_project(name="p", description="", context="")
    project_id = [p["project_id"] for p in store.list_projects()][0]
    frame_id = runner.create_session(project_id)
    return runner, frame_id, project_id


def _hold_the_head(runner, state):
    """Occupy the queue head with a turn that honours cancellation, which is
    what a real agent turn does — `EventCancellation(st.cancel)`."""
    ticket = runner._queue_execution(
        state, owner="agent", owner_id="head", reason="running turn"
    )
    running = threading.Event()

    def _run():
        with runner.executions.admitted(ticket, cancel_event=state.cancel):
            running.set()
            deadline = time.time() + 60
            while time.time() < deadline and not state.cancel.is_set():
                time.sleep(0.02)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    assert running.wait(5)
    return thread


def _stop_within(runner, frame_id, project_id, seconds):
    out: dict = {}

    def _stop():
        try:
            out["value"] = runner.stop_kernel(frame_id, project_id)
        except Exception as error:  # noqa: BLE001
            out["value"] = f"{type(error).__name__}: {error}"

    thread = threading.Thread(target=_stop, daemon=True)
    thread.start()
    thread.join(seconds)
    return (None if thread.is_alive() else out.get("value")), thread.is_alive()


def test_stop_returns_with_work_queued_behind_the_running_turn(session):
    """The defect. Everything below the first assertion is unreachable if Stop
    never returns, which is what made this a hang rather than a delay."""
    runner, frame_id, project_id = session
    state = runner._state(frame_id, project_id)
    _hold_the_head(runner, state)
    for index in range(3):
        runner._queue_execution(
            state, owner="agent", owner_id=f"queued-{index}", reason="follow-up"
        )
    assert len(runner.executions.snapshot(frame_id).get("queue", [])) == 3

    result, still_running = _stop_within(runner, frame_id, project_id, 30)
    assert not still_running, "stop_kernel did not return with 3 items queued"
    assert result["ok"] is True
    assert result["state"] == "stopped"


def test_the_queued_work_is_reported_rather_than_silently_dropped(session):
    """A follow-up the user submitted and that will never run is something they
    are entitled to know about — the same rule as a referenced file dropped
    from a prompt."""
    runner, frame_id, project_id = session
    state = runner._state(frame_id, project_id)
    _hold_the_head(runner, state)
    for index in range(2):
        runner._queue_execution(
            state, owner="agent", owner_id=f"queued-{index}", reason="follow-up"
        )

    result, still_running = _stop_within(runner, frame_id, project_id, 30)
    assert not still_running
    assert len(result["cancelled_queued"]) == 2


def test_the_queue_is_actually_empty_afterwards(session):
    """Reporting the ids is not the same as cancelling the tickets."""
    runner, frame_id, project_id = session
    state = runner._state(frame_id, project_id)
    _hold_the_head(runner, state)
    for index in range(3):
        runner._queue_execution(
            state, owner="agent", owner_id=f"queued-{index}", reason="follow-up"
        )

    _result, still_running = _stop_within(runner, frame_id, project_id, 30)
    assert not still_running
    assert runner.executions.snapshot(frame_id).get("queue") == []


def test_stopping_an_idle_session_is_unchanged(session):
    """The path every existing test took, and the reason this was invisible."""
    runner, frame_id, project_id = session
    result, still_running = _stop_within(runner, frame_id, project_id, 30)
    assert not still_running
    assert result["ok"] is True
    assert result["cancelled_queued"] == []
