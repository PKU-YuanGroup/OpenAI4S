"""Contracts for the local adapters around the pure agent engine."""

from __future__ import annotations

from types import SimpleNamespace

import openai4s.agent.runtime as runtime
from openai4s.agent.actions import CodeCell, NativeToolBatch, NativeToolCall
from openai4s.agent.events import OutcomeProduced, ReplyReceived, RunStarted
from openai4s.agent.models import ExecutionOutcome, ModelReply, RunState
from openai4s.agent.runtime import (
    ChatModel,
    CompactionPolicy,
    LocalActionExecutor,
    TranscriptEventSink,
    TranscriptTurn,
    format_observation,
)
from openai4s.tools.native import ToolSpec


def _native_call(
    index: int,
    *,
    name: str = "lookup",
    wire_id: str | None | object = ...,
    arguments: dict | None = None,
    parse_error: str | None = None,
) -> NativeToolCall:
    call_id = f"call_{index}"
    actual_wire_id = call_id if wire_id is ... else wire_id
    return NativeToolCall(
        id=call_id,
        wire_id=actual_wire_id,
        name=name,
        ordinal=index,
        raw_arguments='{"query":"ATP"}',
        arguments={"query": "ATP"} if arguments is None else arguments,
        parse_error=parse_error,
        provider_meta={"provider": "test"},
    )


class FakeDispatcher:
    def __init__(self, last_output=None):
        self.last_output = last_output
        self.calls = []

    def __call__(self, method, args):
        self.calls.append((method, args))
        return {"ok": True}


class FakeKernel:
    def __init__(self, result=None, after_execute=None):
        self.result = result or {
            "stdout": "",
            "stderr": "",
            "error": None,
            "usage": {},
        }
        self.after_execute = after_execute
        self.calls = []

    def execute(self, code, origin=None):
        self.calls.append((code, origin))
        if self.after_execute is not None:
            self.after_execute()
        return self.result


def _executor(*, kernel=None, dispatcher=None, gate=None, execute_r=None):
    return LocalActionExecutor(
        kernel or FakeKernel(),
        dispatcher or FakeDispatcher(),
        gate or (lambda code, messages: None),
        execute_r or (lambda code: {"stdout": "R", "error": None}),
    )


def test_chat_model_passes_native_schemas_and_is_blocking_by_default():
    spec = ToolSpec(
        "lookup",
        "Look up a fact.",
        {"type": "object", "properties": {"query": {"type": "string"}}},
    )
    cfg = object()
    calls = []

    def fake_chat(messages, received_cfg, **kwargs):
        calls.append((messages, received_cfg, kwargs))
        return {"content": "done"}

    model = ChatModel(cfg, fake_chat, tools=[spec])
    source = [{"role": "user", "content": "look it up"}]
    result = model.complete(source, lambda delta: None)

    assert result == {"content": "done"}
    assert calls == [(source, cfg, {"tools": (spec,)})]
    assert "on_delta" not in calls[0][2]


def test_chat_model_refreshes_callable_session_catalog_each_turn():
    first = ToolSpec("first", "", {"type": "object", "properties": {}})
    second = ToolSpec("second", "", {"type": "object", "properties": {}})
    active = [first]
    seen = []

    def fake_chat(messages, cfg, **kwargs):
        del messages, cfg
        seen.append(tuple(tool.name for tool in kwargs["tools"]))
        return {"content": "done"}

    model = ChatModel(object(), fake_chat, tools=lambda: tuple(active))
    model.complete([], lambda _delta: None)
    active.append(second)
    model.complete([], lambda _delta: None)

    assert seen == [("first",), ("first", "second")]


def test_native_batch_returns_one_canonical_tool_message_per_call(monkeypatch):
    dispatched = []

    def fake_execute(dispatcher, call):
        dispatched.append((dispatcher, call))
        return (f"result for {call['name']}", call["name"] == "lookup")

    monkeypatch.setattr(runtime, "execute_tool_call", fake_execute)
    dispatcher = FakeDispatcher(last_output={"stale": "must not submit"})
    executor = _executor(dispatcher=dispatcher)
    calls = (
        _native_call(0, wire_id=None),
        _native_call(1, name="delegate", arguments={"task": "check"}),
    )

    outcome = executor.execute(
        NativeToolBatch(calls), ModelReply(), RunState([{"role": "user"}])
    )

    assert list(outcome.history_messages) == [
        {
            "role": "tool",
            "tool_call_id": "call_0",
            "wire_id": None,
            "name": "lookup",
            "content": "result for lookup",
            "is_error": False,
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "wire_id": "call_1",
            "name": "delegate",
            "content": "result for delegate",
            "is_error": True,
        },
    ]
    assert all(message["role"] != "user" for message in outcome.history_messages)
    assert [call for _, call in dispatched] == [
        {"name": "lookup", "arguments": {"query": "ATP"}},
        {"name": "delegate", "arguments": {"task": "check"}},
    ]
    assert outcome.completion is None and outcome.stop_reason is None


