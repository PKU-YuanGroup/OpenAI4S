"""Offline lossless native-tool contracts for Gemini generateContent."""

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
        provider="gemini",
        api_key="test-key",
        base_url="https://gemini.invalid",
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


def _call(call_id, raw, arguments, meta, name="lookup", ordinal=0):
    return {
        "id": call_id,
        "wire_id": call_id,
        "name": name,
        "ordinal": ordinal,
        "raw_arguments": raw,
        "arguments": arguments,
        "parse_error": None,
        "provider_meta": meta,
    }


def _text_response(text="Done."):
    return {
        "candidates": [
            {
                "content": {"role": "model", "parts": [{"text": text}]},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {},
    }


def test_gemini_blocking_tools_and_lossless_call_normalization(monkeypatch):
    part = {
        "functionCall": {
            "id": "call-gemini-1",
            "name": "lookup",
            "args": {"query": "ATP synthase"},
        },
        "thoughtSignature": "sig-gemini-1",
    }
    content = {"role": "model", "parts": [{"text": "I will check."}, part]}
    body = {
        "candidates": [{"content": content, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 7},
    }
    cap = _install(monkeypatch, body)
    result = llm.chat(
        [{"role": "user", "content": "Find a fact."}], _cfg(), tools=[_LOOKUP]
    )

    assert cap.calls[0]["url"].endswith(":generateContent")
    assert cap.payload["tools"] == [
        {
            "functionDeclarations": [
                {
                    "name": _LOOKUP.name,
                    "description": _LOOKUP.description,
                    "parametersJsonSchema": _LOOKUP.input_schema,
                }
            ]
        }
    ]
    calls = [
        _call(
            "call-gemini-1",
            '{"query":"ATP synthase"}',
            {"query": "ATP synthase"},
            {"thoughtSignature": "sig-gemini-1"},
        )
    ]
    assert _REQUIRED <= set(result)
    assert result["content"] == "I will check."
    assert result["tool_calls"] == calls
    assert result["assistant_message"] == {
        "role": "assistant",
        "content": "I will check.",
        "tool_calls": calls,
        "wire_state": {"gemini_content": content},
    }
    assert result["finish_reason"] == "tool_calls"
    assert result["raw"] is body


def test_gemini_canonical_tool_result_history_round_trips(monkeypatch):
    raw_content = {
        "role": "model",
        "parts": [
            {
                "functionCall": {
                    "id": "wire-call-1",
                    "name": "lookup",
                    "args": {"query": "ATP synthase"},
                },
                "thoughtSignature": "sig-history-1",
            }
        ],
    }
    cap = _install(monkeypatch, _text_response())
    call = _call(
        "canonical-call-1",
        '{"query":"ATP synthase"}',
        {"query": "ATP synthase"},
        {},
    )
    call["wire_id"] = "wire-call-1"
    messages = [
        {"role": "user", "content": "Find a fact."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [call],
            "wire_state": {"gemini_content": raw_content},
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
    assert cap.payload["contents"] == [
        {"role": "user", "parts": [{"text": "Find a fact."}]},
        raw_content,
        {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "id": "wire-call-1",
                        "name": "lookup",
                        "response": {"error": "lookup failed"},
                    }
                }
            ],
        },
    ]


def test_chat_keeps_legacy_fields_and_adds_canonical_tool_fields(monkeypatch):
    content = {"role": "model", "parts": [{"text": "Plain text."}]}
    _install(
        monkeypatch,
        {
            "candidates": [{"content": content, "finishReason": "STOP"}],
            "usageMetadata": {},
        },
    )
    result = llm.chat([{"role": "user", "content": "Hello."}], _cfg())
    assert _REQUIRED <= set(result)
    assert result["content"] == "Plain text."
    assert result["tool_calls"] == []
    assert result["assistant_message"] == {
        "role": "assistant",
        "content": "Plain text.",
        "tool_calls": [],
        "wire_state": {"gemini_content": content},
    }


def test_gemini_missing_wire_id_uses_stable_local_id_but_never_replays_it(
    monkeypatch,
):
    raw_content = {
        "role": "model",
        "parts": [
            {
                "functionCall": {
                    "name": "lookup",
                    "args": {"query": "ATP synthase"},
                },
                "thoughtSignature": "opaque-signature",
            }
        ],
    }
    cap = _install(
        monkeypatch,
        {
            "candidates": [{"content": raw_content, "finishReason": "STOP"}],
            "usageMetadata": {},
        },
    )
    first = llm.chat(
        [{"role": "user", "content": "Find a fact."}], _cfg(), tools=[_LOOKUP]
    )
    second = llm.chat(
        [{"role": "user", "content": "Find a fact."}], _cfg(), tools=[_LOOKUP]
    )
    assert first["tool_calls"][0]["id"] == second["tool_calls"][0]["id"]
    assert first["tool_calls"][0]["wire_id"] is None

    history = [
        {"role": "user", "content": "Find a fact."},
        first["assistant_message"],
        {
            "role": "tool",
            "tool_call_id": first["tool_calls"][0]["id"],
            "name": "lookup",
            "content": {"answer": "rotary catalysis"},
        },
    ]
    llm.chat(history, _cfg(), tools=[_LOOKUP])
    response = cap.calls[-1]["payload"]["contents"][-1]["parts"][0][
        "functionResponse"
    ]
    assert "id" not in response


def test_gemini_canonical_fallback_replays_all_opaque_part_metadata(monkeypatch):
    cap = _install(monkeypatch, _text_response())
    call = _call(
        "call-gemini-meta",
        '{"query":"ATP"}',
        {"query": "ATP"},
        {
            "thoughtSignature": "opaque-signature",
            "thought": True,
            "providerExtension": {"version": 1},
        },
    )
    llm.chat(
        [
            {"role": "user", "content": "Find a fact."},
            {"role": "assistant", "content": "", "tool_calls": [call]},
            {
                "role": "tool",
                "tool_call_id": call["id"],
                "wire_id": call["wire_id"],
                "name": "lookup",
                "content": "ok",
            },
        ],
        _cfg(),
        tools=[_LOOKUP],
    )
    assert cap.payload["contents"][1]["parts"][0] == {
        "thoughtSignature": "opaque-signature",
        "thought": True,
        "providerExtension": {"version": 1},
        "functionCall": {
            "id": "call-gemini-meta",
            "name": "lookup",
            "args": {"query": "ATP"},
        },
    }


def test_gemini_parallel_calls_replay_signatures_on_original_parts(monkeypatch):
    raw_content = {
        "role": "model",
        "parts": [
            {"text": "Checking both.", "thoughtSignature": "text-signature"},
            {
                "functionCall": {
                    "id": "gemini_parallel_1",
                    "name": "lookup",
                    "args": {"query": "ATP"},
                },
                "thoughtSignature": "call-signature",
            },
            {
                "functionCall": {
                    "id": "gemini_parallel_2",
                    "name": "calculate",
                    "args": {"value": 2},
                }
            },
        ],
    }
    cap = _install(
        monkeypatch,
        {
            "candidates": [{"content": raw_content, "finishReason": "STOP"}],
            "usageMetadata": {},
        },
    )
    first = llm.chat(
        [{"role": "user", "content": "Use both."}],
        _cfg(),
        tools=[_LOOKUP, _CALCULATE],
    )
    history = [{"role": "user", "content": "Use both."}, first["assistant_message"]]
    for call, name, content in zip(
        first["tool_calls"], ["lookup", "calculate"], ["one", "two"]
    ):
        history.append(
            {
                "role": "tool",
                "tool_call_id": call["id"],
                "wire_id": call["wire_id"],
                "name": name,
                "content": content,
            }
        )
    llm.chat(history, _cfg(), tools=[_LOOKUP, _CALCULATE])
    assert cap.calls[-1]["payload"]["contents"][1] == raw_content
