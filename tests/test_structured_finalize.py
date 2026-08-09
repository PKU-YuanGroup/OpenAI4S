"""Contracts for the engine-owned structured terminal action."""

from __future__ import annotations

import json

import pytest

from openai4s.agent.actions import (
    CodeCell,
    FinalizeAction,
    NativeToolBatch,
    NativeToolCall,
    route_action,
)
from openai4s.agent.engine import AgentEngine
from openai4s.agent.events import (
    ActionRouted,
    OutcomeProduced,
    ReplyReceived,
    RunFinished,
)
from openai4s.agent.finalize import (
    execute_finalize_action,
    execution_evidence,
    finalize_response_schema,
    finalize_response_tool_spec,
    reconcile_completion_claims,
    validate_finalize_arguments,
    with_finalize_response,
)
from openai4s.agent.ledger import RuntimeActionLedger, restore_action_history
from openai4s.agent.models import EngineResult, ModelReply, RunState
from openai4s.agent.runtime import LocalActionExecutor
from openai4s.server.action_timeline import ActionTimelineService
from openai4s.server.agent_run import WebActionExecutor, WebEventSink
from openai4s.store import Store
from openai4s.tools import REGISTRY, ToolSpec


def _arguments(**overrides):
    value = {
        "summary": "The inspected evidence supports the requested conclusion.",
        "findings": ["The control and measured value agree."],
        "metrics": {"accuracy": 0.93},
        "artifacts": ["artifact-1", "prediction.csv"],
        "limitations": ["Only one dataset was available."],
        "next_steps": ["Validate on an independent dataset."],
        "completion_bullets": ["Completed the evidence review"],
    }
    value.update(overrides)
    return value


def _prose_arguments(**overrides):
    """A payload with no execution-shaped claims — no artifacts, no metrics,
    reflective bullets — which is what an honest conversational turn (zero
    cells, zero tools) is allowed to finalize with under ledger reconciliation.
    """
    value = {
        "summary": "The inspected evidence supports the requested conclusion.",
        "findings": ["The control and measured value agree."],
        "limitations": ["Only one dataset was available."],
        "next_steps": ["Validate on an independent dataset."],
        "completion_bullets": ["Explained the evidence review"],
    }
    value.update(overrides)
    return value


def _call(
    arguments=None,
    *,
    call_id="final-1",
    name="finalize_response",
    parse_error=None,
):
    raw = json.dumps(arguments, ensure_ascii=False) if arguments is not None else "{}"
    return NativeToolCall(
        id=call_id,
        wire_id=f"wire-{call_id}",
        name=name,
        ordinal=0,
        raw_arguments=raw,
        arguments=arguments,
        parse_error=parse_error,
        provider_meta={"provider": "test"},
    )


class _NeverKernel:
    generation = 0

    def execute(self, *args, **kwargs):
        raise AssertionError(
            f"structured finalization started a kernel: {args!r} {kwargs!r}"
        )


class _NeverDispatcher:
    last_output = None

    def __call__(self, *args, **kwargs):
        raise AssertionError(
            f"structured finalization dispatched a tool: {args!r} {kwargs!r}"
        )


def _local_executor():
    return LocalActionExecutor(
        _NeverKernel(),
        _NeverDispatcher(),
        lambda code, messages: None,
        lambda code: (_ for _ in ()).throw(
            AssertionError(f"structured finalization started R: {code}")
        ),
    )


def _web_executor(*, cancelled=lambda: False, plan_mode=False):
    sent = []
    events = WebEventSink(sent.append, "frame-1", [], lambda usage: None)

    def unexpected(*args, **kwargs):
        raise AssertionError(
            f"structured finalization ran Web work: {args!r} {kwargs!r}"
        )

    return WebActionExecutor(
        dispatcher=lambda: unexpected,
        apply_pending=unexpected,
        execute_cell=unexpected,
        events=events,
        prose_nudge="nudge",
        explore_nudge="explore",
        cancelled=cancelled,
        plan_mode=plan_mode,
    )


