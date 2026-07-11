"""Shared execution rules for provider-native control-tool batches."""

from __future__ import annotations

from typing import Callable

from openai4s.tools import (
    MAX_TOOL_CALLS_PER_TURN,
    finalize_tool_batch,
    tool_validation_error,
)

from .actions import NativeToolBatch, NativeToolCall
from .models import ExecutionOutcome

ToolInvoker = Callable[[NativeToolCall], tuple[str, bool]]


def _never_cancelled() -> bool:
    return False


def execute_native_batch(
    batch: NativeToolBatch,
    invoke: ToolInvoker,
    *,
    limit: int = MAX_TOOL_CALLS_PER_TURN,
    cancelled: Callable[[], bool] = _never_cancelled,
) -> ExecutionOutcome:
    """Execute valid calls and return one canonical result for every call."""
    parts: list[str] = []
    history: list[dict] = []
    for index, call in enumerate(batch.calls):
        if cancelled():
            text = (
                f"[Tool error] {call.name or '<unnamed>'}: "
                "run was cancelled before execution"
            )
            ok = False
        elif index >= limit:
            text = (
                f"[Tool error] {call.name or '<unnamed>'}: call was not run; "
                f"the per-turn limit is {limit}"
            )
            ok = False
        elif call.parse_error is not None or call.arguments is None:
            detail = call.parse_error or "arguments are not a JSON object"
            text = f"[Tool error] {call.name or '<unnamed>'}: {detail}"
            ok = False
        else:
            validation_error = tool_validation_error(call.name, call.arguments)
            if validation_error is not None:
                text = validation_error
                ok = False
            else:
                try:
                    text, ok = invoke(call)
                except Exception as exc:  # noqa: BLE001 — close every protocol call
                    try:
                        detail = str(exc)
                    except Exception:  # noqa: BLE001
                        detail = type(exc).__name__
                    text = f"[Tool error] {call.name or '<unnamed>'}: {detail}"
                    ok = False
        parts.append(text)
        history.append(
            {
                "role": "tool",
                "tool_call_id": call.id,
                "wire_id": call.wire_id,
                "name": call.name,
                "content": text,
                "is_error": not ok,
            }
        )
    observation = finalize_tool_batch(parts, len(batch.calls), [])
    return ExecutionOutcome(tuple(history), observation=observation)


__all__ = ["ToolInvoker", "execute_native_batch"]
