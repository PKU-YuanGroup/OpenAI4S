"""Read-only replay projections for durable RL trajectories.

Replay is deliberately a projection boundary.  Loading an episode never
re-runs a cell, submits a compute job, or resolves an Artifact's latest
version.  Event and decision replay reconstruct immutable records from the RL
repository; Artifact replay checks the exact version references observed by
the episode; execution replay is available only when the caller supplies an
explicit, controlled runner.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Literal, Mapping, Protocol

from .models import ExecutionEvent, Trajectory

ReplayMode = Literal["event", "decision", "execution", "artifact"]


class ReplayError(ValueError):
    """Base error for an invalid or unverifiable replay request."""


class ReplayCursorError(ReplayError):
    """Raised when an event cursor cannot address this episode."""


class ReplayIntegrityError(ReplayError):
    """Raised when durable replay evidence does not form a stable view."""


class ReplayUnavailable(ReplayError):
    """Raised when a requested replay needs an explicitly supplied adapter."""


class ExecutionReplayRunner(Protocol):
    """Controlled execution adapter owned by the embedding application.

    The runner receives a detached trajectory and returns JSON records.  It is
    intentionally not part of the core runtime: the caller decides whether a
    sandbox, network policy, seed, and provider mock make re-execution safe.
    """

    def __call__(self, trajectory: Trajectory) -> Iterable[Mapping[str, Any]]: ...


ArtifactChecker = Callable[[str], Mapping[str, Any] | None]


@dataclass(frozen=True)
class ReplayView:
    """Deterministic, mode-specific read-only replay output."""

    episode_id: str
    mode: ReplayMode
    schema_version: str
    source_sha256: str
    records: tuple[Mapping[str, Any], ...]
    complete: bool
    next_sequence: int
    snapshot_id: str | None = None
    verified: bool = True
    issues: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.episode_id, str) or not self.episode_id.strip():
            raise ValueError("episode_id must be a non-empty string")
        if self.mode not in {"event", "decision", "execution", "artifact"}:
            raise ValueError("unsupported replay mode")
        if not isinstance(self.schema_version, str) or not self.schema_version:
            raise ValueError("schema_version must be a non-empty string")
        if (
            not isinstance(self.source_sha256, str)
            or len(self.source_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.source_sha256)
        ):
            raise ValueError("source_sha256 must be a lowercase SHA-256 digest")
        if not isinstance(self.records, tuple):
            raise TypeError("records must be a tuple")
        if isinstance(self.next_sequence, bool) or not isinstance(
            self.next_sequence, int
        ):
            raise TypeError("next_sequence must be an integer")
        if self.next_sequence < 1:
            raise ValueError("next_sequence must be positive")
        if not isinstance(self.complete, bool) or not isinstance(self.verified, bool):
            raise TypeError("complete and verified must be bools")
        if self.snapshot_id is not None and not self.snapshot_id.strip():
            raise ValueError("snapshot_id must be non-empty when supplied")
        if not isinstance(self.issues, tuple) or any(
            not isinstance(item, str) or not item for item in self.issues
        ):
            raise TypeError("issues must be a tuple of non-empty strings")

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "mode": self.mode,
            "schema_version": self.schema_version,
            "source_sha256": self.source_sha256,
            "snapshot_id": self.snapshot_id,
            "records": [_json_value(record) for record in self.records],
            "complete": self.complete,
            "next_sequence": self.next_sequence,
            "verified": self.verified,
            "issues": list(self.issues),
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class TrajectoryReplayer:
    """Build deterministic replay projections from an RL repository."""

    def __init__(self, repository: Any):
        if not callable(getattr(repository, "load_trajectory", None)):
            raise TypeError("repository must provide load_trajectory()")
        self.repository = repository

    def replay(
        self,
        episode_id: str,
        mode: ReplayMode = "event",
        *,
        after_sequence: int = 0,
        limit: int | None = None,
        snapshot_id: str | None = None,
        execution_runner: ExecutionReplayRunner | None = None,
        artifact_checker: ArtifactChecker | None = None,
    ) -> ReplayView:
        """Return a replay view without mutating the repository.

        ``after_sequence`` and ``limit`` apply to event replay.  Other modes
        use the same arguments for a stable envelope but do not discard their
        records, because observations and decisions have no shared event
        sequence.  A snapshot, when named, is checked against the complete
        reconstructed trajectory before any projection is returned.
        """

        _episode_id(episode_id)
        if mode not in {"event", "decision", "execution", "artifact"}:
            raise ReplayError(f"unsupported replay mode {mode!r}")
        _cursor(after_sequence, limit)
        trajectory = self.repository.load_trajectory(episode_id)
        if trajectory.episode.episode_id != episode_id:
            raise ReplayIntegrityError("repository returned a different episode")
        source_sha256 = trajectory.sha256()
        if snapshot_id is not None:
            get_snapshot = getattr(self.repository, "get_snapshot", None)
            if not callable(get_snapshot):
                raise ReplayUnavailable("repository does not support snapshots")
            try:
                snapshot = get_snapshot(snapshot_id)
            except KeyError as exc:
                raise ReplayIntegrityError(
                    f"snapshot {snapshot_id!r} does not exist"
                ) from exc
            if not snapshot.matches(trajectory):
                raise ReplayIntegrityError(
                    f"snapshot {snapshot_id!r} does not match the durable trajectory"
                )
            source_sha256 = snapshot.trajectory_sha256

        if mode == "event":
            return self._events(
                trajectory,
                source_sha256=source_sha256,
                snapshot_id=snapshot_id,
                after_sequence=after_sequence,
                limit=limit,
            )
        if mode == "decision":
            return self._decisions(
                trajectory, source_sha256=source_sha256, snapshot_id=snapshot_id
            )
        if mode == "artifact":
            return self._artifacts(
                trajectory,
                source_sha256=source_sha256,
                snapshot_id=snapshot_id,
                checker=artifact_checker,
            )
        if execution_runner is None:
            raise ReplayUnavailable(
                "execution replay requires an explicit controlled runner"
            )
        records = tuple(
            _json_mapping(item) for item in execution_runner(deepcopy(trajectory))
        )
        return ReplayView(
            episode_id=episode_id,
            mode="execution",
            schema_version=trajectory.schema_version,
            source_sha256=source_sha256,
            snapshot_id=snapshot_id,
            records=records,
            complete=True,
            next_sequence=len(records) + 1,
        )

    def _events(
        self,
        trajectory: Trajectory,
        *,
        source_sha256: str,
        snapshot_id: str | None,
        after_sequence: int,
        limit: int | None,
    ) -> ReplayView:
        events = tuple(trajectory.execution_events)
        _validate_events(events, trajectory.episode.episode_id)
        latest = events[-1].sequence or 0
        if after_sequence > latest:
            raise ReplayCursorError(
                f"event cursor {after_sequence} is ahead of latest sequence {latest}"
            )
        selected = tuple(
            event for event in events if (event.sequence or 0) > after_sequence
        )
        complete = True
        if limit is not None and len(selected) > limit:
            selected = selected[:limit]
            complete = False
        records = tuple(event.to_dict() for event in selected)
        next_sequence = (
            (selected[-1].sequence or after_sequence) + 1
            if selected
            else after_sequence + 1
        )
        return ReplayView(
            episode_id=trajectory.episode.episode_id,
            mode="event",
            schema_version=trajectory.schema_version,
            source_sha256=source_sha256,
            snapshot_id=snapshot_id,
            records=records,
            complete=complete,
            next_sequence=next_sequence,
        )

    def _decisions(
        self, trajectory: Trajectory, *, source_sha256: str, snapshot_id: str | None
    ) -> ReplayView:
        observations = {item.observation_id: item for item in trajectory.observations}
        decisions = tuple(
            sorted(trajectory.decisions, key=lambda item: (item.turn, item.decision_id))
        )
        records: list[dict[str, Any]] = []
        issues: list[str] = []
        for decision in decisions:
            observation = observations.get(decision.observation_id)
            if observation is None:
                issues.append(
                    f"decision {decision.decision_id!r} references missing observation"
                )
                continue
            records.append({"type": "observation", **observation.to_dict()})
            records.append({"type": "decision", **decision.to_dict()})
        if issues:
            raise ReplayIntegrityError("; ".join(issues))
        return ReplayView(
            episode_id=trajectory.episode.episode_id,
            mode="decision",
            schema_version=trajectory.schema_version,
            source_sha256=source_sha256,
            snapshot_id=snapshot_id,
            records=tuple(records),
            complete=True,
            next_sequence=len(trajectory.execution_events) + 1,
        )

    def _artifacts(
        self,
        trajectory: Trajectory,
        *,
        source_sha256: str,
        snapshot_id: str | None,
        checker: ArtifactChecker | None,
    ) -> ReplayView:
        refs = _artifact_refs(trajectory)
        issues: list[str] = []
        records: list[dict[str, Any]] = []
        for version_id, checksum, source_event_id in refs:
            record: dict[str, Any] = {
                "version_id": version_id,
                "source_event_id": source_event_id,
            }
            if checksum is not None:
                record["checksum"] = checksum
            if checker is not None:
                checked = checker(version_id)
                record_verified = True
                if checked is None:
                    issues.append(f"artifact version {version_id!r} is unavailable")
                    record_verified = False
                else:
                    returned = checked.get("version_id")
                    if returned != version_id:
                        issues.append(
                            f"artifact checker returned {returned!r} for {version_id!r}"
                        )
                        record_verified = False
                    if checksum is not None and checked.get("checksum") != checksum:
                        issues.append(
                            f"artifact version {version_id!r} checksum drifted"
                        )
                        record_verified = False
                record["verified"] = record_verified
            records.append(record)
        if checker is None and refs:
            issues.append("artifact checker was not supplied")
        return ReplayView(
            episode_id=trajectory.episode.episode_id,
            mode="artifact",
            schema_version=trajectory.schema_version,
            source_sha256=source_sha256,
            snapshot_id=snapshot_id,
            records=tuple(records),
            complete=True,
            next_sequence=len(trajectory.execution_events) + 1,
            verified=not issues,
            issues=tuple(issues),
        )


def _episode_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("episode_id must be a non-empty string")
    return value


def _cursor(after_sequence: int, limit: int | None) -> None:
    if isinstance(after_sequence, bool) or not isinstance(after_sequence, int):
        raise TypeError("after_sequence must be an integer")
    if after_sequence < 0:
        raise ValueError("after_sequence must be non-negative")
    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
    ):
        raise ValueError("limit must be a positive integer")


def _validate_events(events: Iterable[ExecutionEvent], episode_id: str) -> None:
    expected = 1
    seen: set[str] = set()
    for event in events:
        if event.episode_id != episode_id:
            raise ReplayIntegrityError("event belongs to a different episode")
        if event.event_id in seen:
            raise ReplayIntegrityError(f"duplicate event {event.event_id!r}")
        seen.add(event.event_id)
        if event.sequence != expected:
            raise ReplayIntegrityError(
                f"event sequence is not contiguous at {event.event_id!r}"
            )
        expected += 1


def _artifact_refs(
    trajectory: Trajectory,
) -> tuple[tuple[str, str | None, str | None], ...]:
    refs: dict[str, tuple[str | None, str | None]] = {}
    reset = trajectory.episode.metadata.get("reset")
    if isinstance(reset, Mapping):
        initial = reset.get("initial_artifacts")
        if isinstance(initial, Mapping):
            for value in initial.values():
                if isinstance(value, str) and value.strip():
                    refs.setdefault(value, (None, "initial"))

    def visit(value: Any, source_event_id: str | None = None) -> None:
        if isinstance(value, Mapping):
            for version_id in _version_values(value):
                refs.setdefault(
                    version_id,
                    (
                        (
                            value.get("checksum")
                            if isinstance(value.get("checksum"), str)
                            else None
                        ),
                        source_event_id,
                    ),
                )
            for item in value.values():
                visit(item, source_event_id)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item, source_event_id)

    for event in trajectory.execution_events:
        visit(event.payload, event.event_id)
    return tuple(
        (version_id, checksum, source_id)
        for version_id, (checksum, source_id) in sorted(refs.items())
    )


def _version_values(value: Mapping[str, Any]) -> tuple[str, ...]:
    result: list[str] = []
    for key in (
        "version_id",
        "artifact_version_id",
        "input_version_id",
        "output_version_id",
    ):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            result.append(candidate)
    for key in ("input_versions", "output_versions", "artifact_version_ids"):
        candidates = value.get(key)
        if isinstance(candidates, (list, tuple)):
            for candidate in candidates:
                if isinstance(candidate, str) and candidate.strip():
                    result.append(candidate)
    return tuple(dict.fromkeys(result))


def _json_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("execution replay records must be mappings")
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise TypeError("execution replay records must encode as objects")
    return decoded


def _json_value(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


__all__ = [
    "ArtifactChecker",
    "ExecutionReplayRunner",
    "ReplayCursorError",
    "ReplayError",
    "ReplayIntegrityError",
    "ReplayMode",
    "ReplayUnavailable",
    "ReplayView",
    "TrajectoryReplayer",
]
