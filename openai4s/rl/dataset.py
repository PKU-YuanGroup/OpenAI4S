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
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Mapping

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
    execution_events: tuple[Mapping[str, Any], ...] = ()
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
            "execution_events",
            tuple(_json(dict(event)) for event in self.execution_events),
        )
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
            "execution_events": [dict(event) for event in self.execution_events],
            "rewards": [dict(reward) for reward in self.rewards],
            "costs": [dict(cost) for cost in self.costs],
            "terminal": self.terminal,
        }


@dataclass(frozen=True)
class RolloutDataset:
    """A deterministic, snapshot-bound projection for a trainer.

    ``segment_rewards`` and ``episode_rewards`` deliberately stay outside
    decision samples when no exact decision join exists. Turning a segment or
    episode annotation into a per-token training target is an algorithmic
    attribution choice and belongs to the external trainer.
    """

    snapshot: DatasetSnapshot
    episode: Mapping[str, Any]
    samples: tuple[TrainingSample, ...]
    segment_rewards: tuple[Mapping[str, Any], ...] = ()
    episode_rewards: tuple[Mapping[str, Any], ...] = ()
    episode_costs: tuple[Mapping[str, Any], ...] = ()
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
        if trajectory.episode.status == "running":
            raise ValueError("training datasets require a terminal episode")
        if not snapshot.matches(trajectory):
            raise ValueError("trajectory does not match the dataset snapshot")

        decisions = tuple(trajectory.decisions)
        known_decisions = {item.decision_id for item in decisions}
        known_segments = {item.segment_id for item in trajectory.segments}
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
            if (
                event.decision_id is not None
                and event.decision_id not in known_decisions
            ):
                raise ValueError(
                    f"execution event {event.event_id!r} references an unknown decision"
                )
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
        for reward in selected_rewards:
            if (
                reward.decision_id is not None
                and reward.decision_id not in known_decisions
            ):
                raise ValueError(
                    f"reward {reward.annotation_id!r} references an unknown decision"
                )
            if (
                reward.segment_id is not None
                and reward.segment_id not in known_segments
            ):
                raise ValueError(
                    f"reward {reward.annotation_id!r} references an unknown segment"
                )
        rewards_by_decision: dict[str, list[RewardAnnotation]] = {
            item.decision_id: [] for item in decisions
        }
        known_segment_ids = {item.segment_id for item in trajectory.segments}
        # Every segment-only reward belongs in the envelope, whether or not a
        # decision touched the segment; otherwise it is in no sample and no
        # envelope at all.
        segment_rewards = tuple(
            item
            for item in selected_rewards
            if item.decision_id is None
            and item.segment_id is not None
            and item.segment_id in known_segment_ids
        )
        for reward in selected_rewards:
            if reward.decision_id in rewards_by_decision:
                rewards_by_decision[reward.decision_id].append(reward)
            # Segment-scoped reward remains in the dataset envelope. It is
            # one annotation for the phase, not N additive per-decision rewards.

        costs_by_decision: dict[str, list[CostRecord]] = {
            item.decision_id: [] for item in decisions
        }
        episode_costs: list[CostRecord] = []
        for cost in trajectory.costs:
            if cost.decision_id in costs_by_decision:
                costs_by_decision[cost.decision_id].append(cost)
            elif cost.decision_id is None:
                episode_costs.append(cost)
            else:
                raise ValueError(
                    f"cost {cost.cost_id!r} references an unknown decision"
                )

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
                    execution_events=tuple(
                        _record_dict(event)
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
            segment_rewards=tuple(_record_dict(reward) for reward in segment_rewards),
            episode_rewards=tuple(
                _record_dict(reward)
                for reward in selected_rewards
                if reward.decision_id is None and reward.segment_id is None
            ),
            episode_costs=tuple(_record_dict(cost) for cost in episode_costs),
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
            "segment_rewards": [dict(reward) for reward in self.segment_rewards],
            "episode_rewards": [dict(reward) for reward in self.episode_rewards],
            "episode_costs": [dict(cost) for cost in self.episode_costs],
        }

    def canonical_json(self) -> str:
        return _canonical(self.to_dict())

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def jsonl(self) -> str:
        """Return a deterministic stream envelope followed by one sample/line."""

        return "\n".join(self.iter_jsonl()) + "\n"

    def iter_jsonl(self) -> Iterator[str]:
        """Yield the deterministic JSONL envelope and samples one line at a time."""

        header = {
            "type": "dataset",
            "schema_version": self.schema_version,
            "snapshot": self.snapshot.to_dict(),
            "episode": dict(self.episode),
            "reward_view": self.to_dict()["reward_view"],
            "segment_rewards": [dict(reward) for reward in self.segment_rewards],
            "episode_rewards": [dict(reward) for reward in self.episode_rewards],
            "episode_costs": [dict(cost) for cost in self.episode_costs],
        }
        yield _canonical(header)
        for sample in self.samples:
            yield _canonical({"type": "sample", **sample.to_dict()})


__all__ = ["RolloutDataset", "SCHEMA_VERSION", "TrainingSample"]
