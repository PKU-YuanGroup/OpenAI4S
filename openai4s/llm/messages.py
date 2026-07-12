"""Content conversion, native-tool schemas, and history normalization."""

from __future__ import annotations

from typing import Any

from .models import LLMError
from .tooling import _history_tool_call, _tool_result_object, _tool_result_text


def _is_parts(content: Any) -> bool:
    return isinstance(content, list)


def _to_openai_content(content: Any) -> Any:
    if not _is_parts(content):
        return content
    out: list[dict] = []
    for p in content:
        if p.get("type") == "text":
            out.append({"type": "text", "text": p.get("text", "")})
        elif p.get("type") == "image":
            if p.get("url"):
                url = p["url"]
            else:
                url = f"data:{p.get('mime', 'image/png')};base64,{p.get('data', '')}"
            out.append({"type": "image_url", "image_url": {"url": url}})
    return out


def _to_anthropic_content(content: Any) -> Any:
    if not _is_parts(content):
        return content
    out: list[dict] = []
    for p in content:
        if p.get("type") == "text":
            out.append({"type": "text", "text": p.get("text", "")})
        elif p.get("type") == "image":
            if p.get("url"):
                src = {"type": "url", "url": p["url"]}
            else:
                src = {
                    "type": "base64",
                    "media_type": p.get("mime", "image/png"),
                    "data": p.get("data", ""),
                }
            out.append({"type": "image", "source": src})
    return out


def _to_gemini_parts(content: Any) -> list[dict]:
    if not _is_parts(content):
        return [{"text": str(content)}]
    out: list[dict] = []
    for p in content:
        if p.get("type") == "text":
            out.append({"text": p.get("text", "")})
        elif p.get("type") == "image":
            if p.get("url"):
                out.append({"file_data": {"file_uri": p["url"]}})
            else:
                out.append(
                    {
                        "inline_data": {
                            "mime_type": p.get("mime", "image/png"),
                            "data": p.get("data", ""),
                        }
                    }
                )
    return out


def _openai_messages(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for message in messages:
        role = message.get("role")
        if role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": message.get("wire_id")
                    or message.get("tool_call_id"),
                    "content": _tool_result_text(message.get("content")),
                }
            )
            continue
        item = {
            "role": role,
            "content": _to_openai_content(message.get("content")),
        }
        if role == "assistant" and message.get("tool_calls"):
            wire_calls: list[dict] = []
            for original_call in message["tool_calls"]:
                call = _history_tool_call(original_call)
                raw = call["raw_arguments"]
                wire_calls.append(
                    {
                        "id": call.get("wire_id") or call.get("id"),
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": raw,
                        },
                    }
                )
            item["tool_calls"] = wire_calls
        out.append(item)
    return out


def _anthropic_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    system_parts: list[str] = []
    conv: list[dict] = []
    pending_results: list[dict] = []

    def flush_results() -> None:
        if pending_results:
            conv.append({"role": "user", "content": list(pending_results)})
            pending_results.clear()

    for message in messages:
        role = message.get("role")
        if role == "system":
            content = message.get("content")
            if isinstance(content, str) and content:
                system_parts.append(content)
            continue
        if role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": message.get("wire_id") or message.get("tool_call_id"),
                "content": _tool_result_text(message.get("content")),
            }
            if message.get("is_error"):
                block["is_error"] = True
            pending_results.append(block)
            continue

        flush_results()
        if role == "assistant":
            wire_state = message.get("wire_state") or {}
            raw_blocks = wire_state.get("anthropic_content")
            if isinstance(raw_blocks, list):
                content = raw_blocks
            else:
                content = []
                text = message.get("content")
                if isinstance(text, str) and text:
                    content.append({"type": "text", "text": text})
                for call in message.get("tool_calls") or ():
                    call = _history_tool_call(call)
                    content.append(
                        {
                            "type": "tool_use",
                            "id": call.get("wire_id") or call.get("id"),
                            "name": call["name"],
                            "input": call["arguments"] or {},
                        }
                    )
            conv.append({"role": "assistant", "content": content})
        else:
            conv.append(
                {
                    "role": "user",
                    "content": _to_anthropic_content(message.get("content")),
                }
            )
    flush_results()
    return "\n\n".join(system_parts), conv