def test_finalize_spec_is_closed_host_strict_and_outside_control_registry():
    spec = finalize_response_tool_spec()

    assert isinstance(spec, ToolSpec)
    assert spec.name == "finalize_response"
    assert spec.strict is False
    assert spec.input_schema["additionalProperties"] is False
    assert set(spec.input_schema["required"]) == {"summary", "completion_bullets"}
    assert set(spec.input_schema["properties"]) == {
        "summary",
        "findings",
        "metrics",
        "artifacts",
        "limitations",
        "next_steps",
        "completion_bullets",
    }
    assert "sole tool call" in spec.description
    assert "finalize_response" not in {tool.name for tool in REGISTRY}

    spec.input_schema["properties"]["summary"]["maxLength"] = 1
    assert finalize_response_schema()["properties"]["summary"]["maxLength"] == 4_000

    catalogue = with_finalize_response((ToolSpec("lookup", "", {}),))
    assert [item.name for item in catalogue] == ["lookup", "finalize_response"]
    with pytest.raises(ValueError, match="engine-owned"):
        with_finalize_response(
            ({"type": "function", "function": {"name": "finalize_response"}},)
        )


def test_host_validation_rejects_missing_unknown_and_semantically_bad_fields():
    assert validate_finalize_arguments(_arguments()) is None
    assert "required property" in validate_finalize_arguments(
        {"completion_bullets": ["Completed the task"]}
    )
    assert "unknown property" in validate_finalize_arguments(
        _arguments(unverified_claim=True)
    )
    cardinality = validate_finalize_arguments(_arguments(completion_bullets=[]))
    assert "completion_bullets" in cardinality and ">= 1" in cardinality
    assert "past-tense" in validate_finalize_arguments(
        _arguments(completion_bullets=["Finish the task"])
    )


def test_router_reserves_finalization_only_for_one_standalone_native_call():
    final = _call(_arguments())
    control = _call({"path": "."}, call_id="list-1", name="list_dir")

    assert route_action("ordinary prose") is None
    assert route_action("", (final,)) == FinalizeAction(final)

    mixed = route_action("", (control, final))
    assert isinstance(mixed, NativeToolBatch)
    assert mixed.calls == (control, final)

    duplicate = route_action("", (final, _call(_arguments(), call_id="final-2")))
    assert isinstance(duplicate, NativeToolBatch)
    assert route_action("```python\nprint(1)\n```", ()) == CodeCell(
        "python", "print(1)\n"
    )


def test_cli_executor_closes_provider_call_before_returning_completion_record():
    call = _call(_prose_arguments())
    outcome = _local_executor().execute(
        FinalizeAction(call), ModelReply(tool_calls=(call,)), RunState([])
    )

    assert outcome.history_messages == (
        {
            "role": "tool",
            "tool_call_id": "final-1",
            "wire_id": "wire-final-1",
            "name": "finalize_response",
            "content": '{"status":"accepted","action":"finalize_response"}',
            "is_error": False,
        },
    )
    assert outcome.completion == {
        "output": {
            key: value
            for key, value in _prose_arguments().items()
            if key != "completion_bullets"
        },
        "completion_bullets": ["Explained the evidence review"],
    }
    assert outcome.stop_reason is None


def test_invalid_finalize_is_a_canonical_error_result_and_does_not_complete():
    call = _call({"summary": "Incomplete", "completion_bullets": []})
    outcome = execute_finalize_action(FinalizeAction(call))

    assert len(outcome.history_messages) == 1
    result = outcome.history_messages[0]
    assert result["role"] == "tool"
    assert result["tool_call_id"] == call.id
    assert result["is_error"] is True
    assert "completion_bullets" in result["content"]
    assert outcome.completion is None

    malformed = _call(None, call_id="bad-json", parse_error="invalid JSON")
    malformed_outcome = execute_finalize_action(FinalizeAction(malformed))
    assert malformed_outcome.history_messages[0]["tool_call_id"] == "bad-json"
    assert "invalid JSON" in malformed_outcome.history_messages[0]["content"]
    assert malformed_outcome.completion is None


