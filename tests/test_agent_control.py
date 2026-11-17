"""Failure and cancellation contracts for native control-tool batches."""

import threading
import time

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


def test_independent_read_only_calls_run_in_parallel_with_ordered_results():
    calls = NativeToolBatch(tuple(_call(index) for index in range(3)))
    all_started = threading.Event()
    started: list[int] = []
    lock = threading.Lock()

    def invoke(call):
        with lock:
            started.append(call.ordinal)
            if len(started) == 3:
                all_started.set()
        if not all_started.wait(1):
            raise AssertionError("independent read-only calls did not overlap")
        # Deliberately complete in reverse order; provider history must not.
        time.sleep((2 - call.ordinal) * 0.005)
        return f"result-{call.ordinal}", True

    outcome = execute_native_batch(
        calls,
        invoke,
        validate=lambda _name, _arguments: None,
        parallel_policy=lambda call: (
            True,
            (f"artifact:version-{call.ordinal}",),
        ),
    )

    assert [item["content"] for item in outcome.history_messages] == [
        "result-0",
        "result-1",
        "result-2",
    ]
    assert [item["tool_call_id"] for item in outcome.history_messages] == [
        "call-0",
        "call-1",
        "call-2",
    ]


def test_a_parallel_wave_sees_the_callers_context():
    """A pool thread starts with an empty context.

    The execution principal travels in a ContextVar, so a team-mode `frames`
    call batched with another read-only call lost it and failed with
    `PrincipalRequired`, while the same call alone -- run on the caller's
    thread -- worked.
    """
    import contextvars

    marker: contextvars.ContextVar[str] = contextvars.ContextVar(
        "marker", default="unset"
    )
    calls = NativeToolBatch(tuple(_call(index) for index in range(3)))
    both_running = threading.Barrier(3, timeout=1)

    def invoke(call):
        both_running.wait()  # proves these ran on pool threads, concurrently
        return marker.get(), True

    token = marker.set("the-callers-value")
    try:
        outcome = execute_native_batch(
            calls,
            invoke,
            validate=lambda _name, _arguments: None,
            parallel_policy=lambda call: (
                True,
                (f"artifact:version-{call.ordinal}",),
            ),
        )
    finally:
        marker.reset(token)

    assert [item["content"] for item in outcome.history_messages] == [
        "the-callers-value"
    ] * 3


def test_conflicting_read_only_resources_are_serialized():
    calls = NativeToolBatch(tuple(_call(index) for index in range(3)))
    active = 0
    peak = 0
    lock = threading.Lock()

    def invoke(call):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.01)
        with lock:
            active -= 1
        return f"result-{call.ordinal}", True

    outcome = execute_native_batch(
        calls,
        invoke,
        validate=lambda _name, _arguments: None,
        parallel_policy=lambda _call: (True, ("workspace:data.csv",)),
    )

    assert peak == 1
    assert all(not item["is_error"] for item in outcome.history_messages)


def test_mutating_call_is_a_barrier_for_all_later_calls():
    calls = NativeToolBatch(tuple(_call(index) for index in range(4)))
    active = 0
    peak_after_mutation = 0
    mutation_finished = False
    lock = threading.Lock()

    def invoke(call):
        nonlocal active, peak_after_mutation, mutation_finished
        with lock:
            active += 1
            if mutation_finished:
                peak_after_mutation = max(peak_after_mutation, active)
        time.sleep(0.005)
        with lock:
            active -= 1
            if call.ordinal == 1:
                mutation_finished = True
        return f"result-{call.ordinal}", True

    outcome = execute_native_batch(
        calls,
        invoke,
        validate=lambda _name, _arguments: None,
        parallel_policy=lambda call: (
            call.ordinal != 1,
            (f"resource:{call.ordinal}",),
        ),
    )

    assert peak_after_mutation == 1
    assert [item["content"] for item in outcome.history_messages] == [
        "result-0",
        "result-1",
        "result-2",
        "result-3",
    ]
