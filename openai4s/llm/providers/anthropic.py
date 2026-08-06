"""Anthropic Messages provider adapter."""

from __future__ import annotations

import json
import os
from typing import Any

from ..messages import _anthropic_messages
from ..models import LLMError
from ..tooling import _apply_anthropic_tools, _assistant_message, _normalized_tool_call

_ANTHROPIC_VERSION = "2023-06-01"


def _chat_anthropic(
    messages,
    cfg,
    base,
    model,
    max_tokens,
    temperature,
    stop,
    on_delta=None,
    *,
    tools=None,
    tool_choice=None,
    parallel_tool_calls=None,
    post_json,
    post_sse,
) -> dict:
    url = f"{base.rstrip('/')}/v1/messages"
    # Anthropic takes a top-level `system` string, not a system message.
    system_txt, conv = _anthropic_messages(messages)
    payload: dict[str, Any] = {
        "model": model,
        "messages": conv,
        "max_tokens": max_tokens or cfg.max_tokens,
        "temperature": cfg.temperature if temperature is None else temperature,
    }
    if system_txt:
        payload["system"] = system_txt
    if stop:
        payload["stop_sequences"] = stop
    _apply_anthropic_tools(payload, tools or [], tool_choice, parallel_tool_calls)
    headers = {
        "Content-Type": "application/json",
        "x-api-key": cfg.api_key,
        "anthropic-version": _ANTHROPIC_VERSION,
    }
    want_stream = on_delta is not None and os.environ.get(
        "OPENAI4S_LLM_STREAM", "1"
    ) not in ("0", "false", "no", "off")
    if want_stream:
        try:
            return _chat_anthropic_stream(
                url, dict(payload), headers, cfg, on_delta, post_sse=post_sse
            )
        except _StreamStartError:
            pass
    body = post_json(url, payload, headers, cfg.timeout_s)
    try:
        blocks = body["content"]
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    except (KeyError, TypeError) as e:
        raise LLMError(f"Unexpected Anthropic-wire response: {body}") from e
    calls: list[dict] = []
    for ordinal, block in enumerate(b for b in blocks if b.get("type") == "tool_use"):
        calls.append(
            _normalized_tool_call(
                provider="anthropic",
                ordinal=ordinal,
                name=block.get("name"),
                arguments=block.get("input", {}),
                wire_id=block.get("id"),
                provider_meta={"block": block},
            )
        )
    provider_finish = body.get("stop_reason")
    wire_state = {"anthropic_content": blocks}
    return {
        "content": text,
        "reasoning": None,
        "usage": body.get("usage", {}),
        "finish_reason": "tool_calls" if calls else provider_finish,
        "provider_finish_reason": provider_finish,
        "tool_calls": calls,
        "assistant_message": _assistant_message(text, calls, wire_state),
        "wire_state": wire_state,
        "raw": body,
    }


class _StreamStartError(Exception):
    """The stream failed before an event, so a blocking retry is safe."""


