"""Gateway wiring for durable generation IDs, attempts, TTL, and cleanup."""

from __future__ import annotations

import uuid

from openai4s.config import Config, LLMConfig
from openai4s.execution import CellRequest
from openai4s.server.gateway import SessionRunner
from openai4s.store import get_store


class _Hub:
    def __init__(self) -> None:
        self.events = []

    def emitter(self, root_frame_id):
        def emit(event):
            event.setdefault("root_frame_id", root_frame_id)
            self.events.append(event)

        return emit

    def broadcast(self, root_frame_id, event):
        self.emitter(root_frame_id)(event)

    def has_subscriber(self, root_frame_id):
        del root_frame_id
        return False


class _Kernel:
    def __init__(self, pid=8123) -> None:
        self.pid = pid
        self.live = True
        self.shutdown_calls = 0
        self.python = "/env/bin/python"
        self.env_name = "base"
        self.env_root = "/env"
        self.cwd = "/workspace"

    def is_alive(self):
        return self.live

    def shutdown(self):
        self.shutdown_calls += 1
        self.live = False

    def interrupt(self):
        pass


def _runner(tmp_path, *, clock=lambda: 1.0):
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    return SessionRunner(
        cfg,
        _Hub(),
        clock=clock,
        start_idle_sweeper=False,
    )


def test_status_and_execution_attempt_share_persistent_generation_uuid(tmp_path):
    runner = _runner(tmp_path)
    frame_id = runner.store.new_frame(project_id="default", kind="turn")
    state = runner._state(frame_id, "default")
    kernel = _Kernel()
    lease = state.kernels.ensure("python", "base", lambda: kernel)

    status = runner.kernel_status(frame_id)
    assert status["generation_id"] == lease.generation_id
    assert str(uuid.UUID(status["generation_id"])) == status["generation_id"]
    attempt_id = runner._allocate_cell_attempt(
        state,
        CellRequest(code="print(1)", origin="user"),
        "cell-uuid",
        None,
    )
    attempt = runner.store.get_execution_attempt(attempt_id)
    assert attempt["generation_id"] == status["generation_id"]

    runner.close()
    assert kernel.shutdown_calls == 1
    generation = runner.store.get_kernel_generation(lease.generation_id)
    assert generation["ended_reason"] == "daemon_shutdown"


def test_attempt_does_not_bind_a_dead_slot_before_lazy_replacement(tmp_path):
    runner = _runner(tmp_path)
    frame_id = runner.store.new_frame(project_id="default", kind="turn")
    state = runner._state(frame_id, "default")
    dead = _Kernel(8130)
    state.kernels.ensure("python", "base", lambda: dead)
    dead.live = False

    attempt_id = runner._allocate_cell_attempt(
        state,
        CellRequest(code="print(2)", origin="user"),
        "cell-replaced",
        None,
    )
    assert runner.store.get_execution_attempt(attempt_id)["generation_id"] is None

    replacement = _Kernel(8131)
    lease = state.kernels.ensure("python", "base", lambda: replacement)
    runner._bind_cell_attempt_generation(attempt_id, state, "python")
    assert (
        runner.store.get_execution_attempt(attempt_id)["generation_id"]
        == lease.generation_id
    )
    runner.close()


def test_gateway_idle_sweep_releases_both_slots_and_emits_ended(tmp_path):
    now = {"s": 0.0}
    runner = _runner(tmp_path, clock=lambda: now["s"])
    frame_id = runner.store.new_frame(project_id="default", kind="turn")
    state = runner._state(frame_id, "default")
    python = _Kernel(8124)
    r = _Kernel(8125)
    state.kernels.ensure("python", "base", lambda: python)
    state.kernels.ensure("r", "r", lambda: r)
    runner.recovery.ttl_s = 10

    now["s"] = 10.0
    assert runner.recovery.sweep_once() == []
    now["s"] = 10.001
    assert runner.recovery.sweep_once() == [frame_id]

    assert python.shutdown_calls == r.shutdown_calls == 1
    status = runner.kernel_status(frame_id)
    assert status["state"] == "ended"
    assert status["ended_reason"] == "idle_ttl"
    assert any(
        event.get("type") == "kernel_status"
        and event.get("status") == "ended"
        for event in runner.hub.events
    )
    runner.close()


def test_idle_sweep_never_waits_behind_a_raced_turn_barrier(tmp_path):
    now = {"s": 0.0}
    runner = _runner(tmp_path, clock=lambda: now["s"])
    frame_id = runner.store.new_frame(project_id="default", kind="turn")
    state = runner._state(frame_id, "default")
    kernel = _Kernel(8126)
    state.kernels.ensure("python", "base", lambda: kernel)
    runner.recovery.ttl_s = 1
    now["s"] = 2.0

    state.turn_lock.acquire()
    try:
        assert runner.recovery.sweep_once() == []
        assert kernel.live
    finally:
        state.turn_lock.release()
    assert runner.recovery.sweep_once() == [frame_id]
    runner.close()


def test_runner_startup_marks_stale_generation_and_attempt_abandoned(tmp_path):
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    store = get_store(cfg.db_path)
    frame_id = store.new_frame(project_id="default", kind="turn")
    generation = store.create_kernel_generation(
        root_frame_id=frame_id,
        language="python",
        owner_instance_id="older-daemon",
        state="active",
        started_at=100,
    )
    group = store.append_action_group(
        root_frame_id=frame_id, turn_id="turn-old", kind="execution"
    )
    attempt = store.allocate_execution_attempt(
        group_id=group["group_id"],
        producing_cell_id="cell-old",
        generation_id=generation["generation_id"],
        owner_instance_id="older-daemon",
        allocated_at=100,
    )

    runner = SessionRunner(
        cfg,
        _Hub(),
        clock=lambda: 1.0,
        start_idle_sweeper=False,
    )
    stale_generation = store.get_kernel_generation(generation["generation_id"])
    stale_attempt = store.get_execution_attempt(attempt["attempt_id"])
    assert stale_generation["state"] == "abandoned"
    assert stale_generation["ended_reason"] == "daemon_restart"
    assert stale_attempt["terminal_state"] == "abandoned"
    assert runner.kernel_status(frame_id)["state"] == "ended"
    assert runner.kernel_status(frame_id)["ended_reason"] == "daemon_restart"
    runner.close()
