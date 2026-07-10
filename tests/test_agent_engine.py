"""Pure orchestration contracts for :mod:`openai4s.agent.engine`."""

from __future__ import annotations

import ast
import copy
from pathlib import Path

import openai4s.agent.engine as engine_module
from openai4s.agent.actions import CodeCell, NativeToolBatch
from openai4s.agent.engine import AgentEngine
from openai4s.agent.events import (
    ActionRouted,
    OutcomeProduced,
    ReplyReceived,
    RunFinished,
    RunStarted,
    TextDelta,
    TurnStarted,
)
from openai4s.agent.models import ExecutionOutcome, ModelReply


def _call(call_id="call_1", name="request_network_access", arguments=None):
    arguments = {"domain": "example.org"} if arguments is None else arguments
    return {
        "id": call_id,
        "wire_id": call_id,
        "name": name,
        "ordinal": 0,
        "raw_arguments": '{"domain":"example.org"}',
        "arguments": arguments,
        "parse_error": None,
        "provider_meta": {"provider": "test"},
    }


def _reply(content="", *, tool_calls=(), assistant_message=None):
    message = assistant_message or {"role": "assistant", "content": content}
    return {
        "content": content,
        "reasoning": None,
        "usage": {},
        "finish_reason": "stop",
        "raw": {"test": True},
        "tool_calls": list(tool_calls),
        "assistant_message": message,
    }


class FakeModel:
    def __init__(self, replies, delta_batches=()):
        self.replies = list(replies)
        self.delta_batches = list(delta_batches)
        self.calls = []

    def complete(self, messages, on_delta):
        self.calls.append(copy.deepcopy(list(messages)))
        index = len(self.calls) - 1
        deltas = self.delta_batches[index] if index < len(self.delta_batches) else ()
        for delta in deltas:
            on_delta(delta)
        return self.replies.pop(0)


class FakeContext:
    def __init__(self, prefix=()):
        self.prefix = list(prefix)
        self.calls = []

    def prepare(self, state):
        self.calls.append(copy.deepcopy(state.messages))
        return [*self.prefix, *state.messages]


class FakeExecutor:
    def __init__(self, outcomes=(), handler=None):
        self.outcomes = list(outcomes)
        self.handler = handler
        self.calls = []

    def execute(self, action, reply, state):
        self.calls.append(
            {
                "action": action,
                "reply": reply,
                "messages": copy.deepcopy(state.messages),
            }
        )
        if self.handler is not None:
            return self.handler(action, reply, state)
        return self.outcomes.pop(0) if self.outcomes else ExecutionOutcome()


class FakeEvents:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


class FakeCancellation:
    def __init__(self, answers=()):
        self.answers = list(answers)
        self.calls = 0

    def cancelled(self):
        self.calls += 1
        return self.answers.pop(0) if self.answers else False


class FakeInterceptor:
    def __init__(self, replacement=None):
        self.replacement = replacement
        self.calls = []

    def intercept(self, reply, state):
        self.calls.append((reply, state.turn))
        if callable(self.replacement):
            return self.replacement(reply, state)
        return self.replacement


def _engine(replies, outcomes=(), *, max_turns=4, **overrides):
    model = FakeModel(replies, overrides.pop("delta_batches", ()))
    context = overrides.pop("context", FakeContext())
    executor = overrides.pop("executor", FakeExecutor(outcomes))
    events = overrides.pop("events", FakeEvents())
    cancellation = overrides.pop("cancellation", FakeCancellation())
    interceptor = overrides.pop("interceptor", FakeInterceptor())
    assert not overrides
    engine = AgentEngine(
        model,
        executor,
        context_policy=context,
        event_sink=events,
        cancellation=cancellation,
        reply_interceptor=interceptor,
        max_turns=max_turns,
    )
    return engine, model, context, executor, events, cancellation, interceptor


def test_code_action_is_routed_to_the_executor_through_fake_ports():
    context = FakeContext([{"role": "system", "content": "prepared"}])
    outcome = ExecutionOutcome(
        history_messages=({"role": "user", "content": "[Observation]\n42"},)
    )
    engine, model, context, executor, events, _, _ = _engine(
        [_reply("working\n```python\nprint(6 * 7)\n```")],
        [outcome],
        max_turns=1,
        context=context,
    )

    result = engine.run([{"role": "user", "content": "compute"}])

    assert executor.calls[0]["action"] == CodeCell("python", "print(6 * 7)\n")
    assert model.calls[0][0] == {"role": "system", "content": "prepared"}
    assert len(context.calls) == 1
    assert result.messages[-1] == {"role": "user", "content": "[Observation]\n42"}
    assert isinstance(events.events[3], ActionRouted)


def test_native_calls_take_priority_over_fenced_code():
    call = _call()
    outcome = ExecutionOutcome(completion={"allowed": True})
    engine, _, _, executor, _, _, _ = _engine(
        [_reply("```python\nraise AssertionError\n```", tool_calls=[call])],
        [outcome],
    )

    result = engine.run([{"role": "user", "content": "continue"}])

    action = executor.calls[0]["action"]
    assert isinstance(action, NativeToolBatch)
    assert [item.id for item in action.calls] == ["call_1"]
    assert result.stop_reason == "submitted"