def test_mixed_batch_treats_finalize_as_nonterminal_and_never_completes():
    control = _call({"path": "."}, call_id="list-1", name="list_dir")
    final = _call(_arguments())

    class Dispatcher:
        last_output = None

        def __call__(self, method, args):
            assert method == "list_dir" and args == [{"path": "."}]
            return {"entries": []}

    executor = LocalActionExecutor(
        _NeverKernel(),
        Dispatcher(),
        lambda code, messages: None,
        lambda code: {"error": "R must not start"},
    )
    outcome = executor.execute(
        NativeToolBatch((control, final)),
        ModelReply(tool_calls=(control, final)),
        RunState([]),
    )

    assert outcome.completion is None
    assert outcome.stop_reason is None
    assert [message["tool_call_id"] for message in outcome.history_messages] == [
        "list-1",
        "final-1",
    ]
    assert outcome.history_messages[-1]["is_error"] is True
    assert "unknown tool" in outcome.history_messages[-1]["content"]


def test_engine_records_assistant_then_tool_result_before_submitted_terminal():
    call = _call(_prose_arguments())
    reply = ModelReply(content="", tool_calls=(call,))

    class Model:
        def complete(self, messages, on_delta):
            del messages, on_delta
            return reply

    result = AgentEngine(Model(), _local_executor(), max_turns=1).run(
        [{"role": "user", "content": "Summarize the completed review."}]
    )

    assert result.stop_reason == "submitted"
    assert result.turns == 1
    assert [message["role"] for message in result.messages] == [
        "user",
        "assistant",
        "tool",
    ]
    assert result.messages[-1]["tool_call_id"] == call.id
    assert result.completion["output"]["summary"].startswith("The inspected")


def test_web_executor_accepts_finalize_without_dispatcher_kernel_or_pending_work():
    call = _call(_prose_arguments())
    outcome = _web_executor().execute(
        FinalizeAction(call), ModelReply(tool_calls=(call,)), RunState([])
    )

    assert outcome.history_messages[0]["tool_call_id"] == call.id
    assert outcome.history_messages[0]["is_error"] is False
    assert outcome.completion is not None


def test_web_cancel_and_plan_close_finalize_call_without_completion():
    call = _call(_arguments())
    reply = ModelReply(tool_calls=(call,))

    cancelled = _web_executor(cancelled=lambda: True).execute(
        FinalizeAction(call), reply, RunState([])
    )
    assert cancelled.stop_reason == "cancelled"
    assert cancelled.history_messages[0]["is_error"] is True
    assert cancelled.completion is None

    planned = _web_executor(plan_mode=True).execute(
        FinalizeAction(call), reply, RunState([])
    )
    assert planned.stop_reason == "plan"
    assert planned.history_messages[0]["is_error"] is True
    assert planned.completion is None


def test_finalize_ledger_roundtrip_and_timeline_projection(tmp_path):
    store = Store(tmp_path / "openai4s.db")
    ledger = RuntimeActionLedger(store, "root-final", "turn-final")
    call = _call(_arguments())
    reply = ModelReply(tool_calls=(call,))
    action = FinalizeAction(call)
    outcome = execute_finalize_action(action)

    ledger.append_user("Finish the response")
    ledger.emit(ReplyReceived(reply, 0))
    ledger.emit(ActionRouted(action, 0))
    ledger.emit(OutcomeProduced(outcome, 0))
    ledger.emit(
        RunFinished(EngineResult((), outcome.completion, "submitted", 1, reply))
    )

    groups = store.list_action_groups("root-final")
    assert [group["kind"] for group in groups] == ["user", "finalize", "terminal"]
    assert groups[1]["events"][0]["resource_keys"] == ["agent:completion"]
    assert groups[1]["events"][1]["result"]["is_error"] is False

    history = restore_action_history(store, "root-final")
    assert [message["role"] for message in history] == ["user", "assistant", "tool"]
    assert history[-1]["tool_call_id"] == call.id

    timeline = ActionTimelineService(store).get("root-final")
    finalized = next(
        group for group in timeline["groups"] if group["kind"] == "finalize"
    )
    assert finalized["status"] == "completed"
    assert finalized["title"].startswith("The inspected evidence")
    store.close()


