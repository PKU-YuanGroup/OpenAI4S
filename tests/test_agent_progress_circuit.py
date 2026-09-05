"""Generic Agent no-progress circuit: thresholds, durability, completion truth."""

from __future__ import annotations

import copy
import json

from openai4s.agent.actions import NativeToolBatch, NativeToolCall
from openai4s.agent.engine import AgentEngine
from openai4s.agent.events import (
    ActionRouted,
    OutcomeProduced,
    ReplyReceived,
    RunFinished,
)
from openai4s.agent.ledger import RuntimeActionLedger, restore_progress_circuit
from openai4s.agent.models import EngineResult, ExecutionOutcome, ModelReply, RunState
from openai4s.agent.progress_circuit import (
    LONG_TEXT_MIN_CHARS,
    NO_PROGRESS_STOP_REASON,
    PROGRESS_REASON_LONG_TEXT,
    PROGRESS_REASON_MALFORMED,
    PROGRESS_REASON_SAME_ACTION,
    PROGRESS_REASON_TOOL_ERROR,
    ProgressCircuit,
    attach_progress_circuit,
    reconstruct_progress_circuit,
)
from openai4s.store import Store


def _call(
    call_id: str = "call_1",
    name: str = "list_dir",
    arguments: dict | None = None,
    *,
    parse_error: str | None = None,
) -> dict:
    arguments = (
        {"path": "."} if arguments is None and parse_error is None else arguments
    )
    raw = "{bad" if parse_error else json.dumps(arguments or {}, separators=(",", ":"))
    return {
        "id": call_id,
        "wire_id": call_id,
        "name": name,
        "ordinal": 0,
        "raw_arguments": raw,
        "arguments": arguments,
        "parse_error": parse_error,
        "provider_meta": {"provider": "test"},
    }


def _reply(content: str = "", *, tool_calls=(), reasoning=None, assistant_message=None):
    message = assistant_message or {"role": "assistant", "content": content}
    if tool_calls and "tool_calls" not in message:
        message = {**message, "tool_calls": list(tool_calls)}
    return {
        "content": content,
        "reasoning": reasoning,
        "usage": {},
        "finish_reason": "stop",
        "raw": {"test": True},
        "tool_calls": list(tool_calls),
        "assistant_message": message,
    }


def _long_text(seed: str = "loop") -> str:
    body = (seed + " the same analysis continues. ") * 40
    assert len(body) >= LONG_TEXT_MIN_CHARS
    return body


class FakeModel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, messages, on_delta):
        del on_delta
        self.calls.append(copy.deepcopy(list(messages)))
        if not self.replies:
            raise AssertionError(
                "provider called after the circuit should have tripped"
            )
        return self.replies.pop(0)


class CountingExecutor:
    def __init__(self, *, error: str | None = None):
        self.calls = []
        self.dispatched = []
        self.error = error

    def execute(self, action, reply, state):
        del reply, state
        self.calls.append(action)
        if isinstance(action, NativeToolBatch):
            history = []
            for call in action.calls:
                malformed = call.parse_error is not None or call.arguments is None
                if not malformed:
                    self.dispatched.append(call)
                text = (
                    f"[Tool error] {call.name}: {call.parse_error or 'bad json'}"
                    if malformed
                    else (
                        f"[Tool error] {call.name}: {self.error}"
                        if self.error
                        else f"{call.name} ok"
                    )
                )
                history.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "wire_id": call.wire_id,
                        "name": call.name,
                        "content": text,
                        "is_error": bool(malformed or self.error),
                    }
                )
            return ExecutionOutcome(tuple(history))
        return ExecutionOutcome()


class PollingExecutor(CountingExecutor):
    """The same call, answered differently each time -- a poll."""

    def __init__(self, contents):
        super().__init__()
        self.contents = list(contents)

    def execute(self, action, reply, state):
        outcome = super().execute(action, reply, state)
        if not isinstance(action, NativeToolBatch) or not self.contents:
            return outcome
        content = self.contents.pop(0)
        return ExecutionOutcome(
            tuple(
                {**message, "content": content} for message in outcome.history_messages
            )
        )


def _engine(replies, executor=None, *, max_turns=10, **overrides):
    model = FakeModel(replies)
    executor = executor or CountingExecutor()
    engine = AgentEngine(
        model,
        executor,
        cancellation=overrides.get("cancellation"),
        completion=overrides.get("completion"),
        max_turns=max_turns,
    )
    return engine, model, executor


def _native_replies(count: int, *, arguments=None, parse_error=None, name="list_dir"):
    replies = []
    for index in range(count):
        call = _call(
            f"call_{index}",
            name=name,
            arguments=arguments,
            parse_error=parse_error,
        )
        replies.append(_reply(tool_calls=[call]))
    return replies


