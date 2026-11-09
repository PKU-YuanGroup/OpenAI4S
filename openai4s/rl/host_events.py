"""Host RPC lifecycle facts for the RL trajectory boundary."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any

from .models import ExecutionEvent


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class HostCallLifecycle:
    """Append bounded start/completion facts for one Host RPC."""

    def __init__(
        self, repository: Any, episode_id: str, *, clock_ms: Any | None = None
    ):
        self.repository = repository
        self.episode_id = episode_id
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    def begin(
        self,
        method: str,
        args: list[Any],
        *,
        decision_id: str | None = None,
        segment_id: str | None = None,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        call_id = "host-call-" + uuid.uuid4().hex
        payload = {
            "call_id": call_id,
            "method": method,
            "arguments_sha256": _digest(args),
        }
        self.repository.append_execution_event(
            ExecutionEvent(
                event_id=f"{call_id}:started",
                episode_id=self.episode_id,
                kind="host_call.started",
                created_at=self.clock_ms(),
                decision_id=decision_id,
                segment_id=segment_id,
                source_id=source_id,
                payload=payload,
            )
        )
        return {
            **payload,
            "decision_id": decision_id,
            "segment_id": segment_id,
            "source_id": source_id,
        }

    def end(
        self,
        handle: dict[str, Any],
        *,
        ok: bool,
        result: Any = None,
        error: BaseException | None = None,
    ) -> None:
        completed = ok and error is None
        payload = {
            "call_id": handle["call_id"],
            "method": handle["method"],
            "arguments_sha256": handle["arguments_sha256"],
        }
        if completed:
            payload["result_sha256"] = _digest(result)
            kind = "host_call.completed"
        else:
            payload["error_category"] = (
                type(error).__name__ if error is not None else "soft_failure"
            )
            kind = "host_call.failed"
        self.repository.append_execution_event(
            ExecutionEvent(
                event_id=f"{handle['call_id']}:{'completed' if completed else 'failed'}",
                episode_id=self.episode_id,
                kind=kind,
                created_at=self.clock_ms(),
                decision_id=handle.get("decision_id"),
                segment_id=handle.get("segment_id"),
                source_id=handle.get("source_id"),
                payload=payload,
            )
        )


__all__ = ["HostCallLifecycle"]