def test_replay_ready_assistant_message_and_tool_results_stay_grouped():
    calls = [_call("call_1"), _call("call_2", "delegate", {"task": "check"})]
    assistant = {
        "role": "assistant",
        "content": "",
        "tool_calls": calls,
        "wire_state": {"openai_message": {"opaque": True}},
    }
    tool_results = (
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "allowed",
        },
        {
            "role": "tool",
            "tool_call_id": "call_2",
            "content": "delegated",
        },
    )
    engine, _, _, executor, _, _, _ = _engine(
        [_reply(tool_calls=calls, assistant_message=assistant)],
        [ExecutionOutcome(history_messages=tool_results, completion={"done": True})],
    )
    user = {"role": "user", "content": "use both"}

    result = engine.run([user])

    assert list(result.messages) == [user, assistant, *tool_results]
    assert executor.calls[0]["messages"][-1] == assistant


def test_plain_reply_is_a_no_action_turn_and_not_success():
    engine, _, _, executor, _, _, _ = _engine(
        [_reply("A prose-only answer is not completion.")], max_turns=1
    )

    result = engine.run([{"role": "user", "content": "answer"}])

    assert executor.calls[0]["action"] is None
    assert result.stop_reason == "max_turns"
    assert result.completion is None


def test_direct_model_reply_gets_a_replay_safe_assistant_message():
    model = FakeModel([ModelReply(content="still working")])
    executor = FakeExecutor([ExecutionOutcome(completion={"done": True})])
    engine = AgentEngine(model, executor)

    result = engine.run([{"role": "user", "content": "continue"}])

    assert result.messages[-1] == {
        "role": "assistant",
        "content": "still working",
    }


def test_completion_without_a_custom_reason_is_the_submitted_success_path():
    engine, _, _, _, _, _, _ = _engine(
        [_reply("```python\nhost.submit_output(...)\n```")],
        [ExecutionOutcome(completion={"answer": 42})],
    )

    result = engine.run([{"role": "user", "content": "finish"}])

    assert result.stop_reason == "submitted"
    assert result.completion == {"answer": 42}
    assert result.turns == 1


def test_max_turns_is_a_hard_bound():
    engine, model, _, executor, events, cancellation, _ = _engine(
        [_reply("still working"), _reply("still working")], max_turns=2
    )

    result = engine.run([{"role": "user", "content": "loop"}])

    assert result.stop_reason == "max_turns"
    assert result.turns == 2
    assert len(model.calls) == len(executor.calls) == 2
    assert cancellation.calls == 2
    assert isinstance(events.events[-1], RunFinished)


def test_cancellation_stops_before_context_model_or_executor_work():
    cancellation = FakeCancellation([True])
    engine, model, context, executor, events, _, interceptor = _engine(
        [_reply("must not run")], cancellation=cancellation
    )

    result = engine.run([{"role": "user", "content": "cancel"}])

    assert result.stop_reason == "cancelled"
    assert result.turns == 0
    assert not model.calls and not context.calls and not executor.calls
    assert not interceptor.calls
    assert [type(event) for event in events.events] == [RunStarted, RunFinished]


def test_interceptor_can_replace_the_reply_before_a_custom_plan_stop():
    replacement = _reply("```r\nplan <- 'pause'\n```")

    def stop_plan(action, reply, state):
        assert action == CodeCell("r", "plan <- 'pause'\n")
        assert reply.content == replacement["content"]
        assert state.messages[-1] == replacement["assistant_message"]
        return ExecutionOutcome(
            completion={"plan": "pause"}, stop_reason="planned_stop"
        )

    interceptor = FakeInterceptor(replacement)
    executor = FakeExecutor(handler=stop_plan)
    engine, _, _, _, _, _, _ = _engine(
        [_reply("unintercepted")],
        executor=executor,
        interceptor=interceptor,
    )

    result = engine.run([{"role": "user", "content": "make a plan"}])

    assert len(interceptor.calls) == 1
    assert result.stop_reason == "planned_stop"
    assert result.completion == {"plan": "pause"}
    assert result.last_reply == ModelReply.from_mapping(replacement)


def test_stream_text_deltas_are_typed_events_in_turn_order():
    engine, _, _, _, events, _, _ = _engine(
        [_reply("hello")],
        [ExecutionOutcome(completion="done")],
        delta_batches=[("hel", "lo")],
    )

    engine.run([{"role": "user", "content": "stream"}])

    deltas = [event for event in events.events if isinstance(event, TextDelta)]
    assert deltas == [TextDelta("hel", 0), TextDelta("lo", 0)]
    assert [type(event) for event in events.events] == [
        RunStarted,
        TurnStarted,
        TextDelta,
        TextDelta,
        ReplyReceived,
        ActionRouted,
        OutcomeProduced,
        RunFinished,
    ]


def test_engine_has_no_direct_runtime_infrastructure_imports():
    source = Path(engine_module.__file__).read_text(encoding="utf-8")
    imports = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.append(module)
            if module == "openai4s":
                imports.extend(f"openai4s.{alias.name}" for alias in node.names)

    forbidden = (
        "openai4s.kernel",
        "openai4s.host_dispatch",
        "openai4s.store",
        "openai4s.server",
    )
    assert not [
        name
        for name in imports
        if any(name == root or name.startswith(root + ".") for root in forbidden)
    ]
