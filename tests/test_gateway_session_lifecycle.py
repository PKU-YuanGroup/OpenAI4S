"""Gateway wiring for durable generation IDs, attempts, TTL, and cleanup."""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.execution import CellRequest
from openai4s.server.gateway import GatewayError, SessionRunner
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
        self.sandbox_status = {
            "mode": "auto",
            "state": "enabled",
            "backend": "seatbelt",
            "enforced": True,
            "self_test_passed": True,
            "network_policy": "blocked",
            "detail": "verified",
        }

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


class _LabTimer:
    def __init__(self, delay, callback, args=()):
        self.delay = delay
        self.callback = callback
        self.args = args
        self.cancelled = False
        self.started = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        self.callback(*self.args)


def _lab_run(manager, root, *, key="first"):
    from openai4s.lab.models import CommandOrigin, LabCaller

    return manager.create_run(
        LabCaller(root, root, None, CommandOrigin.HOST_SDK, None, None),
        {
            "device_id": "fake.extractor.01",
            "profile": "toy-extract-v0",
            "seed": 7,
            "idempotency_key": key,
        },
    )


def test_lab_runner_is_lazy_including_delete_and_close(tmp_path):
    from openai4s.lab.devices import DeviceRegistry
    from openai4s.lab.fake import fake_registration
    from openai4s.lab.manager import LabManager
    from openai4s.lab.models import LabError

    runner = _runner(tmp_path)
    registry = DeviceRegistry()
    registry.register(fake_registration())
    previous = LabManager(
        lambda: runner.store.lab,
        registry,
        instance_id="previous-daemon",
        clock_ms=lambda: 1000,
    )
    run_id = _lab_run(previous, "keep")["run"]["run_id"]
    try:
        runner.delete_session("missing")
        runner.close()
        assert runner._lab_manager is None
        assert runner.store.lab.get_run(run_id)["status"] == "ready"
        with pytest.raises(LabError):
            runner.lab_manager
        assert runner._lab_manager is None
    finally:
        previous.close_all("test")


