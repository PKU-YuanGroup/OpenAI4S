"""Thread-safe append-only trajectory recorder."""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from dataclasses import replace
from typing import Any, Iterable, TypeVar

from .models import (
    CostRecord,
    Episode,
    ExecutionEvent,
    Observation,
    PolicyDecision,
    RewardAnnotation,
    Segment,
    Trajectory,
)
from .snapshot import DatasetSnapshot

Record = TypeVar(
    "Record",
    Observation,
    PolicyDecision,
    Segment,
    ExecutionEvent,
    RewardAnnotation,
    CostRecord,
)


class TrajectoryRecorder:
    """Collect one episode without rewriting records already accepted.

    Every collection is keyed by its public ID.  Re-adding the same ID with
    byte-identical JSON is an idempotent retry; re-adding it with different
    content raises ``ValueError`` so a caller cannot silently fork training
    data after a lost response.
    """

    def __init__(self, episode: Episode, repository: Any | None = None):
        self.repository = repository
        if repository is not None:
            # Reusing a durable episode is required for retries and for
            # segments opened by separate host calls.  The repository's
            # lifecycle projection is authoritative once the episode exists.
            ensure_episode = getattr(repository, "ensure_episode", None)
            if callable(ensure_episode):
                episode = ensure_episode(episode)
            else:
                repository.append_episode(episode)
        self.episode = episode
        self.owner_id = "rl-owner-" + uuid.uuid4().hex
        self._lock = threading.RLock()
        self._observations: dict[str, Observation] = {}
        self._decisions: dict[str, PolicyDecision] = {}
        self._segments: dict[str, Segment] = {}
        self._events: dict[str, ExecutionEvent] = {}
        self._rewards: dict[str, RewardAnnotation] = {}
        self._costs: dict[str, CostRecord] = {}
        self._snapshots: dict[str, DatasetSnapshot] = {}
        self._event_sequence = 0

    @staticmethod
    def _same(left: Any, right: Any) -> bool:
        return json.dumps(
            left.to_dict(), sort_keys=True, separators=(",", ":")
        ) == json.dumps(right.to_dict(), sort_keys=True, separators=(",", ":"))

    def _put(
        self, target: dict[str, Record], key: str, value: Record, append: str
    ) -> Record:
        current = target.get(key)
        if current is not None:
            if not self._same(current, value):
                raise ValueError(
                    f"record {key!r} was already stored with different content"
                )
            return current
        # Persist before caching: a failed append must not leave a cached copy
        # that makes an identical retry report success without storing it.
        if self.repository is not None:
            getattr(self.repository, append)(value)
        target[key] = value
        return value

    def add_observation(self, value: Observation) -> Observation:
        with self._lock:
            self._check_episode(value.episode_id)
            return self._put(
                self._observations, value.observation_id, value, "append_observation"
            )

    def add_decision(self, value: PolicyDecision) -> PolicyDecision:
        with self._lock:
            self._check_episode(value.episode_id)
            return self._put(
                self._decisions, value.decision_id, value, "append_decision"
            )

    def add_segment(self, value: Segment) -> Segment:
        with self._lock:
            self._check_episode(value.episode_id)
            return self._put(self._segments, value.segment_id, value, "append_segment")

    def transition_segment(
        self, segment: Segment, event: ExecutionEvent
    ) -> tuple[Segment, ExecutionEvent]:
        """Append one Segment lifecycle fact and update its projection."""
        with self._lock:
            self._check_episode(segment.episode_id)
            self._check_episode(event.episode_id)
            if self.repository is not None:
                stored_segment, stored_event = self.repository.transition_segment(
                    segment, event
                )
            else:
                from .segments import validate_transition

                current = self._segments.get(segment.segment_id)
                previous = self._events.get(event.event_id)
                validate_transition(current, segment, event, previous)
                stored_segment = segment
                stored_event = event
                if previous is None:
                    if event.sequence is None:
                        stored_event = replace(event, sequence=self._event_sequence + 1)
                    self._events[stored_event.event_id] = stored_event
                    self._event_sequence = stored_event.sequence or self._event_sequence
                else:
                    assert current is not None
                    stored_segment = current
                    stored_event = previous
                self._segments[segment.segment_id] = stored_segment
            self._segments[stored_segment.segment_id] = stored_segment
            self._events[stored_event.event_id] = stored_event
            self._event_sequence = max(self._event_sequence, stored_event.sequence or 0)
            return stored_segment, stored_event

    def open_segments(self) -> tuple[Segment, ...]:
        """Return durable and in-memory open phases for this Episode."""
        with self._lock:
            if self.repository is not None:
                return tuple(
                    item for item in self.snapshot().segments if item.status == "open"
                )
            return tuple(
                item for item in self._segments.values() if item.status == "open"
            )

    def add_event(self, value: ExecutionEvent) -> ExecutionEvent:
        with self._lock:
            self._check_episode(value.episode_id)
            if value.kind == "episode.finished":
                self.close_open_segments(
                    str(value.payload.get("stop_reason") or "interrupted")
                )
            current = self._events.get(value.event_id)
            if current is not None:
                if value.sequence is None:
                    value = replace(value, sequence=current.sequence)
                if not self._same(current, value):
                    raise ValueError(
                        f"record {value.event_id!r} was already stored with different content"
                    )
                return current
            if value.sequence is None and self.repository is None:
                value = replace(value, sequence=self._event_sequence + 1)
            elif value.sequence is not None and value.sequence <= self._event_sequence:
                raise ValueError("event sequence must increase for new events")
            if self.repository is not None:
                value = self.repository.append_execution_event(value)
            assert value.sequence is not None
            self._event_sequence = value.sequence
            self._events[value.event_id] = value
            return value

    def add_reward(self, value: RewardAnnotation) -> RewardAnnotation:
        with self._lock:
            self._check_episode(value.episode_id)
            return self._put(self._rewards, value.annotation_id, value, "append_reward")

    def add_cost(self, value: CostRecord) -> CostRecord:
        with self._lock:
            self._check_episode(value.episode_id)
            return self._put(self._costs, value.cost_id, value, "append_cost")

    def add_provider_cost(
        self,
        evidence: Any,
        compute_job: Any,
        *,
        decision_id: str | None = None,
        cost_id: str | None = None,
    ) -> CostRecord:
        """Validate and append one provider-backed compute cost fact.

        The provider receipt and compute row are checked before the normal
        append-only/idempotent cost path.  Keeping this adapter here lets a
        Store-backed recorder use exactly the same duplicate handling as token
        and wall-time records without teaching the recorder to infer pricing.
        """

        from .cost import provider_cost_record

        value = provider_cost_record(
            self.episode.episode_id,
            evidence,
            compute_job,
            decision_id=decision_id,
            cost_id=cost_id,
        )
        return self.add_cost(value)

    def create_snapshot(
        self, snapshot_id: str, *, created_at: int | None = None
    ) -> DatasetSnapshot:
        """Freeze a terminal trajectory and persist its metadata when configured."""
        with self._lock:
            current = self._snapshots.get(snapshot_id)
            if current is None and self.repository is not None:
                get_snapshot = getattr(self.repository, "get_snapshot", None)
                if callable(get_snapshot):
                    try:
                        current = get_snapshot(snapshot_id)
                    except KeyError:
                        current = None
            effective_created_at = (
                current.created_at
                if current is not None
                else self.now_ms() if created_at is None else created_at
            )
            value = DatasetSnapshot.from_trajectory(
                self.snapshot(), snapshot_id, created_at=effective_created_at
            )
            current = current
            if current is not None:
                if current != value:
                    raise ValueError(
                        f"snapshot {value.snapshot_id!r} conflicts with stored content"
                    )
                return current
            if self.repository is not None:
                append_snapshot = getattr(self.repository, "append_snapshot", None)
                if not callable(append_snapshot):
                    raise TypeError("repository does not support dataset snapshots")
                get_snapshot = getattr(self.repository, "get_snapshot", None)
                if callable(get_snapshot):
                    try:
                        durable = get_snapshot(value.snapshot_id)
                    except KeyError:
                        durable = None
                    if durable is not None:
                        if durable != value:
                            raise ValueError(
                                f"snapshot {value.snapshot_id!r} conflicts with stored content"
                            )
                        self._snapshots[value.snapshot_id] = durable
                        return durable
                append_snapshot(value)
            self._snapshots[value.snapshot_id] = value
            return value

    def _check_episode(self, episode_id: str) -> None:
        if episode_id != self.episode.episode_id:
            raise ValueError("record belongs to a different episode")

    def close_segment(
        self,
        segment_id: str,
        status: str,
        *,
        decision_id: str | None = None,
        reason: str | None = None,
    ) -> Segment:
        from .segments import lifecycle_event

        with self._lock:
            segment = (
                self.repository.get_segment(segment_id)
                if self.repository is not None
                else self._segments.get(segment_id)
            )
            if segment is None:
                raise KeyError(segment_id)
            self._check_episode(segment.episode_id)
            closed = replace(segment, status=status)
            event = lifecycle_event(
                closed,
                started=False,
                at=self.now_ms(),
                decision_id=decision_id,
                reason=reason,
            )
            return self.transition_segment(closed, event)[0]

    def close_open_segments(self, reason: str) -> None:
        from .segments import interrupted_status

        with self._lock:
            # Children close before parents; creation order is not a hierarchy.
            pending = {item.segment_id: item for item in self.open_segments()}
            while pending:
                parents = {item.parent_segment_id for item in pending.values()}
                leaves = [
                    item for item in pending.values() if item.segment_id not in parents
                ]
                if not leaves:
                    raise ValueError("segment hierarchy contains a cycle")
                for segment in sorted(leaves, key=lambda item: item.segment_id):
                    self.close_segment(
                        segment.segment_id, interrupted_status(reason), reason=reason
                    )
                    del pending[segment.segment_id]

    def finish(self, status: str) -> Episode:
        """Set the episode lifecycle projection exactly once."""
        if not isinstance(status, str) or not status.strip():
            raise ValueError("status must be a non-empty string")
        if status == "running":
            raise ValueError("finish status must be terminal")
        with self._lock:
            if self.episode.status != "running" and self.episode.status != status:
                raise ValueError("episode is already terminal")
            self.episode = type(self.episode)(
                **{**self.episode.to_dict(), "status": status}
            )
            if self.repository is not None:
                self.repository.update_episode_status(self.episode.episode_id, status)
            return self.episode

    def terminate(self, status: str, stop_reason: str) -> Episode:
        """Finish an episode whose run ended without ``RunFinished``.

        ``finish`` only sets the status. An aborted run still needs its open
        segments closed and exactly one ``episode.finished`` fact, or dataset
        snapshots and the completion contract refuse the episode.
        """
        with self._lock:
            if self.episode.status == "running" and not any(
                event.kind == "episode.finished" for event in self._events.values()
            ):
                self.add_event(
                    ExecutionEvent(
                        event_id=f"{self.episode.episode_id}:episode.finished",
                        episode_id=self.episode.episode_id,
                        kind="episode.finished",
                        created_at=self.now_ms(),
                        payload={
                            "stop_reason": stop_reason,
                            "completion": None,
                            "turns": None,
                        },
                    )
                )
            return self.finish(status)

    def snapshot(self) -> Trajectory:
        with self._lock:
            local = Trajectory(
                episode=self.episode,
                observations=tuple(
                    sorted(
                        self._observations.values(),
                        key=lambda item: (item.created_at, item.observation_id),
                    )
                ),
                decisions=tuple(
                    sorted(
                        self._decisions.values(),
                        key=lambda item: (item.turn, item.decision_id),
                    )
                ),
                segments=tuple(
                    sorted(
                        self._segments.values(),
                        key=lambda item: (item.created_at, item.segment_id),
                    )
                ),
                execution_events=tuple(
                    sorted(
                        self._events.values(),
                        key=lambda item: (item.sequence or 0, item.event_id),
                    )
                ),
                rewards=tuple(
                    sorted(
                        self._rewards.values(),
                        key=lambda item: (item.created_at, item.annotation_id),
                    )
                ),
                costs=tuple(
                    sorted(
                        self._costs.values(),
                        key=lambda item: (item.created_at, item.cost_id),
                    )
                ),
            )
            # A Store-backed recorder has already flushed every accepted fact.
            # Ask its durable projector for the export boundary so Artifact and
            # Compute references are included in hashes and snapshots too.
            load = getattr(self.repository, "load_trajectory", None)
            if callable(load):
                durable = load(self.episode.episode_id)
                if isinstance(durable, Trajectory):
                    return durable
            return local

    def export_json(self) -> str:
        return self.snapshot().canonical_json()

    def export_jsonl(self) -> str:
        """Export the envelope as deterministic JSONL for streaming loaders."""
        return trajectory_jsonl(self.snapshot())

    @staticmethod
    def trajectory_jsonl(trajectory: Trajectory) -> str:
        """Serialize a detached trajectory with the recorder's JSONL contract."""
        return trajectory_jsonl(trajectory)

    @staticmethod
    def fingerprint(value: dict[str, Any]) -> str:
        """Hash an admission request after canonical JSON normalization."""
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def now_ms() -> int:
        return int(time.time() * 1000)

    def __len__(self) -> int:
        with self._lock:
            return len(self._events) + len(self._decisions)


def trajectory_jsonl(trajectory: Trajectory) -> str:
    """Serialize one trajectory as deterministic, line-delimited JSON."""
    envelope = trajectory.to_dict()
    lines = [
        json.dumps(
            {
                "type": "trajectory",
                "schema_version": envelope["schema_version"],
                "episode": envelope["episode"],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    ]
    for key in (
        "observations",
        "decisions",
        "segments",
        "execution_events",
        "rewards",
        "costs",
    ):
        for record in envelope[key]:
            lines.append(
                json.dumps(
                    {"type": key[:-1], **record},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
    return "\n".join(lines) + "\n"


__all__ = ["TrajectoryRecorder", "trajectory_jsonl"]
