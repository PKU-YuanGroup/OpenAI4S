"""Fast contract tests for the Web adapters around the pure agent engine."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openai4s.agent.actions import (
    MULTI_CELL_NOTE,
    NO_CODE_NUDGE,
    CodeCell,
    NativeToolBatch,
    NativeToolCall,
)
from openai4s.agent.events import ActionRouted, ReplyReceived, TextDelta, TurnStarted
from openai4s.agent.models import ModelReply, RunState
from openai4s.server import agent_run
from openai4s.server.agent_run import ProseStreamer, WebActionExecutor, WebEventSink


def _native_call(
    index: int,
    *,
    name: str = "list_dir",
    wire_id: str | None = None,
    arguments: dict | None = None,
) -> NativeToolCall:
    return NativeToolCall(
        id=f"call-{index}",
        wire_id=wire_id,
        name=name,
        ordinal=index,
        raw_arguments="{}",
        arguments={} if arguments is None else arguments,
        provider_meta={"provider": "test"},
    )


def _event_sink(*, send=None, visible=None, usage=None) -> WebEventSink:
    sent = [] if send is None else send
    shown = [] if visible is None else visible
    used = [] if usage is None else usage
    return WebEventSink(
        sent.append,
        "frame-1",
        shown,
        used.append,
    )


def _executor(
    dispatcher,
    *,
    events=None,
    apply_pending=None,
    execute_cell=None,
    **overrides,
) -> WebActionExecutor:
    return WebActionExecutor(
        dispatcher=lambda: dispatcher,
        apply_pending=apply_pending or (lambda: None),
        execute_cell=execute_cell
        or (lambda action: {"stdout": "", "stderr": "", "error": None}),
        events=events or _event_sink(),
        prose_nudge="submit-nudge",
        explore_nudge="explore-nudge",
        **overrides,
    )


def test_prose_streamer_hides_nested_fences_across_delta_boundaries():
    sent = []
    streamer = ProseStreamer(sent.append, "frame-1")
    reply = (
        "Before.\n"
        "````python\n"
        "readme = '''\n"
        "```tool\n"
        '{"name":"list_dir","arguments":{}}\n'
        "```\n"
        "'''\n"
        "print(readme)\n"
        "````\n"
        "After."
    )

    for start, end in ((0, 13), (13, 31), (31, 67), (67, len(reply))):
        streamer.feed(reply[start:end])
    streamer.finalize()

    assert "".join(event["chunk"] for event in sent) == "Before.\nAfter."
    assert all(
        event
        == {
            "type": "text_chunk",
            "frame_id": "frame-1",
            "block_type": "text",
            "chunk": event["chunk"],
        }
        for event in sent
    )
    assert all("list_dir" not in event["chunk"] for event in sent)


def test_web_event_sink_streams_visible_prose_records_usage_and_visible_block():
    sent = []
    visible = []
    usage = []
    sink = _event_sink(send=sent, visible=visible, usage=usage)
    content = "Visible.\n```python\nprint('hidden')\n```\n"

    sink.emit(TurnStarted(turn=0))
    sink.emit(TextDelta("Visible.\n```python\n", turn=0))
    sink.emit(TextDelta("print('hidden')\n```\n", turn=0))
    reply = ModelReply(
        content=content,
        usage={"prompt_tokens": 11, "completion_tokens": 7},
    )
    sink.emit(ReplyReceived(reply, turn=0))

    assert [event["chunk"] for event in sent] == ["Visible.\n"]
    assert sink.current_prose == "Visible."
    assert len(visible) == 1
    assert visible[0]["text"] == "Visible."
    assert isinstance(visible[0]["at"], int)
    assert usage == [{"prompt_tokens": 11, "completion_tokens": 7}]


def test_web_event_sink_falls_back_when_provider_emits_no_deltas():
    sent = []
    visible = []
    usage = []
    sink = _event_sink(send=sent, visible=visible, usage=usage)
    reply = ModelReply(
        content="Blocking reply.\n```r\nprint(1)\n```",
        usage={"prompt_tokens": 3},
    )

    sink.emit(TurnStarted(turn=2))
    sink.emit(ReplyReceived(reply, turn=2))

    assert sent == [
        {
            "type": "text_chunk",
            "frame_id": "frame-1",
            "block_type": "text",
            "chunk": "Blocking reply.\n",
        }
    ]
    assert [block["text"] for block in visible] == ["Blocking reply."]
    assert usage == [{"prompt_tokens": 3}]


def test_web_event_sink_narrates_tool_only_action_without_leaking_arguments():
    sent = []
    visible = []
    sink = WebEventSink(
        sent.append,
        "frame-1",
        visible,
        lambda usage: None,
        language="zh",
    )
    action = NativeToolBatch(
        (_native_call(0, name="web_search", arguments={"query": "private"}),)
    )

    sink.emit(TurnStarted(turn=0))
    sink.emit(ReplyReceived(ModelReply(content="", tool_calls=action.calls), turn=0))
    sink.emit(ActionRouted(action, turn=0))

    assert len(sent) == 1
    assert sent[0]["block_type"] == "text"
    assert "检索" in sent[0]["chunk"]
    assert "private" not in sent[0]["chunk"]
    assert [block["text"] for block in visible] == [sink.current_prose]
    assert sink.model_prose == ""


def test_web_event_sink_does_not_duplicate_real_prose_at_action_boundary():
    sent = []
    visible = []
    sink = _event_sink(send=sent, visible=visible)
    action = CodeCell("python", "print(42)")

    sink.emit(TurnStarted(turn=0))
    sink.emit(ReplyReceived(ModelReply(content="I will compute it."), turn=0))
    sink.emit(ActionRouted(action, turn=0))

    assert [event["chunk"] for event in sent] == ["I will compute it.\n"]
    assert [block["text"] for block in visible] == ["I will compute it."]


def test_native_batch_returns_canonical_tool_history_and_never_completes(
    monkeypatch,
):
    dispatcher = SimpleNamespace(last_output={"stale": "not completion"})
    applied = []
    invoked = []

    def fake_execute(current_dispatcher, call):
        invoked.append((current_dispatcher, call))
        return f"result:{call['name']}", True

    monkeypatch.setattr(agent_run, "execute_tool_call", fake_execute)
    executor = _executor(
        dispatcher,
        apply_pending=lambda: applied.append("apply"),
    )
    calls = (
        _native_call(0, wire_id=None, arguments={"path": "."}),
        _native_call(
            1,
            name="web_search",
            wire_id="wire-1",
            arguments={"query": "ATP"},
        ),
    )

    outcome = executor.execute(
        NativeToolBatch(calls),
        ModelReply(tool_calls=calls),
        RunState([]),
    )

    assert list(outcome.history_messages) == [
        {
            "role": "tool",
            "tool_call_id": "call-0",
            "wire_id": None,
            "name": "list_dir",
            "content": "result:list_dir",
            "is_error": False,
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "wire_id": "wire-1",
            "name": "web_search",
            "content": "result:web_search",
            "is_error": False,
        },
    ]
    assert [call for _, call in invoked] == [
        {"name": "list_dir", "arguments": {"path": "."}},
        {"name": "web_search", "arguments": {"query": "ATP"}},
    ]
    assert all(current is dispatcher for current, _ in invoked)
    assert applied == ["apply", "apply", "apply"]
    assert outcome.completion is None
    assert outcome.stop_reason is None


def test_code_action_is_the_only_path_that_reads_submit_completion():
    dispatcher = SimpleNamespace(last_output=None)
    submitted = {
        "output": {"answer": 42},
        "completion_bullets": ["computed"],
    }
    executed = []

    def execute_cell(action):
        executed.append(action.code)
        if "submit_output" in action.code:
            dispatcher.last_output = submitted
        return {"stdout": "42\n", "stderr": "", "error": None, "usage": {}}

    executor = _executor(dispatcher, execute_cell=execute_cell)
    first = executor.execute(
        CodeCell("python", "print(6 * 7)\n"),
        ModelReply(content="```python\nprint(6 * 7)\n```"),
        RunState([]),
    )
    second = executor.execute(
        CodeCell("python", "host.submit_output(...)\n"),
        ModelReply(content="```python\nhost.submit_output(...)\n```"),
        RunState([]),
    )

    assert first.completion is None
    assert second.completion is submitted
    assert second.stop_reason is None
    assert executed == ["print(6 * 7)\n", "host.submit_output(...)\n"]


def test_code_action_executes_one_cell_and_warns_about_later_cells():
    dispatcher = SimpleNamespace(last_output=None)
    executed = []
    executor = _executor(
        dispatcher,
        execute_cell=lambda action: (
            executed.append(action.code)
            or {"stdout": "first\n", "stderr": "", "error": None, "usage": {}}
        ),
    )
    reply = ModelReply(
        content=(
            "```python\nprint('first')\n```\n"
            "```python\nprint('second')\n```"
        )
    )

    outcome = executor.execute(
        CodeCell("python", "print('first')\n"), reply, RunState([])
    )

    assert executed == ["print('first')\n"]
    assert "stdout:\nfirst" in outcome.observation
    assert MULTI_CELL_NOTE in outcome.observation
    assert outcome.history_messages == (
        {"role": "user", "content": outcome.observation},
    )


def test_legacy_fenced_tool_still_executes_and_returns_user_observation(monkeypatch):
    dispatcher = SimpleNamespace(last_output=None)
    applied = []
    invoked = []

    def fake_execute(current_dispatcher, call):
        invoked.append((current_dispatcher, call))
        return "legacy result", True

    monkeypatch.setattr(agent_run, "execute_tool_call", fake_execute)
    executor = _executor(
        dispatcher,
        apply_pending=lambda: applied.append("apply"),
    )
    reply = ModelReply(
        content=(
            "```tool\n"
            '{"name":"list_dir","arguments":{"path":"."}}\n'
            "```"
        )
    )

    outcome = executor.execute(None, reply, RunState([]))

    assert invoked == [
        (dispatcher, {"name": "list_dir", "arguments": {"path": "."}})
    ]
    assert applied == ["apply", "apply"]
    assert "legacy result" in outcome.observation
    assert outcome.history_messages == (
        {"role": "user", "content": outcome.observation},
    )


@pytest.mark.parametrize(
    ("explore_mode", "expected"),
    [(False, "submit-nudge"), (True, "explore-nudge")],
)
def test_prose_only_reply_uses_normal_or_explore_nudge(explore_mode, expected):
    events = _event_sink()
    events.current_prose = "A prose-only conclusion"
    executor = _executor(
        SimpleNamespace(last_output=None),
        events=events,
        explore_mode=explore_mode,
    )

    outcome = executor.execute(
        None, ModelReply(content="A prose-only conclusion"), RunState([])
    )

    assert outcome.observation == expected
    assert outcome.history_messages == ({"role": "user", "content": expected},)


def test_empty_reply_uses_no_code_nudge():
    outcome = _executor(SimpleNamespace(last_output=None)).execute(
        None, ModelReply(content=""), RunState([])
    )

    assert outcome.observation == NO_CODE_NUDGE


def test_plan_mode_refuses_native_calls_without_executing_and_closes_history(
    monkeypatch,
):
    calls = (
        _native_call(0, wire_id="wire-0"),
        _native_call(1, name="web_search", wire_id=None),
    )
    reply = ModelReply(content="Plan prose", tool_calls=calls)
    finalized = []
    events = _event_sink()
    events.current_prose = "Plan prose"

    def unexpected(*args, **kwargs):
        raise AssertionError(f"plan mode executed an action: {args!r} {kwargs!r}")

    monkeypatch.setattr(agent_run, "execute_tool_call", unexpected)
    executor = _executor(
        SimpleNamespace(last_output=None),
        events=events,
        apply_pending=unexpected,
        execute_cell=unexpected,
        plan_mode=True,
        finalize_plan=lambda actual_reply, prose: finalized.append(
            (actual_reply, prose)
        ),
    )

    outcome = executor.execute(NativeToolBatch(calls), reply, RunState([]))

    assert finalized == [(reply, "Plan prose")]
    assert outcome.stop_reason == "plan"
    assert outcome.completion is None
    assert [message["tool_call_id"] for message in outcome.history_messages] == [
        "call-0",
        "call-1",
    ]
    assert [message["wire_id"] for message in outcome.history_messages] == [
        "wire-0",
        None,
    ]
    assert all(message["role"] == "tool" for message in outcome.history_messages)
    assert all(message["is_error"] is True for message in outcome.history_messages)
    assert all(
        "tools are disabled in plan mode" in message["content"]
        for message in outcome.history_messages
    )


def test_trailing_environment_failure_is_returned_without_dangling_history(
    monkeypatch,
):
    calls = (
        _native_call(0),
        _native_call(1, name="env_use", arguments={"name": "base"}),
    )
    apply_count = 0

    def apply_pending():
        nonlocal apply_count
        apply_count += 1
        if apply_count == 3:
            raise RuntimeError("spawn failed")

    monkeypatch.setattr(agent_run, "execute_tool_call", lambda *args: ("ok", True))
    executor = _executor(
        SimpleNamespace(last_output=None),
        apply_pending=apply_pending,
    )

    outcome = executor.execute(
        NativeToolBatch(calls), ModelReply(tool_calls=calls), RunState([])
    )

    assert len(outcome.history_messages) == 2
    assert "pending environment switch failed" in outcome.observation
    assert any(message["is_error"] for message in outcome.history_messages)
    assert "spawn failed" in outcome.history_messages[-1]["content"]


def test_cancelled_run_stops_before_plan_or_action(monkeypatch):
    calls = (_native_call(0, wire_id="wire-0"),)

    def unexpected(*args, **kwargs):
        raise AssertionError(f"cancelled run executed work: {args!r} {kwargs!r}")

    monkeypatch.setattr(agent_run, "execute_tool_call", unexpected)
    executor = _executor(
        SimpleNamespace(last_output=None),
        apply_pending=unexpected,
        execute_cell=unexpected,
        plan_mode=True,
        finalize_plan=unexpected,
        cancelled=lambda: True,
    )

    outcome = executor.execute(
        NativeToolBatch(calls),
        ModelReply(tool_calls=calls),
        RunState([]),
    )

    assert outcome.stop_reason == "cancelled"
    assert outcome.completion is None
    assert len(outcome.history_messages) == 1
    assert outcome.history_messages[0]["tool_call_id"] == "call-0"
    assert outcome.history_messages[0]["is_error"] is True
    assert "cancelled before execution" in outcome.history_messages[0]["content"]
