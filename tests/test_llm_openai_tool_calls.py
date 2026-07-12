"""Offline lossless native-tool contracts for OpenAI Chat."""

from __future__ import annotations

import copy

import pytest

from openai4s import llm
from openai4s.config import LLMConfig
from openai4s.tools.native import ToolSpec, control_tool_specs

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
        provider="chatgpt",
        api_key="test-key",
        base_url="https://openai.invalid/v1",
        model="test-model",
    )


class _Capture:
    def __init__(self, response: dict):
        self.response = response
        self.calls: list[dict] = []

    def __call__(self, url, payload, headers, timeout):
        self.calls.append({"url": url, "payload": payload, "headers": headers})
        return self.response

    @property
    def payload(self):
        assert len(self.calls) == 1
        return self.calls[0]["payload"]


def _install(monkeypatch, response: dict) -> _Capture:
    capture = _Capture(response)
    monkeypatch.setattr(llm.transport, "post_json", capture)
    return capture


def _call(
    call_id,
    raw,
    arguments,
    *,
    name="lookup",
    ordinal=0,
    parse_error=None,
    meta=None,
):
    return {
        "id": call_id,
        "wire_id": call_id,
        "name": name,
        "ordinal": ordinal,
        "raw_arguments": raw,
        "arguments": arguments,
        "parse_error": parse_error,
        "provider_meta": meta or {"type": "function"},
    }


def _assert_reply(result, content, calls, wire_state):
    assert _REQUIRED <= set(result)
    assert result["content"] == content
    assert result["tool_calls"] == calls
    assert result["assistant_message"] == {
        "role": "assistant",
        "content": content,
        "tool_calls": calls,
        "wire_state": wire_state,
    }


def _text_body(text="Done."):
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {},
    }


def test_openai_chat_blocking_tools_and_lossless_call_normalization(monkeypatch):
    raw_call = {
        "id": "call-openai-1",
        "type": "function",
        "function": {
            "name": "lookup",
            "arguments": '{"query":"ATP synthase"}',
        },
    }
    raw_message = {"role": "assistant", "content": None, "tool_calls": [raw_call]}
    body = {
        "choices": [{"message": raw_message, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 7},
    }
    cap = _install(monkeypatch, body)

    result = llm.chat(
        [{"role": "user", "content": "Find a fact."}], _cfg(), tools=[_LOOKUP]
    )

    assert cap.calls[0]["url"].endswith("/chat/completions")
    assert cap.payload["tools"] == [
        {
            "type": "function",
            "function": {
                "name": _LOOKUP.name,
                "description": _LOOKUP.description,
                "parameters": _LOOKUP.input_schema,
                "strict": False,
            },
        }
    ]
    assert cap.payload["parallel_tool_calls"] is True
    calls = [
        _call(
            "call-openai-1",
            '{"query":"ATP synthase"}',
            {"query": "ATP synthase"},
        )
    ]
    _assert_reply(result, "", calls, {"openai_message": raw_message})
    assert result["finish_reason"] == "tool_calls"
    assert result["raw"] is body


def test_malformed_openai_arguments_keep_raw_text_and_parse_error(monkeypatch):
    raw_call = {
        "id": "call-malformed-1",
        "type": "function",
        "function": {"name": "lookup", "arguments": '{"query":'},
    }
    raw_message = {"role": "assistant", "content": None, "tool_calls": [raw_call]}
    _install(
        monkeypatch,
        {
            "choices": [{"message": raw_message, "finish_reason": "tool_calls"}],
            "usage": {},
        },
    )

    result = llm.chat(
        [{"role": "user", "content": "Find a fact."}], _cfg(), tools=[_LOOKUP]
    )
    call = result["tool_calls"][0]

    assert set(call) == {
        "id",
        "wire_id",
        "name",
        "ordinal",
        "raw_arguments",
        "arguments",
        "parse_error",
        "provider_meta",
    }
    assert call["id"] == call["wire_id"] == "call-malformed-1"
    assert call["name"] == "lookup" and call["ordinal"] == 0
    assert call["raw_arguments"] == '{"query":' and call["arguments"] is None
    assert "invalid JSON arguments" in call["parse_error"]
    assert call["provider_meta"] == {"type": "function"}
    assert result["assistant_message"]["tool_calls"] == [call]


def test_registry_control_specs_are_accepted_by_public_chat(monkeypatch):
    spec = control_tool_specs()[0]
    cap = _install(monkeypatch, _text_body())
    llm.chat(
        [{"role": "user", "content": "Inspect the workspace."}],
        _cfg(),
        tools=control_tool_specs(),
    )
    assert cap.payload["tools"][0]["function"] == {
        "name": spec.name,
        "description": spec.description,
        "parameters": spec.input_schema,
        "strict": spec.strict,
    }


def test_openai_canonical_tool_result_history_round_trips(monkeypatch):
    raw_tool = {
        "id": "wire-call-1",
        "type": "function",
        "function": {
            "name": "lookup",
            "arguments": '{"query":"ATP synthase"}',
        },
    }
    call = _call(
        "canonical-call-1",
        '{"query":"ATP synthase"}',
        {"query": "ATP synthase"},
    )
    call["wire_id"] = "wire-call-1"
    cap = _install(monkeypatch, _text_body())
    messages = [
        {"role": "user", "content": "Find a fact."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [call],
            "wire_state": {
                "openai_message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [raw_tool],
                }
            },
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
        {"role": "assistant", "content": "", "tool_calls": [raw_tool]},
        {
            "role": "tool",
            "tool_call_id": "wire-call-1",
            "content": "lookup failed",
        },
    ]


def test_chat_keeps_legacy_fields_and_adds_canonical_tool_fields(monkeypatch):
    message = {"role": "assistant", "content": "Plain text."}
    _install(
        monkeypatch,
        {"choices": [{"message": message, "finish_reason": "stop"}], "usage": {}},
    )
    result = llm.chat([{"role": "user", "content": "Hello."}], _cfg())
    _assert_reply(result, "Plain text.", [], {"openai_message": message})


def _emit_interleaved(on_event):
    events = [
        {"choices": [{"delta": {"content": "Checking "}}]},
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 1,
                                "id": "call-stream-2",
                                "type": "function",
                                "function": {
                                    "name": "calculate",
                                    "arguments": '{"value":',
                                },
                            },
                            {
                                "index": 0,
                                "id": "call-stream-1",
                                "type": "function",
                                "function": {
                                    "name": "lookup",
                                    "arguments": '{"query":"ATP',
                                },
                            },
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "content": "now.",
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": ' synthase"}'}},
                            {"index": 1, "function": {"arguments": "2}"}},
                        ],
                    }
                }
            ]
        },
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 9}},
    ]
    for event in events:
        on_event(event)


