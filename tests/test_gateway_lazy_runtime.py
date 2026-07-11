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
            del origin
            if "host.submit_output" in code:
                self.dispatcher.last_output = {"output": {"ok": True}}
            if on_chunk is not None:
                on_chunk("live output")
            if "interrupt_result()" in code:
                return {
                    "id": cell_id,
                    "stdout": "",
                    "stderr": "",
                    "error": "Interrupted",
                    "interrupted": True,
                    "usage": {},
                }
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


def test_tool_only_structured_finalize_completes_without_kernel(monkeypatch, tmp_path):
    runner = gateway_mod.SessionRunner(
        _cfg(tmp_path, max_turns=1), _Hub(), start_idle_sweeper=False
    )
    frame_id = _frame(runner)
    exposed: list[set[str]] = []
    arguments = {
        "summary": "The session metadata is ready.",
        "findings": ["No scientific kernel was required."],
        "completion_bullets": ["Reported session metadata"],
    }

    def fake_chat(messages, cfg, **kwargs):
        del messages, cfg
        exposed.append({spec.name for spec in kwargs["tools"]})
        call = {
            "id": "final-web",
            "wire_id": "final-web",
            "name": "finalize_response",
            "ordinal": 0,
            "raw_arguments": json.dumps(arguments),
            "arguments": arguments,
            "parse_error": None,
            "provider_meta": {},
        }
        return {
            "content": "",
            "tool_calls": [call],
            "assistant_message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [call],
            },
            "usage": {},
        }

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_spawn_title_summary", lambda *args: None)
    result = runner.run_message(frame_id, "default", "Describe the session")
    state = runner._state(frame_id, "default")

    assert result["status"] == "completed"
    assert "finalize_response" in exposed[0]
    assert state.kernel is None
    assert state.last_engine_completion["output"]["summary"] == arguments["summary"]
    assistant = [
        item
        for item in runner.store.list_messages(frame_id)
        if item.get("role") == "assistant"
    ]
    assert any(arguments["summary"] in item["content"] for item in assistant)
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
    assert result["cell"]["state_revision"] == 1
    assert result["cell"]["generation_id"]
    assert len(kernels) == 2
    repl_events = [
        event
        for event in runner.hub.events
        if event.get("root_frame_id") == repl_frame
    ]
    assert [
        event["type"]
        for event in repl_events
        if event["type"].startswith("notebook_cell_")
    ] == [
        "notebook_cell_start",
        "notebook_cell_chunk",
        "notebook_cell_finished",
    ]
    assert next(
        event for event in repl_events if event["type"] == "notebook_cell_chunk"
    )["chunk"] == "live output"
    start = next(
        event for event in repl_events if event["type"] == "notebook_cell_start"
    )
    finished = next(
        event for event in repl_events if event["type"] == "notebook_cell_finished"
    )
    assert start["state_revision"] == finished["state_revision"] == 1
    assert (
        start["generation_id"]
        == finished["generation_id"]
        == result["cell"]["generation_id"]
    )
    assert not any(event["type"] == "text_chunk" for event in repl_events)
    attempts = runner.store.list_execution_attempts(root_frame_id=repl_frame)
    assert len(attempts) == 1
    assert attempts[0]["terminal_state"] == "completed"
    assert attempts[0]["state_revision"] == 1
    assert attempts[0]["generation_id"] == start["generation_id"]


def test_reopened_repl_allocates_after_durable_attempt_revision(
    monkeypatch, tmp_path
):
    runner = gateway_mod.SessionRunner(_cfg(tmp_path), _Hub())
    _install_fake_runtime(monkeypatch, runner)
    frame_id = _frame(runner)
    runner.store.log_cell(
        frame_id=frame_id,
        root_frame_id=frame_id,
        code="historical()",
        result={"id": "historical-cell"},
        cell_index=5,
    )
    group = runner.store.append_action_group(
        root_frame_id=frame_id,
        turn_id="failed-before-log",
        kind="execution",
    )
    runner.store.allocate_execution_attempt(
        group_id=group["group_id"],
        producing_cell_id="failed-before-log",
        state_revision=7,
    )

    result = runner.run_repl(frame_id, "default", "print('after reopen')")

    assert result["cell"]["state_revision"] == 8
    assert [
        cell["state_revision"] for cell in runner.store.list_cells(frame_id)
    ] == [5, 8]
    attempt = next(
        item
        for item in runner.store.list_execution_attempts(root_frame_id=frame_id)
        if item["state_revision"] == 8
    )
    start = next(
        event
        for event in runner.hub.events
        if event.get("type") == "notebook_cell_start"
        and event.get("root_frame_id") == frame_id
    )
    finished = next(
        event
        for event in runner.hub.events
        if event.get("type") == "notebook_cell_finished"
        and event.get("root_frame_id") == frame_id
    )
    assert start["state_revision"] == finished["state_revision"] == 8
    assert (
        start["generation_id"]
        == finished["generation_id"]
        == result["cell"]["generation_id"]
        == attempt["generation_id"]
    )


def test_repl_response_and_persisted_cell_keep_interrupted_terminal_state(
    monkeypatch, tmp_path
):
    runner = gateway_mod.SessionRunner(_cfg(tmp_path), _Hub())
    _install_fake_runtime(monkeypatch, runner)
    frame_id = _frame(runner)

    result = runner.run_repl(frame_id, "default", "interrupt_result()")

    assert result["cell"]["status"] == "interrupted"
    assert result["cell"]["error"] == "Interrupted"
    assert runner.store.list_cells(frame_id)[0]["status"] == "interrupted"


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