def test_same_action_three_times_blocks_fourth_dispatch():
    engine, model, executor = _engine(_native_replies(5), max_turns=10)

    result = engine.run([{"role": "user", "content": "look around"}])

    assert result.stop_reason == NO_PROGRESS_STOP_REASON
    assert result.progress_reason == PROGRESS_REASON_SAME_ACTION
    assert result.completion is None
    assert len(executor.dispatched) == 3
    assert len(model.calls) == 3


def test_an_identical_poll_whose_results_move_is_progress_not_a_loop():
    """`collect_children(timeout=300)`, `exec_peek`, `compute_result` are
    called again with the same arguments by design; the answer moving is the
    progress. Counting the call alone failed the turn on the third poll --
    the one that delivered the child's finished output."""
    executor = PollingExecutor(
        [
            '{"status": "running", "progress": "10%"}',
            '{"status": "running", "progress": "55%"}',
            '{"status": "running", "progress": "90%"}',
            '{"status": "completed", "output": "done"}',
            '{"status": "completed", "output": "done"}',
        ]
    )
    engine, model, executor = _engine(
        _native_replies(5, name="collect_children", arguments={"timeout": 300}),
        executor,
        max_turns=5,
    )

    result = engine.run([{"role": "user", "content": "wait for the child"}])

    # Five polls, four distinct answers: the turn limit ends the run, not the
    # circuit, and every poll was dispatched.
    assert result.stop_reason == "max_turns"
    assert result.progress_reason is None
    assert len(executor.dispatched) == 5


def test_an_identical_poll_whose_results_stopped_moving_still_trips():
    """The control: a status payload that never changes is the loop."""
    executor = PollingExecutor(['{"status": "running"}'] * 5)
    engine, model, executor = _engine(
        _native_replies(5, name="collect_children", arguments={"timeout": 300}),
        executor,
        max_turns=10,
    )

    result = engine.run([{"role": "user", "content": "wait for the child"}])

    assert result.stop_reason == NO_PROGRESS_STOP_REASON
    assert result.progress_reason == PROGRESS_REASON_SAME_ACTION
    assert len(executor.dispatched) == 3


def test_malformed_twice_trips_without_a_third_provider_call():
    engine, model, executor = _engine(
        _native_replies(4, parse_error="invalid JSON", arguments=None),
        max_turns=10,
    )

    result = engine.run([{"role": "user", "content": "fix the call"}])

    assert result.stop_reason == NO_PROGRESS_STOP_REASON
    assert result.progress_reason == PROGRESS_REASON_MALFORMED
    assert result.completion is None
    assert executor.dispatched == []
    assert len(executor.calls) == 2
    assert len(model.calls) == 2


def test_similar_tool_error_twice_trips():
    engine, model, executor = _engine(
        _native_replies(4),
        CountingExecutor(error="path does not exist"),
        max_turns=10,
    )

    result = engine.run([{"role": "user", "content": "read missing"}])

    assert result.stop_reason == NO_PROGRESS_STOP_REASON
    assert result.progress_reason == PROGRESS_REASON_TOOL_ERROR
    assert result.completion is None
    assert len(executor.dispatched) == 2
    assert len(model.calls) == 2


def test_long_text_three_times_trips():
    text = _long_text()
    engine, model, executor = _engine(
        [_reply(text) for _ in range(5)],
        max_turns=10,
    )

    result = engine.run([{"role": "user", "content": "summarise"}])

    assert result.stop_reason == NO_PROGRESS_STOP_REASON
    assert result.progress_reason == PROGRESS_REASON_LONG_TEXT
    assert result.completion is None
    assert len(model.calls) == 3
    assert len(executor.calls) == 3


def test_different_arguments_do_not_trip():
    replies = [
        _reply(tool_calls=[_call(f"call_{index}", arguments={"path": path})])
        for index, path in enumerate((".", "src", "docs", "tests"))
    ]
    engine, model, executor = _engine(replies, max_turns=4)

    result = engine.run([{"role": "user", "content": "list several places"}])

    assert result.stop_reason == "max_turns"
    assert result.progress_reason is None
    assert len(executor.dispatched) == 4
    assert len(model.calls) == 4


def test_reasoning_insert_does_not_reset_same_action_streak():
    replies = [
        _reply(tool_calls=[_call("call_0")]),
        _reply("", reasoning="considering the directory listing"),
        _reply(tool_calls=[_call("call_1")]),
        _reply(tool_calls=[_call("call_2")]),
        _reply(tool_calls=[_call("call_3")]),
    ]
    engine, model, executor = _engine(replies, max_turns=10)

    result = engine.run([{"role": "user", "content": "keep looking"}])

    assert result.stop_reason == NO_PROGRESS_STOP_REASON
    assert result.progress_reason == PROGRESS_REASON_SAME_ACTION
    assert len(executor.dispatched) == 3
    assert len(model.calls) == 4


