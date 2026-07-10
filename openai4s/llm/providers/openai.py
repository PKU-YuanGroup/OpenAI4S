"""OpenAI-compatible Chat Completions provider adapter."""

from __future__ import annotations

import os
from typing import Any

from ..messages import _openai_messages
from ..models import LLMError
from ..tooling import _apply_chat_tools, _assistant_message, _normalized_tool_call
from ..transport import _BROWSER_UA


def _chat_openai(
    messages,
    cfg,
    base,
    model,
    max_tokens,
    temperature,
    stop,
    on_delta=None,
    *,
    post_json,
    post_sse,
    tools=None,
    tool_choice=None,
    parallel_tool_calls=None,
) -> dict:
    url = f"{base.rstrip('/')}/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": _openai_messages(messages),
        "max_tokens": max_tokens or cfg.max_tokens,
        "temperature": cfg.temperature if temperature is None else temperature,
    }
    if stop:
        payload["stop"] = stop
    # Some OpenAI-compatible proxies (e.g. apiany.org, behind Cloudflare) reject
    # urllib's default UA and expose reasoning models — allow env overrides.
    effort = os.environ.get("OPENAI4S_LLM_REASONING_EFFORT")
    if effort:
        payload["reasoning_effort"] = effort
    _apply_chat_tools(payload, tools or [], tool_choice, parallel_tool_calls)
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg.api_key}",
        "User-Agent": os.environ.get("OPENAI4S_LLM_USER_AGENT", _BROWSER_UA),
    }
    # Real token streaming: when a delta callback is supplied AND streaming isn't
    # explicitly disabled, POST with `stream:true` and forward each token to
    # on_delta as it arrives, so prose renders live instead of one blob per turn.
    # Falls back to the blocking path if the stream can't even start (some proxies
    # 4xx on `stream`), so a provider that refuses SSE still works.
    want_stream = on_delta is not None and os.environ.get(
        "OPENAI4S_LLM_STREAM", "1"
    ) not in ("0", "false", "no", "off")
    if want_stream:
        try:
            return _chat_openai_stream(
                url,
                dict(payload),
                headers,
                cfg,
                on_delta,
                post_sse=post_sse,
            )
        except _StreamStartError:
            pass  # SSE refused before any bytes — retry blocking below
    body = post_json(url, payload, headers, cfg.timeout_s)
    try:
        choice = body["choices"][0]
        msg = choice["message"]
    except (KeyError, IndexError) as e:
        raise LLMError(f"Unexpected OpenAI-wire response: {body}") from e
    content = msg.get("content") or ""
    calls: list[dict] = []
    for ordinal, raw_call in enumerate(msg.get("tool_calls") or ()):
        function = raw_call.get("function") or {}
        calls.append(
            _normalized_tool_call(
                provider="openai",
                ordinal=ordinal,
                name=function.get("name"),
                arguments=function.get("arguments", ""),
                wire_id=raw_call.get("id"),
                provider_meta={"type": raw_call.get("type", "function")},
            )
        )
    provider_finish = choice.get("finish_reason")
    wire_state = {"openai_message": msg}
    return {
        "content": content,
        "reasoning": msg.get("reasoning_content"),
        "usage": body.get("usage", {}),
        "finish_reason": "tool_calls" if calls else provider_finish,
        "provider_finish_reason": provider_finish,
        "tool_calls": calls,
        "assistant_message": _assistant_message(content, calls, wire_state),
        "wire_state": wire_state,
        "raw": body,
    }


class _StreamStartError(Exception):
    """The streaming request failed before yielding any data — safe to fall back
    to a blocking call (nothing was emitted to the client yet)."""


def _chat_openai_stream(
    url, payload, headers, cfg, on_delta, *, post_sse
) -> dict:
    payload["stream"] = True
    # Ask for a usage row on the terminal chunk (ignored by proxies that don't
    # grok it; harmless when unsupported).
    payload["stream_options"] = {"include_usage": True}
    headers = {**headers, "Accept": "text/event-stream"}
    parts: list[str] = []
    reasoning: list[str] = []
    state: dict[str, Any] = {
        "usage": {},
        "finish": None,
        "started": False,
        "terminal": False,
        "tool_calls": {},
    }

    def _on_event(evt: dict) -> None:
        if evt.get("error") or evt.get("type") == "error":
            state["started"] = True
            error = evt.get("error")
            if isinstance(error, dict):
                detail = error.get("message") or str(error)
            else:
                detail = error or evt.get("message") or str(evt)
            raise LLMError(f"OpenAI stream error: {detail}")
        if evt.get("usage"):
            state["started"] = True
            state["usage"] = evt["usage"]
        choices = evt.get("choices") or []
        if not choices:
            return
        state["started"] = True
        ch = choices[0]
        delta = ch.get("delta") or {}
        piece = delta.get("content")
        if piece:
            parts.append(piece)
            try:
                on_delta(piece)
            except Exception:  # noqa: BLE001 — a UI callback must never kill the stream
                pass
        rc = delta.get("reasoning_content") or delta.get("reasoning")
        if rc:
            reasoning.append(rc)
        for fragment in delta.get("tool_calls") or ():
            try:
                index = int(fragment.get("index", 0))
            except (TypeError, ValueError):
                index = len(state["tool_calls"])
            acc = state["tool_calls"].setdefault(
                index, {"id": None, "type": "function", "name": "", "arguments": []}
            )
            if fragment.get("id"):
                acc["id"] = fragment["id"]
            if fragment.get("type"):
                acc["type"] = fragment["type"]
            function = fragment.get("function") or {}
            if function.get("name"):
                acc["name"] = function["name"]
            if function.get("arguments"):
                acc["arguments"].append(function["arguments"])
        if ch.get("finish_reason"):
            state["finish"] = ch["finish_reason"]
            state["terminal"] = True

    timeout = max(cfg.timeout_s, 60.0)
    try:
        post_sse(url, payload, headers, timeout, _on_event)
    except LLMError:
        # Connection/HTTP error. If we already streamed tokens, surfacing a hard
        # error would duplicate/contradict what the user saw — but nothing was
        # committed downstream yet, so re-raise as a start error only when we
        # never emitted, else propagate.
        if not state["started"]:
            raise _StreamStartError()
        raise
    if not state["terminal"]:
        if not state["started"]:
            raise _StreamStartError()
        raise LLMError("OpenAI stream ended before a terminal finish_reason")
    content = "".join(parts)
    calls: list[dict] = []
    openai_calls: list[dict] = []
    for ordinal, index in enumerate(sorted(state["tool_calls"])):
        acc = state["tool_calls"][index]
        raw_arguments = "".join(acc["arguments"])
        calls.append(
            _normalized_tool_call(
                provider="openai",
                ordinal=ordinal,
                name=acc["name"],
                arguments=raw_arguments,
                wire_id=acc["id"],
                provider_meta={"type": acc["type"], "index": index},
            )
        )
        openai_calls.append(
            {
                "id": acc["id"],
                "type": acc["type"],
                "function": {"name": acc["name"], "arguments": raw_arguments},
            }
        )
    provider_finish = state["finish"] or "stop"
    wire_state = {
        "openai_message": {
            "role": "assistant",
            "content": content or None,
            "tool_calls": openai_calls,
        }
    }
    return {
        "content": content,
        "reasoning": "".join(reasoning) or None,
        "usage": state["usage"],
        "finish_reason": "tool_calls" if calls else provider_finish,
        "provider_finish_reason": provider_finish,
        "tool_calls": calls,
        "assistant_message": _assistant_message(content, calls, wire_state),
        "wire_state": wire_state,
        "raw": None,
    }
