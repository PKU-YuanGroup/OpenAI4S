"""Contracts for the lazy Web-session control and execution planes."""

from __future__ import annotations

import json
from types import SimpleNamespace

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server.session_runtime import SessionRuntime


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


class _Dispatcher:
    def __init__(self, frame_id: str) -> None:
        self.frame_id = frame_id
        self.last_output = None
        self.active_env_bin = None
        self.active_r_env = None
        self.calls: list[tuple[str, list]] = []

    def __call__(self, method: str, args: list):
        self.calls.append((method, args))
        if method == "list_dir":
            return {"path": ".", "count": 0, "entries": []}
        return {"ok": True}


def _cfg(tmp_path, *, max_turns: int = 1) -> Config:
    return Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=max_turns,
    )


def _frame(runner) -> str:
    frame_id = runner.store.new_frame(
        kind="turn", project_id="default", status="ready"
    )
    runner.store.update_frame(frame_id, name="Lazy runtime test")
    return frame_id


def _native_call() -> dict:
    arguments = {"path": "."}
    return {
        "id": "call-list",
        "wire_id": "call-list",
        "name": "list_dir",
        "ordinal": 0,
        "raw_arguments": json.dumps(arguments),
        "arguments": arguments,
        "parse_error": None,
        "provider_meta": {"provider": "test"},
    }


def _install_fake_runtime(monkeypatch, runner, *, expect_attempt_for=None):
    dispatchers: list[_Dispatcher] = []
    kernels = []
    expect_attempt_for = expect_attempt_for if expect_attempt_for is not None else set()

    def build_dispatcher(_cfg, *, frame_id, workspace):
        del workspace
        dispatcher = _Dispatcher(frame_id)
        dispatchers.append(dispatcher)
        return dispatcher

    class FakeKernel:
        def __init__(self, dispatcher, **options) -> None:
            self.dispatcher = dispatcher
            self.options = options
            self.live = True
            if dispatcher.frame_id in expect_attempt_for:
                assert runner.store.list_execution_attempts(
                    root_frame_id=dispatcher.frame_id
                ), "execution attempt must exist before kernel construction"
            kernels.append(self)

        def is_alive(self) -> bool:
            return self.live

        def execute(self, code, origin="agent", on_chunk=None, *, cell_id=None):
            del origin, on_chunk
            if "host.submit_output" in code:
                self.dispatcher.last_output = {"output": {"ok": True}}
            return {
                "id": cell_id,
                "stdout": "",
                "stderr": "",
                "error": None,
                "usage": {},
            }

        def interrupt(self) -> None:
            pass

        def shutdown(self) -> None:
            self.live = False

        def restart(self) -> None:
            self.live = True

        def kill_worker(self) -> None:
            self.live = False

    monkeypatch.setattr(gateway_mod, "build_dispatcher", build_dispatcher)
    monkeypatch.setattr(gateway_mod, "Kernel", FakeKernel)
    monkeypatch.setattr(runner, "_wire_delegation", lambda state: None)
    monkeypatch.setattr(runner, "_spawn_title_summary", lambda *args, **kwargs: None)
    runner.skills = SimpleNamespace(system_context="", bootstrap_code="")
    return dispatchers, kernels


def test_session_creation_plan_and_native_tool_turn_do_not_spawn(monkeypatch, tmp_path):
    runner = gateway_mod.SessionRunner(_cfg(tmp_path), _Hub())
    dispatchers, kernels = _install_fake_runtime(monkeypatch, runner)

    created = _frame(runner)
    state = runner._state(created, "default")
    assert state.runtime.ready is False
    assert state.kernel is None

    monkeypatch.setattr(
        gateway_mod,
        "chat",
        lambda *args, **kwargs: {"content": "A reviewable plan.", "usage": {}},
    )
    planned = runner.run_message(created, "default", "Plan this", plan=True)
    assert planned["status"] == "completed"
    assert state.runtime.ready is True
    assert state.kernel is None
    assert kernels == []

    tool_frame = _frame(runner)
    call = _native_call()
    monkeypatch.setattr(
        gateway_mod,
        "chat",
        lambda *args, **kwargs: {
            "content": "Inspecting metadata.",
            "usage": {},
            "tool_calls": [call],
            "assistant_message": {
                "role": "assistant",
                "content": "Inspecting metadata.",
                "tool_calls": [call],
            },
        },
    )
    tool_result = runner.run_message(tool_frame, "default", "List files")
    tool_state = runner._state(tool_frame, "default")
    assert tool_result["status"] == "failed"  # one tool turn, then max-turn stop
    assert tool_state.kernel is None
    assert kernels == []
    assert tool_state.dispatcher.calls == [("list_dir", [{"path": "."}])]
    assert len(dispatchers) == 2


