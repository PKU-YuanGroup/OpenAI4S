"""Dependency-free verifier boundary for RL reward annotations."""

from __future__ import annotations

from copy import deepcopy
from typing import Iterable, Protocol

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


__all__ = ["RewardVerifier", "apply_verifier"]
