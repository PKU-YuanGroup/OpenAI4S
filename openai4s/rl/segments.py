"""Shared Segment lifecycle rules, independent from execution and reward."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from .models import ExecutionEvent, Segment

SEGMENT_METADATA = "openai4s.segment"


def lifecycle_event(
    segment: Segment,
    *,
    started: bool,
    at: int,
    decision_id: str | None = None,
    reason: str | None = None,
) -> ExecutionEvent:
    kind = "started" if started else "closed"
    return ExecutionEvent(
        event_id=f"{segment.segment_id}:{kind}",
        episode_id=segment.episode_id,
        kind=f"segment.{kind}",
        created_at=at,
        decision_id=decision_id if decision_id is not None else segment.decision_id,
        segment_id=segment.segment_id,
        payload=(
            segment.to_dict()
            if started
            else {"status": segment.status, "reason": reason}
        ),
    )


def validate_transition(
    current: Segment | None,
    segment: Segment,
    event: ExecutionEvent,
    previous: ExecutionEvent | None,
) -> None:
    """Validate both an initial transition and an exact retry without writes."""
    if event.segment_id != segment.segment_id or event.episode_id != segment.episode_id:
        raise ValueError("segment lifecycle event identity mismatch")
    if event.kind not in {"segment.started", "segment.closed"}:
        raise ValueError("unsupported segment lifecycle transition")
    starting = event.kind == "segment.started"
    if starting != (segment.status == "open"):
        raise ValueError("segment lifecycle status mismatch")
    if previous is not None:
        candidate = event.to_dict()
        candidate["created_at"] = previous.created_at
        candidate["sequence"] = previous.sequence
        if starting:
            candidate["payload"] = dict(candidate["payload"])
            candidate["payload"]["created_at"] = previous.payload.get("created_at")
        if candidate != previous.to_dict():
            raise ValueError("segment lifecycle retry conflicts with stored content")
        return
    if starting:
        if current is not None:
            raise ValueError(
                "segment already exists without matching lifecycle evidence"
            )
    elif current is None or current.status != "open":
        raise ValueError("only an open segment can close")
    elif replace(segment, status="open") != current:
        raise ValueError("segment identity changed while closing")


def interrupted_status(reason: str) -> str:
    """An unclosed phase is interrupted, never inferred scientifically successful."""
    return (
        "failed"
        if reason in {"failed", "error", "execution_failure"}
        else "interrupted"
    )


def outcome_status(observation: Any, stop_reason: str | None) -> str:
    """Label only explicit execution failures; completed does not imply reward."""
    if stop_reason in {"cancelled", "timeout", "interrupted"}:
        return "interrupted"
    if stop_reason in {"failed", "error", "execution_failure", "scientific_failure"}:
        return "failed"
    if isinstance(observation, Mapping) and (
        observation.get("error") or observation.get("ok") is False
    ):
        return "failed"
    return "completed"