def test_reconciliation_rejects_execution_claims_without_ledger_evidence():
    zero = {"cells": 0, "tool_calls": 0}

    assert reconcile_completion_claims(_prose_arguments(), zero) is None
    error = reconcile_completion_claims(_arguments(), zero)
    assert "artifacts" in error and "metrics" in error and "ledger" in error
    flagged = reconcile_completion_claims(
        _prose_arguments(completion_bullets=["Computed the standard deviation"]),
        zero,
    )
    assert "Computed the standard deviation" in flagged
    # CJK bullets carry no tense morphology; the verb heuristic never applies.
    assert (
        reconcile_completion_claims(
            _prose_arguments(completion_bullets=["完成了证据审阅"]), zero
        )
        is None
    )
    # Any real execution satisfies the reconciliation for every claim shape.
    assert reconcile_completion_claims(_arguments(), {"cells": 1}) is None
    assert reconcile_completion_claims(_arguments(), {"tool_calls": 2}) is None
    # A corrupted evidence mapping degrades to zero counts, never a crash.
    assert "ledger" in reconcile_completion_claims(
        _arguments(), {"cells": "three", "tool_calls": -1}
    )


def test_zero_execution_finalize_with_claims_is_refused_but_repairable():
    call = _call(_arguments(completion_bullets=["Computed the standard deviation"]))

    for executor in (_local_executor(), _web_executor()):
        outcome = executor.execute(
            FinalizeAction(call), ModelReply(tool_calls=(call,)), RunState([])
        )
        result = outcome.history_messages[0]
        assert result["is_error"] is True
        assert "ledger" in result["content"]
        assert outcome.completion is None
        # A refused reconciliation is repairable, not terminal: the model can
        # run the claimed work (or restate the completion) on the next turn.
        assert outcome.stop_reason is None

    # Direct calls without an evidence ledger keep the legacy behaviour.
    legacy = execute_finalize_action(FinalizeAction(call))
    assert legacy.history_messages[0]["is_error"] is False
    assert legacy.completion is not None


def test_cli_finalize_accepts_execution_claims_after_a_real_cell_ran():
    class Kernel:
        generation = 0

        def execute(self, code, origin="agent"):
            assert origin == "agent"
            return {"stdout": "4\n", "stderr": "", "error": None}

    class Dispatcher:
        last_output = None

    executor = LocalActionExecutor(
        Kernel(),
        Dispatcher(),
        lambda code, messages: None,
        lambda code: {"error": "R must not start"},
    )
    state = RunState([])
    cell = CodeCell("python", "print(2 + 2)\n")
    executor.execute(cell, ModelReply(content="```python\nprint(2 + 2)\n```"), state)
    assert execution_evidence(state.metadata) == {"cells": 1, "tool_calls": 0}

    call = _call(_arguments(completion_bullets=["Computed the requested value"]))
    outcome = executor.execute(
        FinalizeAction(call), ModelReply(tool_calls=(call,)), state
    )
    assert outcome.history_messages[0]["is_error"] is False
    assert outcome.completion is not None


def test_safety_refused_cell_never_counts_as_execution_evidence():
    executor = LocalActionExecutor(
        _NeverKernel(),
        _NeverDispatcher(),
        lambda code, messages: "cell refused by the safety gate",
        lambda code: {"error": "R must not start"},
    )
    state = RunState([])
    cell = CodeCell("python", "import os\n")
    refused = executor.execute(
        cell, ModelReply(content="```python\nimport os\n```"), state
    )
    assert "refused" in str(refused.observation)
    assert execution_evidence(state.metadata) == {"cells": 0, "tool_calls": 0}

    call = _call(_arguments())
    outcome = executor.execute(
        FinalizeAction(call), ModelReply(tool_calls=(call,)), state
    )
    assert outcome.history_messages[0]["is_error"] is True
    assert outcome.completion is None


