"""Anthropic Messages provider adapter."""

from __future__ import annotations

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
    body = post_json(url, payload, headers, cfg.timeout_s)
    try:
        blocks = body["content"]
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    except (KeyError, TypeError) as e:
        raise LLMError(f"Unexpected Anthropic-wire response: {body}") from e
    calls: list[dict] = []
    for ordinal, block in enumerate(
        b for b in blocks if b.get("type") == "tool_use"
    ):
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
