"""Thread-safe append-only trajectory recorder."""

from __future__ import annotations

import hashlib
import json
import threading
import time
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

    def _put(self, target: dict[str, Record], key: str, value: Record) -> Record:
        current = target.get(key)
        if current is not None:
            if not self._same(current, value):
                raise ValueError(
                    f"record {key!r} was already stored with different content"
                )
            return current
        target[key] = value
        return value

    def add_observation(self, value: Observation) -> Observation:
        with self._lock:
            self._check_episode(value.episode_id)
            result = self._put(self._observations, value.observation_id, value)
            if self.repository is not None and result is value:
                self.repository.append_observation(value)
            return result

    def add_decision(self, value: PolicyDecision) -> PolicyDecision:
        with self._lock:
            self._check_episode(value.episode_id)
            result = self._put(self._decisions, value.decision_id, value)
            if self.repository is not None and result is value:
                self.repository.append_decision(value)
            return result

    def add_segment(self, value: Segment) -> Segment:
        with self._lock:
            self._check_episode(value.episode_id)
            result = self._put(self._segments, value.segment_id, value)
            if self.repository is not None and result is value:
                self.repository.append_segment(value)
            return result

    def add_event(self, value: ExecutionEvent) -> ExecutionEvent:
        with self._lock:
            self._check_episode(value.episode_id)
            current = self._events.get(value.event_id)
            if current is not None:
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
            result = self._put(self._rewards, value.annotation_id, value)
            if self.repository is not None and result is value:
                self.repository.append_reward(value)
            return result

    def add_cost(self, value: CostRecord) -> CostRecord:
        with self._lock:
            self._check_episode(value.episode_id)
            result = self._put(self._costs, value.cost_id, value)
            if self.repository is not None and result is value:
                self.repository.append_cost(value)
            return result

    def create_snapshot(
        self, snapshot_id: str, *, created_at: int | None = None
    ) -> DatasetSnapshot:
        """Freeze a terminal trajectory and persist its metadata when configured."""
        with self._lock:
            value = DatasetSnapshot.from_trajectory(
                self.snapshot(),
                snapshot_id,
                created_at=self.now_ms() if created_at is None else created_at,
            )
            current = self._snapshots.get(value.snapshot_id)
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

    def finish(self, status: str) -> Episode:
        """Set the episode lifecycle projection exactly once."""
        if not isinstance(status, str) or not status.strip():
            raise ValueError("status must be a non-empty string")
        with self._lock:
            if self.episode.status != "running" and self.episode.status != status:
                raise ValueError("episode is already terminal")
            self.episode = type(self.episode)(
                **{**self.episode.to_dict(), "status": status}
            )
            if self.repository is not None:
                self.repository.update_episode_status(self.episode.episode_id, status)
            return self.episode

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
        trajectory = self.snapshot().to_dict()
        lines = [
            json.dumps(
                {
                    "type": "trajectory",
                    "schema_version": trajectory["schema_version"],
                    "episode": trajectory["episode"],
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
            for record in trajectory[key]:
                lines.append(
                    json.dumps(
                        {"type": key[:-1], **record},
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
        return "\n".join(lines) + "\n"

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