def _record_same_action(store, root: str, count: int, *, contents=None) -> None:
    """`count` identical `list_dir` groups; `contents` varies each result.

    The default result is the one `CountingExecutor` produces, because the
    same-action streak now continues across a restart only when the persisted
    answer is the answer the live path sees again.
    """
    for index in range(count):
        ledger = RuntimeActionLedger(store, root, f"turn-{index}")
        if index == 0:
            ledger.append_user({"role": "user", "content": "look around"})
        call = NativeToolCall(
            id=f"call-{index}",
            wire_id=f"wire-{index}",
            name="list_dir",
            ordinal=0,
            raw_arguments='{"path":"."}',
            arguments={"path": "."},
        )
        reply = ModelReply(
            content="",
            tool_calls=(call,),
            assistant_message={
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                    }
                ],
            },
        )
        ledger.emit(ReplyReceived(reply, index))
        ledger.emit(ActionRouted(NativeToolBatch((call,)), index))
        ledger.emit(
            OutcomeProduced(
                ExecutionOutcome(
                    (
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "wire_id": call.wire_id,
                            "name": call.name,
                            "content": (
                                "list_dir ok" if contents is None else contents[index]
                            ),
                            "is_error": False,
                        },
                    )
                ),
                index,
            )
        )


def test_restore_from_the_ledger_applies_the_result_delta_rule(tmp_path):
    """The persisted result rows carry content, so a restart reads the same
    poll the live path saw: three moving answers are one streak of length
    one, and only trailing identical answers count."""
    store = Store(tmp_path / "poll.db")
    root = store.new_frame(project_id="default", status="ready")
    _record_same_action(
        store, root, 4, contents=["10%", "55%", "completed", "completed"]
    )

    restored = restore_progress_circuit(store, root)
    assert not restored.tripped
    assert restored.same_action_streak == 2
    store.close()


def test_an_empty_epoch_is_restored_without_reading_a_single_group(tmp_path):
    """Right after `append_user` the epoch is empty by construction. The
    restore used to prove that by decoding every group on the branch."""
    store = Store(tmp_path / "empty-epoch.db")
    root = store.new_frame(project_id="default", status="ready")
    _record_same_action(store, root, 3)
    RuntimeActionLedger(store, root, "turn-next").append_user(
        {"role": "user", "content": "try something else"}
    )
    reads = []
    original = store.list_action_groups

    def counting(*args, **kwargs):
        reads.append(kwargs)
        return original(*args, **kwargs)

    store.list_action_groups = counting  # type: ignore[method-assign]
    restored = restore_progress_circuit(store, root)
    assert restored == ProgressCircuit()
    assert reads == []
    store.close()


def test_restart_rebuilds_from_ledger_not_live_runstate(tmp_path):
    store = Store(tmp_path / "circuit.db")
    root = store.new_frame(project_id="default", status="ready")
    _record_same_action(store, root, 3)
    stale_state = RunState([{"role": "user", "content": "look around"}], max_turns=10)
    stale_circuit = ProgressCircuit()
    stale_circuit.same_action_streak = 0
    attach_progress_circuit(stale_state, stale_circuit)
    del stale_state
    del stale_circuit

    restored = restore_progress_circuit(store, root)
    assert restored.tripped
    assert restored.trip_reason == PROGRESS_REASON_SAME_ACTION
    assert restored.same_action_streak == 3

    state = RunState([{"role": "user", "content": "look around"}], max_turns=10)
    attach_progress_circuit(state, restored)
    engine, model, executor = _engine(_native_replies(3), max_turns=10)
    result = engine.run(state)

    assert result.stop_reason == NO_PROGRESS_STOP_REASON
    assert result.completion is None
    assert model.calls == []
    assert executor.dispatched == []
    terminal = store.list_action_groups(root)
    assert any(group.get("kind") == "native_tools" for group in terminal)
    store.close()


def test_restart_mid_streak_does_not_zero_then_trips_on_next_same_action(tmp_path):
    store = Store(tmp_path / "mid-streak.db")
    root = store.new_frame(project_id="default", status="ready")
    _record_same_action(store, root, 2)

    first = restore_progress_circuit(store, root)
    assert first.same_action_streak == 2
    assert not first.tripped
    del first

    restored = restore_progress_circuit(store, root)
    assert restored.same_action_streak == 2
    state = RunState([{"role": "user", "content": "look around"}], max_turns=10)
    attach_progress_circuit(state, restored)
    engine, model, executor = _engine(_native_replies(3), max_turns=10)
    result = engine.run(state)

    assert result.stop_reason == NO_PROGRESS_STOP_REASON
    assert result.progress_reason == PROGRESS_REASON_SAME_ACTION
    assert len(executor.dispatched) == 1
    assert len(model.calls) == 1
    store.close()


