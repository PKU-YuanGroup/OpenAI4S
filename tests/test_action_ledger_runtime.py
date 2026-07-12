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
from openai4s.server.action_timeline import ActionTimelineService
from openai4s.store import Store
from openai4s.tools.catalog import SessionToolCatalog
from openai4s.tools.dynamic import DynamicToolRegistry


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


def _reply(
    calls=(),
    *,
    content: str = "I will inspect the evidence.",
    usage: dict | None = None,
) -> ModelReply:
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
        usage=usage or {},
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
        usage={
            "input_tokens": 120,
            "output_tokens": 30,
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
        },
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
    ledger.emit(RunFinished(EngineResult((), {"ok": True}, "submitted", 1, reply)))

    groups = store.list_action_groups("root-1")
    assert [group["kind"] for group in groups] == [
        "user",
        "native_tools",
        "terminal",
    ]
    tools = groups[1]
    assert tools["provider"] == "ark"
    assert tools["model"] == "science-model"
    assert tools["usage"]["total_tokens"] == 150
    assert tools["cost_usd"] is None
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


def test_branch_history_inherits_only_the_checkpointed_parent_prefix(tmp_path):
    store = Store(tmp_path / "branch-history.db")
    root = store.new_frame(project_id="default", status="ready")
    before = RuntimeActionLedger(store, root, "turn-before")
    before.append_user({"role": "user", "content": "before fork"})
    checkpoint = store.create_session_checkpoint(
        root_frame_id=root,
        branch_id=root,
        reason="fork base",
        workspace_tree_id="a" * 64,
        action_cursor=0,
    )
    store.fork_session_branch(
        root_frame_id=root,
        from_checkpoint_id=checkpoint["checkpoint_id"],
        branch_id="branch-alt",
    )
    later = RuntimeActionLedger(store, root, "turn-parent-later")
    later.append_user({"role": "user", "content": "parent only"})
    child = RuntimeActionLedger(
        store,
        root,
        "turn-child",
        branch_id="branch-alt",
    )
    child.append_user({"role": "user", "content": "child only"})

    root_history = restore_action_history(store, root, branch_id=root)
    child_history = restore_action_history(store, root, branch_id="branch-alt")

    assert [item["content"] for item in root_history] == [
        "before fork",
        "parent only",
    ]
    assert [item["content"] for item in child_history] == [
        "before fork",
        "child only",
    ]
    store.close()


def test_session_dynamic_tool_taxonomy_survives_restart_projection(tmp_path):
    class Worker:
        def invoke(self, manifest, arguments):
            del manifest
            return {"total": sum(arguments["values"])}

    implementation_marker = "MODEL_CODE_MUST_NOT_REACH_TIMELINE"
    registry = DynamicToolRegistry(
        "root-dynamic",
        tmp_path,
        tmp_path / "dynamic-manifests",
        worker=Worker(),
    )
    manifest = registry.define(
        {
            "name": "sum_session_values",
            "description": "Sum session measurements.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "values": {"type": "array", "items": {"type": "number"}},
                    "access_token": {"type": "string"},
                },
                "required": ["values", "access_token"],
                "additionalProperties": False,
            },
            "output_schema": {
                "type": "object",
                "properties": {"total": {"type": "number"}},
                "required": ["total"],
                "additionalProperties": False,
            },
            "implementation": (
                "def execute(args):\n"
                f"    # {implementation_marker}\n"
                "    return {'total': sum(args['values'])}\n"
            ),
            "smoke_args": {"values": [1, 2], "access_token": "smoke-token"},
            "ttl_s": 600,
        }
    )
    catalog = SessionToolCatalog(registry)
    proxy = catalog.get("sum_session_values")
    assert proxy is not None
    store = Store(tmp_path / "dynamic-ledger.db")
    ledger = RuntimeActionLedger(
        store,
        "root-dynamic",
        "turn-dynamic",
        tool_resolver=catalog.get,
    )
    ledger.append_user("sum the values")
    secret = "session-private-token"
    arguments = {"values": [3, 4], "access_token": secret}
    call = NativeToolCall(
        id="call-dynamic",
        wire_id="wire-dynamic",
        name="sum_session_values",
        ordinal=0,
        raw_arguments=('{"values":[3,4],"access_token":"session-private-token"}'),
        arguments=arguments,
    )
    reply = _reply((call,), content="Using the session capability.")
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
                        "content": {"total": 7},
                        "is_error": False,
                    },
                )
            ),
            0,
        )
    )
    ledger.emit(RunFinished(EngineResult((), None, "max_turns", 1, reply)))

    native = store.list_action_groups("root-dynamic")[1]
    proposed = native["events"][0]
    assert proposed["side_effect_class"] == proxy.side_effect_class == "read_only"
    assert (
        proposed["resource_keys"]
        == list(proxy.resource_keys(arguments))
        == [f"dynamic_tool:{manifest.manifest_id}"]
    )
    assert secret not in repr(native)
    assert proposed["canonical_arguments"]["arguments"]["access_token"] == REDACTED
    store.close()

    reopened = Store(tmp_path / "dynamic-ledger.db")
    timeline = ActionTimelineService(reopened).get("root-dynamic")
    dynamic_group = next(
        group for group in timeline["groups"] if group["kind"] == "native_tools"
    )
    public_event = dynamic_group["events"][0]
    assert public_event["side_effect_class"] == "read_only"
    assert public_event["resource_keys"] == [f"dynamic_tool:{manifest.manifest_id}"]
    assert not {"arguments", "raw_arguments", "wire_id"} & set(public_event)
    assert secret not in repr(timeline)
    assert implementation_marker not in repr(timeline)
    history = restore_action_history(reopened, "root-dynamic")
    assert [message["role"] for message in history] == ["user", "assistant", "tool"]
    assert secret not in repr(history)
    reopened.close()


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
