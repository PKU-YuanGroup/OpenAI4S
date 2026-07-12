"""Offline native-tool contracts for the OpenAI Responses wire."""

from __future__ import annotations

import copy

import pytest

from openai4s import llm
from openai4s.config import LLMConfig
from openai4s.tools.native import ToolSpec

_LOOKUP = ToolSpec(
    name="lookup",
    description="Look up a scientific fact.",
    input_schema={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
)


def _cfg() -> LLMConfig:
    return LLMConfig(
        provider="openai_responses",
        api_key="test-key",
        base_url="https://responses.invalid/v1",
        model="test-model",
    )


def _tool_response_events(on_event) -> list[dict]:
    output = [
        {
            "type": "reasoning",
            "id": "rs_1",
            "summary": [],
            "encrypted_content": "opaque-reasoning-state",
        },
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_responses_1",
            "name": "lookup",
            "arguments": '{"query":"ATP synthase"}',
            "status": "completed",
        },
    ]
    on_event(
        {
            "type": "response.output_item.added",
            "output_index": 1,
            "item": {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_responses_1",
                "name": "lookup",
                "arguments": "",
            },
        }
    )
    on_event(
        {
            "type": "response.function_call_arguments.delta",
            "output_index": 1,
            "item_id": "fc_1",
            "delta": '{"query":"ATP',
        }
    )
    on_event(
        {
            "type": "response.function_call_arguments.delta",
            "output_index": 1,
            "item_id": "fc_1",
            "delta": ' synthase"}',
        }
    )
    on_event(
        {
            "type": "response.output_item.done",
            "output_index": 1,
            "item": output[1],
        }
    )
    on_event(
        {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "output": output,
                "usage": {
                    "input_tokens": 20,
                    "output_tokens": 8,
                    "total_tokens": 28,
                },
            },
        }
    )
    return output


def test_responses_provider_is_reachable_and_preserves_output_items(monkeypatch):
    captured: dict = {}
    final_output: list[dict] = []

    def fake_sse(url, payload, headers, timeout, on_event):
        captured.update(url=url, payload=payload, headers=headers, timeout=timeout)
        final_output.extend(_tool_response_events(on_event))

    monkeypatch.setattr(llm.transport, "post_sse", fake_sse)

    result = llm.chat(
        [{"role": "user", "content": "Find a fact."}],
        _cfg(),
        tools=[_LOOKUP],
    )

    assert llm.provider_spec("openai_responses")["wire"] == "responses"
    assert captured["url"].endswith("/responses")
    assert captured["payload"]["tools"] == [
        {
            "type": "function",
            "name": "lookup",
            "description": _LOOKUP.description,
            "parameters": _LOOKUP.input_schema,
            "strict": False,
        }
    ]
    assert captured["payload"]["include"] == ["reasoning.encrypted_content"]
    assert result["finish_reason"] == "tool_calls"
    assert result["tool_calls"] == [
        {
            "id": "call_responses_1",
            "wire_id": "call_responses_1",
            "name": "lookup",
            "ordinal": 0,
            "raw_arguments": '{"query":"ATP synthase"}',
            "arguments": {"query": "ATP synthase"},
            "parse_error": None,
            "provider_meta": {"status": "completed", "item_id": "fc_1"},
        }
    ]
    assert result["wire_state"] == {"responses_output": final_output}
    assert result["assistant_message"]["wire_state"] == result["wire_state"]


def test_responses_history_replays_output_before_function_result(monkeypatch):
    captured: list[dict] = []

    def first_sse(url, payload, headers, timeout, on_event):
        captured.append(copy.deepcopy(payload))
        _tool_response_events(on_event)

    monkeypatch.setattr(llm.transport, "post_sse", first_sse)
    first = llm.chat(
        [{"role": "user", "content": "Find a fact."}],
        _cfg(),
        tools=[_LOOKUP],
    )

    def second_sse(url, payload, headers, timeout, on_event):
        captured.append(copy.deepcopy(payload))
        output = [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Done."}],
            }
        ]
        on_event(
            {
                "type": "response.completed",
                "response": {"status": "completed", "output": output, "usage": {}},
            }
        )

    monkeypatch.setattr(llm.transport, "post_sse", second_sse)
    history = [
        {"role": "user", "content": "Find a fact."},
        first["assistant_message"],
        {
            "role": "tool",
            "tool_call_id": "call_responses_1",
            "wire_id": "call_responses_1",
            "name": "lookup",
            "content": {"answer": "rotary catalysis"},
        },
    ]
    result = llm.chat(history, _cfg(), tools=[_LOOKUP])

    assert result["content"] == "Done."
    second_input = captured[1]["input"]
    assert second_input[1:3] == first["wire_state"]["responses_output"]
    assert second_input[3] == {
        "type": "function_call_output",
        "call_id": "call_responses_1",
        "output": '{"answer": "rotary catalysis"}',
    }


def test_responses_rejects_incomplete_and_unterminated_tool_streams(monkeypatch):
    def incomplete_sse(url, payload, headers, timeout, on_event):
        on_event(
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {
                    "type": "function_call",
                    "id": "fc_partial",
                    "call_id": "call_partial",
                    "name": "lookup",
                    "arguments": '{"query":',
                },
            }
        )
        on_event(
            {
                "type": "response.incomplete",
                "response": {"incomplete_details": {"reason": "max_output_tokens"}},
            }
        )

    monkeypatch.setattr(llm.transport, "post_sse", incomplete_sse)
    with pytest.raises(llm.LLMError, match="response incomplete"):
        llm.chat(
            [{"role": "user", "content": "Find a fact."}],
            _cfg(),
            tools=[_LOOKUP],
        )

    def truncated_sse(url, payload, headers, timeout, on_event):
        on_event(
            {
                "type": "response.function_call_arguments.delta",
                "output_index": 0,
                "item_id": "fc_partial",
                "delta": '{"query":',
            }
        )

    monkeypatch.setattr(llm.transport, "post_sse", truncated_sse)
    with pytest.raises(llm.LLMError, match="before response.completed"):
        llm.chat(
            [{"role": "user", "content": "Find a fact."}],
            _cfg(),
            tools=[_LOOKUP],
        )


def test_responses_can_rebuild_canonical_call_history_without_wire_state(
    monkeypatch,
):
    captured: dict = {}

    def complete_sse(url, payload, headers, timeout, on_event):
        captured.update(copy.deepcopy(payload))
        on_event(
            {
                "type": "response.completed",
                "response": {"status": "completed", "output": [], "usage": {}},
            }
        )

    monkeypatch.setattr(llm.transport, "post_sse", complete_sse)
    history = [
        {"role": "user", "content": "Find a fact."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_responses_1",
                    "wire_id": "call_responses_1",
                    "name": "lookup",
                    "ordinal": 0,
                    "raw_arguments": '{"query":"ATP synthase"}',
                    "arguments": {"query": "ATP synthase"},
                    "parse_error": None,
                    "provider_meta": {"item_id": "fc_1"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_responses_1",
            "content": "ok",
        },
    ]

    llm.chat(history, _cfg(), tools=[_LOOKUP])

    assert captured["input"][1] == {
        "type": "function_call",
        "id": "fc_1",
        "call_id": "call_responses_1",
        "name": "lookup",
        "arguments": '{"query":"ATP synthase"}',
    }
    assert captured["input"][2] == {
        "type": "function_call_output",
        "call_id": "call_responses_1",
        "output": "ok",
    }
