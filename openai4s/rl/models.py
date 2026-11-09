"""Stable, JSON-native records used by the RL runtime boundary.

These records describe what happened during a rollout.  They intentionally do
not contain reward computation or optimizer state.  Reward annotations are
separate immutable records so changing a verifier never rewrites execution
history.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Mapping


def _text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _optional_text(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    return _text(name, value)


def _finite(name: str, value: float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite number or None")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number or None")
    return result


def _finite_value(name: str, value: float) -> float:
    result = _finite(name, value)
    assert result is not None
    return result


def _integer(name: str, value: int | None, *, minimum: int = 0) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum} or None")
    return value


def _json_value(value: Any) -> Any:
    """Return a detached JSON-compatible value or fail loudly.

    Silent ``str(value)`` coercion would make a trajectory look complete while
    losing token/logprob or scientific evidence types.  Callers may use the
    explicit ``repr`` field in their payload when they really need text.
    """

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("trajectory payload contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        return {
            _text("payload key", str(key)): _json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise TypeError(f"trajectory payload contains unsupported value {type(value)!r}")


def _payload(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("payload must be a mapping")
    return _json_value(value)


def _tuple_floats(
    name: str, values: tuple[float, ...] | list[float] | None
) -> tuple[float, ...] | None:
    if values is None:
        return None
    if not isinstance(values, (tuple, list)):
        raise TypeError(f"{name} must be a list or tuple")
    return tuple(_finite_value(name, value) for value in values)


def _tuple_ints(
    name: str, values: tuple[int, ...] | list[int] | None
) -> tuple[int, ...] | None:
    if values is None:
        return None
    if not isinstance(values, (tuple, list)):
        raise TypeError(f"{name} must be a list or tuple")
    checked: list[int] = []
    for value in values:
        checked_value = _integer(name, value, minimum=0)
        assert checked_value is not None
        checked.append(checked_value)
    return tuple(checked)


@dataclass(frozen=True)
class Episode:
    """Identity and lifecycle projection for one trainer-visible rollout."""

    episode_id: str
    task: str
    session_id: str
    created_at: int
    policy_name: str | None = None
    policy_version: str | None = None
    parent_episode_id: str | None = None
    status: str = "running"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _text("episode_id", self.episode_id)
        _text("task", self.task)
        _text("session_id", self.session_id)
        _integer("created_at", self.created_at)
        _optional_text("policy_name", self.policy_name)
        _optional_text("policy_version", self.policy_version)
        _optional_text("parent_episode_id", self.parent_episode_id)
        _text("status", self.status)
        object.__setattr__(self, "metadata", _payload(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "task": self.task,
            "session_id": self.session_id,
            "created_at": self.created_at,
            "policy_name": self.policy_name,
            "policy_version": self.policy_version,
            "parent_episode_id": self.parent_episode_id,
            "status": self.status,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class PolicyDecision:
    """One policy sampling boundary, including optional white-box telemetry."""

    decision_id: str
    episode_id: str
    turn: int
    observation_id: str
    prompt_hash: str
    policy_name: str
    policy_version: str
    action: Mapping[str, Any]
    created_at: int
    session_id: str | None = None
    parent_decision_id: str | None = None
    frame_id: str | None = None
    action_group_id: str | None = None
    action_logprob: float | None = None
    token_ids: tuple[int, ...] | list[int] | None = None
    token_logprobs: tuple[float, ...] | list[float] | None = None
    loss_mask: tuple[int, ...] | list[int] | None = None
    raw_response: Mapping[str, Any] | None = None
    status: str = "sampled"
    assistant_message: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("decision_id", self.decision_id),
            ("episode_id", self.episode_id),
            ("observation_id", self.observation_id),
            ("prompt_hash", self.prompt_hash),
            ("policy_name", self.policy_name),
            ("policy_version", self.policy_version),
        ):
            _text(name, value)
        _integer("turn", self.turn)
        _integer("created_at", self.created_at)
        _optional_text("session_id", self.session_id)
        _optional_text("parent_decision_id", self.parent_decision_id)
        _optional_text("frame_id", self.frame_id)
        _optional_text("action_group_id", self.action_group_id)
        _finite("action_logprob", self.action_logprob)
        object.__setattr__(self, "action", _payload(self.action))
        object.__setattr__(self, "token_ids", _tuple_ints("token_ids", self.token_ids))
        object.__setattr__(
            self, "token_logprobs", _tuple_floats("token_logprobs", self.token_logprobs)
        )
        object.__setattr__(self, "loss_mask", _tuple_ints("loss_mask", self.loss_mask))
        if self.token_logprobs is not None and self.token_ids is not None:
            if len(self.token_logprobs) != len(self.token_ids):
                raise ValueError("token_ids and token_logprobs must have equal length")
        if self.loss_mask is not None and self.token_ids is not None:
            if len(self.loss_mask) != len(self.token_ids):
                raise ValueError("loss_mask and token_ids must have equal length")
            if any(value not in (0, 1) for value in self.loss_mask):
                raise ValueError("loss_mask values must be 0 or 1")
        if self.raw_response is not None:
            object.__setattr__(self, "raw_response", _payload(self.raw_response))
        if self.assistant_message is not None:
            object.__setattr__(
                self, "assistant_message", _payload(self.assistant_message)
            )
        _text("status", self.status)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "decision_id": self.decision_id,
            "episode_id": self.episode_id,
            "turn": self.turn,
            "observation_id": self.observation_id,
            "prompt_hash": self.prompt_hash,
            "policy_name": self.policy_name,
            "policy_version": self.policy_version,
            "action": dict(self.action),
            "created_at": self.created_at,
            "session_id": self.session_id,
            "parent_decision_id": self.parent_decision_id,
            "frame_id": self.frame_id,
            "action_group_id": self.action_group_id,
            "action_logprob": self.action_logprob,
            "token_ids": list(self.token_ids) if self.token_ids is not None else None,
            "token_logprobs": (
                list(self.token_logprobs) if self.token_logprobs is not None else None
            ),
            "loss_mask": list(self.loss_mask) if self.loss_mask is not None else None,
            "raw_response": (
                dict(self.raw_response) if self.raw_response is not None else None
            ),
            "status": self.status,
        }
        if self.assistant_message is not None:
            data["assistant_message"] = _payload(self.assistant_message)
        return data


@dataclass(frozen=True)
class Observation:
    observation_id: str
    episode_id: str
    decision_id: str
    messages: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]]
    created_at: int
    source: str = "agent_context"
    truncated: bool = False

    def __post_init__(self) -> None:
        for name, value in (
            ("observation_id", self.observation_id),
            ("episode_id", self.episode_id),
            ("decision_id", self.decision_id),
            ("source", self.source),
        ):
            _text(name, value)
        _integer("created_at", self.created_at)
        if not isinstance(self.messages, (tuple, list)):
            raise TypeError("messages must be a list or tuple")
        normalized = tuple(_payload(message) for message in self.messages)
        object.__setattr__(self, "messages", normalized)
        if not isinstance(self.truncated, bool):
            raise TypeError("truncated must be a bool")

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "episode_id": self.episode_id,
            "decision_id": self.decision_id,
            "messages": [dict(message) for message in self.messages],
            "created_at": self.created_at,
            "source": self.source,
            "truncated": self.truncated,
        }


@dataclass(frozen=True)
class Segment:
    segment_id: str
    episode_id: str
    name: str
    created_at: int
    decision_id: str | None = None
    parent_segment_id: str | None = None
    objective: str | None = None
    status: str = "open"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (
            ("segment_id", self.segment_id),
            ("episode_id", self.episode_id),
            ("name", self.name),
            ("status", self.status),
        ):
            _text(name, value)
        _integer("created_at", self.created_at)
        _optional_text("decision_id", self.decision_id)
        _optional_text("parent_segment_id", self.parent_segment_id)
        _optional_text("objective", self.objective)
        object.__setattr__(self, "metadata", _payload(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "episode_id": self.episode_id,
            "name": self.name,
            "created_at": self.created_at,
            "decision_id": self.decision_id,
            "parent_segment_id": self.parent_segment_id,
            "objective": self.objective,
            "status": self.status,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ExecutionEvent:
    event_id: str
    episode_id: str
    kind: str
    created_at: int
    payload: Mapping[str, Any] = field(default_factory=dict)
    decision_id: str | None = None
    segment_id: str | None = None
    source_id: str | None = None
    sequence: int | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("event_id", self.event_id),
            ("episode_id", self.episode_id),
            ("kind", self.kind),
        ):
            _text(name, value)
        _integer("created_at", self.created_at)
        _integer("sequence", self.sequence, minimum=1)
        _optional_text("decision_id", self.decision_id)
        _optional_text("segment_id", self.segment_id)
        _optional_text("source_id", self.source_id)
        object.__setattr__(self, "payload", _payload(self.payload))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "episode_id": self.episode_id,
            "kind": self.kind,
            "created_at": self.created_at,
            "payload": dict(self.payload),
            "decision_id": self.decision_id,
            "segment_id": self.segment_id,
            "source_id": self.source_id,
            "sequence": self.sequence,
        }


@dataclass(frozen=True)
class RewardAnnotation:
    annotation_id: str
    episode_id: str
    value: float
    verifier: str
    verifier_version: str
    created_at: int
    decision_id: str | None = None
    segment_id: str | None = None
    components: Mapping[str, float] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (
            ("annotation_id", self.annotation_id),
            ("episode_id", self.episode_id),
            ("verifier", self.verifier),
            ("verifier_version", self.verifier_version),
        ):
            _text(name, value)
        _finite("value", self.value)
        _integer("created_at", self.created_at)
        _optional_text("decision_id", self.decision_id)
        _optional_text("segment_id", self.segment_id)
        if not isinstance(self.components, Mapping):
            raise TypeError("components must be a mapping")
        checked = {
            str(key): _finite(f"components[{key}]", item)
            for key, item in self.components.items()
        }
        object.__setattr__(self, "components", checked)
        object.__setattr__(self, "metadata", _payload(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "annotation_id": self.annotation_id,
            "episode_id": self.episode_id,
            "value": self.value,
            "verifier": self.verifier,
            "verifier_version": self.verifier_version,
            "created_at": self.created_at,
            "decision_id": self.decision_id,
            "segment_id": self.segment_id,
            "components": dict(self.components),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class CostRecord:
    cost_id: str
    episode_id: str
    created_at: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    wall_time_ms: int | None = None
    gpu_time_ms: int | None = None
    cost_usd: float | None = None
    decision_id: str | None = None
    source: str = "runtime"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _text("cost_id", self.cost_id)
        _text("episode_id", self.episode_id)
        _text("source", self.source)
        _integer("created_at", self.created_at)
        for name in (
            "prompt_tokens",
            "completion_tokens",
            "wall_time_ms",
            "gpu_time_ms",
        ):
            _integer(name, getattr(self, name))
        _finite("cost_usd", self.cost_usd)
        _optional_text("decision_id", self.decision_id)
        object.__setattr__(self, "metadata", _payload(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "cost_id": self.cost_id,
            "episode_id": self.episode_id,
            "created_at": self.created_at,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "wall_time_ms": self.wall_time_ms,
            "gpu_time_ms": self.gpu_time_ms,
            "cost_usd": self.cost_usd,
            "decision_id": self.decision_id,
            "source": self.source,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class RunReceipt:
    """Durable-adapter identity used to make rollout admission idempotent."""

    request_id: str
    fingerprint: str
    episode_id: str
    state: str
    accepted_at: int
    updated_at: int

    def __post_init__(self) -> None:
        for name, value in (
            ("request_id", self.request_id),
            ("fingerprint", self.fingerprint),
            ("episode_id", self.episode_id),
            ("state", self.state),
        ):
            _text(name, value)
        _integer("accepted_at", self.accepted_at)
        _integer("updated_at", self.updated_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "fingerprint": self.fingerprint,
            "episode_id": self.episode_id,
            "state": self.state,
            "accepted_at": self.accepted_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class Trajectory:
    """A complete export envelope consumed by an external RL trainer."""

    episode: Episode
    observations: tuple[Observation, ...] = ()
    decisions: tuple[PolicyDecision, ...] = ()
    segments: tuple[Segment, ...] = ()
    execution_events: tuple[ExecutionEvent, ...] = ()
    rewards: tuple[RewardAnnotation, ...] = ()
    costs: tuple[CostRecord, ...] = ()
    schema_version: str = "openai4s.science-trajectory.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "episode": self.episode.to_dict(),
            "observations": [item.to_dict() for item in self.observations],
            "decisions": [item.to_dict() for item in self.decisions],
            "segments": [item.to_dict() for item in self.segments],
            "execution_events": [item.to_dict() for item in self.execution_events],
            "rewards": [item.to_dict() for item in self.rewards],
            "costs": [item.to_dict() for item in self.costs],
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()
