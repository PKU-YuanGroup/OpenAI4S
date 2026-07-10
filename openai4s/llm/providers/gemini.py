"""Gemini generateContent provider adapter."""

from __future__ import annotations

from typing import Any

from ..messages import _gemini_contents
from ..models import LLMError
from ..tooling import _apply_gemini_tools, _assistant_message, _normalized_tool_call


def _chat_gemini(
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
    url = f"{base.rstrip('/')}/v1beta/models/{model}:generateContent"
    system_txt, contents = _gemini_contents(messages)
    payload: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": max_tokens or cfg.max_tokens,
            "temperature": cfg.temperature if temperature is None else temperature,
        },
    }
    if system_txt:
        payload["systemInstruction"] = {"parts": [{"text": system_txt}]}
    if stop:
        payload["generationConfig"]["stopSequences"] = stop
    _apply_gemini_tools(payload, tools or [], tool_choice, parallel_tool_calls)
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": cfg.api_key,
    }
    body = post_json(url, payload, headers, cfg.timeout_s)
    try:
        cand = body["candidates"][0]
        raw_content = cand["content"]
        parts = raw_content["parts"]
        text = "".join(p.get("text", "") for p in parts if "text" in p)
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"Unexpected Gemini-wire response: {body}") from e
    calls: list[dict] = []
    for ordinal, part in enumerate(p for p in parts if p.get("functionCall")):
        function = part["functionCall"]
        meta = {k: v for k, v in part.items() if k != "functionCall"}
        calls.append(
            _normalized_tool_call(
                provider="gemini",
                ordinal=ordinal,
                name=function.get("name"),
                arguments=function.get("args", {}),
                wire_id=function.get("id"),
                provider_meta=meta,
            )
        )
    provider_finish = cand.get("finishReason")
    wire_state = {"gemini_content": raw_content}
    return {
        "content": text,
        "reasoning": None,
        "usage": body.get("usageMetadata", {}),
        "finish_reason": "tool_calls" if calls else provider_finish,
        "provider_finish_reason": provider_finish,
        "tool_calls": calls,
        "assistant_message": _assistant_message(text, calls, wire_state),
        "wire_state": wire_state,
        "raw": body,
    }
