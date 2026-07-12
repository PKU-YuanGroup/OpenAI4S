"""Integration contracts for the AgentEngine-backed Web session runner.

These tests keep the concrete kernel offline.  They exercise the composition
boundary in ``SessionRunner._loop``: native control tools, cancellation, plan
mode, environment switches, and typed-delta projection onto the existing Web
event protocol.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
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


def _cfg(tmp_path, *, max_turns: int = 3) -> Config:
    return Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=max_turns,
    )


def _native_call(
    call_id: str,
    name: str,
    arguments: dict,
    *,
    ordinal: int = 0,
) -> dict:
    return {
        "id": call_id,
        "wire_id": call_id,
        "name": name,
        "ordinal": ordinal,
        "raw_arguments": json.dumps(arguments, separators=(",", ":")),
        "arguments": arguments,
        "parse_error": None,
        "provider_meta": {"provider": "test"},
    }


def _native_reply(content: str, calls: list[dict]) -> tuple[dict, dict]:
    assistant = {
        "role": "assistant",
        "content": content,
        "tool_calls": calls,
    }
    return (
        {
            "content": content,
            "usage": {},
            "tool_calls": calls,
            "assistant_message": assistant,
        },
        assistant,
    )


def _prepare_message_runner(monkeypatch, tmp_path, dispatcher):
    cfg = _cfg(tmp_path)
    hub = _Hub()
    runner = gateway_mod.SessionRunner(cfg, hub)
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    runner.store.update_frame(frame_id, name="Existing test session")

    def ensure_runtime(state):
        state.dispatcher = dispatcher
        state.messages = [{"role": "system", "content": "sys"}]
        return dispatcher

    monkeypatch.setattr(runner, "_ensure_runtime", ensure_runtime)
    monkeypatch.setattr(runner, "_spawn_title_summary", lambda *args, **kwargs: None)
    return runner, hub, frame_id


def test_native_file_control_calls_create_versioned_artifacts(tmp_path):
    runner = gateway_mod.SessionRunner(_cfg(tmp_path), _Hub())
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    events = []
    target = state.workspace / "analysis.md"

    def write_first():
        target.write_text("first", encoding="utf-8")
        return "ok", True

    def write_second():
        target.write_text("second", encoding="utf-8")
        return "ok", True

    first = runner._invoke_control_with_artifacts(
        state,
        SimpleNamespace(name="write_file"),
        events.append,
        write_first,
    )
    assert first == ("ok", True)
    artifact = runner.store.artifact_by_filename("analysis.md", frame_id, strict=True)
    assert artifact is not None
    first_version = artifact["latest_version_id"]

    second = runner._invoke_control_with_artifacts(
        state,
        SimpleNamespace(name="edit_file"),
        events.append,
        write_second,
    )
    assert second == ("ok", True)

    artifact = runner.store.artifact_by_filename("analysis.md", frame_id, strict=True)
    versions = runner.store.list_versions(artifact["artifact_id"])
    assert len(versions) == 2
    assert artifact["latest_version_id"] != first_version
    assert (
        Path(runner.store.version_meta(first_version)["snapshot_path"]).read_text(
            encoding="utf-8"
        )
        == "first"
    )
    assert any(event.get("type") == "artifact_created" for event in events)


def test_web_native_schema_history_and_cell_only_completion(monkeypatch, tmp_path):
    class Dispatcher:
        def __init__(self) -> None:
            self.last_output = {"stale": "must be cleared"}
            self.calls: list[tuple[str, list[dict]]] = []
            self.output_seen: list[object] = []

        def __call__(self, method, args):
            self.calls.append((method, args))
            self.output_seen.append(self.last_output)
            return {"entries": [{"name": "result.csv", "type": "file"}]}

    dispatcher = Dispatcher()
    runner, _hub, frame_id = _prepare_message_runner(monkeypatch, tmp_path, dispatcher)
    call = _native_call("call-list", "list_dir", {"path": "."})
    first_reply, assistant_message = _native_reply("Checking files.", [call])
    model_calls: list[list[dict]] = []
    tool_name_sets: list[set[str]] = []

    def fake_chat(messages, cfg, on_delta=None, **kwargs):
        del cfg
        assert callable(on_delta)
        model_calls.append(copy.deepcopy(list(messages)))
        tool_name_sets.append({spec.name for spec in kwargs["tools"]})
        if len(model_calls) == 1:
            return first_reply

        history_tail = messages[-2:]
        assert history_tail[0] == assistant_message
        tool_result = history_tail[1]
        assert tool_result["role"] == "tool"
        assert tool_result["tool_call_id"] == "call-list"
        assert tool_result["wire_id"] == "call-list"
        assert tool_result["name"] == "list_dir"
        assert tool_result["is_error"] is False
        assert "result.csv" in tool_result["content"]
        return {
            "content": (
                "```python\n"
                "host.submit_output({'files': ['result.csv']}, ['done'])\n"
                "```"
            ),
            "usage": {},
        }

    def fake_execute(state, code, origin, emit, stream=True, language="python"):
        del code, origin, emit, stream, language
        state.dispatcher.last_output = {
            "output": {"files": ["result.csv"]},
            "completion_bullets": ["done"],
        }
        return {"result": {"stdout": "", "stderr": "", "error": None}}

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_execute_and_log", fake_execute)

    result = runner.run_message(frame_id, "default", "Inspect my files")

    assert result["status"] == "completed"
    assert len(model_calls) == 2
    assert dispatcher.calls == [("list_dir", [{"path": "."}])]
    assert dispatcher.output_seen == [None]
    assert all("list_dir" in names and "env_use" in names for names in tool_name_sets)
    assert all(
        "bash" not in names and "submit_output" not in names for names in tool_name_sets
    )
    assert [message["role"] for message in model_calls[1][-2:]] == [
        "assistant",
        "tool",
    ]


def test_cancel_after_llm_reply_prevents_returned_cell(monkeypatch, tmp_path):
    dispatcher = SimpleNamespace(last_output=None)
    runner, hub, frame_id = _prepare_message_runner(monkeypatch, tmp_path, dispatcher)
    model_calls = []

    def fake_chat(messages, cfg, on_delta=None, **kwargs):
        del messages, cfg, on_delta, kwargs
        model_calls.append(1)
        runner._state(frame_id, "default").cancel.set()
        return {
            "content": "```python\nraise AssertionError('must not run')\n```",
            "usage": {},
        }

    def unexpected_execute(*args, **kwargs):
        raise AssertionError(f"cancelled cell was executed: {args!r} {kwargs!r}")

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_execute_and_log", unexpected_execute)
    monkeypatch.setattr(
        runner,
        "_run_reviewer",
        lambda *args, **kwargs: pytest.fail("cancelled turn must not be reviewed"),
    )
    runner.store.set_setting(f"review:auto:{frame_id}", "1")

    result = runner.run_message(frame_id, "default", "Run a cell")

    assert result["status"] == "cancelled"
    assert model_calls == [1]
    assert hub.events[-1]["type"] == "frame_update"
    assert hub.events[-1]["status"] == "cancelled"
    stored = runner.store.list_messages(frame_id)
    assert [message["role"] for message in stored] == ["user", "assistant"]
    assert stored[-1]["content"] == "_已取消。_"


@pytest.mark.parametrize("with_native_call", [False, True])
def test_plan_mode_blocks_code_and_native_tools_and_closes_history(
    monkeypatch, tmp_path, with_native_call
):
    class RefusingDispatcher:
        last_output = None

        def __call__(self, method, args):
            raise AssertionError(f"plan mode dispatched {method!r} with {args!r}")

    dispatcher = RefusingDispatcher()
    runner, hub, frame_id = _prepare_message_runner(monkeypatch, tmp_path, dispatcher)
    content = (
        "I will inspect the data first.\n\n"
        "```json\n"
        '{"title":"Safe plan","rationale":"inspect before analysis",'
        '"confidence":"high","steps":['
        '{"id":"s1","title":"Inspect","detail":"read inputs",'
        '"deliverables":["inventory.csv"]}]}\n'
        "```\n"
        "```python\nraise AssertionError('plan cell must not run')\n```"
    )
    calls = (
        [_native_call("plan-call", "list_dir", {"path": "."})]
        if with_native_call
        else []
    )
    if calls:
        response, assistant_message = _native_reply(content, calls)
    else:
        response = {"content": content, "usage": {}}
        assistant_message = {"role": "assistant", "content": content}
    model_count = 0

    def fake_chat(messages, cfg, on_delta=None, **kwargs):
        nonlocal model_count
        del messages, cfg, on_delta
        model_count += 1
        assert kwargs["tools"] == ()
        return response

    def unexpected_execute(*args, **kwargs):
        raise AssertionError(f"plan cell was executed: {args!r} {kwargs!r}")

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_execute_and_log", unexpected_execute)

    result = runner.run_message(
        frame_id, "default", "Draft a reviewable plan", plan=True
    )

    assert result["status"] == "completed"
    assert model_count == 1
    assert runner.store.cell_count(frame_id) == 0
    plan = runner.store.get_plan_by_frame(frame_id)
    assert plan is not None and plan["status"] == "draft"
    assert plan["steps"][0]["title"] == "Inspect"
    assert any(
        artifact["filename"].startswith("plan_")
        for artifact in runner.store.list_artifacts()
    )

    state = runner._state(frame_id, "default")
    if with_native_call:
        assert state.messages[-2] == assistant_message
        result_message = state.messages[-1]
        assert result_message["role"] == "tool"
        assert result_message["tool_call_id"] == "plan-call"
        assert result_message["is_error"] is True
        assert "disabled in plan mode" in result_message["content"]
    else:
        assert state.messages[-1] == assistant_message
        assert all(message["role"] != "tool" for message in state.messages)

    ready_index = next(
        index for index, event in enumerate(hub.events) if event["type"] == "plan_ready"
    )
    terminal_index = max(
        index
        for index, event in enumerate(hub.events)
        if event["type"] == "frame_update" and event.get("status") == "completed"
    )
    assert ready_index < terminal_index


def test_native_env_switch_rebinds_dispatcher_before_next_call(monkeypatch, tmp_path):
    runner = gateway_mod.SessionRunner(_cfg(tmp_path), _Hub())
    state = runner._state("frame-env-native", "default")
    state.messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "switch then inspect"},
    ]
    dispatch_order: list[tuple[str, str]] = []

    class Dispatcher:
        def __init__(self, label: str) -> None:
            self.label = label
            self.last_output = None

        def __call__(self, method, args):
            dispatch_order.append((self.label, method))
            if method == "env_use":
                state.pending_env = args[0]["name"]
            return {"ok": True}

    state.dispatcher = Dispatcher("old")
    calls = [
        _native_call("env-call", "env_use", {"name": "struct"}),
        _native_call("list-call", "list_dir", {"path": "."}, ordinal=1),
    ]
    first_reply, _assistant = _native_reply("", calls)
    replies = iter(
        [
            first_reply,
            {
                "content": "```python\nhost.submit_output({'ok': True}, ['done'])\n```",
                "usage": {},
            },
        ]
    )
    model_histories: list[list[dict]] = []

    def fake_chat(messages, cfg, on_delta=None, **kwargs):
        del cfg, on_delta
        assert kwargs["tools"]
        model_histories.append(copy.deepcopy(list(messages)))
        return next(replies)

    def apply_pending(current, emit):
        del emit
        dispatch_order.append(("apply", current.pending_env))
        current.env_name = current.pending_env
        current.pending_env = None
        current.dispatcher = Dispatcher("new")

    def fake_execute(current, code, origin, emit, stream=True, language="python"):
        del code, origin, emit, stream, language
        current.dispatcher.last_output = {"output": {"ok": True}}
        return {"result": {"stdout": "", "stderr": "", "error": None}}

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_apply_pending_env", apply_pending)
    monkeypatch.setattr(runner, "_execute_and_log", fake_execute)

    reason = runner._loop(state, lambda event: None, [])

    assert reason == "submitted"
    assert dispatch_order == [
        ("old", "env_use"),
        ("apply", "struct"),
        ("new", "list_dir"),
    ]
    assert [message["role"] for message in model_histories[1][-3:]] == [
        "assistant",
        "tool",
        "tool",
    ]
    assert [message["tool_call_id"] for message in model_histories[1][-2:]] == [
        "env-call",
        "list-call",
    ]


def test_streamed_deltas_hide_fences_and_precede_tool_and_terminal_events(
    monkeypatch, tmp_path
):
    dispatcher = SimpleNamespace(last_output=None)
    runner, hub, frame_id = _prepare_message_runner(monkeypatch, tmp_path, dispatcher)
    reply = (
        "Before.\n"
        "```python\n"
        "host.submit_output({'ok': True}, ['done'])\n"
        "```\n"
        "After."
    )

    def fake_chat(messages, cfg, on_delta=None, **kwargs):
        del messages, cfg, kwargs
        assert callable(on_delta)
        for offset in range(0, len(reply), 5):
            on_delta(reply[offset : offset + 5])
        return {"content": reply, "usage": {}}

    def fake_execute(state, code, origin, emit, stream=True, language="python"):
        del code, origin, stream, language
        emit(
            {
                "type": "text_chunk",
                "frame_id": state.root_frame_id,
                "block_type": "tool",
                "chunk": "CELL-RAN",
            }
        )
        state.dispatcher.last_output = {"output": {"ok": True}}
        return {"result": {"stdout": "", "stderr": "", "error": None}}

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_execute_and_log", fake_execute)

    result = runner.run_message(frame_id, "default", "Stream one cell")

    assert result["status"] == "completed"
    text_events = [
        event
        for event in hub.events
        if event.get("type") == "text_chunk" and event.get("block_type") == "text"
    ]
    visible = "".join(event["chunk"] for event in text_events)
    # Anything after the action fence was generated before the cell ran and
    # therefore cannot be a trustworthy result narration.
    assert visible == "Before.\n"
    assert "host.submit_output" not in visible

    reset_index = next(
        index for index, event in enumerate(hub.events) if event["type"] == "text_reset"
    )
    text_indices = [
        index
        for index, event in enumerate(hub.events)
        if event.get("type") == "text_chunk" and event.get("block_type") == "text"
    ]
    tool_index = next(
        index
        for index, event in enumerate(hub.events)
        if event.get("type") == "text_chunk" and event.get("chunk") == "CELL-RAN"
    )
    terminal_index = max(
        index
        for index, event in enumerate(hub.events)
        if event.get("type") == "frame_update" and event.get("status") == "completed"
    )
    assert reset_index < min(text_indices) <= max(text_indices) < tool_index
    assert tool_index < terminal_index
    stored = runner.store.list_messages(frame_id)
    assert stored[-1]["role"] == "assistant"
    assert stored[-1]["content"] == "Before."


def test_code_only_failure_is_visible_after_real_cell_outcome(monkeypatch, tmp_path):
    dispatcher = SimpleNamespace(last_output=None)
    runner, hub, frame_id = _prepare_message_runner(monkeypatch, tmp_path, dispatcher)
    reply = "```python\nprint(missing_name)\n```\nThis worked perfectly."

    def fake_chat(messages, cfg, on_delta=None, **kwargs):
        del messages, cfg, kwargs
        assert callable(on_delta)
        on_delta(reply)
        return {"content": reply, "usage": {}}

    def fake_execute(state, code, origin, emit, stream=True, language="python"):
        del state, code, origin, emit, stream, language
        return {
            "result": {
                "stdout": "",
                "stderr": "",
                "error": "NameError: name 'missing_name' is not defined",
            }
        }

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_execute_and_log", fake_execute)

    result = runner.run_message(frame_id, "default", "Run one broken cell")

    assert result["status"] == "failed"
    visible = "".join(
        event.get("chunk", "")
        for event in hub.events
        if event.get("type") == "text_chunk" and event.get("block_type") == "text"
    )
    assert "This cell failed" in visible
    assert "NameError" in visible
    assert "This worked perfectly" not in visible
    stored = runner.store.list_messages(frame_id)
    assert any("NameError" in message["content"] for message in stored)


def test_conversational_json_fence_does_not_cut_off_later_public_prose(
    monkeypatch, tmp_path
):
    dispatcher = SimpleNamespace(last_output=None)
    runner, hub, frame_id = _prepare_message_runner(monkeypatch, tmp_path, dispatcher)
    reply = (
        "The public response shape is:\n"
        '```json\n{"summary": "..."}\n```\n'
        "I will now verify the values.\n"
        "```python\nhost.submit_output({'summary': 'verified'}, ['Verified values'])\n```\n"
        "Unverified trailing claim."
    )

    def fake_chat(messages, cfg, on_delta=None, **kwargs):
        del messages, cfg, kwargs
        assert callable(on_delta)
        for offset in range(0, len(reply), 7):
            on_delta(reply[offset : offset + 7])
        return {"content": reply, "usage": {}}

    def fake_execute(state, code, origin, emit, stream=True, language="python"):
        del code, origin, emit, stream, language
        state.dispatcher.last_output = {
            "output": {"summary": "verified"},
            "completion_bullets": ["Verified values"],
        }
        return {"result": {"stdout": "", "stderr": "", "error": None}}

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_execute_and_log", fake_execute)

    result = runner.run_message(frame_id, "default", "Verify the values")

    assert result["status"] == "completed"
    visible = "".join(
        event.get("chunk", "")
        for event in hub.events
        if event.get("type") == "text_chunk" and event.get("block_type") == "text"
    )
    assert "I will now verify the values." in visible
    assert '"summary": "..."' not in visible
    assert "Unverified trailing claim." not in visible