def test_session_runtime_reuses_delegation_tree_and_scoped_capabilities(tmp_path):
    runner = gateway_mod.SessionRunner(
        _cfg(tmp_path), _Hub(), start_idle_sweeper=False
    )
    frame_id = _frame(runner)
    state = runner._state(frame_id, "default")

    dispatcher = runner._ensure_runtime(state)
    first = state.delegation_runner
    assert first is not None
    assert dispatcher.skill_loader.capabilities.session_id == frame_id
    assert dispatcher.skill_loader.capabilities.project_id == "default"

    state.model = "next-model"
    runner._wire_delegation(state)
    assert state.delegation_runner is first
    assert dispatcher._delegate_fn is first
    assert first.cfg.llm.model == "next-model"
    runner.close()


def test_first_code_spawns_once_and_stop_does_not_break_tool_only_runtime(
    monkeypatch, tmp_path
):
    runner = gateway_mod.SessionRunner(_cfg(tmp_path), _Hub())
    dispatchers, kernels = _install_fake_runtime(monkeypatch, runner)
    frame_id = _frame(runner)

    monkeypatch.setattr(
        gateway_mod,
        "chat",
        lambda *args, **kwargs: {
            "content": "```python\nhost.submit_output({'ok': True}, ['done'])\n```",
            "usage": {},
        },
    )
    completed = runner.run_message(frame_id, "default", "Compute")
    state = runner._state(frame_id, "default")
    assert completed["status"] == "completed"
    assert len(kernels) == 1
    dispatcher = state.dispatcher

    runner.stop_kernel(frame_id)
    assert state.kernel is None
    assert state.dispatcher is dispatcher

    call = _native_call()
    monkeypatch.setattr(
        gateway_mod,
        "chat",
        lambda *args, **kwargs: {
            "content": "Checking metadata.",
            "usage": {},
            "tool_calls": [call],
            "assistant_message": {
                "role": "assistant",
                "content": "Checking metadata.",
                "tool_calls": [call],
            },
        },
    )
    runner.run_message(frame_id, "default", "List files after stop")
    assert len(kernels) == 1
    assert state.kernel is None
    assert state.dispatcher is dispatcher
    assert len(dispatchers) == 1


def test_explicit_start_and_repl_spawn_but_repl_attempt_precedes_spawn(
    monkeypatch, tmp_path
):
    runner = gateway_mod.SessionRunner(_cfg(tmp_path), _Hub())
    expected_attempts: set[str] = set()
    _dispatchers, kernels = _install_fake_runtime(
        monkeypatch, runner, expect_attempt_for=expected_attempts
    )

    start_frame = _frame(runner)
    started = runner.start_kernel(start_frame)
    assert started["state"] == "running"
    assert len(kernels) == 1

    repl_frame = _frame(runner)
    expected_attempts.add(repl_frame)
    result = runner.run_repl(repl_frame, "default", "print('hello')")
    assert result["cell"]["status"] == "ok"
    assert len(kernels) == 2
    attempts = runner.store.list_execution_attempts(root_frame_id=repl_frame)
    assert len(attempts) == 1
    assert attempts[0]["terminal_state"] == "completed"


def test_environment_selection_without_kernel_only_persists(monkeypatch, tmp_path):
    runner = gateway_mod.SessionRunner(_cfg(tmp_path), _Hub())
    frame_id = _frame(runner)
    state = runner._state(frame_id, "default")
    environment = SimpleNamespace(
        name="struct",
        language="python",
        interpreter="struct-python",
        bin_dir="/envs/struct/bin",
        python_version=lambda: "3.14",
    )
    from openai4s.kernel import environments as envmod

    monkeypatch.setattr(envmod, "get_environment", lambda name: environment)
    monkeypatch.setattr(
        gateway_mod,
        "Kernel",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("environment selection spawned a kernel")
        ),
    )

    changed = runner.set_env(frame_id, "struct")
    assert changed["state"] == "none"
    assert state.kernel is None
    assert state.desired_env == "struct"
    assert runner.store.get_frame(frame_id)["runtime_env"] == "struct"


def test_session_runtime_factory_is_lazy_and_singleton():
    runtime = SessionRuntime()
    created = []

    dispatcher = runtime.ensure(lambda: created.append(object()) or created[-1])
    assert runtime.ensure(lambda: object()) is dispatcher
    assert created == [dispatcher]