def test_compaction_does_not_zero_because_ledger_is_the_authority(tmp_path):
    store = Store(tmp_path / "compact.db")
    root = store.new_frame(project_id="default", status="ready")
    _record_same_action(store, root, 3)

    compacted_messages = [
        {"role": "system", "content": "compacted handoff"},
        {"role": "user", "content": "[Observation] earlier work was summarised"},
    ]
    from_messages = reconstruct_progress_circuit([])
    from_ledger = restore_progress_circuit(store, root)

    assert not from_messages.tripped
    assert from_ledger.tripped
    assert from_ledger.same_action_streak == 3
    assert compacted_messages[1]["role"] == "user"
    store.close()


def test_new_external_user_message_resets_epoch(tmp_path):
    store = Store(tmp_path / "reset.db")
    root = store.new_frame(project_id="default", status="ready")
    _record_same_action(store, root, 3)
    RuntimeActionLedger(store, root, "turn-new").append_user(
        {"role": "user", "content": "try a different approach"}
    )

    restored = restore_progress_circuit(store, root)
    assert not restored.tripped
    assert restored.same_action_streak == 0
    store.close()


def test_cancel_wins_over_an_already_tripped_circuit():
    class Cancelled:
        def cancelled(self):
            return True

    state = RunState([{"role": "user", "content": "stop"}], max_turns=10)
    circuit = ProgressCircuit()
    circuit.trip_reason = PROGRESS_REASON_SAME_ACTION
    attach_progress_circuit(state, circuit)
    engine, model, executor = _engine(
        _native_replies(3),
        max_turns=10,
        cancellation=Cancelled(),
    )

    result = engine.run(state)

    assert result.stop_reason == "cancelled"
    assert result.progress_reason is None
    assert model.calls == []
    assert executor.calls == []


def test_max_turns_still_wins_when_the_circuit_is_below_threshold():
    engine, model, executor = _engine(_native_replies(5), max_turns=2)

    result = engine.run([{"role": "user", "content": "two tries"}])

    assert result.stop_reason == "max_turns"
    assert result.progress_reason is None
    assert len(executor.dispatched) == 2
    assert len(model.calls) == 2


def test_successful_submit_is_not_rewritten_as_no_progress():
    replies = _native_replies(1)
    executor = CountingExecutor()
    engine, model, _ = _engine(replies, executor, max_turns=10)

    original_execute = executor.execute

    def complete_on_first(action, reply, state):
        outcome = original_execute(action, reply, state)
        return ExecutionOutcome(
            outcome.history_messages,
            completion={"ok": True},
        )

    executor.execute = complete_on_first
    result = engine.run([{"role": "user", "content": "once then done"}])

    assert result.stop_reason == "submitted"
    assert result.completion == {"ok": True}
    assert result.progress_reason is None
    assert len(model.calls) == 1


def test_ledger_terminal_for_no_progress_is_failed_not_completed(tmp_path):
    store = Store(tmp_path / "terminal.db")
    root = store.new_frame(project_id="default", status="ready")
    ledger = RuntimeActionLedger(store, root, "turn-term")
    ledger.append_user({"role": "user", "content": "loop"})
    reply = ModelReply(content="still going")
    ledger.emit(
        RunFinished(
            EngineResult(
                (),
                None,
                NO_PROGRESS_STOP_REASON,
                3,
                reply,
                progress_reason=PROGRESS_REASON_SAME_ACTION,
            )
        )
    )
    groups = store.list_action_groups(root)
    terminal = groups[-1]
    assert terminal["kind"] == "terminal"
    event = terminal["events"][0]
    assert event["type"] == "failed"
    assert event["result"]["reason"] == NO_PROGRESS_STOP_REASON
    assert event["result"]["progress_reason"] == PROGRESS_REASON_SAME_ACTION
    assert "completion" not in event["result"]
    store.close()


def test_metadata_cache_is_not_durable_authority(tmp_path):
    store = Store(tmp_path / "cache.db")
    root = store.new_frame(project_id="default", status="ready")
    _record_same_action(store, root, 2)
    state = RunState([{"role": "user", "content": "look"}], max_turns=4)
    cached = ProgressCircuit()
    cached.same_action_streak = 0
    attach_progress_circuit(state, cached)
    del state

    restored = restore_progress_circuit(store, root)
    assert restored.same_action_streak == 2
    assert restored.same_action_streak != cached.same_action_streak
    store.close()
