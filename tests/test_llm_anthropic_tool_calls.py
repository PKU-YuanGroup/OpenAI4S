"""Offline lossless native-tool contracts for Anthropic Messages."""

from __future__ import annotations

import copy

from openai4s import llm
from openai4s.config import LLMConfig
from openai4s.tools.native import ToolSpec

_REQUIRED = {
    "content",
    "reasoning",
    "usage",
    "finish_reason",
    "raw",
    "tool_calls",
    "assistant_message",
}
_LOOKUP = ToolSpec(
    "lookup",
    "Look up a scientific fact.",
    {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    },
)
_CALCULATE = ToolSpec(
    "calculate",
    "Evaluate a small calculation.",
    {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
    },
)


def _cfg() -> LLMConfig:
    return LLMConfig(
        provider="claude",
        api_key="test-key",
        base_url="https://anthropic.invalid",
        model="test-model",
    )


class _Capture:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url, payload, headers, timeout):
        self.calls.append({"url": url, "payload": payload, "headers": headers})
        return self.response

    @property
    def payload(self):
        assert len(self.calls) == 1
        return self.calls[0]["payload"]


def _install(monkeypatch, response):
    capture = _Capture(response)
    monkeypatch.setattr(llm.transport, "post_json", capture)
    return capture


def _call(call_id, name, ordinal, raw, arguments, block):
    return {
        "id": call_id,
        "wire_id": call_id,
        "name": name,
        "ordinal": ordinal,
        "raw_arguments": raw,
        "arguments": arguments,
        "parse_error": None,
        "provider_meta": {"block": block},
    }


def _assert_reply(result, content, calls, blocks):
    assert _REQUIRED <= set(result)
    assert result["content"] == content
    assert result["tool_calls"] == calls
    assert result["assistant_message"] == {
        "role": "assistant",
        "content": content,
        "tool_calls": calls,
        "wire_state": {"anthropic_content": blocks},
    }


def _text_response(text="Done."):
    return {
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {},
    }


def test_anthropic_blocking_tools_and_lossless_call_normalization(monkeypatch):
    block = {
        "type": "tool_use",
        "id": "toolu-anthropic-1",
        "name": "lookup",
        "input": {"query": "ATP synthase"},
    }
    blocks = [{"type": "text", "text": "I will check."}, block]
    body = {
        "content": blocks,
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 12, "output_tokens": 7},
    }
    cap = _install(monkeypatch, body)

    result = llm.chat(
        [{"role": "user", "content": "Find a fact."}], _cfg(), tools=[_LOOKUP]
    )

    assert cap.calls[0]["url"].endswith("/v1/messages")
    native = cap.payload["tools"][0]
    assert native["name"] == _LOOKUP.name
    assert native["description"] == _LOOKUP.description
    assert native["input_schema"] == _LOOKUP.input_schema
    assert native.get("strict", False) is False
    calls = [
        _call(
            "toolu-anthropic-1",
            "lookup",
            0,
            '{"query":"ATP synthase"}',
            {"query": "ATP synthase"},
            block,
        )
    ]
    _assert_reply(result, "I will check.", calls, blocks)
    assert result["finish_reason"] == "tool_calls"
    assert result["raw"] is body


def test_anthropic_canonical_tool_result_history_round_trips(monkeypatch):
    blocks = [
        {
            "type": "tool_use",
            "id": "wire-call-1",
            "name": "lookup",
            "input": {"query": "ATP synthase"},
        }
    ]
    call = _call(
        "canonical-call-1",
        "lookup",
        0,
        '{"query":"ATP synthase"}',
        {"query": "ATP synthase"},
        blocks[0],
    )
    call["wire_id"] = "wire-call-1"
    cap = _install(monkeypatch, _text_response())
    messages = [
        {"role": "user", "content": "Find a fact."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [call],
            "wire_state": {"anthropic_content": blocks},
        },
        {
            "role": "tool",
            "name": "lookup",
            "wire_id": "wire-call-1",
            "content": "lookup failed",
            "is_error": True,
        },
    ]
    original = copy.deepcopy(messages)
    llm.chat(messages, _cfg(), tools=[_LOOKUP])

    assert messages == original
    assert cap.payload["messages"] == [
        {"role": "user", "content": "Find a fact."},
        {"role": "assistant", "content": blocks},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "wire-call-1",
                    "content": "lookup failed",
                    "is_error": True,
                }
            ],
        },
    ]


def test_chat_keeps_legacy_fields_and_adds_canonical_tool_fields(monkeypatch):
    blocks = [{"type": "text", "text": "Plain text."}]
    _install(
        monkeypatch,
        {"content": blocks, "stop_reason": "end_turn", "usage": {}},
    )
    result = llm.chat([{"role": "user", "content": "Hello."}], _cfg())
    _assert_reply(result, "Plain text.", [], blocks)


def test_anthropic_parallel_results_stay_in_one_adjacent_user_message(monkeypatch):
    blocks = [
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "lookup",
            "input": {"query": "ATP"},
        },
        {
            "type": "tool_use",
            "id": "toolu_2",
            "name": "calculate",
            "input": {"value": 2},
        },
    ]
    cap = _install(monkeypatch, _text_response())
    calls = [
        _call("toolu_1", "lookup", 0, '{"query":"ATP"}', {"query": "ATP"}, blocks[0]),
        _call("toolu_2", "calculate", 1, '{"value":2}', {"value": 2}, blocks[1]),
    ]
    history = [
        {"role": "user", "content": "Use two tools."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": calls,
            "wire_state": {"anthropic_content": blocks},
        },
        {
            "role": "tool",
            "tool_call_id": "toolu_1",
            "wire_id": "toolu_1",
            "name": "lookup",
            "content": "one",
        },
        {
            "role": "tool",
            "tool_call_id": "toolu_2",
            "wire_id": "toolu_2",
            "name": "calculate",
            "content": "two",
        },
    ]
    llm.chat(history, _cfg(), tools=[_LOOKUP, _CALCULATE])

    assert cap.payload["messages"][-2] == {
        "role": "assistant",
        "content": blocks,
    }
    result = cap.payload["messages"][-1]
    assert result["role"] == "user"
    assert [block["tool_use_id"] for block in result["content"]] == [
        "toolu_1",
        "toolu_2",
    ]
