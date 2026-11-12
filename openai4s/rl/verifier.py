"""Dependency-free verifier boundary for RL reward annotations."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Protocol

from .models import RewardAnnotation, Trajectory
from .recorder import TrajectoryRecorder


class RewardVerifier(Protocol):
    """A versioned verifier that scores facts without changing them."""

    name: str
    version: str

    def verify(self, trajectory: Trajectory) -> Iterable[RewardAnnotation]:
        """Return append-only annotations for one immutable trajectory view."""


def apply_verifier(
    recorder: TrajectoryRecorder, verifier: RewardVerifier
) -> tuple[RewardAnnotation, ...]:
    """Run a verifier and append its annotations without rewriting facts.

    The verifier sees a snapshot, never the mutable recorder. All annotations
    are validated before the first write, and the execution-event projection
    is compared before and after the call. A verifier version therefore adds a
    new reward view while preserving the rollout's execution evidence.
    """

    before = deepcopy(recorder.snapshot())
    before_execution = tuple(item.to_dict() for item in before.execution_events)
    name = _identity("name", verifier.name)
    version = _identity("version", verifier.version)
    raw = verifier.verify(before)
    annotations = tuple(raw)
    existing = {item.annotation_id: item for item in before.rewards}
    known_evidence = _evidence_ids(before)
    known_decisions = {item.decision_id for item in before.decisions}
    known_segments = {item.segment_id for item in before.segments}
    seen: set[str] = set()
    for annotation in annotations:
        if not isinstance(annotation, RewardAnnotation):
            raise TypeError("verifier must return RewardAnnotation records")
        if annotation.episode_id != before.episode.episode_id:
            raise ValueError("verifier annotation belongs to a different episode")
        if annotation.verifier != name:
            raise ValueError("verifier annotation name does not match verifier")
        if annotation.verifier_version != version:
            raise ValueError("verifier annotation version does not match verifier")
        if (
            annotation.decision_id is not None
            and annotation.decision_id not in known_decisions
        ):
            raise ValueError(
                f"verifier annotation references unknown decision "
                f"{annotation.decision_id!r}"
            )
        if (
            annotation.segment_id is not None
            and annotation.segment_id not in known_segments
        ):
            raise ValueError(
                f"verifier annotation references unknown segment "
                f"{annotation.segment_id!r}"
            )
        missing = [
            reference
            for reference in annotation.evidence_refs
            if reference not in known_evidence
        ]
        if missing:
            raise ValueError(
                "verifier annotation references unknown evidence: "
                + ", ".join(repr(item) for item in missing)
            )
        if annotation.annotation_id in seen:
            raise ValueError("verifier returned duplicate annotation IDs")
        seen.add(annotation.annotation_id)
        current = existing.get(annotation.annotation_id)
        if current is not None and current != annotation:
            raise ValueError("verifier annotation conflicts with stored content")

    after = recorder.snapshot()
    if tuple(item.to_dict() for item in after.execution_events) != before_execution:
        raise RuntimeError("verifier changed execution events")
    for annotation in annotations:
        recorder.add_reward(annotation)
    return annotations


def _identity(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"verifier {name} must be a non-empty string")
    return value


def _evidence_ids(trajectory: Trajectory) -> set[str]:
    """Collect durable IDs a reward annotation may cite.

    Evidence references are intentionally ID-only.  A verifier can cite an
    event, policy input, segment, cost, Artifact version, or Compute job that
    is present in this immutable trajectory view; arbitrary strings would make
    a reward look evidenced while leaving no auditable join.
    """

    result: set[str] = set()
    for collection in (
        trajectory.observations,
        trajectory.decisions,
        trajectory.segments,
        trajectory.execution_events,
        trajectory.costs,
    ):
        result.update(
            str(getattr(item, field))
            for item in collection
            for field in (
                "observation_id",
                "decision_id",
                "segment_id",
                "event_id",
                "cost_id",
            )
            if getattr(item, field, None)
        )

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key in (
                "version_id",
                "artifact_version_id",
                "input_version_id",
                "output_version_id",
                "job_id",
                "compute_job_id",
                "input_versions",
                "output_versions",
                "artifact_version_ids",
                "job_ids",
                "compute_job_ids",
            ):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    result.add(candidate)
                elif isinstance(candidate, (list, tuple)):
                    result.update(
                        item
                        for item in candidate
                        if isinstance(item, str) and item.strip()
                    )
            for child in value.values():
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)

    for event in trajectory.execution_events:
        if event.source_id:
            result.add(event.source_id)
        visit(event.payload)
    return result


__all__ = ["RewardVerifier", "apply_verifier"]
