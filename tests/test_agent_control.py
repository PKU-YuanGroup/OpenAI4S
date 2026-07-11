"""Failure and cancellation contracts for native control-tool batches."""

from openai4s.agent.actions import NativeToolBatch, NativeToolCall
from openai4s.agent.control import execute_native_batch


def _call(index: int) -> NativeToolCall:
    return NativeToolCall(
        id=f"call-{index}",
        wire_id=f"wire-{index}",
        name=f"tool_{index}",
        ordinal=index,
        raw_arguments="{}",
        arguments={},
    )


def test_invocation_failure_does_not_drop_later_tool_results():
    calls = NativeToolBatch(tuple(_call(index) for index in range(3)))

    def invoke(call):
        if call.ordinal == 0:
            raise RuntimeError("environment restart failed")
        return f"ok-{call.ordinal}", True

    outcome = execute_native_batch(calls, invoke)

    assert len(outcome.history_messages) == 3
    assert outcome.history_messages[0]["is_error"] is True
    assert "environment restart failed" in outcome.history_messages[0]["content"]
    assert [item["is_error"] for item in outcome.history_messages[1:]] == [
        False,
        False,
    ]


def test_mid_batch_cancellation_stops_side_effects_but_closes_every_call():
    calls = NativeToolBatch(tuple(_call(index) for index in range(3)))
    cancelled = False
    invoked = []

    def invoke(call):
        nonlocal cancelled
        invoked.append(call.id)
        cancelled = True
        return "first completed", True

    outcome = execute_native_batch(
        calls,
        invoke,
        cancelled=lambda: cancelled,
    )

    assert invoked == ["call-0"]
    assert len(outcome.history_messages) == 3
    assert outcome.history_messages[0]["is_error"] is False
    assert all(item["is_error"] for item in outcome.history_messages[1:])
    assert all(
        "cancelled before execution" in item["content"]
        for item in outcome.history_messages[1:]
    )


def test_normalized_known_tool_arguments_are_validated_before_invocation():
    call = NativeToolCall(
        id="call-invalid",
        wire_id="wire-invalid",
        name="read_text_file",
        ordinal=0,
        raw_arguments='{"path":3,"surprise":true}',
        arguments={"path": 3, "surprise": True},
    )
    invoked = []

    outcome = execute_native_batch(
        NativeToolBatch((call,)),
        lambda native_call: (invoked.append(native_call.id) or "ran", True),
    )

    assert invoked == []
    assert outcome.history_messages[0]["is_error"] is True
    assert outcome.history_messages[0]["role"] == "tool"
    assert "invalid arguments" in outcome.history_messages[0]["content"]
    assert "$.path: expected string" in outcome.history_messages[0]["content"]
    assert "$.surprise: unknown property" in outcome.history_messages[0]["content"]
