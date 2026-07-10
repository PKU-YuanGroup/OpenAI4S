"""Gateway integration contracts for supervised Python/R kernel lifecycles."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod


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


def _runner(tmp_path):
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=3,
    )
    return gateway_mod.SessionRunner(cfg, _Hub())


class _RecordingKernel:
    def __init__(self, name: str, events: list[str] | None = None) -> None:
        self.name = name
        self.dispatcher = SimpleNamespace(last_output=None)
        self.events = events if events is not None else []
        self.live = True
        self.interrupt_calls = 0
        self.shutdown_calls = 0
        self.restart_calls = 0
        self.kill_calls = 0
        self.execute_entered = threading.Event()
        self.execute_release = threading.Event()
        self.release_on_interrupt = False

    def is_alive(self) -> bool:
        return self.live

    def execute(self, code, origin="agent", on_chunk=None, *, cell_id=None):
        self.events.append(f"{self.name}:execute-enter")
        self.execute_entered.set()
        assert self.execute_release.wait(2)
        self.events.append(f"{self.name}:execute-exit")
        return {"stdout": "", "stderr": "", "error": None}

    def interrupt(self) -> None:
        self.interrupt_calls += 1
        self.events.append(f"{self.name}:interrupt")
        if self.release_on_interrupt:
            self.execute_release.set()

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self.events.append(f"{self.name}:shutdown")
        self.live = False

    def restart(self) -> None:
        self.restart_calls += 1
        self.live = True

    def kill_worker(self) -> None:
        self.kill_calls += 1
        self.live = False
        self.execute_release.set()


def test_stop_interrupts_both_slots_then_waits_for_execution_barrier(tmp_path):
    runner = _runner(tmp_path)
    st = runner._state("frame-stop", "default")
    events: list[str] = []
    python = _RecordingKernel("python", events)
    python.release_on_interrupt = True
    r = _RecordingKernel("r", events)
    st.kernels.ensure("python", "base", lambda: python)
    st.kernels.ensure("r", None, lambda: r)

    def execute_turn() -> None:
        with st.turn_lock:
            python.execute("pass")

    turn = threading.Thread(target=execute_turn)
    turn.start()
    assert python.execute_entered.wait(1)

    result = runner.stop_kernel(st.root_frame_id)
    turn.join(1)

    assert not turn.is_alive()
    assert result["state"] == "stopped"
    assert python.interrupt_calls == r.interrupt_calls == 1
    assert events.index("python:interrupt") < events.index("python:execute-exit")
    assert events.index("python:execute-exit") < events.index("python:shutdown")
    assert runner.kernel_status(st.root_frame_id)["state"] == "stopped"
    # Stop keeps cancellation asserted until the next explicit start/turn.
    assert st.cancel.is_set()


def test_stop_intent_cannot_be_overtaken_by_a_new_start(monkeypatch, tmp_path):
    runner = _runner(tmp_path)
    st = runner._state("frame-stop-race", "default")
    current = _RecordingKernel("current")
    st.kernels.ensure("python", "base", lambda: current)
    cancel_reached = threading.Event()
    let_stop_wait_for_barrier = threading.Event()
    original_cancel = runner.cancel

    def paused_cancel(root_frame_id: str) -> None:
        original_cancel(root_frame_id)
        cancel_reached.set()
        assert let_stop_wait_for_barrier.wait(2)

    monkeypatch.setattr(runner, "cancel", paused_cancel)
    replacement = _RecordingKernel("replacement")
    start_entered = threading.Event()

    def ensure(state) -> None:
        start_entered.set()
        state.kernels.ensure("python", "base", lambda: replacement)

    monkeypatch.setattr(runner, "_ensure_kernel", ensure)
    stop_result = {}
    start_result = {}
    start_attempted = threading.Event()
    stopping = threading.Thread(
        target=lambda: stop_result.update(runner.stop_kernel(st.root_frame_id))
    )
    stopping.start()
    assert cancel_reached.wait(1)

    def start() -> None:
        start_attempted.set()
        start_result.update(runner.start_kernel(st.root_frame_id))

    starting = threading.Thread(target=start)
    starting.start()
    assert start_attempted.wait(1)
    assert not start_entered.wait(0.1)

    let_stop_wait_for_barrier.set()
    stopping.join(1)
    starting.join(1)

    assert not stopping.is_alive() and not starting.is_alive()
    assert stop_result["state"] == "stopped"
    assert start_result["state"] == "running"
    assert current.shutdown_calls == 1
    assert st.kernel is replacement
    assert not st.cancel.is_set()
    lifecycle = [
        event["status"]
        for event in runner.hub.events
        if event.get("type") == "kernel_status"
    ]
    assert lifecycle[-2:] == ["stopped", "started"]


def test_ensure_replaces_a_dead_supervised_python_worker(monkeypatch, tmp_path):
    runner = _runner(tmp_path)
    st = runner._state("frame-dead", "default")
    old = _RecordingKernel("old")
    st.kernels.ensure("python", "base", lambda: old)
    old.live = False
    replacement = _RecordingKernel("replacement")
    calls = []

    def spawn(state):
        calls.append("spawn")
        return state.kernels.ensure("python", "base", lambda: replacement)

    monkeypatch.setattr(runner, "_spawn_kernel", spawn)
    runner._ensure_kernel(st)

    assert calls == ["spawn"]
    assert st.kernel is replacement
    assert old.shutdown_calls == 1


def test_python_bootstrap_runs_outside_supervisor_lock(monkeypatch, tmp_path):
    runner = _runner(tmp_path)
    st = runner._state("frame-bootstrap", "default")
    st.messages = [{"role": "system", "content": "test"}]
    env = SimpleNamespace(
        name="base",
        interpreter="base-python",
        root=tmp_path / "base",
        is_conda=False,
        bin_dir=None,
    )
    monkeypatch.setattr(
        runner,
        "_resolve_env",
        lambda state: (setattr(state, "env_name", "base") or env),
    )
    monkeypatch.setattr(runner, "_wire_delegation", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        gateway_mod,
        "build_dispatcher",
        lambda *args, **kwargs: SimpleNamespace(active_r_env=None),
    )
    runner.skills = SimpleNamespace(bootstrap_code="bootstrap()")

    class BlockingBootstrapKernel:
        instance = None
        created = threading.Event()

        def __init__(self, dispatcher, **kwargs) -> None:
            self.dispatcher = dispatcher
            self.live = True
            self.entered = threading.Event()
            self.release = threading.Event()
            BlockingBootstrapKernel.instance = self
            BlockingBootstrapKernel.created.set()

        def is_alive(self) -> bool:
            return self.live

        def execute(self, code, origin="agent", on_chunk=None):
            self.entered.set()
            assert self.release.wait(2)
            return {"stdout": "", "stderr": "", "error": None}

        def interrupt(self) -> None:
            self.release.set()

        def shutdown(self) -> None:
            self.live = False

    monkeypatch.setattr(gateway_mod, "Kernel", BlockingBootstrapKernel)
    spawn_done = threading.Event()

    def spawn() -> None:
        with st.turn_lock:
            runner._spawn_kernel(st)
        spawn_done.set()

    spawning = threading.Thread(target=spawn)
    spawning.start()
    assert BlockingBootstrapKernel.created.wait(1)
    kernel = BlockingBootstrapKernel.instance
    assert kernel is not None
    assert kernel.entered.wait(1)

    interrupt_done = threading.Event()

    def interrupt() -> None:
        st.kernels.interrupt("python")
        interrupt_done.set()

    interrupting = threading.Thread(target=interrupt)
    interrupting.start()
    acquired_without_bootstrap_finishing = interrupt_done.wait(0.5)
    if not acquired_without_bootstrap_finishing:
        # Cleanup makes a regression fail promptly instead of hanging pytest.
        kernel.release.set()
    interrupting.join(1)
    spawning.join(1)

    assert acquired_without_bootstrap_finishing
    assert spawn_done.is_set()


def test_environment_replacement_commits_worker_dispatcher_and_active_env_together(
    monkeypatch, tmp_path
):
    from openai4s.kernel import environments as envmod

    runner = _runner(tmp_path)
    frame_id = runner.store.new_frame(
        kind="turn", project_id="default", status="ready"
    )
    st = runner._state(frame_id, "default")
    st.messages = [{"role": "system", "content": "test"}]
    envs = {
        "base": SimpleNamespace(
            name="base",
            interpreter="base-python",
            root=tmp_path / "base",
            is_conda=False,
            bin_dir=str(tmp_path / "base" / "bin"),
            language="python",
            python_version=lambda: "3.14",
        ),
        "struct": SimpleNamespace(
            name="struct",
            interpreter="struct-python",
            root=tmp_path / "struct",
            is_conda=False,
            bin_dir=str(tmp_path / "struct" / "bin"),
            language="python",
            python_version=lambda: "3.14",
        ),
    }
    monkeypatch.setattr(envmod, "get_environment", envs.get)
    monkeypatch.setattr(envmod, "default_env_name", lambda: "base")

    dispatchers = []

    class Dispatcher:
        def __init__(self) -> None:
            self.last_output = None
            self.active_r_env = None

        def __call__(self, method, args):
            return None

    def make_dispatcher(*args, **kwargs):
        dispatcher = Dispatcher()
        dispatchers.append(dispatcher)
        return dispatcher

    fail_struct = {"value": True}
    kernels = []

    class FakeKernel:
        def __init__(self, dispatcher, python, **kwargs) -> None:
            if python == "struct-python" and fail_struct["value"]:
                raise RuntimeError("struct worker failed to start")
            self.dispatcher = dispatcher
            self.python = python
            self.options = kwargs
            self.live = True
            self.shutdown_calls = 0
            kernels.append(self)

        def is_alive(self) -> bool:
            return self.live

        def shutdown(self) -> None:
            self.shutdown_calls += 1
            self.live = False

    bootstrapped = []
    monkeypatch.setattr(gateway_mod, "build_dispatcher", make_dispatcher)
    monkeypatch.setattr(gateway_mod, "Kernel", FakeKernel)
    monkeypatch.setattr(runner, "_wire_delegation", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        runner, "_run_bootstrap", lambda state, kernel=None: bootstrapped.append(kernel)
    )

    st.desired_env = "base"
    first = runner._spawn_kernel(st)
    old_kernel = first.kernel
    old_dispatcher = st.dispatcher
    old_dispatcher.active_r_env = "r-special"
    background = old_dispatcher.background_kernel_factory()
    assert background.python == "base-python"
    assert background.options["cwd"] == str(st.workspace)

    with pytest.raises(RuntimeError, match="failed to start"):
        runner.set_env(frame_id, "struct")

    assert st.kernel is old_kernel
    assert st.dispatcher is old_dispatcher
    assert st.env_name == "base"
    assert st.desired_env == "struct"
    assert old_kernel.shutdown_calls == 0
    assert st.kernels.status("python")["generation"] == 0

    fail_struct["value"] = False
    changed = runner.set_env(frame_id, "struct")

    assert changed["generation"] == 1
    assert st.kernel is kernels[-1] and st.kernel is not old_kernel
    assert st.dispatcher is st.kernel.dispatcher
    assert st.dispatcher.active_r_env == "r-special"
    replacement_background = st.dispatcher.background_kernel_factory()
    assert replacement_background.python == "struct-python"
    assert replacement_background.options["cwd"] == str(st.workspace)
    assert st.env_name == "struct"
    assert old_kernel.shutdown_calls == 1
    assert bootstrapped == [old_kernel, st.kernel]


def test_r_slot_is_lazy_reused_and_soft_fails_without_touching_python(
    monkeypatch, tmp_path
):
    from openai4s.kernel import environments as envmod
    from openai4s.kernel import r_kernel as r_kernel_mod

    runner = _runner(tmp_path)
    st = runner._state("frame-r", "default")
    st.dispatcher = SimpleNamespace(active_r_env=None)
    python = _RecordingKernel("python")
    py_lease = st.kernels.ensure("python", "base", lambda: python)
    created = []

    def get_environment(name):
        return SimpleNamespace(name=name) if name else None

    def spawn_r_kernel(*, cwd, env):
        name = env.name if env is not None else "default"
        if name == "broken":
            raise RuntimeError("R is missing")
        kernel = _RecordingKernel(name)
        created.append(kernel)
        return kernel

    monkeypatch.setattr(envmod, "get_environment", get_environment)
    monkeypatch.setattr(r_kernel_mod, "spawn_r_kernel", spawn_r_kernel)

    assert runner._ensure_r_kernel(st) is None
    first_r = st.r_kernel
    assert runner._ensure_r_kernel(st) is None
    assert st.r_kernel is first_r and len(created) == 1

    st.dispatcher.active_r_env = "r-special"
    assert runner._ensure_r_kernel(st) is None
    second_r = st.r_kernel
    assert second_r is not first_r
    assert first_r.shutdown_calls == 1

    st.dispatcher.active_r_env = "broken"
    error = runner._ensure_r_kernel(st)
    assert error == "R kernel unavailable: R is missing"
    assert st.r_kernel is second_r and second_r.shutdown_calls == 0
    assert st.r_env_name == "r-special"
    assert st.kernels.lease("python") == py_lease


def test_r_execution_exception_shuts_down_the_exact_desynchronized_lease(tmp_path):
    runner = _runner(tmp_path)
    st = runner._state("frame-r-error", "default")
    st.dispatcher = SimpleNamespace(active_r_env=None)

    class BrokenR(_RecordingKernel):
        def execute(self, code, origin="agent", on_chunk=None, *, cell_id=None):
            raise RuntimeError("malformed protocol frame")

    kernel = BrokenR("r")
    st.kernels.ensure("r", None, lambda: kernel)
    runner._ensure_r_kernel = lambda state: None

    with pytest.raises(RuntimeError, match="malformed protocol"):
        runner._execute_and_log(
            st,
            "stop('bad frame')",
            "agent",
            lambda event: None,
            stream=False,
            language="r",
        )

    assert st.r_kernel is None
    assert kernel.shutdown_calls == 1


def test_watchdog_passes_canonical_cell_id_to_kernel(tmp_path):
    runner = _runner(tmp_path)
    state = runner._state("frame-cell-id", "default")
    seen = []

    class ImmediateKernel:
        def is_alive(self):
            return True

        def execute(self, code, origin="agent", on_chunk=None, *, cell_id=None):
            seen.append((code, origin, cell_id))
            return {"id": cell_id, "stdout": "", "stderr": "", "error": None}

        def interrupt(self):
            pass

        def shutdown(self):
            pass

    state.kernels.ensure("python", "base", ImmediateKernel)

    result = runner._execute_with_watchdog(
        state,
        "print('identified')",
        "agent",
        None,
        cell_id="cell-shared",
    )

    assert result["id"] == "cell-shared"
    assert seen == [("print('identified')", "agent", "cell-shared")]


def test_watchdog_hard_kill_restarts_exact_python_lease(monkeypatch, tmp_path):
    runner = _runner(tmp_path)
    st = runner._state("frame-watchdog", "default")

    class HungKernel(_RecordingKernel):
        def execute(self, code, origin="agent", on_chunk=None, *, cell_id=None):
            self.execute_entered.set()
            assert self.execute_release.wait(2)
            raise RuntimeError("worker pipe closed")

        def interrupt(self) -> None:
            self.interrupt_calls += 1

    kernel = HungKernel("python")
    first = st.kernels.ensure("python", "base", lambda: kernel)
    bootstrapped = []
    monkeypatch.setenv("OPENAI4S_CELL_TIMEOUT", "0.01")
    monkeypatch.setattr(gateway_mod, "_WATCHDOG_INTERRUPT_GRACE_S", 0.01)
    monkeypatch.setattr(gateway_mod, "_WATCHDOG_KILL_GRACE_S", 0.1)
    monkeypatch.setattr(
        runner, "_run_bootstrap", lambda state, target=None: bootstrapped.append(target)
    )

    with pytest.raises(TimeoutError, match="cell exceeded"):
        runner._execute_with_watchdog(st, "hang()", "agent", None)

    recovered = st.kernels.lease("python")
    assert recovered is not None and recovered.kernel is first.kernel
    assert recovered.generation == 1
    assert kernel.interrupt_calls == kernel.kill_calls == kernel.restart_calls == 1
    assert bootstrapped == [kernel]
