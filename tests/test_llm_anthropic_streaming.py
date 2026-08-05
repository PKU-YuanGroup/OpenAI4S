"""Offline Anthropic Messages streaming contracts."""

import pytest

from openai4s import llm
from openai4s.config import LLMConfig
from openai4s.llm.models import LLMError


def _cfg():
    return LLMConfig(
        provider="claude",
        api_key="test-key",
        base_url="https://anthropic.invalid",
        model="test-model",
    )


def _install_stream(monkeypatch, events):
    captured = {}

    def post_sse(url, payload, headers, timeout, on_event):
        captured.update(url=url, payload=payload, headers=headers, timeout=timeout)
        for event in events:
            on_event(event)

    monkeypatch.setattr(llm.transport, "post_sse", post_sse)
    monkeypatch.setattr(
        llm.transport,
        "post_json",
        lambda *_args: pytest.fail("streaming must not use the blocking transport"),
    )
    return captured


def test_anthropic_stream_forwards_text_and_merges_usage(monkeypatch):
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 4}}},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "hel"},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "lo"},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 2},
        },
        {"type": "message_stop"},
    ]
    capture = _install_stream(monkeypatch, events)
    deltas = []

    result = llm.chat(
        [{"role": "user", "content": "hello"}], _cfg(), on_delta=deltas.append
    )

    assert capture["url"].endswith("/v1/messages")
    assert capture["payload"]["stream"] is True
    assert capture["headers"]["Accept"] == "text/event-stream"
    assert deltas == ["hel", "lo"]
    assert result["content"] == "hello"
    assert result["usage"]["input_tokens"] == 4
    assert result["usage"]["output_tokens"] == 2
    assert result["finish_reason"] == "end_turn"
    assert result["raw"] is None


def test_anthropic_stream_reassembles_tool_input(monkeypatch):
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 5}}},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "lookup",
                "input": {},
            },
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"query":'},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '"ATP"}'},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use"},
            "usage": {"output_tokens": 4},
        },
        {"type": "message_stop"},
    ]
    _install_stream(monkeypatch, events)

    result = llm.chat(
        [{"role": "user", "content": "look it up"}],
        _cfg(),
        on_delta=lambda _piece: None,
    )

    assert result["finish_reason"] == "tool_calls"
    assert result["tool_calls"][0]["id"] == "toolu_1"
    assert result["tool_calls"][0]["name"] == "lookup"
    assert result["tool_calls"][0]["arguments"] == {"query": "ATP"}
    assert result["wire_state"]["anthropic_content"][0]["input"] == {"query": "ATP"}


def test_anthropic_stream_error_after_start_does_not_replay(monkeypatch):
    blocking_calls = []

    def post_sse(_url, _payload, _headers, _timeout, on_event):
        on_event({"type": "message_start", "message": {"usage": {}}})
        on_event({"type": "error", "error": {"message": "overloaded"}})

    monkeypatch.setattr(llm.transport, "post_sse", post_sse)
    monkeypatch.setattr(
        llm.transport, "post_json", lambda *_args: blocking_calls.append(1)
    )

    with pytest.raises(LLMError, match="overloaded"):
        llm.chat(
            [{"role": "user", "content": "hello"}],
            _cfg(),
            on_delta=lambda _piece: None,
        )
    assert blocking_calls == []


def test_anthropic_stream_start_failure_falls_back_to_blocking(monkeypatch):
    blocking_calls = []

    def post_sse(*_args):
        raise LLMError("connection refused")

    def post_json(_url, payload, _headers, _timeout):
        blocking_calls.append(payload)
        return {
            "content": [{"type": "text", "text": "fallback"}],
            "stop_reason": "end_turn",
            "usage": {},
        }

    monkeypatch.setattr(llm.transport, "post_sse", post_sse)
    monkeypatch.setattr(llm.transport, "post_json", post_json)

    result = llm.chat(
        [{"role": "user", "content": "hello"}],
        _cfg(),
        on_delta=lambda _piece: None,
    )

    assert result["content"] == "fallback"
    assert len(blocking_calls) == 1
    assert "stream" not in blocking_calls[0]
