"""Shared Segment lifecycle rules, independent from execution and reward."""

from __future__ import annotations

import time
import uuid
from dataclasses import replace
from typing import Any, Mapping

from .models import Episode, ExecutionEvent, Segment

SEGMENT_METADATA = "openai4s.segment"


class HostSegments:
    """Host-facing phase ownership; lexical TLS is managed by the adapter."""

    def __init__(
        self,
        repository: Any,
        episode_id: str,
        context: Mapping[str, Any],
        frame_id: str | None,
    ):
        self.repository = repository
        self.episode_id = episode_id
        self.context = context
        self.frame_id = frame_id
        self.owner_id = str(context["rl_owner_id"])

    def start(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        now = int(time.time() * 1000)
        episode = self.repository.get_episode(self.episode_id)
        if episode is None:
            episode = self.repository.ensure_episode(
                Episode(
                    self.episode_id,
                    str(spec.get("episode_task") or "OpenAI4S rollout"),
                    str(self.frame_id or self.episode_id),
                    now,
                    metadata={"source": "host.segment"},
                )
            )
        if episode.status != "running":
            raise ValueError("segment lifecycle requires a running episode")
        persistent = spec.get("persistent", False)
        if not isinstance(persistent, bool):
            raise ValueError("persistent must be a bool")
        parent_id = spec.get("parent_segment_id")
        if parent_id:
            parent_id = self._resolve(str(parent_id))
        active = self.repository.active_phase(self.episode_id, self.owner_id)
        requested = spec.get("segment_id")
        segment_id = (
            self._scoped(str(requested))
            if requested
            else "seg-" + uuid.uuid4().hex[:16]
        )
        if (
            persistent
            and active is not None
            and active.segment_id != segment_id
            and parent_id != active.segment_id
        ):
            raise ValueError("begin a nested phase with an explicit active parent")
        metadata = dict(spec.get("metadata") or {})
        metadata[SEGMENT_METADATA] = {
            "source": "explicit",
            "persistent": persistent,
            "owner_id": self.owner_id,
        }
        segment = Segment(
            segment_id,
            self.episode_id,
            str(spec.get("name") or "segment"),
            now,
            decision_id=self.context.get("decision_id"),
            parent_segment_id=parent_id,
            objective=spec.get("objective"),
            metadata=metadata,
        )
        existing = self.repository.get_segment(segment_id)
        if existing is not None and existing.episode_id != self.episode_id:
            raise ValueError("segment belongs to a different episode")
        if existing is not None:
            # Opening decision/time are observed facts, not caller retry identity.
            segment = replace(
                segment,
                created_at=existing.created_at,
                decision_id=existing.decision_id,
            )
        segment, event = self.repository.transition_segment(
            segment, lifecycle_event(segment, started=True, at=now)
        )
        if persistent:
            if segment.status != "open":
                raise ValueError("cannot reactivate a closed phase")
        return {
            "segment_id": segment.segment_id,
            "episode_id": self.episode_id,
            "sequence": event.sequence,
        }

    def resume(self, segment_id: str) -> dict[str, Any]:
        segment = self._segment(segment_id)
        info = segment.metadata.get(SEGMENT_METADATA, {})
        if segment.status != "open" or not info.get("persistent"):
            raise ValueError("only an open persistent phase can be resumed")
        active = self.repository.active_phase(self.episode_id, self.owner_id)
        if active is not None and active.segment_id != segment.segment_id:
            raise ValueError("another phase is active")
        self.repository.set_active_phase(
            self.episode_id, self.owner_id, segment.segment_id
        )
        return {"segment_id": segment.segment_id, "episode_id": self.episode_id}

    def close(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        segment = self._segment(str(spec.get("segment_id") or ""))
        status = str(spec.get("status") or "completed")
        if status == "open":
            raise ValueError("segment close status cannot be 'open'")
        if segment.status not in {"open", status}:
            raise ValueError(f"segment is already {segment.status}")
        trajectory = self.repository.load_trajectory(self.episode_id)
        if any(
            s.parent_segment_id == segment.segment_id and s.status == "open"
            for s in trajectory.segments
        ):
            raise ValueError("cannot close a segment with open children")
        closed = replace(segment, status=status)
        event = lifecycle_event(
            closed,
            started=False,
            at=int(time.time() * 1000),
            decision_id=self.context.get("decision_id"),
            reason=spec.get("error"),
        )
        if segment.status != "open":
            # A repeated close can arrive in a later decision. Compare the
            # requested status/reason but keep the original closing decision.
            prior = next(
                (
                    e
                    for e in trajectory.execution_events
                    if e.event_id == event.event_id
                ),
                None,
            )
            if prior is not None:
                event = replace(event, decision_id=prior.decision_id)
        _, event = self.repository.transition_segment(closed, event)
        return {"ok": True, "status": status, "sequence": event.sequence}

    def require_open(self, segment_id: str) -> Segment:
        """Return an open segment of this episode that this owner may write.

        Observations and outputs pass the same existence, episode and owner
        checks as resume/close, so a caller cannot append to another owner's
        phase by naming it.
        """
        segment = self._segment(segment_id)
        if segment.status != "open":
            raise ValueError(f"segment is already {segment.status}")
        return segment

    def _scoped(self, segment_id: str) -> str:
        """Scope a caller-chosen id to this episode, as inferred ids are.

        Segment ids are Store-global keys, so the same documented name in
        another rollout sharing the Store must not collide with this one.
        """
        prefix = f"{self.episode_id}:"
        return segment_id if segment_id.startswith(prefix) else prefix + segment_id

    def _resolve(self, segment_id: str) -> str:
        """Return the stored id for a caller's raw or episode-scoped id."""
        found = self.repository.get_segment(segment_id)
        if found is not None and found.episode_id == self.episode_id:
            return segment_id
        scoped = self._scoped(segment_id)
        if self.repository.get_segment(scoped) is not None:
            return scoped
        return segment_id

    def _segment(self, segment_id: str) -> Segment:
        segment = self.repository.get_segment(self._resolve(segment_id))
        if segment is None:
            raise ValueError(f"unknown segment {segment_id!r}")
        if segment.episode_id != self.episode_id:
            raise ValueError("segment belongs to a different episode")
        info = segment.metadata.get(SEGMENT_METADATA, {})
        if info.get("owner_id") not in {None, self.owner_id}:
            raise ValueError("phase belongs to a different execution owner")
        return segment


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
