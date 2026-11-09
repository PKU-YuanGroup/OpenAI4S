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

    def __init__(self, episode: Episode):
        self.episode = episode
        self._lock = threading.RLock()
        self._observations: dict[str, Observation] = {}
        self._decisions: dict[str, PolicyDecision] = {}
        self._segments: dict[str, Segment] = {}
        self._events: dict[str, ExecutionEvent] = {}
        self._rewards: dict[str, RewardAnnotation] = {}
        self._costs: dict[str, CostRecord] = {}
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
            return self._put(self._observations, value.observation_id, value)

    def add_decision(self, value: PolicyDecision) -> PolicyDecision:
        with self._lock:
            self._check_episode(value.episode_id)
            return self._put(self._decisions, value.decision_id, value)

    def add_segment(self, value: Segment) -> Segment:
        with self._lock:
            self._check_episode(value.episode_id)
            return self._put(self._segments, value.segment_id, value)

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
            self._event_sequence += 1
            if value.sequence is None:
                value = replace(value, sequence=self._event_sequence)
            elif value.sequence <= self._event_sequence:
                raise ValueError("event sequence must increase for new events")
            assert value.sequence is not None
            self._event_sequence = value.sequence
            self._events[value.event_id] = value
            return value

    def add_reward(self, value: RewardAnnotation) -> RewardAnnotation:
        with self._lock:
            self._check_episode(value.episode_id)
            return self._put(self._rewards, value.annotation_id, value)

    def add_cost(self, value: CostRecord) -> CostRecord:
        with self._lock:
            self._check_episode(value.episode_id)
            return self._put(self._costs, value.cost_id, value)

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
            return self.episode

    def snapshot(self) -> Trajectory:
        with self._lock:
            return Trajectory(
                episode=self.episode,
                observations=tuple(self._observations.values()),
                decisions=tuple(self._decisions.values()),
                segments=tuple(self._segments.values()),
                execution_events=tuple(self._events.values()),
                rewards=tuple(self._rewards.values()),
                costs=tuple(self._costs.values()),
            )

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
