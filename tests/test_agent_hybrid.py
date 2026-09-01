"""Minimal offline integration regressions for the hybrid Agent facade."""

from __future__ import annotations

import copy

import openai4s.agent.loop as loop_mod
from openai4s.agent import Agent
from openai4s.agent.actions import NO_CODE_NUDGE


def _reply(content: str, *, tool_calls=()):
    assistant = {"role": "assistant", "content": content}
    if tool_calls:
        assistant["tool_calls"] = list(tool_calls)
        assistant["wire_state"] = {"test_wire": {"opaque": True}}
    return {
        "content": content,
        "reasoning": None,
        "usage": {},
        "finish_reason": "stop",
        "raw": {"offline": True},
        "tool_calls": list(tool_calls),
        "assistant_message": assistant,
    }


class ScriptedChat:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, messages, cfg, **kwargs):
        self.calls.append(
            {
                "messages": copy.deepcopy(messages),
                "cfg": cfg,
                "kwargs": copy.deepcopy(kwargs),
            }
        )
        return self.replies.pop(0)


def test_native_call_beats_code_then_canonical_tool_history_reaches_next_turn(
    monkeypatch,
    tmp_path,
):
    call = {
        "id": "local_list",
        "wire_id": "wire_list",
        "name": "list_dir",
        "ordinal": 0,
        "raw_arguments": '{"path":"."}',
        "arguments": {"path": "."},
        "parse_error": None,
        "provider_meta": {"provider": "offline"},
    }
    decoy_code = (
        "Native must win over this cell.\n"
        "```python\n"
        "host.submit_output({'wrong': True}, ['Submitted the wrong branch'])\n"
        "```"
    )
    submit_code = (
        "```python\n"
        "host.submit_output({'winner': 'second'}, ['Completed native then code'])\n"
        "```"
    )
    scripted = ScriptedChat(
        [_reply(decoy_code, tool_calls=[call]), _reply(submit_code)]
    )
    monkeypatch.setattr(loop_mod, "chat", scripted)

    result = Agent(
        use_skills=False,
        allow_delegate=False,
        max_turns=3,
        workspace=tmp_path,
    ).run("list the workspace, then submit")

    assert result["stop_reason"] == "submitted"
    assert result["submitted_output"]["output"] == {"winner": "second"}
    assert len(scripted.calls) == 2

    schemas = scripted.calls[0]["kwargs"]["tools"]
    assert schemas
    names = {schema.name for schema in schemas}
    assert "list_dir" in names
    assert names.isdisjoint({"submit_output", "bash"})

    second_messages = scripted.calls[1]["messages"]
    assistant_index = next(
        index
        for index, message in enumerate(second_messages)
        if message.get("role") == "assistant" and message.get("tool_calls")
    )
    assistant = second_messages[assistant_index]
    tool_result = second_messages[assistant_index + 1]
    assert assistant["content"] == decoy_code
    assert tool_result == {
        "role": "tool",
        "tool_call_id": "local_list",
        "wire_id": "wire_list",
        "name": "list_dir",
        "content": tool_result["content"],
        "is_error": False,
    }
    assert [message["role"] for message in second_messages[assistant_index + 1 :]] == [
        "tool"
    ]


def test_reused_agent_clears_previous_submission_before_a_new_task(monkeypatch):
    first_submit = (
        "```python\n"
        "host.submit_output({'task': 1}, ['Completed the first task'])\n"
        "```"
    )
    scripted = ScriptedChat(
        [_reply(first_submit), _reply("Plain prose is not task completion.")]
    )
    monkeypatch.setattr(loop_mod, "chat", scripted)
    agent = Agent(use_skills=False, allow_delegate=False, max_turns=1)

    first = agent.run("first task")
    second = agent.run("second task")

    assert first["stop_reason"] == "submitted"
    assert first["submitted_output"]["output"] == {"task": 1}
    assert second["stop_reason"] == "max_turns"
    assert second["submitted_output"] is None
    assert second["final_message"] is None
    assert second["transcript"] == [
        {"role": "assistant", "content": "Plain prose is not task completion."},
        {"role": "observation", "content": NO_CODE_NUDGE},
    ]
    assert agent.dispatcher is not None
    assert agent.dispatcher.last_output is None