def test_lab_daemon_start_reconciles_and_server_close_releases(tmp_path, monkeypatch):
    from openai4s.lab.devices import DeviceRegistry
    from openai4s.lab.fake import fake_registration
    from openai4s.lab.manager import LabManager
    from openai4s.server import gateway

    cfg = Config(
        data_dir=tmp_path,
        port=0,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    monkeypatch.setenv("OPENAI4S_SEED_DEMO", "0")
    store = get_store(cfg.db_path)
    registry = DeviceRegistry()
    registry.register(fake_registration())
    previous = LabManager(
        lambda: store.lab,
        registry,
        instance_id="previous-daemon",
        clock_ms=lambda: 1000,
    )
    old_id = _lab_run(previous, "old")["run"]["run_id"]
    server = None
    try:
        server = gateway.build_app_server(cfg)
        assert store.lab.get_run(old_id)["end_reason"] == "provider_lost"
        manager = server.runner.lab_manager
        manager._registry.register(fake_registration())
        fresh = _lab_run(manager, "new")["run"]["run_id"]
        live = manager._live[fresh]
        server.server_close()
        assert not live.port.alive(live.session_id)
        assert store.lab.get_run(fresh)["end_reason"] == "provider_lost"
    finally:
        if server is not None:
            server.server_close()
        previous.close_all("test")


def test_lab_manager_composition_is_singleton_and_tracks_store_generations(tmp_path):
    from openai4s.lab.fake import fake_registration

    runner = _runner(tmp_path)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            managers = list(pool.map(lambda _: runner.lab_manager, range(8)))
        assert all(manager is managers[0] for manager in managers)
        manager = managers[0]
        manager._registry.register(fake_registration())
        runner.store.close()
        current = get_store(runner.cfg.db_path)
        assert current is not runner.store
        run_id = _lab_run(manager, "root")["run"]["run_id"]
        assert current.lab.get_run(run_id)["status"] == "ready"
        # The rest of SessionRunner historically retains its Store; this test
        # exercises only the Lab ledger provider across the replacement.
        runner.store = current
    finally:
        runner.close()


def test_lab_updates_coalesce_manager_writes_and_cancel_on_deletion(tmp_path):
    from openai4s.lab.fake import fake_registration

    runner = _runner(tmp_path)
    timers = []

    def timer(*args, **kwargs):
        value = _LabTimer(*args, **kwargs)
        timers.append(value)
        return value

    runner._lab_updates._timer_factory = timer
    try:
        manager = runner.lab_manager
        manager._registry.register(fake_registration())
        one = _lab_run(manager, "root")["run"]["run_id"]
        _lab_run(manager, "root", key="second")
        assert len(timers) == 1 and not runner.hub.events
        assert timers[0].delay == 0.25 and timers[0].started
        timers[0].fire()
        assert runner.hub.events == [
            {
                "type": "lab_update",
                "root_frame_id": "root",
                "frame_id": "root",
                "run_id": None,
                "latest_event_seq": runner.store.lab.latest_event_seq("root"),
            }
        ]
        runner._lab_updates.changed("root", one)
        runner._lab_updates.changed("root", one)
        assert len(timers) == 2
        timers[1].fire()
        assert runner.hub.events[-1]["run_id"] == one
        runner._lab_updates.changed("root", one)
        runner._release_session_lab("root")
        assert timers[-1].cancelled
        before = list(runner.hub.events)
        timers[-1].fire()
        assert runner.hub.events == before
        timer_count = len(timers)
        runner._lab_updates.changed("root", "late-provider-completion")
        assert len(timers) == timer_count
        runner._lab_updates.changed("other", "run")
        timers[-1].fire()
        assert runner.hub.events[-1]["root_frame_id"] == "other"
        before = list(runner.hub.events)
        runner._lab_updates.changed("other", "run")
        runner.close()
        assert timers[-1].cancelled
        runner._lab_updates.changed("other", "late")
        timers[-1].fire()
        assert runner.hub.events == before
    finally:
        runner.close()


def test_lab_shutdown_continues_after_ledger_failure(tmp_path, monkeypatch):
    from openai4s.lab.fake import fake_registration
    from openai4s.lab.models import ErrorCode, LabError

    runner = _runner(tmp_path)
    manager = runner.lab_manager
    manager._registry.register(fake_registration())
    run_id = _lab_run(manager, "root")["run"]["run_id"]
    live = manager._live[run_id]

    def unavailable(*args, **kwargs):
        raise LabError(ErrorCode.PERSISTENCE_UNAVAILABLE, "ledger unavailable")

    monkeypatch.setattr(runner.store.lab, "end_run", unavailable)
    runner.close()
    assert not live.port.alive(live.session_id)
    assert runner._lab_updates._closed


def test_status_and_execution_attempt_share_persistent_generation_uuid(tmp_path):
    runner = _runner(tmp_path)
    frame_id = runner.store.new_frame(project_id="default", kind="turn")
    state = runner._state(frame_id, "default")
    kernel = _Kernel()
    lease = state.kernels.ensure("python", "base", lambda: kernel)

    status = runner.kernel_status(frame_id)
    assert status["generation_id"] == lease.generation_id
    assert str(uuid.UUID(status["generation_id"])) == status["generation_id"]
    # CellExecutionService increments the session revision immediately before
    # calling this lower-level allocation hook.
    state.cell_index = 1
    attempt_id = runner._allocate_cell_attempt(
        state,
        CellRequest(code="print(1)", origin="user"),
        "cell-uuid",
        None,
    )
    attempt = runner.store.get_execution_attempt(attempt_id)
    assert attempt["state_revision"] == 1
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

    state.cell_index = 1
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
        event.get("type") == "kernel_status" and event.get("status") == "ended"
        for event in runner.hub.events
    )
    sandbox = runner.workbench.security(frame_id)["sandbox"]
    assert sandbox["state"] == "enabled"
    assert sandbox["enforced"] is True
    assert sandbox["self_test_passed"] is True
    assert sandbox["generation_ended"] is True
    assert sandbox["ended_languages"] == ["python", "r"]
    assert all(
        runtime["generation_ended_reason"] == "idle_ttl"
        for runtime in sandbox["runtimes"]
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


def test_project_delete_blocks_new_session_and_runtime_admission(tmp_path):
    runner = _runner(tmp_path)
    runner.store.create_project(project_id="science", name="Science")
    existing = runner.create_session("science")
    entered = threading.Event()
    release = threading.Event()
    real_delete = runner.deletions.delete_project

    def blocked_delete(project_id):
        entered.set()
        assert release.wait(2)
        return real_delete(project_id)

    runner.deletions.delete_project = blocked_delete
    with ThreadPoolExecutor(max_workers=1) as pool:
        deletion = pool.submit(runner.delete_project, "science")
        assert entered.wait(2)
        with pytest.raises(GatewayError) as creating:
            runner.create_session("science")
        assert creating.value.code == 409
        with pytest.raises(GatewayError) as starting:
            runner._state(existing, "science")
        assert starting.value.code == 409
        release.set()
        assert deletion.result(timeout=2)["ok"] is True

    with pytest.raises(GatewayError) as gone:
        runner.create_session("science")
    assert gone.value.code == 404
    runner.close()