def test_native_parse_error_never_dispatches(monkeypatch):
    def unexpected_dispatch(*args):
        raise AssertionError(f"parse-error call was dispatched: {args!r}")

    monkeypatch.setattr(runtime, "execute_tool_call", unexpected_dispatch)
    malformed = NativeToolCall(
        id="bad_0",
        wire_id="wire_bad_0",
        name="lookup",
        ordinal=0,
        raw_arguments='{"query":',
        arguments=None,
        parse_error="invalid JSON",
        provider_meta={},
    )

    outcome = _executor().execute(
        NativeToolBatch((malformed,)), ModelReply(), RunState([])
    )

    assert outcome.history_messages[0]["tool_call_id"] == "bad_0"
    assert outcome.history_messages[0]["wire_id"] == "wire_bad_0"
    assert outcome.history_messages[0]["is_error"] is True
    assert "invalid JSON" in outcome.history_messages[0]["content"]


def test_native_limit_skips_dispatch_but_never_drops_tool_results(monkeypatch):
    dispatched = []

    def fake_execute(dispatcher, call):
        dispatched.append(call)
        return "ok", True

    monkeypatch.setattr(runtime, "execute_tool_call", fake_execute)
    calls = tuple(_native_call(index) for index in range(18))

    outcome = _executor().execute(
        NativeToolBatch(calls), ModelReply(), RunState([])
    )

    assert len(dispatched) == runtime.MAX_TOOL_CALLS_PER_TURN == 16
    assert len(outcome.history_messages) == len(calls) == 18
    assert [message["tool_call_id"] for message in outcome.history_messages] == [
        f"call_{index}" for index in range(18)
    ]
    assert all(message["role"] == "tool" for message in outcome.history_messages)
    assert all(message["is_error"] for message in outcome.history_messages[16:])
    assert all(
        "was not run" in message["content"]
        for message in outcome.history_messages[16:]
    )


def test_code_observation_notes_extra_cells_and_only_submit_sets_completion():
    dispatcher = FakeDispatcher()
    gate_calls = []
    first_kernel = FakeKernel(
        {"stdout": "42\n", "stderr": "", "error": None, "usage": {}}
    )
    executor = _executor(
        kernel=first_kernel,
        dispatcher=dispatcher,
        gate=lambda code, messages: gate_calls.append((code, messages)),
    )
    reply = ModelReply(
        content=(
            "```python\nprint(6 * 7)\n```\n"
            "```python\nraise AssertionError\n```"
        )
    )

    first = executor.execute(
        CodeCell("python", "print(6 * 7)\n"),
        reply,
        RunState([{"role": "user", "content": "compute"}]),
    )

    assert first.completion is None
    assert first.history_messages[0]["role"] == "user"
    assert "stdout:\n42" in first.observation
    assert runtime.MULTI_CELL_NOTE in first.observation
    assert first_kernel.calls == [("print(6 * 7)\n", "agent")]
    assert len(gate_calls) == 1

    submitted = {"output": {"answer": 42}, "completion_bullets": ["done"]}
    second_kernel = FakeKernel(
        after_execute=lambda: setattr(dispatcher, "last_output", submitted)
    )
    second_executor = _executor(kernel=second_kernel, dispatcher=dispatcher)
    second = second_executor.execute(
        CodeCell("python", "host.submit_output(...)"),
        ModelReply(content="```python\nhost.submit_output(...)\n```"),
        RunState([]),
    )
    assert second.completion is submitted
    assert second.stop_reason is None


def test_none_action_keeps_legacy_tool_fallback_as_user_history(monkeypatch):
    calls = []

    def fake_run(dispatcher, parsed_calls, errors):
        calls.append((dispatcher, parsed_calls, errors))
        return "[Tool Results]\nlegacy result"

    monkeypatch.setattr(runtime, "run_tool_calls", fake_run)
    dispatcher = FakeDispatcher()
    executor = _executor(dispatcher=dispatcher)
    fenced = (
        "```tool\n"
        '{"name":"list_dir","arguments":{"path":"."}}\n'
        "```"
    )

    legacy = executor.execute(None, ModelReply(content=fenced), RunState([]))
    prose = executor.execute(None, ModelReply(content="plain prose"), RunState([]))

    assert len(calls) == 1
    assert calls[0][1] == [{"name": "list_dir", "arguments": {"path": "."}}]
    assert legacy.history_messages == (
        {"role": "user", "content": "[Tool Results]\nlegacy result"},
    )
    assert prose.observation == runtime.NO_CODE_NUDGE
    assert prose.history_messages == (
        {"role": "user", "content": runtime.NO_CODE_NUDGE},
    )


