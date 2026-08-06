"""Offline Anthropic Messages streaming contracts."""

import pytest

from openai4s import llm
from openai4s.config import LLMConfig
from openai4s.llm.messages import _anthropic_messages
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


def test_stream_disabled_by_env_never_opens_an_sse_connection(monkeypatch):
    """The knob has to reach this wire too, or a proxy that mishandles SSE has
    no way out short of dropping the delta callback."""
    monkeypatch.setenv("OPENAI4S_LLM_STREAM", "0")
    blocking_calls = []

    def post_json(_url, payload, _headers, _timeout):
        blocking_calls.append(payload)
        return {
            "content": [{"type": "text", "text": "blocking"}],
            "stop_reason": "end_turn",
            "usage": {},
        }

    monkeypatch.setattr(
        llm.transport,
        "post_sse",
        lambda *_args: pytest.fail("OPENAI4S_LLM_STREAM=0 must not open a stream"),
    )
    monkeypatch.setattr(llm.transport, "post_json", post_json)

    result = llm.chat(
        [{"role": "user", "content": "hello"}], _cfg(), on_delta=lambda _piece: None
    )

    assert result["content"] == "blocking"
    assert len(blocking_calls) == 1


# The content the two paths have to agree on: text before a tool call, the call
# itself, and text after it — the ordering a single accumulator gets wrong.
_BLOCKING_BODY = {
    "content": [
        {"type": "text", "text": "Let me look."},
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "lookup",
            "input": {"query": "ATP"},
        },
        {"type": "text", "text": " Done."},
    ],
    "stop_reason": "tool_use",
    "usage": {"input_tokens": 7, "output_tokens": 11},
}

_STREAM_EVENTS = [
    {"type": "message_start", "message": {"usage": {"input_tokens": 7}}},
    {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "text", "text": ""},
    },
    {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": "Let me look."},
    },
    {"type": "content_block_stop", "index": 0},
    {
        "type": "content_block_start",
        "index": 1,
        "content_block": {"type": "tool_use", "id": "toolu_1", "name": "lookup"},
    },
    {
        "type": "content_block_delta",
        "index": 1,
        "delta": {"type": "input_json_delta", "partial_json": '{"query":'},
    },
    {
        "type": "content_block_delta",
        "index": 1,
        "delta": {"type": "input_json_delta", "partial_json": '"ATP"}'},
    },
    {"type": "content_block_stop", "index": 1},
    {
        "type": "content_block_start",
        "index": 2,
        "content_block": {"type": "text", "text": ""},
    },
    {
        "type": "content_block_delta",
        "index": 2,
        "delta": {"type": "text_delta", "text": " Done."},
    },
    {"type": "content_block_stop", "index": 2},
    {
        "type": "message_delta",
        "delta": {"stop_reason": "tool_use"},
        "usage": {"output_tokens": 11},
    },
    {"type": "message_stop"},
]


def test_streaming_and_blocking_normalize_the_same_reply(monkeypatch):
    """The adapter contract in `providers/README.md`, asserted rather than
    asserted-about. Every earlier defect in this reconstruction — a coerced
    block type, a tool `input` left as a string, a missing finish reason —
    shows up here as a divergence from the path that was already correct."""
    _install_stream(monkeypatch, _STREAM_EVENTS)
    streamed = llm.chat(
        [{"role": "user", "content": "look it up"}], _cfg(), on_delta=lambda _p: None
    )

    monkeypatch.setattr(
        llm.transport,
        "post_sse",
        lambda *_args: pytest.fail("the blocking leg must not stream"),
    )
    monkeypatch.setattr(llm.transport, "post_json", lambda *_args: dict(_BLOCKING_BODY))
    blocking = llm.chat([{"role": "user", "content": "look it up"}], _cfg())

    # `raw` is the one field that legitimately differs: a stream has no single
    # response body to hand back.
    assert streamed["raw"] is None
    assert blocking["raw"] == _BLOCKING_BODY
    assert {k: v for k, v in streamed.items() if k != "raw"} == {
        k: v for k, v in blocking.items() if k != "raw"
    }
    assert streamed["content"] == "Let me look. Done."
    assert streamed["wire_state"]["anthropic_content"] == _BLOCKING_BODY["content"]


