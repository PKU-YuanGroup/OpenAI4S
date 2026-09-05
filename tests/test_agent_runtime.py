"""Contracts for the local adapters around the pure agent engine."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

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


def test_chat_model_cancel_releases_owner_and_quarantines_late_provider_output():
    entered = threading.Event()
    release = threading.Event()
    provider_done = threading.Event()
    shared_cancel = threading.Event()
    late_cancel_checks = []
    deltas = []
    abandoned = []
    accounted = threading.Event()
    result = {}

    def fake_chat(messages, cfg, **kwargs):
        del messages, cfg
        entered.set()
        assert release.wait(5), "test did not release the fake provider"
        late_cancel_checks.append(kwargs["should_cancel"]())
        kwargs["on_delta"]("must not escape after Stop")
        provider_done.set()
        return {
            "content": "late reply",
            "usage": {"prompt_tokens": 17, "completion_tokens": 3},
        }

    def account(reply):
        abandoned.append(reply["usage"])
        accounted.set()

    model = ChatModel(
        object(),
        fake_chat,
        stream=True,
        cancellation=SimpleNamespace(cancelled=shared_cancel.is_set),
        abandoned_reply=account,
    )
    owner = threading.Thread(
        target=lambda: result.setdefault("reply", model.complete([], deltas.append))
    )
    owner.start()
    assert entered.wait(2), "fake provider call did not start"

    shared_cancel.set()
    owner.join(1)
    assert not owner.is_alive(), "Stop waited for the blocked provider request"
    assert result["reply"]["finish_reason"] == "cancelled"

    # Admission of the next queued turn clears this shared Event. The old
    # request's private latch must remain cancelled, or it can ABA-revive and
    # stream its late response into the new turn.
    shared_cancel.clear()
    release.set()
    assert provider_done.wait(2), "detached fake provider did not finish"
    assert accounted.wait(2), "late provider usage was not accounted"
    assert late_cancel_checks == [True]
    assert deltas == []
    assert abandoned == [{"prompt_tokens": 17, "completion_tokens": 3}]


def test_a_call_cancelled_before_it_starts_never_reaches_the_provider():
    """Stop can land between ``Thread.start()`` and ``chat_fn``'s first line.

    The detached design exists because a urllib request already on the wire
    cannot be recalled. In this window nothing has been sent yet, so there is
    nothing to salvage: the request must not go out at all. It would be billed
    for a turn nobody is reading, and on the Web path ``chat_fn`` is
    ``_invoke_model_with_auto_budget``, which snapshots the LIVE Auto Mode run
    id at its own entry -- by then the run of the turn admitted *after* Stop.
    """

    calls: list[dict] = []
    probes = {"n": 0}

    def cancelled_after_spawn() -> bool:
        # False only for the pre-spawn check; true from the moment the thread
        # could be running, whichever side of the handoff asks first.
        probes["n"] += 1
        return probes["n"] > 1

    def fake_chat(messages, cfg, **kwargs):
        del messages, cfg
        calls.append(kwargs)
        return {"content": "must not be requested", "usage": {"prompt_tokens": 9}}

    accounted: list[object] = []
    model = ChatModel(
        object(),
        fake_chat,
        cancellation=SimpleNamespace(cancelled=cancelled_after_spawn),
        abandoned_reply=accounted.append,
    )

    reply = model.complete([], lambda _text: None)

    assert reply["finish_reason"] == "cancelled"
    assert calls == [], "a cancelled call still reached the provider"
    # Nothing was requested, so there is no usage to meter and no reply to
    # quarantine -- the accounting sink must stay untouched rather than
    # inventing a zero.
    assert accounted == []


def test_chat_model_projects_stream_deltas_on_the_owning_thread():
    shared_cancel = threading.Event()
    provider_threads = []
    callback_threads = []
    deltas = []

    def fake_chat(messages, cfg, **kwargs):
        del messages, cfg
        provider_threads.append(threading.get_ident())
        kwargs["on_delta"]("one")
        kwargs["on_delta"]("two")
        return {"content": "one two"}

    owner_thread = threading.get_ident()
    model = ChatModel(
        object(),
        fake_chat,
        stream=True,
        cancellation=SimpleNamespace(cancelled=shared_cancel.is_set),
    )

    def collect_delta(text):
        callback_threads.append(threading.get_ident())
        deltas.append(text)

    reply = model.complete([], collect_delta)

    assert reply["content"] == "one two"
    assert provider_threads and provider_threads != [owner_thread]
    assert callback_threads == [owner_thread, owner_thread]
    assert deltas == ["one", "two"]


def test_chat_model_bounds_detached_provider_calls(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "_PROVIDER_CALL_BUDGET",
        runtime._DetachedCallBudget(1, per_scope_limit=4),
    )
    entered = threading.Event()
    release = threading.Event()
    shared_cancel = threading.Event()

    def blocked_chat(messages, cfg, **kwargs):
        del messages, cfg, kwargs
        entered.set()
        assert release.wait(5)
        return {"content": "late"}

    model = ChatModel(
        object(),
        blocked_chat,
        cancellation=SimpleNamespace(cancelled=shared_cancel.is_set),
    )
    owner = threading.Thread(target=lambda: model.complete([], lambda _text: None))
    owner.start()
    assert entered.wait(2)
    shared_cancel.set()
    owner.join(1)
    assert not owner.is_alive()
    assert runtime._PROVIDER_CALL_BUDGET.outstanding() == 1

    second = ChatModel(
        object(),
        blocked_chat,
        cancellation=SimpleNamespace(cancelled=lambda: False),
    )
    with pytest.raises(RuntimeError, match="cancelled model requests"):
        second.complete([], lambda _text: None)

    release.set()
    deadline = time.monotonic() + 2
    while runtime._PROVIDER_CALL_BUDGET.outstanding() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runtime._PROVIDER_CALL_BUDGET.outstanding() == 0


def test_chat_model_budget_never_charges_a_live_provider_call():
    """Only *detached* calls are bounded.

    Charging every cancellable request would bound healthy concurrency: each
    delegated child carries a cancellation, the fan-out cap of 48 is per node,
    and the session cap is 1000 -- so a nested fan-out routinely holds more
    live calls than the budget and would start refusing requests nobody
    cancelled.
    """

    budget = runtime._DetachedCallBudget(2, per_scope_limit=99)
    live = [budget.track() for _ in range(5)]
    for _ in range(10):
        budget.admit()  # no live call is charged, so admission stays open
    assert budget.outstanding() == 0

    live[0].detach()
    live[1].detach()
    assert budget.outstanding() == 2
    with pytest.raises(RuntimeError, match="cancelled model requests"):
        budget.admit()

    live[0].settle()
    budget.admit()
    for call in live:
        call.settle()
    assert budget.outstanding() == 0


def test_one_session_cannot_stack_cancelled_calls_against_a_stale_ledger(
    monkeypatch,
):
    """Stop releases the turn while the request keeps billing.

    ``enforce_llm_quota`` is check-then-call: it reads stored ``llm_*``
    counters and reserves nothing for a call in flight, so every Stop-and-
    resend passes a ledger that has not been charged for the request still
    running. Only this budget decides how far one session can outrun its own
    accounting, so the per-scope limit has to bite well before the process one.
    """

    monkeypatch.setattr(
        runtime,
        "_PROVIDER_CALL_BUDGET",
        runtime._DetachedCallBudget(128, per_scope_limit=2),
    )
    release = threading.Event()
    started = threading.Semaphore(0)

    def blocked_chat(messages, cfg, **kwargs):
        del messages, cfg, kwargs
        started.release()
        assert release.wait(5)
        return {"content": "late"}

    def stop_one(scope):
        shared = threading.Event()
        model = ChatModel(
            object(),
            blocked_chat,
            cancellation=SimpleNamespace(cancelled=shared.is_set),
            call_scope=scope,
        )
        owner = threading.Thread(target=lambda: model.complete([], lambda _t: None))
        owner.start()
        assert started.acquire(timeout=2)
        shared.set()
        owner.join(2)
        assert not owner.is_alive()

    try:
        stop_one("frame-a")
        stop_one("frame-a")
        # A third Stop on the same session would be a third concurrent billed
        # request against counters none of them have reached yet.
        with pytest.raises(RuntimeError, match="this session already has"):
            ChatModel(
                object(),
                blocked_chat,
                cancellation=SimpleNamespace(cancelled=lambda: False),
                call_scope="frame-a",
            ).complete([], lambda _t: None)

        # Another session is unaffected: the bound is per session, and the
        # process ceiling is far away.
        stop_one("frame-b")
        assert runtime._PROVIDER_CALL_BUDGET.outstanding("frame-a") == 2
        assert runtime._PROVIDER_CALL_BUDGET.outstanding("frame-b") == 1
    finally:
        release.set()

    deadline = time.monotonic() + 5
    while runtime._PROVIDER_CALL_BUDGET.outstanding() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runtime._PROVIDER_CALL_BUDGET.outstanding("frame-a") == 0


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


def test_writing_native_call_uses_delegated_capture_hooks(monkeypatch):
    events = []

    class Hooks:
        def before_native(self, call):
            events.append(("before", call.id))
            return "snapshot-token"

        def after_native(self, call, token, result):
            events.append(("after", call.id, token, result))

    monkeypatch.setattr(
        runtime,
        "execute_tool_call",
        lambda dispatcher, call: ("wrote file", True),
    )
    executor = LocalActionExecutor(
        FakeKernel(),
        FakeDispatcher(),
        lambda code, messages: None,
        lambda code: {"stdout": "R", "error": None},
        cell_hooks=Hooks(),
    )
    call = _native_call(
        0,
        name="write_file",
        arguments={"path": "child.txt", "content": "child bytes"},
    )

    outcome = executor.execute(
        NativeToolBatch((call,)), ModelReply(), RunState([{"role": "user"}])
    )

    assert outcome.history_messages[0]["is_error"] is False
    assert events == [
        ("before", "call_0"),
        ("after", "call_0", "snapshot-token", ("wrote file", True)),
    ]


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

    outcome = _executor().execute(NativeToolBatch(calls), ModelReply(), RunState([]))

    assert len(dispatched) == runtime.MAX_TOOL_CALLS_PER_TURN == 16
    assert len(outcome.history_messages) == len(calls) == 18
    assert [message["tool_call_id"] for message in outcome.history_messages] == [
        f"call_{index}" for index in range(18)
    ]
    assert all(message["role"] == "tool" for message in outcome.history_messages)
    assert all(message["is_error"] for message in outcome.history_messages[16:])
    assert all(
        "was not run" in message["content"] for message in outcome.history_messages[16:]
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
            "```python\nprint(6 * 7)\n```\n" "```python\nraise AssertionError\n```"
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


def test_cli_revalidates_mid_cell_completion_after_capture_hooks():
    class RevalidatingDispatcher(FakeDispatcher):
        def __init__(self):
            super().__init__()
            self.revalidations = 0

        def revalidate_pending_completion(self):
            self.revalidations += 1
            if self.last_output is None:
                return None
            self.last_output = None
            return "verified source bytes changed after submission"

    dispatcher = RevalidatingDispatcher()
    submitted = {"output": {"answer": 42}, "completion_bullets": ["Computed it"]}
    kernel = FakeKernel(
        after_execute=lambda: setattr(dispatcher, "last_output", submitted)
    )

    outcome = _executor(kernel=kernel, dispatcher=dispatcher).execute(
        CodeCell("python", "host.submit_output(...); mutate_source()"),
        ModelReply(content="```python\nhost.submit_output(...)\n```"),
        RunState([]),
    )

    assert dispatcher.revalidations == 1
    assert outcome.completion is None
    assert "rejected after cell capture" in outcome.observation
    assert "source bytes changed" in outcome.observation


def test_code_observation_notes_an_incomplete_tail_after_the_executed_cell():
    kernel = FakeKernel({"stdout": "first\n", "stderr": "", "error": None, "usage": {}})
    executor = _executor(kernel=kernel, dispatcher=FakeDispatcher())
    reply = ModelReply(
        content=("```python\nprint('first')\n```\n" "```r\nresult <- unfinished(\n")
    )

    outcome = executor.execute(
        CodeCell("python", "print('first')\n"), reply, RunState([])
    )

    assert kernel.calls == [("print('first')\n", "agent")]
    assert runtime.MULTI_CELL_NOTE in outcome.observation


def test_none_action_keeps_legacy_tool_fallback_as_user_history(monkeypatch):
    calls = []

    def fake_run(dispatcher, parsed_calls, errors):
        calls.append((dispatcher, parsed_calls, errors))
        return "[Tool Results]\nlegacy result"

    monkeypatch.setattr(runtime, "run_tool_calls", fake_run)
    dispatcher = FakeDispatcher()
    executor = _executor(dispatcher=dispatcher)
    fenced = "```tool\n" '{"name":"list_dir","arguments":{"path":"."}}\n' "```"

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


def test_none_action_can_fall_back_to_code_when_native_tools_are_unavailable():
    executor = LocalActionExecutor(
        FakeKernel(),
        FakeDispatcher(),
        lambda code, messages: None,
        lambda code: {"stdout": "R", "error": None},
        prose_nudge=runtime.NO_NATIVE_COMPLETION_NUDGE,
    )

    outcome = executor.execute(None, ModelReply(content="plain prose"), RunState([]))

    assert outcome.observation == runtime.NO_NATIVE_COMPLETION_NUDGE
    assert "host.submit_output" in outcome.observation
    assert "finalize_response" not in outcome.observation


def test_incomplete_cell_is_not_executed_and_requests_a_full_replacement():
    kernel = FakeKernel()
    executor = _executor(kernel=kernel, dispatcher=FakeDispatcher())

    outcome = executor.execute(
        None,
        ModelReply(content="```python\nvalue = compute(\n"),
        RunState([]),
    )

    assert outcome.observation == runtime.INCOMPLETE_CELL_NUDGE
    assert "NOTHING" in outcome.observation
    assert kernel.calls == []


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

    def fake_compact(
        messages,
        cfg,
        *,
        keep_recent,
        archive_dir,
        archive_metadata,
        large_output_chars,
        **_kwargs,
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
    # A one-token window with a zero trigger ratio forces compaction on any
    # non-empty history; the policy has a single trigger path.
    cfg = SimpleNamespace(
        compaction_dir="archive", context_window_tokens=1, compaction_trigger_ratio=0.0
    )

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

    def no_yield(messages, cfg, **kwargs):
        attempts.append(kwargs)
        return messages

    monkeypatch.setattr(runtime, "compact", no_yield)
    policy = CompactionPolicy(
        SimpleNamespace(
            compaction_dir="archive",
            context_window_tokens=1,
            compaction_trigger_ratio=0.0,
        )
    )
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
        "[system] The cell stopped at the first exception. Statements after "
        "that line did not run, and their variables/files must not be assumed "
        "to exist. Repair with one complete cell beginning before the failed "
        "dependency; never send only a continuation fragment.\n"
        "[usage wall=1.0s cpu=0.5s rss=64kb]"
    )


def test_format_observation_reminds_models_that_host_is_injected():
    observation = format_observation(
        {"error": "ModuleNotFoundError: No module named 'host'"}
    )

    assert "never `import host`" in observation
