"""Web execution admission, event projection, and Gateway concurrency tests."""

from __future__ import annotations

import itertools
import threading
import time

from openai4s.config import Config, LLMConfig
from openai4s.execution import ExecutionCancelled, TicketState
from openai4s.server import gateway as gateway_mod
from openai4s.server.execution_coordinator import WebExecutionCoordinator


class _Hub:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.lock = threading.Lock()

    def emitter(self, root_frame_id: str):
        def emit(event: dict) -> None:
            event.setdefault("root_frame_id", root_frame_id)
            with self.lock:
                self.events.append(event)

        return emit

    def broadcast(self, root_frame_id: str, event: dict) -> None:
        self.emitter(root_frame_id)(event)

    def has_subscriber(self, root_frame_id: str) -> bool:
        return False


def _runner(tmp_path):
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=3,
    )
    return gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)


def _wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.002)
    raise AssertionError("condition was not reached")


def test_web_projection_orders_queued_running_finalizing_completed():
    events: list[dict] = []
    ids = itertools.count(1)
    coordinator = WebExecutionCoordinator(
        lambda _root, event: events.append(event),
        id_factory=lambda: f"exec-{next(ids)}",
    )
    cancel = threading.Event()
    ticket = coordinator.submit(
        "frame-a",
        owner="agent",
        owner_id="job-a",
        metadata={"reason": "user message"},
    )

    with coordinator.admitted(ticket, cancel_event=cancel):
        assert coordinator.mark_finalizing(ticket, reason="persisting result")

    states = [
        event
        for event in events
        if event.get("type") == "execution_state"
        and event.get("execution_id") == ticket.execution_id
    ]
    assert [event["status"] for event in states] == [
        "queued",
        "running",
        "finalizing",
        "completed",
    ]
    assert states[0]["queue_position"] == 1
    assert states[0]["reason"] == "user message"
    assert states[1]["queue_position"] == 0
    assert states[2]["owner"] == {"kind": "agent", "id": "job-a"}


def test_exact_cancel_interrupts_only_matching_ticket_lease():
    coordinator = WebExecutionCoordinator(lambda *_args: None)
    active_cancel = threading.Event()
    active = coordinator.submit("frame-a", owner="agent", owner_id="job-a")
    queued = coordinator.submit("frame-a", owner="user_repl", owner_id="cell-b")
    lease = object()
    interrupted: list[object] = []
    entered = threading.Event()
    release = threading.Event()

    def run_active() -> None:
        with coordinator.admitted(active, cancel_event=active_cancel):
            assert coordinator.bind_lease(
                lease, lambda exact: interrupted.append(exact) or True
            )
            entered.set()
            assert release.wait(2)

    thread = threading.Thread(target=run_active)
    thread.start()
    assert entered.wait(1)

    wrong = coordinator.cancel(
        "frame-a",
        execution_id=queued.execution_id,
        owner=queued.owner,
        reason="cancel queued notebook cell",
    )
    assert wrong["ok"] is True and wrong["scope"] == "queued"
    assert not active_cancel.is_set()
    assert interrupted == []

    mismatch = coordinator.cancel(
        "frame-a",
        execution_id=active.execution_id,
        owner="agent",
        owner_id="some-other-job",
    )
    assert mismatch["ok"] is False
    assert interrupted == []

    exact = coordinator.cancel(
        "frame-a",
        execution_id=active.execution_id,
        owner=active.owner,
        reason="stop exact agent",
    )
    assert exact["ok"] is True and exact["scope"] == "running"
    assert exact["interrupted"] is True
    assert active_cancel.is_set()
    assert interrupted == [lease]
    release.set()
    thread.join(1)
    assert not thread.is_alive()


