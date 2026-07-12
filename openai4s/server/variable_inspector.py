"""Read-only projection of an already-live kernel namespace.

The inspector never creates a session or worker and never enters the Cell
transaction.  It takes the existing session's legacy execution barrier only
with a non-blocking acquire, then re-checks coordinator/recovery occupancy
before issuing the dedicated manager protocol request.  This preserves the
manager's single-frame-reader invariant without pretending inspection is a
scientific execution.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from typing import Any

from openai4s.kernel import KernelBusyError

_LANGUAGES = frozenset({"python", "r"})
_FINGERPRINT = re.compile(r"^[a-f0-9]{8,128}$")


class VariableInspectorService:
    """Inspect an existing idle worker through narrow injected ports."""

    def __init__(
        self,
        *,
        state_for: Callable[[str], Any | None],
        execution_snapshot: Callable[[str], Mapping[str, Any]],
        recovering: Callable[[str], bool],
        latest_generation: Callable[..., Mapping[str, Any] | None],
        latest_state_revision: Callable[[str], int],
        active_branch: Callable[[str], str] | None = None,
    ) -> None:
        self._state_for = state_for
        self._execution_snapshot = execution_snapshot
        self._recovering = recovering
        self._latest_generation = latest_generation
        self._latest_state_revision = latest_state_revision
        self._active_branch = active_branch or (lambda root_frame_id: root_frame_id)

    def inspect(self, root_frame_id: str, language: str) -> dict[str, Any]:
        language = str(language or "").lower()
        if language not in _LANGUAGES:
            raise ValueError("language must be python or r")
        state = self._state_for(root_frame_id)
        if state is None:
            return self._inactive(root_frame_id, language)
        if self._recovering(root_frame_id):
            return self._unavailable(root_frame_id, language, "restoring")

        # All Cell/lifecycle/recovery paths cross this exact barrier.  A
        # non-blocking acquire makes Busy observable instead of queueing an
        # inspector behind a potentially long scientific execution.
        if not state.turn_lock.acquire(blocking=False):
            return self._unavailable(root_frame_id, language, "busy")
        try:
            if self._recovering(root_frame_id):
                return self._unavailable(root_frame_id, language, "restoring")
            snapshot = self._execution_snapshot(root_frame_id) or {}
            if (
                snapshot.get("owner")
                or snapshot.get("queued_count")
                or snapshot.get("queue")
            ):
                return self._unavailable(root_frame_id, language, "busy")
            lease = state.kernels.lease(language)
            if lease is None or not _alive(lease.kernel):
                return self._inactive(root_frame_id, language)
            inspect_variables = getattr(state.kernels, "inspect_variables", None)
            if not callable(inspect_variables):
                return self._unavailable(root_frame_id, language, "unsupported")
            try:
                payload = inspect_variables(language, limit=200)
            except KernelBusyError:
                return self._unavailable(root_frame_id, language, "busy")
            except Exception:  # noqa: BLE001 - protocol errors stay private
                return self._unavailable(root_frame_id, language, "failed")
            return {
                "available": True,
                "root_frame_id": root_frame_id,
                "branch_id": str(getattr(state, "branch_id", root_frame_id)),
                "language": language,
                "state": "active",
                "generation_id": _text(lease.generation_id, 96),
                "state_revision": max(
                    0, int(self._latest_state_revision(root_frame_id) or 0)
                ),
                **_safe_payload(payload),
            }
        finally:
            state.turn_lock.release()

    def _inactive(self, root_frame_id: str, language: str) -> dict[str, Any]:
        generation = self._latest_generation(
            root_frame_id,
            language,
            branch_id=self._active_branch(root_frame_id),
        )
        state = "ended" if generation is not None else "not_started"
        return self._unavailable(
            root_frame_id,
            language,
            state,
            generation_id=_text((generation or {}).get("generation_id"), 96),
        )

    def _unavailable(
        self,
        root_frame_id: str,
        language: str,
        state: str,
        *,
        generation_id: str = "",
    ) -> dict[str, Any]:
        return {
            "available": False,
            "root_frame_id": root_frame_id,
            "branch_id": self._active_branch(root_frame_id),
            "language": language,
            "state": state,
            "generation_id": generation_id or None,
            "state_revision": max(
                0, int(self._latest_state_revision(root_frame_id) or 0)
            ),
            "variables": [],
            "truncated": False,
            "reason": {
                "busy": "kernel is busy",
                "ended": "kernel generation has ended",
                "not_started": "kernel has not been started",
                "restoring": "kernel recovery is in progress",
                "unsupported": "kernel does not support variable inspection",
                "failed": "variable inspection failed closed",
            }.get(state, "variable inspection is unavailable"),
        }


def _safe_payload(payload: Any) -> dict[str, Any]:
    source = payload if type(payload) is dict else {}
    variables = source.get("variables")
    safe: list[dict[str, Any]] = []
    for raw in variables if isinstance(variables, list) else ():
        if len(safe) >= 500:
            break
        if type(raw) is not dict:
            # Skip a single malformed element; ``break`` here would silently
            # drop every remaining (valid) variable after it.
            continue
        name = _text(raw.get("name"), 160)
        type_name = _text(raw.get("type"), 160)
        if not name or not type_name:
            continue
        item: dict[str, Any] = {"name": name, "type": type_name}
        kind = _text(raw.get("kind"), 32)
        if kind:
            item["kind"] = kind
        length = raw.get("length")
        if type(length) is int and length >= 0:
            item["length"] = min(length, 1_000_000_000_000)
        if "preview" in raw:
            preview = _safe_preview(raw.get("preview"))
            if preview is not _MISSING:
                item["preview"] = preview
        fingerprint = _text(raw.get("fingerprint"), 128).lower()
        if _FINGERPRINT.fullmatch(fingerprint):
            item["fingerprint"] = fingerprint
        safe.append(item)
    return {
        "variables": safe,
        "truncated": bool(source.get("truncated")) or len(safe) >= 500,
    }


_MISSING = object()


def _safe_preview(value: Any) -> Any:
    value_type = type(value)
    if value is None or value_type is bool:
        return value
    if value_type is int:
        return max(-(2**63), min(2**63 - 1, value))
    if value_type is float:
        return value if math.isfinite(value) else _MISSING
    if value_type is str:
        return _text(value, 240)
    return _MISSING


def _text(value: Any, limit: int) -> str:
    if type(value) is not str:
        return ""
    cleaned = "".join(char for char in value if char >= " " or char in "\t")
    return cleaned[:limit]


def _alive(kernel: Any) -> bool:
    try:
        return bool(kernel and kernel.is_alive())
    except Exception:  # noqa: BLE001
        return False


__all__ = ["VariableInspectorService"]