def test_unrecognized_blocks_survive_into_the_next_turn(monkeypatch):
    """`wire_state` is replayed verbatim as the next turn's assistant content,
    so a block this reconstruction does not model cannot be rendered down to
    text: `{"type": "text", "text": ""}` is not a lossy summary of an encrypted
    reasoning block, it is a request the *following* turn fails on."""
    events = [
        {"type": "message_start", "message": {"usage": {}}},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "redacted_thinking", "data": "ENCRYPTED"},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "text_delta", "text": "answer"},
        },
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {}},
        {"type": "message_stop"},
    ]
    _install_stream(monkeypatch, events)

    result = llm.chat(
        [{"role": "user", "content": "hi"}], _cfg(), on_delta=lambda _p: None
    )

    assert result["wire_state"]["anthropic_content"] == [
        {"type": "redacted_thinking", "data": "ENCRYPTED"},
        {"type": "text", "text": "answer"},
    ]
    _, conv = _anthropic_messages(
        [{"role": "user", "content": "hi"}, result["assistant_message"]]
    )
    assert conv[1]["content"][0] == {"type": "redacted_thinking", "data": "ENCRYPTED"}
    assert not [
        block
        for block in conv[1]["content"]
        if block.get("type") == "text" and not block.get("text")
    ], "an empty text block is rejected by the Messages API on the next request"


def test_thinking_blocks_reassemble_with_their_signature(monkeypatch):
    events = [
        {"type": "message_start", "message": {"usage": {}}},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "step one, "},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "step two"},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "signature_delta", "signature": "sig-abc"},
        },
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {}},
        {"type": "message_stop"},
    ]
    deltas = []
    _install_stream(monkeypatch, events)

    result = llm.chat(
        [{"role": "user", "content": "hi"}], _cfg(), on_delta=deltas.append
    )

    assert result["wire_state"]["anthropic_content"] == [
        {"type": "thinking", "thinking": "step one, step two", "signature": "sig-abc"}
    ]
    # Reasoning is not prose; forwarding it as a text delta would render it as
    # the answer.
    assert deltas == []
    assert result["content"] == ""


def test_unparsed_tool_input_stays_an_object_on_the_wire(monkeypatch):
    """A truncated fragment must not become the block's `input`: that field is
    an object on this wire, and the block is replayed as-is next turn. The
    fragment is not discarded — it goes where a parse failure is representable."""
    events = [
        {"type": "message_start", "message": {"usage": {}}},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "id": "toolu_x", "name": "lookup"},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"query":'},
        },
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {}},
        {"type": "message_stop"},
    ]
    _install_stream(monkeypatch, events)

    result = llm.chat(
        [{"role": "user", "content": "hi"}], _cfg(), on_delta=lambda _p: None
    )

    block = result["wire_state"]["anthropic_content"][0]
    assert block["input"] == {}
    call = result["tool_calls"][0]
    assert call["arguments"] is None
    assert call["parse_error"]
    assert call["raw_arguments"] == '{"query":'


def test_a_stream_without_a_stop_reason_still_names_one(monkeypatch):
    """`finish_reason: None` is how the loop reports a reply it never finished
    reading, so a complete reply must not borrow that signal."""
    events = [
        {"type": "message_start", "message": {"usage": {}}},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "done"},
        },
        {"type": "message_stop"},
    ]
    _install_stream(monkeypatch, events)

    result = llm.chat(
        [{"role": "user", "content": "hi"}], _cfg(), on_delta=lambda _p: None
    )

    assert result["finish_reason"] == "end_turn"
    assert result["provider_finish_reason"] == "end_turn"