def test_kernel_interrupt_rejects_queued_ticket_without_cancelling_it():
    coordinator = WebExecutionCoordinator(lambda *_args: None)
    active_cancel = threading.Event()
    active = coordinator.submit("frame-a", owner="user_repl", owner_id="cell-a")
    queued = coordinator.submit("frame-a", owner="agent", owner_id="job-b")
    entered = threading.Event()
    release = threading.Event()

    def run_active() -> None:
        with coordinator.admitted(active, cancel_event=active_cancel):
            entered.set()
            assert release.wait(2)

    thread = threading.Thread(target=run_active)
    thread.start()
    assert entered.wait(1)

    result = coordinator.interrupt(
        "frame-a",
        execution_id=queued.execution_id,
        owner=queued.owner,
        reason="must not interrupt a queued Agent",
    )

    assert result["ok"] is False
    assert result["scope"] == "queued"
    assert queued.state is TicketState.QUEUED
    assert not queued.cancellation.is_set()
    assert not active_cancel.is_set()
    assert (
        coordinator.snapshot("frame-a")["owner"]["execution_id"] == active.execution_id
    )

    release.set()
    thread.join(1)
    assert not thread.is_alive()


def test_repl_owner_releases_then_queued_agent_continues(monkeypatch, tmp_path):
    runner = _runner(tmp_path)
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    repl_entered = threading.Event()
    release_repl = threading.Event()
    agent_entered = threading.Event()
    order: list[str] = []

    def fake_execute(*_args, **_kwargs):
        order.append("repl-enter")
        repl_entered.set()
        assert release_repl.wait(2)
        order.append("repl-exit")
        return {
            "idx": 1,
            "result": {"stdout": "ok", "stderr": "", "error": None},
            "figures": [],
            "files_written": [],
        }

    def fake_run(*_args, **_kwargs):
        order.append("agent")
        agent_entered.set()
        return {"status": "completed", "frame_id": frame_id}

    monkeypatch.setattr(runner, "_execute_and_log", fake_execute)
    monkeypatch.setattr(runner, "run_message", fake_run)

    repl_result: dict = {}
    repl = threading.Thread(
        target=lambda: repl_result.update(
            runner.run_repl(frame_id, "default", "print('hello')")
        )
    )
    repl.start()
    assert repl_entered.wait(1)

    job = runner.submit_message(frame_id, "default", "continue after cell")
    snapshot = runner.executions.snapshot(frame_id)
    assert snapshot["owner"]["owner"]["kind"] == "user_repl"
    assert snapshot["queue"][0]["execution_id"] == job.execution_id
    assert snapshot["queue"][0]["queue_position"] == 1
    assert not agent_entered.is_set()

    release_repl.set()
    repl.join(1)
    assert not repl.is_alive()
    assert agent_entered.wait(1)
    assert job.wait_result()["status"] == "completed"
    assert order == ["repl-enter", "repl-exit", "agent"]
    assert repl_result["status"] == "completed"
    assert repl_result["cell"]["state_revision"] == 1
    assert repl_result["cell"]["generation_id"] is None
    runner.close()


def test_submit_repl_returns_ticket_before_cell_finishes(monkeypatch, tmp_path):
    runner = _runner(tmp_path)
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    entered = threading.Event()
    release = threading.Event()

    def fake_run_repl(*_args, **_kwargs):
        entered.set()
        assert release.wait(2)
        return {
            "status": "completed",
            "frame_id": frame_id,
            "execution_id": "repl-async",
        }

    monkeypatch.setattr(runner, "run_repl", fake_run_repl)

    job = runner.submit_repl(
        frame_id,
        "default",
        "print('queued')",
        execution_id="repl-async",
    )

    assert job.execution_id == "repl-async"
    assert job.execution_owner == {"kind": "user_repl", "id": "repl-async"}
    assert entered.wait(1)
    assert job.done.is_set() is False
    snapshot = runner.executions.snapshot(frame_id)
    assert snapshot["owner"]["execution_id"] == "repl-async"
    release.set()
    assert job.wait_result()["status"] == "completed"
    runner.close()


