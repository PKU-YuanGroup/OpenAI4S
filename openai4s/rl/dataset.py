"""Snapshot-checked training data projection for external RL consumers.

The trajectory remains the execution fact source.  This module only joins
those facts at an explicit DatasetSnapshot boundary and emits one sample per
policy decision.  It does not choose an advantage estimator or invent a
missing reward.  A verifier view may be selected explicitly; without one, all
versioned annotations remain visible in the sample facts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .models import (
    CostRecord,
    ExecutionEvent,
    PolicyDecision,
    RewardAnnotation,
    Trajectory,
)
from .snapshot import DatasetSnapshot

SCHEMA_VERSION = "openai4s.rl-training.v1"


def _json(value: Any) -> Any:
    """Detach a JSON value without coercing unsupported scientific facts."""

    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise TypeError("training projection contains a non-JSON value") from exc


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _record_dict(value: Any) -> dict[str, Any]:
    method = getattr(value, "to_dict", None)
    if not callable(method):
        raise TypeError("training projection requires trajectory records")
    result = method()
    if not isinstance(result, dict):
        raise TypeError("trajectory record did not produce an object")
    return _json(result)


@dataclass(frozen=True)
class TrainingSample:
    """One policy decision and only the facts explicitly joined to it."""

    episode_id: str
    decision_id: str
    turn: int
    observation: Mapping[str, Any]
    decision: Mapping[str, Any]
    segment_ids: tuple[str, ...] = ()
    execution_event_ids: tuple[str, ...] = ()
    rewards: tuple[Mapping[str, Any], ...] = ()
    costs: tuple[Mapping[str, Any], ...] = ()
    terminal: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.episode_id, str) or not self.episode_id:
            raise ValueError("episode_id must be a non-empty string")
        if not isinstance(self.decision_id, str) or not self.decision_id:
            raise ValueError("decision_id must be a non-empty string")
        if (
            isinstance(self.turn, bool)
            or not isinstance(self.turn, int)
            or self.turn < 0
        ):
            raise ValueError("turn must be a non-negative integer")
        object.__setattr__(self, "observation", _json(dict(self.observation)))
        object.__setattr__(self, "decision", _json(dict(self.decision)))
        object.__setattr__(self, "segment_ids", tuple(self.segment_ids))
        object.__setattr__(self, "execution_event_ids", tuple(self.execution_event_ids))
        object.__setattr__(
            self,
            "rewards",
            tuple(_json(dict(reward)) for reward in self.rewards),
        )
        object.__setattr__(
            self,
            "costs",
            tuple(_json(dict(cost)) for cost in self.costs),
        )
        if not isinstance(self.terminal, bool):
            raise TypeError("terminal must be a bool")

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "decision_id": self.decision_id,
            "turn": self.turn,
            "observation": dict(self.observation),
            "decision": dict(self.decision),
            "segment_ids": list(self.segment_ids),
            "execution_event_ids": list(self.execution_event_ids),
            "rewards": [dict(reward) for reward in self.rewards],
            "costs": [dict(cost) for cost in self.costs],
            "terminal": self.terminal,
        }


@dataclass(frozen=True)
class RolloutDataset:
    """A deterministic, snapshot-bound projection for a trainer.

    ``episode_rewards`` deliberately stays outside decision samples.  Turning
    an episode or segment annotation into a per-token training target is an
    algorithmic attribution choice and belongs to the external trainer.
    """

    snapshot: DatasetSnapshot
    episode: Mapping[str, Any]
    samples: tuple[TrainingSample, ...]
    episode_rewards: tuple[Mapping[str, Any], ...] = ()
    reward_verifier: str | None = None
    reward_verifier_version: str | None = None
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_trajectory(
        cls,
        trajectory: Trajectory,
        snapshot: DatasetSnapshot,
        *,
        verifier: str | None = None,
        verifier_version: str | None = None,
    ) -> "RolloutDataset":
        if not isinstance(trajectory, Trajectory):
            raise TypeError("trajectory must be a Trajectory")
        if not isinstance(snapshot, DatasetSnapshot):
            raise TypeError("snapshot must be a DatasetSnapshot")
        if (verifier is None) != (verifier_version is None):
            raise ValueError("verifier and verifier_version must be supplied together")
        if not snapshot.matches(trajectory):
            raise ValueError("trajectory does not match the dataset snapshot")

        decisions = tuple(trajectory.decisions)
        observations = {item.observation_id: item for item in trajectory.observations}
        segments_by_decision: dict[str, set[str]] = {
            item.decision_id: set() for item in decisions
        }
        for segment in trajectory.segments:
            if segment.decision_id in segments_by_decision:
                segments_by_decision[segment.decision_id].add(segment.segment_id)
        events_by_decision: dict[str, list[ExecutionEvent]] = {
            item.decision_id: [] for item in decisions
        }
        for event in trajectory.execution_events:
            if event.decision_id in events_by_decision:
                events_by_decision[event.decision_id].append(event)
                if event.segment_id is not None:
                    segments_by_decision[event.decision_id].add(event.segment_id)

        selected_rewards = tuple(
            item
            for item in trajectory.rewards
            if verifier is None
            or (item.verifier == verifier and item.verifier_version == verifier_version)
        )
        rewards_by_decision: dict[str, list[RewardAnnotation]] = {
            item.decision_id: [] for item in decisions
        }
        for reward in selected_rewards:
            if reward.decision_id in rewards_by_decision:
                rewards_by_decision[reward.decision_id].append(reward)
            elif reward.segment_id is not None:
                for decision_id, segment_ids in segments_by_decision.items():
                    if reward.segment_id in segment_ids:
                        rewards_by_decision[decision_id].append(reward)

        costs_by_decision: dict[str, list[CostRecord]] = {
            item.decision_id: [] for item in decisions
        }
        for cost in trajectory.costs:
            if cost.decision_id in costs_by_decision:
                costs_by_decision[cost.decision_id].append(cost)

        samples: list[TrainingSample] = []
        for decision in decisions:
            observation = observations.get(decision.observation_id)
            if observation is None:
                raise ValueError(
                    f"decision {decision.decision_id!r} references a missing observation"
                )
            samples.append(
                TrainingSample(
                    episode_id=trajectory.episode.episode_id,
                    decision_id=decision.decision_id,
                    turn=decision.turn,
                    observation=_record_dict(observation),
                    decision=_record_dict(decision),
                    segment_ids=tuple(
                        sorted(segments_by_decision[decision.decision_id])
                    ),
                    execution_event_ids=tuple(
                        event.event_id
                        for event in events_by_decision[decision.decision_id]
                    ),
                    rewards=tuple(
                        _record_dict(reward)
                        for reward in rewards_by_decision[decision.decision_id]
                    ),
                    costs=tuple(
                        _record_dict(cost)
                        for cost in costs_by_decision[decision.decision_id]
                    ),
                    terminal=trajectory.episode.status != "running"
                    and decision is decisions[-1],
                )
            )

        return cls(
            snapshot=snapshot,
            episode=_record_dict(trajectory.episode),
            samples=tuple(samples),
            episode_rewards=tuple(
                _record_dict(reward)
                for reward in selected_rewards
                if reward.decision_id is None and reward.segment_id is None
            ),
            reward_verifier=verifier,
            reward_verifier_version=verifier_version,
        )

    def to_dict(self) -> dict[str, Any]:
        reward_view = None
        if self.reward_verifier is not None:
            reward_view = {
                "verifier": self.reward_verifier,
                "verifier_version": self.reward_verifier_version,
            }
        return {
            "schema_version": self.schema_version,
            "snapshot": self.snapshot.to_dict(),
            "episode": dict(self.episode),
            "reward_view": reward_view,
            "samples": [sample.to_dict() for sample in self.samples],
            "episode_rewards": [dict(reward) for reward in self.episode_rewards],
        }

    def canonical_json(self) -> str:
        return _canonical(self.to_dict())

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def jsonl(self) -> str:
        """Return a deterministic stream envelope followed by one sample/line."""

        header = {
            "type": "dataset",
            "schema_version": self.schema_version,
            "snapshot": self.snapshot.to_dict(),
            "episode": dict(self.episode),
            "reward_view": self.to_dict()["reward_view"],
            "episode_rewards": [dict(reward) for reward in self.episode_rewards],
        }
        lines = [_canonical(header)]
        lines.extend(
            _canonical({"type": "sample", **sample.to_dict()})
            for sample in self.samples
        )
        return "\n".join(lines) + "\n"


__all__ = ["RolloutDataset", "SCHEMA_VERSION", "TrainingSample"]
