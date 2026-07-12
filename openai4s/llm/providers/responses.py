"""OpenAI Responses provider adapter."""

from __future__ import annotations

import os
from typing import Any

from ..messages import _responses_input
from ..models import LLMError
from ..tooling import _apply_responses_tools, _assistant_message, _normalized_tool_call
from ..transport import _BROWSER_UA


def _chat_responses(
    messages,
    cfg,
    base,
    model,
    max_tokens,
    temperature,
    stop,
    on_delta=None,
    *,
    post_sse,
    tools=None,
    tool_choice=None,
    parallel_tool_calls=None,
) -> dict:
    """OpenAI Responses API (Codex `wire_api = responses`). Streams SSE:
    text arrives as `response.output_text.delta` events; usage on
    `response.completed`. System messages become `instructions`."""
    url = f"{base.rstrip('/')}/responses"
    instructions, input_items = _responses_input(messages)
    if not input_items:
        input_items.append(
            {"role": "user", "content": [{"type": "input_text", "text": ""}]}
        )
    effort = os.environ.get("OPENAI4S_LLM_REASONING_EFFORT") or "high"
    payload: dict[str, Any] = {
        "model": model,
        "input": input_items,
        "reasoning": {"effort": effort},
        "include": ["reasoning.encrypted_content"],
        "store": False,
        "stream": True,
    }
    # NB: this proxy rejects `max_output_tokens` — leave it off.
    if instructions:
        payload["instructions"] = instructions
    _apply_responses_tools(payload, tools or [], tool_choice, parallel_tool_calls)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": os.environ.get("OPENAI4S_LLM_USER_AGENT", _BROWSER_UA),
        "Accept": "text/event-stream",
    }
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    text_parts: list[str] = []
    state: dict[str, Any] = {
        "usage": {},
        "error": None,
        "output": {},
        "completed_output": None,
        "response": None,
        "terminal": False,
    }

    def remember_item(index: Any, item: dict) -> None:
        try:
            key = int(index)
        except (TypeError, ValueError):
            key = len(state["output"])
        state["output"][key] = item

    def _on_event(evt: dict) -> None:
        t = evt.get("type")
        if t == "response.output_text.delta":
            d = evt.get("delta") or ""
            if d:
                text_parts.append(d)
                if on_delta:
                    try:
                        on_delta(d)
                    except (
                        Exception
                    ):  # noqa: BLE001 - a UI callback must not kill the stream
                        pass
        elif t in ("response.output_item.added", "response.output_item.done"):
            item = evt.get("item") or {}
            remember_item(evt.get("output_index"), item)
            if item.get("type") == "message" and not text_parts:
                for part in item.get("content") or []:
                    if part.get("text"):
                        text_parts.append(part["text"])
        elif t == "response.function_call_arguments.delta":
            index = evt.get("output_index")
            try:
                key = int(index)
            except (TypeError, ValueError):
                key = len(state["output"])
            item = state["output"].setdefault(
                key,
                {
                    "type": "function_call",
                    "id": evt.get("item_id"),
                    "call_id": evt.get("call_id"),
                    "name": evt.get("name", ""),
                    "arguments": "",
                },
            )
            item["arguments"] = (item.get("arguments") or "") + (evt.get("delta") or "")
        elif t == "response.function_call_arguments.done":
            index = evt.get("output_index")
            try:
                key = int(index)
            except (TypeError, ValueError):
                key = len(state["output"])
            item = state["output"].setdefault(key, {"type": "function_call"})
            for field, source in (
                ("id", "item_id"),
                ("call_id", "call_id"),
                ("name", "name"),
                ("arguments", "arguments"),
            ):
                if evt.get(source) is not None:
                    item[field] = evt[source]
        elif t == "response.completed":
            response = evt.get("response") or {}
            state["response"] = response
            state["terminal"] = True
            if isinstance(response.get("output"), list):
                state["completed_output"] = response["output"]
            u = response.get("usage") or {}
            # Preserve the native detail objects here; the provider-neutral
            # client maps them after the wire call.  Flattening at this layer
            # would discard cached/reasoning counters before normalization.
            state["usage"] = dict(u)
        elif t == "response.incomplete":
            response = evt.get("response") or {}
            details = response.get("incomplete_details") or {}
            reason = details.get("reason") or "unknown reason"
            state["error"] = f"response incomplete: {reason}"
        elif t in ("response.failed", "response.error", "error"):
            resp = evt.get("response") or evt
            err = (resp.get("error") or {}) if isinstance(resp, dict) else {}
            # a flat `error` event carries `message` at the top level, while
            # response.failed nests it under response.error
            state["error"] = err.get("message") or evt.get("message") or str(evt)[:400]

    # Idle (no-bytes) timeout for the stream. Respect the configured timeout so a
    # stalled/hung model finalises the turn promptly instead of "running forever";
    # keep a 60s floor so a heavy-reasoning model that pauses between events isn't
    # cut off (raise OPENAI4S_LLM_TIMEOUT for such models).
    timeout = max(cfg.timeout_s, 60.0)
    post_sse(url, payload, headers, timeout, _on_event)
    if state["error"]:
        raise LLMError(f"responses API error: {state['error']}")
    if not state["terminal"]:
        raise LLMError("Responses stream ended before response.completed")
    output = state["completed_output"]
    if not isinstance(output, list):
        output = [state["output"][i] for i in sorted(state["output"])]
    if not text_parts:
        for item in output:
            if item.get("type") != "message":
                continue
            for part in item.get("content") or ():
                if part.get("text"):
                    text_parts.append(part["text"])
    calls: list[dict] = []
    for ordinal, item in enumerate(
        item for item in output if item.get("type") == "function_call"
    ):
        provider_meta = {
            key: value
            for key, value in item.items()
            if key not in ("type", "call_id", "name", "arguments", "id")
        }
        if item.get("id") is not None:
            provider_meta["item_id"] = item["id"]
        calls.append(
            _normalized_tool_call(
                provider="responses",
                ordinal=ordinal,
                name=item.get("name"),
                arguments=item.get("arguments", ""),
                wire_id=item.get("call_id"),
                provider_meta=provider_meta,
            )
        )
    content = "".join(text_parts)
    response = state["response"] or {}
    provider_finish = response.get("status") or "completed"
    wire_state = {"responses_output": output}
    return {
        "content": content,
        "reasoning": None,
        "usage": state["usage"],
        "finish_reason": "tool_calls" if calls else "stop",
        "provider_finish_reason": provider_finish,
        "tool_calls": calls,
        "assistant_message": _assistant_message(content, calls, wire_state),
        "wire_state": wire_state,
        "raw": None,
    }
