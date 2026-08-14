"""OpenAI-compatible Chat Completions provider adapter."""

from __future__ import annotations

import os
from typing import Any

from ..messages import _openai_messages
from ..models import LLMError, TransportError, status_is_retryable
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
        "User-Agent": os.environ.get("OPENAI4S_LLM_USER_AGENT", _BROWSER_UA),
    }
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
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


# HTTP-equivalent statuses for error events delivered inside an otherwise
# successful SSE response.  This is deliberately an exact allowlist: provider
# messages are arbitrary prose and must never become classification input.
_STREAM_ERROR_STATUS = {
    "RequestBurstTooFast": 429,
    "ServerOverloaded": 429,
    "TooManyRequests": 429,
    "rate_limit": 429,
    "rate_limit_exceeded": 429,
    "too_many_requests": 429,
    "invalid_api_key": 401,
    "unauthorized": 401,
    "authentication_error": 401,
    "server_error": 500,
    "internal_server_error": 500,
    "service_unavailable": 503,
    "overloaded_error": 503,
}

# Some OpenAI-compatible endpoints support Chat Completions but reject the
# streaming-only request fields or SSE Accept header.  One blocking retry keeps
# that compatibility path without giving retryable capacity failures (or
# credential refusals) a fresh request budget.
_STREAM_COMPATIBILITY_FALLBACK_STATUS = frozenset({400, 404, 405, 406, 415, 422, 501})
_STREAM_AUTH_ERROR_CODES = frozenset(
    {"invalid_api_key", "unauthorized", "authentication_error"}
)


def _chat_openai_stream(url, payload, headers, cfg, on_delta, *, post_sse) -> dict:
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
        "output_committed": False,
    }

    def _discard_uncommitted_attempt() -> None:
        """Drop state an SSE attempt accumulated without publishing.

        The transport may replay a typed provider error.  Reasoning, usage and
        partial tool fragments are adapter-local until a terminal reply, so
        they are safe to discard; carrying them into the next attempt would
        corrupt its reply.  Visible content is never discarded or replayed.
        """

        if state["output_committed"]:
            return
        parts.clear()
        reasoning.clear()
        state["usage"] = {}
        state["finish"] = None
        state["terminal"] = False
        state["tool_calls"].clear()

    def _on_event(evt: dict) -> None:
        if evt.get("error") or evt.get("type") == "error":
            state["started"] = True
            error = evt.get("error")
            if isinstance(error, dict):
                detail = error.get("message") or str(error)
                raw_code = error.get("code")
                raw_type = error.get("type")
                provider_code = raw_code if type(raw_code) is str else None
                provider_type = raw_type if type(raw_type) is str else None
                # An SSE connection can answer HTTP 200 and carry the real
                # failure in an error event.  Infer an HTTP-equivalent status
                # only from this closed set of provider signals -- never from
                # message prose.  The raw code remains private diagnostic
                # evidence; public surfaces use models.llm_failure_code().
                status = next(
                    (
                        _STREAM_ERROR_STATUS[value]
                        for value in (provider_code, provider_type)
                        if value in _STREAM_ERROR_STATUS
                    ),
                    None,
                )
                if status is not None:
                    _discard_uncommitted_attempt()
                    raise TransportError(
                        f"OpenAI stream error: {detail}",
                        provider=cfg.provider,
                        operation="post_sse",
                        status=status,
                        error_code=provider_code or provider_type,
                        retryable=status_is_retryable(status),
                        output_committed=bool(state["output_committed"]),
                    )
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
            state["output_committed"] = True
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
    except TransportError as exc:
        # A typed HTTP/SSE failure already passed through the bounded transport
        # retry policy.  Treating it as "streaming unsupported" would start a
        # second blocking request with a fresh retry budget (3 SSE + 3 JSON).
        # Preserve the historical one-shot compatibility fallback only for
        # non-retryable protocol-shape refusals before any stream event.  Auth,
        # capacity and connection failures must never take this branch.
        if (
            not state["started"]
            and not exc.retryable
            and exc.status in _STREAM_COMPATIBILITY_FALLBACK_STATUS
            and exc.error_code not in _STREAM_AUTH_ERROR_CODES
        ):
            raise _StreamStartError() from exc
        raise
    except LLMError:
        # An untyped failure before the first semantic event may mean this
        # compatible endpoint simply does not implement SSE. Keep the historical
        # blocking fallback for that narrow case; typed transport failures above
        # have already exhausted their one retry budget.
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