def test_web_finalize_accepts_execution_claims_after_a_real_cell_ran():
    sent = []
    events = WebEventSink(sent.append, "frame-1", [], lambda usage: None)

    class Dispatcher:
        last_output = None

    executor = WebActionExecutor(
        dispatcher=lambda: Dispatcher(),
        apply_pending=lambda: None,
        execute_cell=lambda action: {"stdout": "ok\n", "stderr": "", "error": None},
        events=events,
        prose_nudge="nudge",
        explore_nudge="explore",
    )
    state = RunState([])
    cell = CodeCell("python", "print(1)\n")
    executor.execute(cell, ModelReply(content="```python\nprint(1)\n```"), state)
    assert execution_evidence(state.metadata) == {"cells": 1, "tool_calls": 0}

    call = _call(_arguments(completion_bullets=["Computed the requested value"]))
    outcome = executor.execute(
        FinalizeAction(call), ModelReply(tool_calls=(call,)), state
    )
    assert outcome.history_messages[0]["is_error"] is False
    assert outcome.completion is not None


def test_web_refused_cell_result_never_counts_as_execution_evidence():
    """The Web sibling of the safety-gate contract.

    The gateway's ``execute_cell`` returns its full outcome dict whose
    ``executed`` bit is False for safety-refused / runtime-unavailable soft
    errors — result dicts byte-identical to real failures.  Counting them let
    a Web turn whose only cell was refused finalize with fabricated
    execution claims.
    """
    sent = []
    events = WebEventSink(sent.append, "frame-1", [], lambda usage: None)

    class Dispatcher:
        last_output = None

    executor = WebActionExecutor(
        dispatcher=lambda: Dispatcher(),
        apply_pending=lambda: None,
        execute_cell=lambda action: {
            "result": {"stdout": "", "stderr": "", "error": "refused"},
            "executed": False,
        },
        events=events,
        prose_nudge="nudge",
        explore_nudge="explore",
    )
    state = RunState([])
    cell = CodeCell("python", "import os\n")
    executor.execute(cell, ModelReply(content="```python\nimport os\n```"), state)
    assert execution_evidence(state.metadata) == {"cells": 0, "tool_calls": 0}

    call = _call(_arguments(completion_bullets=["Computed the requested value"]))
    outcome = executor.execute(
        FinalizeAction(call), ModelReply(tool_calls=(call,)), state
    )
    assert outcome.history_messages[0]["is_error"] is True
    assert outcome.completion is None


def test_refused_native_batch_never_counts_as_execution_evidence():
    """A batch whose every call was refused without dispatch is not evidence.

    Parse/validation/limit refusals are answered by ``execute_native_batch``
    itself — no tool runs.  Counting declarations let one malformed call back
    a fabricated finalize.
    """
    bad = NativeToolCall(
        id="t-1",
        wire_id="wire-t-1",
        name="read_file",
        ordinal=0,
        raw_arguments="{not json",
        arguments=None,
        parse_error="arguments are not valid JSON",
    )

    class NeverInvokedDispatcher:
        last_output = None

        def execute_host_call(self, *args, **kwargs):  # pragma: no cover
            raise AssertionError("a refused call must not reach the dispatcher")

    local = LocalActionExecutor(
        _NeverKernel(),
        NeverInvokedDispatcher(),
        lambda code, messages: None,
        lambda code: {"error": "R must not start"},
    )
    state = RunState([])
    local.execute(NativeToolBatch((bad,)), ModelReply(tool_calls=(bad,)), state)
    assert execution_evidence(state.metadata) == {"cells": 0, "tool_calls": 0}

    call = _call(_arguments(completion_bullets=["Queried the database"]))
    refused = local.execute(FinalizeAction(call), ModelReply(tool_calls=(call,)), state)
    assert refused.history_messages[0]["is_error"] is True
    assert refused.completion is None


def test_cli_r_unavailable_soft_error_never_counts_as_execution_evidence():
    """The R runner's spawn-failure dict carries no "stdout": nothing ran."""
    executor = LocalActionExecutor(
        _NeverKernel(),
        _NeverDispatcher(),
        lambda code, messages: None,
        lambda code: {"error": "R kernel unavailable: Rscript not found"},
    )
    state = RunState([])
    cell = CodeCell("r", "summary(1:3)\n")
    outcome = executor.execute(
        cell, ModelReply(content="```r\nsummary(1:3)\n```"), state
    )
    assert "R kernel unavailable" in str(outcome.observation)
    assert execution_evidence(state.metadata) == {"cells": 0, "tool_calls": 0}
