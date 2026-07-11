"""Runtime Action Ledger writing, redaction, and restart reduction."""

from __future__ import annotations

from openai4s.agent.actions import CodeCell, NativeToolBatch, NativeToolCall
from openai4s.agent.events import (
    ActionRouted,
    OutcomeProduced,
    ReplyReceived,
    RunFinished,
)
from openai4s.agent.ledger import REDACTED, RuntimeActionLedger, restore_action_history
from openai4s.agent.models import EngineResult, ExecutionOutcome, ModelReply
from openai4s.store import Store


def _call(index: int, *, token: str = "live-secret") -> NativeToolCall:
    return NativeToolCall(
        id=f"call-{index}",
        wire_id=f"wire-{index}",
        name="web_search",
        ordinal=index,
        raw_arguments=f'{{"query":"NIF3","token":"{token}"}}',
        arguments={"query": "NIF3", "token": token},
        provider_meta={"authorization": f"Bearer {token}"},
    )


def _reply(calls=(), *, content: str = "I will inspect the evidence.") -> ModelReply:
    calls = tuple(calls)
    return ModelReply(
        content=content,
        tool_calls=calls,
        wire_state={
            "openai_message": {
                "tool_calls": [
                    {
                        "function": {
                            "name": call.name,
                            "arguments": call.raw_arguments,
                        }
                    }
                    for call in calls
                ]
            }
        },
    )


def test_runtime_writer_roundtrips_native_group_and_redacts_arguments(tmp_path):
    store = Store(tmp_path / "openai4s.db")
    ledger = RuntimeActionLedger(
        store,
        "root-1",
        "turn-1",
        provider="ark",
        model="science-model",
    )
    ledger.append_user(
        {
            "role": "user",
            "content": "Find NIF3 evidence api_key=user-secret Bearer user-bearer",
        }
    )
    call = _call(0)
    reply = _reply(
        (call,),
        content=(
            "I will inspect the evidence password=assistant-secret "
            "Bearer assistant-bearer"
        ),
    )
    ledger.emit(ReplyReceived(reply, 0))
    ledger.emit(ActionRouted(NativeToolBatch((call,)), 0))
    ledger.emit(
        OutcomeProduced(
            ExecutionOutcome(
                (
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "wire_id": call.wire_id,
                        "name": call.name,
                        "content": {"items": [], "api_key": "result-secret"},
                        "is_error": False,
                    },
                )
            ),
            0,
        )
    )
    ledger.emit(
        RunFinished(
            EngineResult((), {"ok": True}, "submitted", 1, reply)
        )
    )

    groups = store.list_action_groups("root-1")
    assert [group["kind"] for group in groups] == [
        "user",
        "native_tools",
        "terminal",
    ]
    tools = groups[1]
    assert tools["provider"] == "ark"
    assert tools["model"] == "science-model"
    serialized = repr(tools)
    assert "live-secret" not in serialized
    assert "result-secret" not in serialized
    assert "assistant-secret" not in serialized
    assert REDACTED in serialized
    assert "user-secret" not in repr(groups[0])
    assert "user-bearer" not in repr(groups[0])
    assert "assistant-bearer" not in serialized
    assert tools["assistant_content"].count(REDACTED) == 2
    assert tools["events"][0]["canonical_arguments"]["arguments"]["token"] == REDACTED
    assert REDACTED in tools["events"][0]["raw_arguments"]

    history = restore_action_history(store, "root-1")
    assert [message["role"] for message in history] == [
        "user",
        "assistant",
        "tool",
    ]
    assert history[-1]["tool_call_id"] == "call-0"
    assert history[-1]["is_error"] is False
    store.close()


def test_reducer_closes_interrupted_native_group_atomically(tmp_path):
    store = Store(tmp_path / "openai4s.db")
    ledger = RuntimeActionLedger(store, "root-1", "turn-1")
    ledger.append_user("continue")
    calls = (_call(0), _call(1))
    reply = _reply(calls)
    ledger.emit(ReplyReceived(reply, 0))
    ledger.emit(ActionRouted(NativeToolBatch(calls), 0))
    # Simulate daemon cancellation after the declaration but before either
    # result was persisted.
    ledger.append_terminal("cancelled")
    store.close()

    reopened = Store(tmp_path / "openai4s.db")
    history = restore_action_history(reopened, "root-1")
    assert [message["role"] for message in history] == [
        "user",
        "assistant",
        "tool",
        "tool",
    ]
    assert [message["tool_call_id"] for message in history[-2:]] == [
        "call-0",
        "call-1",
    ]
    assert all(message["is_error"] for message in history[-2:])
    assert all("cancelled" in message["content"] for message in history[-2:])
    reopened.close()


def test_code_assistant_and_observation_restore_after_reopen(tmp_path):
    store = Store(tmp_path / "openai4s.db")
    ledger = RuntimeActionLedger(
        store,
        "root-code",
        "turn-code",
        provider="claude",
        model="analysis-model",
    )
    ledger.append_user({"role": "user", "content": "calculate"})
    reply = ModelReply(
        content="```python\nprint(42)\n```",
        wire_state={"response_id": "response-1"},
    )
    ledger.emit(ReplyReceived(reply, 0))
    ledger.emit(ActionRouted(CodeCell("python", "print(42)\n"), 0))
    ledger.emit(
        OutcomeProduced(
            ExecutionOutcome(
                ({"role": "user", "content": "[Observation]\nstdout:\n42"},),
                observation="[Observation]\nstdout:\n42",
            ),
            0,
        )
    )
    ledger.emit(RunFinished(EngineResult((), None, "max_turns", 1, reply)))
    store.close()

    reopened = Store(tmp_path / "openai4s.db")
    history = restore_action_history(reopened, "root-code")
    assert history == [
        {"role": "user", "content": "calculate"},
        {
            "role": "assistant",
            "content": "```python\nprint(42)\n```",
            "wire_state": {"response_id": "response-1"},
        },
        {"role": "user", "content": "[Observation]\nstdout:\n42"},
    ]
    terminal = reopened.list_action_groups("root-code")[-1]
    assert terminal["events"][0]["result"]["reason"] == "max_turns"
    reopened.close()


def test_plan_no_action_reduces_to_plan_message_not_interruption(tmp_path):
    store = Store(tmp_path / "openai4s.db")
    ledger = RuntimeActionLedger(store, "root-plan", "turn-plan")
    ledger.append_user("make a plan")
    reply = ModelReply(content="Here is the plan.")
    ledger.emit(ReplyReceived(reply, 0))
    ledger.emit(ActionRouted(None, 0))
    ledger.emit(OutcomeProduced(ExecutionOutcome(stop_reason="plan"), 0))
    ledger.emit(RunFinished(EngineResult((), None, "plan", 1, reply)))

    history = restore_action_history(store, "root-plan")
    assert [message["role"] for message in history] == [
        "user",
        "assistant",
        "user",
    ]
    assert "Plan mode ended" in history[-1]["content"]
    assert "interrupted" not in history[-1]["content"]
    store.close()
