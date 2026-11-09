"""Immutable dataset snapshots for trainer input selection."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from .models import Trajectory


@dataclass(frozen=True)
class DatasetSnapshot:
    """A content-addressed, terminal view of one trajectory."""

    snapshot_id: str
    episode_id: str
    schema_version: str
    trajectory_sha256: str
    event_sequence_max: int
    created_at: int
    record_counts: Mapping[str, int]

    def __post_init__(self) -> None:
        _text("snapshot_id", self.snapshot_id)
        _text("episode_id", self.episode_id)
        _text("schema_version", self.schema_version)
        _text("trajectory_sha256", self.trajectory_sha256)
        _integer("event_sequence_max", self.event_sequence_max)
        _integer("created_at", self.created_at)
        if not isinstance(self.record_counts, Mapping):
            raise TypeError("record_counts must be a mapping")
        counts = {
            _text("record type", str(key)): _integer("record count", value)
            for key, value in self.record_counts.items()
        }
        object.__setattr__(self, "record_counts", MappingProxyType(counts))

    @classmethod
    def from_trajectory(
        cls, trajectory: Trajectory, snapshot_id: str, *, created_at: int
    ) -> "DatasetSnapshot":
        _text("snapshot_id", snapshot_id)
        if trajectory.episode.status == "running":
            raise ValueError("dataset snapshots require a terminal episode")
        open_segments = [
            segment.segment_id
            for segment in trajectory.segments
            if segment.status == "open"
        ]
        if open_segments:
            raise ValueError("dataset snapshots cannot include open segments")
        sequences = [event.sequence for event in trajectory.execution_events]
        if any(sequence is None for sequence in sequences):
            raise ValueError("dataset snapshots require sequenced execution events")
        ordered = sorted(int(sequence) for sequence in sequences)
        if ordered != list(range(1, len(ordered) + 1)):
            raise ValueError("dataset snapshots require contiguous event sequences")
        return cls(
            snapshot_id=snapshot_id,
            episode_id=trajectory.episode.episode_id,
            schema_version=trajectory.schema_version,
            trajectory_sha256=trajectory.sha256(),
            event_sequence_max=max(ordered, default=0),
            created_at=_integer("created_at", created_at),
            record_counts={
                "observations": len(trajectory.observations),
                "decisions": len(trajectory.decisions),
                "segments": len(trajectory.segments),
                "execution_events": len(trajectory.execution_events),
                "rewards": len(trajectory.rewards),
                "costs": len(trajectory.costs),
            },
        )

    def matches(self, trajectory: Trajectory) -> bool:
        """Return whether a candidate is exactly the captured dataset view."""

        return (
            trajectory.episode.episode_id == self.episode_id
            and trajectory.schema_version == self.schema_version
            and trajectory.sha256() == self.trajectory_sha256
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "episode_id": self.episode_id,
            "schema_version": self.schema_version,
            "trajectory_sha256": self.trajectory_sha256,
            "event_sequence_max": self.event_sequence_max,
            "created_at": self.created_at,
            "record_counts": dict(self.record_counts),
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def _text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _integer(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


__all__ = ["DatasetSnapshot"]