def _gemini_contents(messages: list[dict]) -> tuple[str, list[dict]]:
    system_parts: list[str] = []
    contents: list[dict] = []
    pending_results: list[dict] = []
    known_calls: dict[str, tuple[str, str | None]] = {}

    def flush_results() -> None:
        if pending_results:
            contents.append({"role": "user", "parts": list(pending_results)})
            pending_results.clear()

    for message in messages:
        role = message.get("role")
        if role == "system":
            content = message.get("content")
            if isinstance(content, str) and content:
                system_parts.append(content)
            continue
        if role == "tool":
            value = message.get("content")
            call_id = message.get("tool_call_id")
            known_name, known_wire = known_calls.get(str(call_id), ("", None))
            response = _tool_result_object(
                value, is_error=bool(message.get("is_error"))
            )
            function_response = {
                "name": message.get("name") or known_name,
                "response": response,
            }
            wire_id = message.get("wire_id") or known_wire
            if wire_id:
                function_response["id"] = wire_id
            pending_results.append({"functionResponse": function_response})
            continue

        flush_results()
        if role == "assistant":
            normalized_calls = [
                _history_tool_call(call) for call in message.get("tool_calls") or ()
            ]
            for call in normalized_calls:
                canonical_id = call.get("id")
                if canonical_id:
                    known_calls[str(canonical_id)] = (
                        call.get("name", ""),
                        call.get("wire_id"),
                    )
            wire_state = message.get("wire_state") or {}
            raw_content = wire_state.get("gemini_content")
            if isinstance(raw_content, dict) and isinstance(
                raw_content.get("parts"), list
            ):
                contents.append(raw_content)
                continue
            parts: list[dict] = []
            text = message.get("content")
            if isinstance(text, str) and text:
                parts.append({"text": text})
            for call in normalized_calls:
                function_call = {
                    "name": call.get("name", ""),
                    "args": call.get("arguments") or {},
                }
                if call.get("wire_id"):
                    function_call["id"] = call["wire_id"]
                meta = call.get("provider_meta") or {}
                # Opaque Gemini Part metadata (notably thoughtSignature) must
                # remain on the exact part where the provider emitted it.
                part = {k: v for k, v in meta.items() if k != "functionCall"}
                part["functionCall"] = function_call
                parts.append(part)
            contents.append({"role": "model", "parts": parts})
        else:
            contents.append(
                {"role": "user", "parts": _to_gemini_parts(message.get("content"))}
            )
    flush_results()
    return "\n\n".join(system_parts), contents


def _responses_input(messages: list[dict]) -> tuple[str, list[dict]]:
    instructions: list[str] = []
    items: list[dict] = []
    for message in messages:
        role = message.get("role")
        text = _flatten_text(message.get("content"))
        if role == "system":
            if text:
                instructions.append(text)
            continue
        if role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.get("wire_id") or message.get("tool_call_id"),
                    "output": _tool_result_text(message.get("content")),
                }
            )
            continue
        if role == "assistant":
            wire_state = message.get("wire_state") or {}
            raw_output = wire_state.get("responses_output")
            if isinstance(raw_output, list):
                items.extend(raw_output)
                continue
            calls = message.get("tool_calls") or ()
            if calls:
                if text:
                    items.append(
                        {
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": text}],
                        }
                    )
                for original_call in calls:
                    call = _history_tool_call(original_call)
                    call_id = call.get("wire_id") or call.get("id")
                    if not call_id:
                        raise LLMError(
                            "Responses tool-call history is missing a call_id"
                        )
                    item = {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": call["name"],
                        "arguments": call["raw_arguments"],
                    }
                    item_id = (call.get("provider_meta") or {}).get("item_id")
                    if item_id:
                        item["id"] = item_id
                    items.append(item)
                continue
        ptype = "output_text" if role == "assistant" else "input_text"
        items.append({"role": role, "content": [{"type": ptype, "text": text}]})
    return "\n\n".join(instructions), items


def _flatten_text(content: Any) -> str:
    """Collapse a string-or-parts message content into plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            p.get("text", "")
            for p in content
            if isinstance(p, dict)
            and p.get("type") in ("text", "input_text", "output_text")
        )
    return str(content)