def test_openai_chat_sse_aggregates_interleaved_calls_losslessly(monkeypatch):
    captured = {}

    def fake_sse(url, payload, headers, timeout, on_event):
        captured.update(url=url, payload=payload, headers=headers, timeout=timeout)
        _emit_interleaved(on_event)

    monkeypatch.setattr(llm.transport, "post_sse", fake_sse)
    monkeypatch.setenv("OPENAI4S_LLM_STREAM", "1")
    deltas = []
    result = llm.chat(
        [{"role": "user", "content": "Use both tools."}],
        _cfg(),
        tools=[_LOOKUP, _CALCULATE],
        on_delta=deltas.append,
    )

    calls = [
        _call(
            "call-stream-1",
            '{"query":"ATP synthase"}',
            {"query": "ATP synthase"},
            meta={"type": "function", "index": 0},
        ),
        _call(
            "call-stream-2",
            '{"value":2}',
            {"value": 2},
            name="calculate",
            ordinal=1,
            meta={"type": "function", "index": 1},
        ),
    ]
    raw_calls = [
        {
            "id": "call-stream-1",
            "type": "function",
            "function": {"name": "lookup", "arguments": '{"query":"ATP synthase"}'},
        },
        {
            "id": "call-stream-2",
            "type": "function",
            "function": {"name": "calculate", "arguments": '{"value":2}'},
        },
    ]
    assert captured["url"].endswith("/chat/completions")
    assert captured["payload"]["stream"] is True
    assert deltas == ["Checking ", "now."]
    _assert_reply(
        result,
        "Checking now.",
        calls,
        {
            "openai_message": {
                "role": "assistant",
                "content": "Checking now.",
                "tool_calls": raw_calls,
            }
        },
    )
    assert result["reasoning"] is None
    assert result["usage"] == {
        "input_tokens": 20,
        "output_tokens": 9,
        "cache_read": 0,
        "cache_write": 0,
        "reasoning_tokens": 0,
        "prompt_tokens": 20,
        "completion_tokens": 9,
        "total_tokens": 29,
    }
    assert result["finish_reason"] == "tool_calls" and result["raw"] is None


def test_openai_chat_sse_rejects_partial_call_without_terminal_event(monkeypatch):
    def partial(url, payload, headers, timeout, on_event):
        on_event(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_partial",
                                    "type": "function",
                                    "function": {
                                        "name": "lookup",
                                        "arguments": '{"query":',
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr(llm.transport, "post_sse", partial)
    with pytest.raises(llm.LLMError, match="terminal finish_reason"):
        llm.chat(
            [{"role": "user", "content": "Use a tool."}],
            _cfg(),
            tools=[_LOOKUP],
            on_delta=lambda _text: None,
        )


def test_openai_chat_sse_error_event_is_not_an_empty_success(monkeypatch):
    def error(url, payload, headers, timeout, on_event):
        on_event({"error": {"message": "provider overloaded"}})

    monkeypatch.setattr(llm.transport, "post_sse", error)
    with pytest.raises(llm.LLMError, match="provider overloaded"):
        llm.chat(
            [{"role": "user", "content": "Use a tool."}],
            _cfg(),
            tools=[_LOOKUP],
            on_delta=lambda _text: None,
        )


def _assert_stream_fallback(monkeypatch, fake_sse):
    cap = _install(monkeypatch, _text_body("fallback"))
    monkeypatch.setattr(llm.transport, "post_sse", fake_sse)
    result = llm.chat(
        [{"role": "user", "content": "Hello."}],
        _cfg(),
        on_delta=lambda _text: None,
    )
    assert result["content"] == "fallback"
    assert len(cap.calls) == 1


def test_openai_empty_stream_falls_back_before_any_semantic_event(monkeypatch):
    def empty_sse(url, payload, headers, timeout, on_event):
        return None

    _assert_stream_fallback(monkeypatch, empty_sse)


def test_openai_stream_read_error_falls_back_before_first_event(monkeypatch):
    def broken_sse(url, payload, headers, timeout, on_event):
        raise llm.LLMError("connection reset while reading stream")

    _assert_stream_fallback(monkeypatch, broken_sse)