def test_compaction_expands_tail_to_keep_assistant_tool_group_atomic(monkeypatch):
    assistant = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": f"call_{index}"} for index in range(5)],
    }
    tools = [
        {"role": "tool", "tool_call_id": f"call_{index}", "content": "ok"}
        for index in range(5)
    ]
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "old"},
        {"role": "user", "content": "old observation"},
        assistant,
        *tools,
    ]
    captured = {}

    monkeypatch.setattr(runtime, "should_compact", lambda messages, cfg: True)

    def fake_compact(
        messages,
        cfg,
        *,
        keep_recent,
        archive_dir,
        archive_metadata,
        large_output_chars,
    ):
        captured.update(
            keep_recent=keep_recent,
            tail=messages[-keep_recent:],
            archive_dir=archive_dir,
            archive_metadata=archive_metadata,
            large_output_chars=large_output_chars,
        )
        return messages

    monkeypatch.setattr(runtime, "compact", fake_compact)
    cfg = SimpleNamespace(compaction_dir="archive")

    prepared = CompactionPolicy(cfg).prepare(RunState(messages))

    assert prepared is messages
    assert captured["keep_recent"] == 6
    assert captured["tail"] == [assistant, *tools]
    assert captured["archive_dir"] == "archive"
    assert captured["archive_metadata"].active_kernel_generation is None
    assert captured["large_output_chars"] == runtime.DEFAULT_LARGE_OUTPUT_CHARS


def test_compaction_circuit_breaker_stops_repeated_low_yield_calls(monkeypatch):
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "old"},
        {"role": "user", "content": "old observation"},
        {"role": "assistant", "content": "recent"},
    ]
    attempts = []
    monkeypatch.setattr(runtime, "should_compact", lambda messages, cfg: True)

    def no_yield(messages, cfg, **kwargs):
        attempts.append(kwargs)
        return messages

    monkeypatch.setattr(runtime, "compact", no_yield)
    policy = CompactionPolicy(SimpleNamespace(compaction_dir="archive"))
    state = RunState(messages)

    policy.prepare(state)
    policy.prepare(state)
    policy.prepare(state)

    assert len(attempts) == 2
    assert policy.low_yield_streak == 2
    assert policy.circuit_open is True
    assert state.metadata["compaction_circuit_open"] is True


def test_executor_records_kernel_generation_change_for_safe_handoff():
    kernel = FakeKernel()
    kernel.generation = 9
    state = RunState(
        [{"role": "user", "content": "continue"}],
        metadata={"active_kernel_generation": 8},
    )

    _executor(kernel=kernel).execute(
        CodeCell("python", "print('ready')"),
        ModelReply(content="```python\nprint('ready')\n```"),
        state,
    )

    assert state.metadata["previous_kernel_generation"] == 8
    assert state.metadata["active_kernel_generation"] == 9
    assert state.metadata["kernel_restarted"] is True


def test_transcript_sink_projects_only_reply_and_observed_outcome_events():
    transcript = []
    logs = []
    sink = TranscriptEventSink(transcript, log=lambda *parts: logs.append(parts))
    reply = ModelReply(content="working")

    sink.emit(RunStarted(max_turns=2, history_size=1))
    sink.emit(ReplyReceived(reply, turn=0))
    sink.emit(OutcomeProduced(ExecutionOutcome(observation="observed"), turn=0))
    sink.emit(OutcomeProduced(ExecutionOutcome(), turn=1))

    assert transcript == [
        TranscriptTurn("assistant", "working"),
        TranscriptTurn("observation", "observed"),
    ]
    assert len(logs) == 2


def test_format_observation_preserves_stable_error_and_usage_protocol():
    observation = format_observation(
        {
            "stdout": "value\n",
            "stderr": "warning\n",
            "error": "boom\n",
            "trace": {"error_lineno": 3},
            "usage": {"wall_s": 1.0, "cpu_s": 0.5, "peak_rss_kb": 64},
        }
    )

    assert observation == (
        "[Observation]\n"
        "stdout:\nvalue\n"
        "stderr:\nwarning\n"
        "ERROR (cell line 3):\nboom\n"
        "[usage wall=1.0s cpu=0.5s rss=64kb]"
    )
