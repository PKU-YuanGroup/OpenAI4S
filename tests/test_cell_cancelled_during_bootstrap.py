"""A Cell whose kernel was still bootstrapping when its execution was cancelled.

A Notebook Cell on a fresh session first spawns and bootstraps its kernel
(`prepare_language` -> Skill bootstrap, ~10 seconds on a cold macOS worker)
while the admitted ticket holds the session's turn lock. `openai4s stop`
landing in that window cancels the ticket, but nothing can interrupt a
bootstrap no lease is bound to yet -- and nothing looked at the cancellation
once the bootstrap returned. So the user's code started anyway, *during daemon
shutdown*, printed its first line and ran until the watchdog's next poll
noticed. Observed on a real daemon: `execution_log` held stdout `start`,
`wall_s` 1.0, recorded after SIGTERM.

The Cell must instead be finished as interrupted without its code running.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.store import get_store


class _Hub:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emitter(self, root_frame_id: str):
        def emit(event: dict) -> None:
            event.setdefault("root_frame_id", root_frame_id)
            self.events.append(event)

        return emit

    def broadcast(self, root_frame_id: str, event: dict) -> None:
        event.setdefault("root_frame_id", root_frame_id)
        self.events.append(event)

    def has_subscriber(self, root_frame_id: str) -> bool:
        return False


def _wait_for(predicate, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_a_cell_cancelled_while_its_kernel_bootstraps_never_runs(tmp_path):
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    hub = _Hub()
    runner = gateway_mod.SessionRunner(cfg, hub, start_idle_sweeper=False)
    closing: threading.Thread | None = None
    release = threading.Event()
    try:
        frame_id = runner.store.new_frame(
            kind="turn", project_id="default", status="ready"
        )
        st = runner._state(frame_id, "default")
        workspace = Path(st.local_workspace)

        # Hold the real bootstrap host-side, on the job thread that holds the
        # turn lock -- the same place a slow Skill bootstrap holds it.
        bootstrapping = threading.Event()
        real_bootstrap = runner._run_bootstrap

        def slow_bootstrap(*args, **kwargs):
            bootstrapping.set()
            release.wait(60)
            return real_bootstrap(*args, **kwargs)

        runner._run_bootstrap = slow_bootstrap
        job = runner.submit_repl(
            frame_id,
            "default",
            "open('user-code-ran', 'w').write('ran')\nprint('user code ran')",
            execution_id="repl-during-shutdown",
        )
        assert bootstrapping.wait(60), "the kernel never reached its bootstrap"

        closing = threading.Thread(target=runner.close, daemon=True)
        closing.start()
        assert _wait_for(st.cancel.is_set), "shutdown never cancelled the Cell"
        release.set()

        job.done.wait(120)
        assert job.done.is_set(), "the cancelled Cell never finished"
        closing.join(120)
        assert not closing.is_alive(), "daemon shutdown did not finish"
    finally:
        release.set()
        if closing is None:
            runner.close()

    assert not (workspace / "user-code-ran").exists(), "user code ran during shutdown"
    store = get_store(cfg.db_path)
    attempts = store.list_execution_attempts(root_frame_id=frame_id)
    assert [attempt["terminal_state"] for attempt in attempts] == ["interrupted"]
    cells = store.frame_detail(frame_id)["cells"]
    assert len(cells) == 1
    assert cells[0]["interrupted"]
    assert "user code ran" not in str(cells[0].get("stdout") or "")
    finished = [
        event for event in hub.events if event.get("type") == "notebook_cell_finished"
    ]
    assert [event["status"] for event in finished] == ["interrupted"]


def _session(runner) -> object:
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    return runner._state(frame_id, "default")


def test_the_stop_marker_alone_refuses_a_cell_with_no_admitted_ticket(tmp_path):
    """`st.cancel` is what the watchdog and a pre-coordinator holder observe.

    It must refuse the Cell on its own, with no ticket bound to this thread.
    """

    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    try:
        st = _session(runner)
        assert runner.executions.current(st.root_frame_id) is None
        assert not runner._cell_cancelled(st)
        st.cancel.set()
        assert runner._cell_cancelled(st)
    finally:
        runner.close()


def test_a_cancelled_ticket_refuses_the_cell_before_the_stop_marker_is_set(tmp_path):
    """Stop, session close and shutdown cancel the ticket *first*.

    Only afterwards do they signal the event bound to it, so between the two a
    Cell about to start sees a cancelled ticket and a clear `st.cancel`. The
    ticket alone must refuse it -- and only for its own session.
    """

    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    try:
        st = _session(runner)
        other = _session(runner)
        ticket = runner.executions.submit(
            st.root_frame_id,
            owner="user_repl",
            owner_id="notebook",
            execution_id="repl-cancelled-first",
            resource_keys=("workspace",),
        )
        # Bound to an event other than `st.cancel`, so cancelling the ticket
        # leaves the session's own marker exactly as the window leaves it.
        bound = threading.Event()
        with runner.executions.admitted(ticket, cancel_event=bound, timeout=10):
            assert not runner._cell_cancelled(st)
            result = runner.executions.cancel(
                st.root_frame_id,
                execution_id="repl-cancelled-first",
                owner="user_repl",
                owner_id="notebook",
            )
            assert result["ok"], result
            assert ticket.cancellation.is_set()
            assert not st.cancel.is_set()
            assert runner._cell_cancelled(st)
            assert not runner._cell_cancelled(other)
    finally:
        runner.close()
