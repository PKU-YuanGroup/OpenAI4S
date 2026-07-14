"""Engine-owned structured finalization for non-scientific turns.

``finalize_response`` is deliberately not a control-plane ``Tool`` and is
never registered in :mod:`openai4s.tools.registry`.  It is a terminal action
understood by the agent engine: providers receive a metadata-only ``ToolSpec``
and the Host validates the same closed schema again before accepting the
completion.

Scientific execution keeps its existing, stronger cell-completion contract:
``host.submit_output(...)`` remains the only completion signal emitted from a
Python Code-as-Action cell.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Iterable, Mapping, TypedDict

from openai4s.host.completion import validate_completion_bullets
from openai4s.tools import ToolSpec, finalize_tool_batch, validate_json_schema

from .actions import FINALIZE_RESPONSE_NAME, FinalizeAction, NativeToolCall
from .models import ExecutionOutcome


class CompletionRecord(TypedDict):
    """The completion payload shared with ``host.submit_output`` consumers."""

    output: dict[str, Any]
    completion_bullets: list[str]


_TEXT_ITEM = {"type": "string", "minLength": 1, "maxLength": 2_000}
_FINALIZE_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "minLength": 1,
            "maxLength": 4_000,
            "description": "A concise answer grounded in work that actually completed.",
        },
        "findings": {
            "type": "array",
            "items": _TEXT_ITEM,
            "maxItems": 50,
            "description": "Optional evidence-backed findings.",
        },
        "metrics": {
            "type": "object",
            "additionalProperties": {"type": "number"},
            "description": "Optional finite numeric metrics keyed by stable names.",
        },
        "artifacts": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 1_000},
            "maxItems": 100,
            "description": "Optional artifact IDs, version IDs, or workspace paths.",
        },
        "limitations": {
            "type": "array",
            "items": _TEXT_ITEM,
            "maxItems": 50,
            "description": "Optional limitations or unresolved uncertainty.",
        },
        "next_steps": {
            "type": "array",
            "items": _TEXT_ITEM,
            "maxItems": 20,
            "description": "Optional concrete follow-up steps.",
        },
        "completion_bullets": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 500},
            "minItems": 1,
            "maxItems": 4,
            "description": "One to four completed-action phrases.",
        },
    },
    "required": ["summary", "completion_bullets"],
    "additionalProperties": False,
}


def finalize_response_schema() -> dict[str, Any]:
    """Return an isolated copy of the Host-enforced completion schema."""

    return copy.deepcopy(_FINALIZE_RESPONSE_SCHEMA)


def finalize_response_tool_spec() -> ToolSpec:
    """Return the provider-neutral terminal declaration.

    The schema is closed and is strictly revalidated by the Host.  Wire-level
    ``strict`` stays false because the portable strict subset requires every
    declared property to be required, which would make the structured fields
    that are intentionally optional impossible to omit.
    """

    return ToolSpec(
        name=FINALIZE_RESPONSE_NAME,
        description=(
            "Finish the current response with a structured, evidence-grounded "
            "completion. Call this only when it is the sole tool call in the "
            "assistant turn. It does not replace host.submit_output for a "
            "Python scientific cell."
        ),
        input_schema=finalize_response_schema(),
        strict=False,
    )


def with_finalize_response(tools: Iterable[Any]) -> tuple[Any, ...]:
    """Append the engine terminal declaration to a provider tool catalogue."""

    values = tuple(tools)
    names = {_tool_spec_name(tool) for tool in values}
    if FINALIZE_RESPONSE_NAME in names:
        raise ValueError(
            "finalize_response is engine-owned and cannot be supplied by a tool registry"
        )
    return (*values, finalize_response_tool_spec())


def _tool_spec_name(tool: Any) -> str:
    if not isinstance(tool, Mapping):
        return str(getattr(tool, "name", "") or "")
    function = tool.get("function")
    source = function if isinstance(function, Mapping) else tool
    return str(source.get("name") or "")


def validate_finalize_arguments(arguments: Any) -> str | None:
    """Host-side validation for one provider-originated terminal payload."""

    issues = validate_json_schema(
        arguments,
        _FINALIZE_RESPONSE_SCHEMA,
        unknown_properties="forbid",
    )
    if issues:
        return "invalid arguments: " + "; ".join(str(issue) for issue in issues)
    # Preserve parity with the in-kernel completion contract: JSON Schema can
    # express the cardinality and non-empty strings, while this semantic guard
    # verifies that the bullets describe completed work.
    bullet_error = validate_completion_bullets(arguments["completion_bullets"])
    return str(bullet_error) if bullet_error is not None else None


def _completion_record(arguments: Mapping[str, Any]) -> CompletionRecord:
    """Build the existing renderer-compatible completion envelope."""

    payload = copy.deepcopy(dict(arguments))
    bullets = list(payload.pop("completion_bullets"))
    return {
        "output": payload,
        "completion_bullets": bullets,
    }


def execute_finalize_action(
    action: FinalizeAction,
    *,
    refusal: str | None = None,
    stop_reason: str | None = None,
) -> ExecutionOutcome:
    """Close the provider call, then optionally accept structured completion.

    Even malformed, cancelled, or plan-mode declarations produce exactly one
    canonical provider tool result.  A validation failure is observable but is
    never a completion signal, allowing the model to repair it next turn.
    """

    call = action.call
    error = refusal or _call_error(call)
    record: CompletionRecord | None = None
    if error is None:
        error = validate_finalize_arguments(call.arguments)
    if error is None:
        assert call.arguments is not None
        record = _completion_record(call.arguments)
        text = json.dumps(
            {"status": "accepted", "action": FINALIZE_RESPONSE_NAME},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    else:
        text = f"[Tool error] {FINALIZE_RESPONSE_NAME}: {error}"

    result = {
        "role": "tool",
        "tool_call_id": call.id,
        "wire_id": call.wire_id,
        "name": FINALIZE_RESPONSE_NAME,
        "content": text,
        "is_error": error is not None,
    }
    return ExecutionOutcome(
        (result,),
        observation=finalize_tool_batch([text], 1, []),
        completion=record,
        stop_reason=stop_reason,
    )


def _call_error(call: NativeToolCall) -> str | None:
    if call.name != FINALIZE_RESPONSE_NAME:
        return f"unexpected terminal action name {call.name!r}"
    if call.parse_error is not None:
        return call.parse_error
    if call.arguments is None:
        return "arguments are not a JSON object"
    return None


__all__ = [
    "CompletionRecord",
    "execute_finalize_action",
    "finalize_response_schema",
    "finalize_response_tool_spec",
    "validate_finalize_arguments",
    "with_finalize_response",
]
