"""Provider-neutral native-tool schemas and call normalization."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .models import LLMError


def _tool_attr(tool: Any, name: str, default: Any = None) -> Any:
    if isinstance(tool, dict):
        return tool.get(name, default)
    return getattr(tool, name, default)


def _canonical_tool_specs(tools: list[Any] | tuple[Any, ...] | None) -> list[dict]:
    """Return provider-neutral native function declarations.

    The public ``chat`` facade accepts either ``tools.native.ToolSpec`` values
    or equivalent dictionaries.  Keeping this coercion here prevents provider
    adapters from importing the tool registry (and keeps the LLM layer usable
    by classifier/compaction calls with no agent dependencies).
    """
    out: list[dict] = []
    for tool in tools or ():
        source = tool
        if isinstance(tool, dict) and isinstance(tool.get("function"), dict):
            # OpenAI-compatible declarations are accepted at the public facade
            # for backward compatibility, then normalized before wire routing.
            source = tool["function"]
        name = _tool_attr(source, "name")
        description = _tool_attr(source, "description", "")
        schema = _tool_attr(source, "input_schema")
        if schema is None:
            schema = _tool_attr(source, "parameters", {})
        if not isinstance(name, str) or not name:
            raise LLMError("native tool is missing a non-empty name")
        if not isinstance(schema, dict):
            raise LLMError(f"native tool {name!r} has a non-object input schema")
        schema = dict(schema)
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        out.append(
            {
                "name": name,
                "description": str(description or ""),
                "input_schema": schema,
                "strict": bool(_tool_attr(source, "strict", False)),
            }
        )
    return out


def _parse_tool_arguments(value: Any) -> tuple[str, dict | None, str | None]:
    """Preserve exact arguments while exposing a validated object view."""
    if isinstance(value, str):
        raw = value
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError) as e:
            return raw, None, f"invalid JSON arguments: {e}"
    else:
        try:
            raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as e:
            return str(value), None, f"arguments are not JSON serializable: {e}"
        decoded = value
    if not isinstance(decoded, dict):
        return raw, None, "tool arguments must decode to a JSON object"
    return raw, decoded, None


def _normalized_tool_call(
    *,
    provider: str,
    ordinal: int,
    name: Any,
    arguments: Any,
    wire_id: Any = None,
    provider_meta: dict | None = None,
) -> dict:
    raw, parsed, parse_error = _parse_tool_arguments(arguments)
    wire = wire_id if isinstance(wire_id, str) and wire_id else None
    if wire:
        canonical_id = wire
    else:
        identity = f"{provider}\0{ordinal}\0{name}\0{raw}".encode(
            "utf-8", "surrogatepass"
        )
        canonical_id = f"local-{provider}-{hashlib.sha256(identity).hexdigest()[:16]}"
    if not isinstance(name, str) or not name:
        parse_error = parse_error or "tool call is missing a non-empty name"
        name = ""
    return {
        "id": canonical_id,
        "wire_id": wire,
        "name": name,
        "ordinal": ordinal,
        "raw_arguments": raw,
        "arguments": parsed,
        "parse_error": parse_error,
        "provider_meta": dict(provider_meta or {}),
    }


def _tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(content)


def _tool_result_object(content: Any, *, is_error: bool = False) -> dict:
    if is_error:
        return {"error": _tool_result_text(content)}
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            decoded = json.loads(content)
        except (TypeError, ValueError):
            decoded = None
        if isinstance(decoded, dict):
            return decoded
    return {"output": _tool_result_text(content)}


def _assistant_message(content: str, tool_calls: list[dict], wire_state: dict) -> dict:
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": tool_calls,
        "wire_state": wire_state,
    }


def _history_tool_call(call: dict) -> dict:
    """Accept canonical calls and the legacy OpenAI-compatible history shape."""
    function = call.get("function")
    if isinstance(function, dict):
        raw, arguments, parse_error = _parse_tool_arguments(
            function.get("arguments", "")
        )
        return {
            "id": call.get("id"),
            "wire_id": call.get("id"),
            "name": function.get("name", ""),
            "raw_arguments": raw,
            "arguments": arguments,
            "parse_error": parse_error,
            "provider_meta": {"type": call.get("type", "function")},
        }
    raw = call.get("raw_arguments")
    if not isinstance(raw, str):
        raw = _parse_tool_arguments(call.get("arguments", {}))[0]
    return {
        "id": call.get("id"),
        "wire_id": call.get("wire_id"),
        "name": call.get("name", ""),
        "raw_arguments": raw,
        "arguments": call.get("arguments"),
        "parse_error": call.get("parse_error"),
        "provider_meta": call.get("provider_meta") or {},
    }


def _chat_tool_schema(spec: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": spec["name"],
            "description": spec["description"],
            "parameters": spec["input_schema"],
            "strict": spec["strict"],
        },
    }


def _anthropic_tool_schema(spec: dict) -> dict:
    return {
        "name": spec["name"],
        "description": spec["description"],
        "input_schema": spec["input_schema"],
        "strict": spec["strict"],
    }


def _gemini_tool_schema(spec: dict) -> dict:
    return {
        "name": spec["name"],
        "description": spec["description"],
        "parametersJsonSchema": spec["input_schema"],
    }


def _responses_tool_schema(spec: dict) -> dict:
    return {
        "type": "function",
        "name": spec["name"],
        "description": spec["description"],
        "parameters": spec["input_schema"],
        "strict": spec["strict"],
    }


def _named_tool_choice(tool_choice: Any) -> str | None:
    if isinstance(tool_choice, dict):
        name = tool_choice.get("name")
        if isinstance(name, str) and name:
            return name
    if isinstance(tool_choice, str) and tool_choice not in (
        "auto",
        "none",
        "required",
        "any",
    ):
        return tool_choice
    return None


def _apply_chat_tools(
    payload: dict,
    specs: list[dict],
    tool_choice: Any,
    parallel_tool_calls: bool | None,
) -> None:
    if not specs:
        return
    payload["tools"] = [_chat_tool_schema(spec) for spec in specs]
    named = _named_tool_choice(tool_choice)
    if named:
        payload["tool_choice"] = {"type": "function", "function": {"name": named}}
    elif tool_choice in ("auto", "none", "required"):
        payload["tool_choice"] = tool_choice
    if parallel_tool_calls is not None:
        payload["parallel_tool_calls"] = bool(parallel_tool_calls)


def _apply_anthropic_tools(
    payload: dict,
    specs: list[dict],
    tool_choice: Any,
    parallel_tool_calls: bool | None,
) -> None:
    if not specs:
        return
    payload["tools"] = [_anthropic_tool_schema(spec) for spec in specs]
    named = _named_tool_choice(tool_choice)
    if named:
        choice: dict[str, Any] = {"type": "tool", "name": named}
    elif tool_choice in ("required", "any"):
        choice = {"type": "any"}
    elif tool_choice == "none":
        choice = {"type": "none"}
    else:
        choice = {"type": "auto"}
    if parallel_tool_calls is not None:
        choice["disable_parallel_tool_use"] = not parallel_tool_calls
    if tool_choice is not None or parallel_tool_calls is not None:
        payload["tool_choice"] = choice


def _apply_gemini_tools(
    payload: dict,
    specs: list[dict],
    tool_choice: Any,
    parallel_tool_calls: bool | None,
) -> None:
    del parallel_tool_calls  # generateContent has no equivalent control
    if not specs:
        return
    payload["tools"] = [
        {"functionDeclarations": [_gemini_tool_schema(spec) for spec in specs]}
    ]
    named = _named_tool_choice(tool_choice)
    if named:
        config = {"mode": "ANY", "allowedFunctionNames": [named]}
    elif tool_choice in ("required", "any"):
        config = {"mode": "ANY"}
    elif tool_choice == "none":
        config = {"mode": "NONE"}
    else:
        config = {"mode": "AUTO"}
    if tool_choice is not None:
        payload["toolConfig"] = {"functionCallingConfig": config}


def _apply_responses_tools(
    payload: dict,
    specs: list[dict],
    tool_choice: Any,
    parallel_tool_calls: bool | None,
) -> None:
    if not specs:
        return
    payload["tools"] = [_responses_tool_schema(spec) for spec in specs]
    named = _named_tool_choice(tool_choice)
    if named:
        payload["tool_choice"] = {"type": "function", "name": named}
    elif tool_choice in ("auto", "none", "required"):
        payload["tool_choice"] = tool_choice
    if parallel_tool_calls is not None:
        payload["parallel_tool_calls"] = bool(parallel_tool_calls)