def _chat_anthropic_stream(url, payload, headers, cfg, on_delta, *, post_sse) -> dict:
    payload["stream"] = True
    headers = {**headers, "Accept": "text/event-stream"}
    state: dict[str, Any] = {
        "blocks": {},
        "usage": {},
        "finish": None,
        "started": False,
        "terminal": False,
    }

    def _block(index: int) -> dict[str, Any]:
        return state["blocks"].setdefault(index, {"type": "text", "text": []})

    def _emit(piece: str) -> None:
        if not piece:
            return
        try:
            on_delta(piece)
        except Exception:  # noqa: BLE001 - a UI callback must not kill the stream
            pass

    def _on_event(evt: dict) -> None:
        event_type = evt.get("type")
        if event_type == "error" or evt.get("error"):
            state["started"] = True
            error = evt.get("error")
            detail = error.get("message") if isinstance(error, dict) else error
            raise LLMError(f"Anthropic stream error: {detail or evt}")
        if event_type == "ping":
            return
        state["started"] = True
        if event_type == "message_start":
            message = evt.get("message") or {}
            state["usage"].update(message.get("usage") or {})
            return
        if event_type == "content_block_start":
            index = int(evt.get("index", 0))
            raw = evt.get("content_block") or {}
            kind = raw.get("type")
            if kind == "tool_use":
                state["blocks"][index] = {
                    "type": "tool_use",
                    "id": raw.get("id"),
                    "name": raw.get("name"),
                    "input_json": [],
                }
            elif kind == "thinking":
                initial = raw.get("thinking") or ""
                state["blocks"][index] = {
                    "type": "thinking",
                    "thinking": [initial],
                }
            elif kind in (None, "text"):
                initial = raw.get("text") or ""
                state["blocks"][index] = {"type": "text", "text": [initial]}
                _emit(initial)
            else:
                # Anything else — `redacted_thinking`, a server-tool block, a
                # type added after this was written — is carried through
                # unchanged. The reply's `wire_state` becomes the next turn's
                # assistant content verbatim (see `_anthropic_messages`), so
                # coercing an unrecognized block into `{"type": "text",
                # "text": ""}` is not a lossy render: it is a request Anthropic
                # rejects, one turn after the turn that produced it. The
                # blocking path keeps these blocks intact; so must this one.
                state["blocks"][index] = dict(raw)
            return
        if event_type == "content_block_delta":
            index = int(evt.get("index", 0))
            delta = evt.get("delta") or {}
            kind = delta.get("type")
            block = _block(index)
            if kind == "text_delta":
                piece = delta.get("text") or ""
                block.setdefault("text", []).append(piece)
                _emit(piece)
            elif kind == "input_json_delta":
                block.setdefault("input_json", []).append(
                    delta.get("partial_json") or ""
                )
            elif kind == "thinking_delta":
                block.setdefault("thinking", []).append(delta.get("thinking") or "")
            elif kind == "signature_delta" and delta.get("signature"):
                block["signature"] = delta["signature"]
            return
        if event_type == "message_delta":
            delta = evt.get("delta") or {}
            if delta.get("stop_reason") is not None:
                state["finish"] = delta["stop_reason"]
            state["usage"].update(evt.get("usage") or {})
            return
        if event_type == "message_stop":
            state["terminal"] = True

    timeout = max(cfg.timeout_s, 60.0)
    try:
        post_sse(url, payload, headers, timeout, _on_event)
    except LLMError:
        if not state["started"]:
            raise _StreamStartError()
        raise
    if not state["terminal"]:
        if not state["started"]:
            raise _StreamStartError()
        raise LLMError("Anthropic stream ended before message_stop")

    blocks: list[dict[str, Any]] = []
    # Arguments to normalize, per tool_use block, paired with that block's
    # position in `blocks`. They live beside the wire block and never inside
    # it: the block is replayed to Anthropic verbatim, so it may carry only
    # fields the Messages API accepts.
    tool_arguments: list[tuple[int, Any]] = []
    for index in sorted(state["blocks"]):
        acc = dict(state["blocks"][index])
        kind = acc.get("type")
        if kind == "tool_use":
            raw_input = "".join(acc.pop("input_json", []))
            try:
                parsed = json.loads(raw_input) if raw_input else {}
            except ValueError:
                parsed = None
            # `input` is an object on this wire. Fragments that never formed
            # valid JSON still reach the normalized call — which carries a
            # `parse_error` field for exactly this — while the wire block keeps
            # a shape the next request can actually carry.
            acc["input"] = parsed if isinstance(parsed, dict) else {}
            tool_arguments.append(
                (len(blocks), acc["input"] if isinstance(parsed, dict) else raw_input)
            )
        elif kind == "thinking":
            acc["thinking"] = "".join(acc.get("thinking", []))
        elif kind == "text":
            # A delta that arrived without its `content_block_start` lands on
            # the default text accumulator; drop the stray key rather than
            # replaying it as part of the block.
            acc.pop("input_json", None)
            acc["text"] = "".join(acc.get("text", []))
        blocks.append(acc)

    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    calls: list[dict] = []
    for ordinal, (position, arguments) in enumerate(tool_arguments):
        block = blocks[position]
        calls.append(
            _normalized_tool_call(
                provider="anthropic",
                ordinal=ordinal,
                name=block.get("name"),
                arguments=arguments,
                wire_id=block.get("id"),
                provider_meta={"block": block},
            )
        )
    # Parity with the OpenAI stream's `or "stop"`: a reply whose finish reason
    # is None is indistinguishable from one the loop never finished reading.
    provider_finish = state["finish"] or "end_turn"
    wire_state = {"anthropic_content": blocks}
    return {
        "content": text,
        "reasoning": None,
        "usage": state["usage"],
        "finish_reason": "tool_calls" if calls else provider_finish,
        "provider_finish_reason": provider_finish,
        "tool_calls": calls,
        "assistant_message": _assistant_message(text, calls, wire_state),
        "wire_state": wire_state,
        "raw": None,
    }
