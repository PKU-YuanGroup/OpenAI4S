"""Shared execution rules for provider-native control-tool batches."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from openai4s.tools import (
    MAX_TOOL_CALLS_PER_TURN,
    finalize_tool_batch,
    get_tool,
    tool_validation_error,
)

from .actions import NativeToolBatch, NativeToolCall
from .models import ExecutionOutcome

ToolInvoker = Callable[[NativeToolCall], tuple[str, bool]]
ToolValidator = Callable[[str, object], str | None]
ToolParallelPolicy = Callable[[NativeToolCall], tuple[bool, tuple[str, ...]] | None]
GroupPreparation = Callable[[], None]

_MAX_PARALLEL_READS = 8


def _never_cancelled() -> bool:
    return False


def execute_native_batch(
    batch: NativeToolBatch,
    invoke: ToolInvoker,
    *,
    limit: int = MAX_TOOL_CALLS_PER_TURN,
    cancelled: Callable[[], bool] = _never_cancelled,
    validate: ToolValidator = tool_validation_error,
    parallel_policy: ToolParallelPolicy | None = None,
    max_parallel_reads: int = _MAX_PARALLEL_READS,
    prepare_group: GroupPreparation | None = None,
) -> ExecutionOutcome:
    """Execute a batch and return one canonical result for every declaration.

    Calls remain sequential unless ``parallel_policy`` positively identifies a
    contiguous leading lane as read-only and gives it non-conflicting resource
    keys.  The first mutating/unknown call is a barrier for the rest of the
    batch, which keeps environment/session mutations ordered.  Parallel results
    are always written back by the provider's original ordinal.
    """
    results: list[tuple[str, bool] | None] = [None] * len(batch.calls)
    scheduling: list[tuple[bool, tuple[str, ...]] | None] = [None] * len(batch.calls)
    for index, call in enumerate(batch.calls):
        if index >= limit:
            results[index] = (
                f"[Tool error] {call.name or '<unnamed>'}: call was not run; "
                f"the per-turn limit is {limit}",
                False,
            )
        elif call.parse_error is not None or call.arguments is None:
            detail = call.parse_error or "arguments are not a JSON object"
            results[index] = (
                f"[Tool error] {call.name or '<unnamed>'}: {detail}",
                False,
            )
        else:
            validation_error = validate(call.name, call.arguments)
            if validation_error is not None:
                results[index] = (validation_error, False)
            elif parallel_policy is not None:
                try:
                    scheduling[index] = parallel_policy(call)
                except Exception:  # noqa: BLE001 - metadata failure is a barrier
                    scheduling[index] = None

    # Parallelism is deliberately limited to the prefix before the first
    # mutating or unclassified call. A mutating tool can change environment,
    # permissions, catalogs, or dispatcher state needed by every later call.
    cursor = 0
    while cursor < len(batch.calls):
        if results[cursor] is not None:
            cursor += 1
            continue
        policy = scheduling[cursor]
        if policy is None or not policy[0]:
            break
        cursor += 1
    parallel_indices = [
        index
        for index in range(cursor)
        if results[index] is None
        and scheduling[index] is not None
        and scheduling[index][0]
    ]
    _execute_read_only_waves(
        parallel_indices,
        batch.calls,
        scheduling,
        results,
        invoke,
        cancelled,
        max_workers=max_parallel_reads,
        prepare_group=prepare_group,
    )

    # Everything after the safety barrier is strictly ordered. Preflight
    # failures remain in place and do not suppress canonical results later.
    for index in range(cursor, len(batch.calls)):
        if results[index] is not None:
            continue
        call = batch.calls[index]
        if cancelled():
            results[index] = (_cancelled_text(call), False)
        elif (preparation_error := _prepare_error(prepare_group)) is not None:
            results[index] = (
                f"[Tool error] {call.name or '<unnamed>'}: {preparation_error}",
                False,
            )
        else:
            results[index] = _safe_invoke(call, invoke)

    # A cancellation between preflight and an empty/finished wave must still
    # close every declaration. This is also a defensive totality guard.
    for index, result in enumerate(results):
        if result is None:
            call = batch.calls[index]
            results[index] = (
                _cancelled_text(call)
                if cancelled()
                else f"[Tool error] {call.name or '<unnamed>'}: call was not run",
                False,
            )

    parts: list[str] = []
    history: list[dict] = []
    for call, result in zip(batch.calls, results):
        assert result is not None
        text, ok = result
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


def tool_parallel_policy(
    call: NativeToolCall,
    catalog: Any = None,
) -> tuple[bool, tuple[str, ...]] | None:
    """Resolve class-declared scheduling metadata for one native call."""

    resolver = get_tool if catalog is None else getattr(catalog, "get", None)
    if not callable(resolver):
        return None
    tool = resolver(call.name)
    if tool is None:
        return None
    return bool(tool.read_only), tuple(tool.resource_keys(call.arguments or {}))


def _execute_read_only_waves(
    indices: list[int],
    calls: tuple[NativeToolCall, ...],
    scheduling: list[tuple[bool, tuple[str, ...]] | None],
    results: list[tuple[str, bool] | None],
    invoke: ToolInvoker,
    cancelled: Callable[[], bool],
    *,
    max_workers: int,
    prepare_group: GroupPreparation | None,
) -> None:
    remaining = list(indices)
    worker_limit = max(1, int(max_workers))
    while remaining:
        if cancelled():
            for index in remaining:
                results[index] = (_cancelled_text(calls[index]), False)
            return
        wave: list[int] = []
        deferred: list[int] = []
        occupied: list[tuple[str, ...]] = []
        for index in remaining:
            policy = scheduling[index]
            keys = policy[1] if policy is not None else ()
            if any(_resources_conflict(keys, prior) for prior in occupied):
                deferred.append(index)
                continue
            wave.append(index)
            occupied.append(keys)
        preparation_error = _prepare_error(prepare_group)
        if preparation_error is not None:
            for index in wave:
                call = calls[index]
                results[index] = (
                    f"[Tool error] {call.name or '<unnamed>'}: " f"{preparation_error}",
                    False,
                )
            remaining = deferred
            continue
        if len(wave) == 1:
            index = wave[0]
            results[index] = _safe_invoke(calls[index], invoke)
        else:
            with ThreadPoolExecutor(
                max_workers=min(worker_limit, len(wave)),
                thread_name_prefix="openai4s-tool-read",
            ) as pool:
                futures = {
                    index: pool.submit(_safe_invoke, calls[index], invoke)
                    for index in wave
                }
                # Resolve in provider order even when physical completion order
                # differs, preserving a stable assistant/tool history group.
                for index in wave:
                    results[index] = futures[index].result()
        remaining = deferred


def _safe_invoke(
    call: NativeToolCall,
    invoke: ToolInvoker,
) -> tuple[str, bool]:
    try:
        return invoke(call)
    except Exception as exc:  # noqa: BLE001 — close every protocol call
        try:
            detail = str(exc)
        except Exception:  # noqa: BLE001
            detail = type(exc).__name__
        return f"[Tool error] {call.name or '<unnamed>'}: {detail}", False


def _prepare_error(prepare_group: GroupPreparation | None) -> str | None:
    if prepare_group is None:
        return None
    try:
        prepare_group()
        return None
    except Exception as exc:  # noqa: BLE001 - close every protocol call
        try:
            detail = str(exc)
        except Exception:  # noqa: BLE001
            detail = type(exc).__name__
        return f"batch preparation failed: {detail}"


def _cancelled_text(call: NativeToolCall) -> str:
    return (
        f"[Tool error] {call.name or '<unnamed>'}: "
        "run was cancelled before execution"
    )


def _resources_conflict(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    if not left or not right:
        # Missing resource identity is not proof of independence.
        return True
    for first in left:
        first_namespace, _, first_target = first.partition(":")
        for second in right:
            second_namespace, _, second_target = second.partition(":")
            if first_namespace != second_namespace:
                continue
            if first_target in {"", "*"} or second_target in {"", "*"}:
                return True
            if first == second:
                return True
            if first_namespace == "workspace":
                first_path = first_target.rstrip("/")
                second_path = second_target.rstrip("/")
                if first_path == "." or second_path == ".":
                    return True
                if first_path.startswith(second_path + "/") or second_path.startswith(
                    first_path + "/"
                ):
                    return True
    return False


__all__ = [
    "ToolInvoker",
    "ToolParallelPolicy",
    "GroupPreparation",
    "execute_native_batch",
    "tool_parallel_policy",
]