def test_cancel_queued_repl_never_cancels_active_agent(monkeypatch, tmp_path):
    runner = _runner(tmp_path)
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    agent_entered = threading.Event()
    release_agent = threading.Event()
    repl_executed = threading.Event()

    def fake_run(*_args, **_kwargs):
        agent_entered.set()
        assert release_agent.wait(2)
        return {"status": "completed", "frame_id": frame_id}

    def should_not_execute(*_args, **_kwargs):
        repl_executed.set()
        raise AssertionError("cancelled queued REPL must not execute")

    monkeypatch.setattr(runner, "run_message", fake_run)
    monkeypatch.setattr(runner, "_execute_and_log", should_not_execute)
    job = runner.submit_message(frame_id, "default", "long agent task")
    assert agent_entered.wait(1)

    repl_errors: list[BaseException] = []

    def run_repl() -> None:
        try:
            runner.run_repl(
                frame_id,
                "default",
                "print('never')",
                execution_id="repl-client-exact",
            )
        except BaseException as error:  # noqa: BLE001 - asserted below
            repl_errors.append(error)

    repl = threading.Thread(target=run_repl)
    repl.start()
    _wait_for(lambda: runner.executions.snapshot(frame_id)["queued_count"] == 1)
    queued = runner.executions.snapshot(frame_id)["queue"][0]
    assert queued["execution_id"] == "repl-client-exact"
    assert queued["owner"] == {
        "kind": "user_repl",
        "id": "repl-client-exact",
    }
    result = runner.cancel(
        frame_id,
        queued["execution_id"],
        owner=queued["owner"],
        reason="cancel only queued notebook cell",
    )

    assert result["ok"] is True and result["scope"] == "queued"
    assert not runner._state(frame_id, "default").cancel.is_set()
    assert not job.done.is_set()
    repl.join(1)
    assert not repl.is_alive()
    assert len(repl_errors) == 1
    assert isinstance(repl_errors[0], ExecutionCancelled)
    assert not repl_executed.is_set()

    release_agent.set()
    assert job.wait_result()["status"] == "completed"
    runner.close()


def test_http_cancel_forwards_exact_execution_and_owner(monkeypatch, tmp_path):
    runner = _runner(tmp_path)
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    calls: list[tuple] = []

    def cancel(root_frame_id, execution_id=None, **kwargs):
        calls.append((root_frame_id, execution_id, kwargs))
        return {
            "ok": True,
            "frame_id": root_frame_id,
            "execution_id": execution_id,
        }

    monkeypatch.setattr(runner, "cancel", cancel)
    handler = object.__new__(gateway_mod.make_handler(runner.cfg, runner.hub, runner))
    replies: list[tuple[int, dict]] = []
    handler._query = lambda: {}
    handler._body = lambda: {
        "execution_id": "exec-exact",
        "owner": {"kind": "user_repl", "id": "cell-exact"},
        "reason": "cancel this cell only",
    }
    handler._json = lambda payload, code=200: replies.append((code, payload))

    handler._api("POST", f"/frames/{frame_id}/cancel")

    assert calls == [
        (
            frame_id,
            "exec-exact",
            {
                "owner": {"kind": "user_repl", "id": "cell-exact"},
                "owner_id": "cell-exact",
                "reason": "cancel this cell only",
            },
        )
    ]
    assert replies == [
        (200, {"ok": True, "frame_id": frame_id, "execution_id": "exec-exact"})
    ]
    runner.close()


def test_http_cancel_without_exact_identity_fails_closed(monkeypatch, tmp_path):
    runner = _runner(tmp_path)
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    monkeypatch.setattr(
        runner,
        "cancel",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("incomplete cancellation reached runner")
        ),
    )
    handler = object.__new__(gateway_mod.make_handler(runner.cfg, runner.hub, runner))
    replies: list[tuple[int, dict]] = []
    handler._query = lambda: {}
    handler._body = lambda: {}
    handler._json = lambda payload, code=200: replies.append((code, payload))

    handler._api("POST", f"/frames/{frame_id}/cancel")

    assert replies[0][0] == 400
    assert replies[0][1]["ok"] is False
    assert "execution_id" in replies[0][1]["error"]
    runner.close()


def test_runner_cancel_without_identity_never_resolves_current_owner(tmp_path):
    runner = _runner(tmp_path)
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")

    result = runner.cancel(frame_id)

    assert result["ok"] is False
    assert "exact cancellation requires" in result["reason"]
    runner.close()
