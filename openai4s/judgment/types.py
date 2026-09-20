"""Typed questions, answers, and results for the experimental judgment layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, TypeAlias

_STATUS = Literal["ok", "uncertain", "unavailable", "disabled"]
_KIND = Literal["noul", "choice", "score"]


def _require_instructions(instructions: object) -> str:
    if not isinstance(instructions, str) or not instructions.strip():
        raise ValueError("instructions must be a non-empty string")
    return instructions


def _freeze_mapping(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return dict(value)


@dataclass(frozen=True)
class Noul:
    """Binary fact question. The model returns P(yes) only; no confidence."""

    instructions: str
    criteria: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "instructions", _require_instructions(self.instructions)
        )
        if self.criteria is None:
            return
        if not isinstance(self.criteria, Mapping):
            raise ValueError("Noul criteria must be a mapping or None")
        keys = set(self.criteria)
        if keys != {"true", "false"}:
            raise ValueError(
                "Noul criteria must be None or contain exactly the keys 'true' and 'false'"
            )
        frozen: dict[str, str] = {}
        for key in ("true", "false"):
            item = self.criteria[key]
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"Noul criteria[{key!r}] must be a non-empty string")
            frozen[key] = item
        object.__setattr__(self, "criteria", frozen)

    def to_api(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "noul",
            "instructions": self.instructions,
        }
        if self.criteria is not None:
            payload["criteria"] = {
                "true": self.criteria["true"],
                "false": self.criteria["false"],
            }
        return payload


@dataclass(frozen=True)
class Choice:
    """Single-choice question. 2–255 options; descriptions may be strings or objects."""

    instructions: str
    options: Mapping[str, str | Mapping[str, Any]]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "instructions", _require_instructions(self.instructions)
        )
        if not isinstance(self.options, Mapping):
            raise ValueError("Choice options must be a mapping")
        frozen: dict[str, str | dict[str, Any]] = {}
        for raw_name, description in self.options.items():
            if not isinstance(raw_name, str) or not raw_name.strip():
                raise ValueError("Choice option names must be non-empty strings")
            if isinstance(description, Mapping):
                frozen[raw_name] = dict(description)
            elif isinstance(description, str):
                frozen[raw_name] = description
            else:
                raise ValueError(
                    f"Choice option {raw_name!r} description must be a string or object"
                )
        count = len(frozen)
        if count < 2 or count > 255:
            raise ValueError("Choice must have between 2 and 255 options")
        object.__setattr__(self, "options", frozen)

    def to_api(self) -> dict[str, Any]:
        return {
            "type": "choice",
            "instructions": self.instructions,
            "criteria": dict(self.options),
        }


@dataclass(frozen=True)
class Score:
    """Ordered scale. 2–10 levels; the API criteria field is the level list."""

    instructions: str
    levels: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "instructions", _require_instructions(self.instructions)
        )
        if isinstance(self.levels, str) or not isinstance(self.levels, (tuple, list)):
            raise ValueError("Score levels must be a sequence of strings")
        frozen: list[str] = []
        for item in self.levels:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("Score levels must be non-empty strings")
            frozen.append(item)
        if len(frozen) < 2 or len(frozen) > 10:
            raise ValueError("Score must have between 2 and 10 levels")
        object.__setattr__(self, "levels", tuple(frozen))

    def to_api(self) -> dict[str, Any]:
        return {
            "type": "score",
            "instructions": self.instructions,
            "criteria": list(self.levels),
        }


Question: TypeAlias = Noul | Choice | Score


@dataclass(frozen=True)
class Answer:
    """One typed answer. Noul has no probabilities or confidence."""

    kind: _KIND
    value: float | str
    probabilities: Mapping[str, float] | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        if self.kind not in ("noul", "choice", "score"):
            raise ValueError(f"invalid answer kind: {self.kind!r}")
        object.__setattr__(self, "probabilities", _freeze_mapping(self.probabilities))
        if self.kind == "noul":
            # Jev answers a Noul with P(yes) alone: no distribution and no
            # confidence (plan 5.3 / 5.4). Leaving the fields merely unset
            # would let a later wave read a number the backend never sent.
            if self.probabilities is not None or self.confidence is not None:
                raise ValueError(
                    "noul answers carry no probabilities and no confidence"
                )
            if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
                raise ValueError("noul answer value must be P(yes) as a number")
        elif self.kind == "choice":
            if not isinstance(self.value, str) or not self.value.strip():
                raise ValueError("choice answer value must be the selected option name")
        else:
            if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
                raise ValueError(
                    "score answer value must be the probability-weighted level"
                )

    def to_dict(self) -> dict[str, Any]:
        probabilities = self.probabilities
        return {
            "kind": self.kind,
            "value": self.value,
            "probabilities": dict(probabilities) if probabilities is not None else None,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class JudgmentResult:
    """Host-facing result. Four statuses are normal returns, not errors."""

    status: _STATUS
    purpose: str
    template_id: str
    template_version: str
    policy_version: str
    provider: str
    model: str
    calibrated: bool
    state_sha256: str
    answers: Mapping[str, Answer] = field(default_factory=dict)
    usage: Mapping[str, int] = field(default_factory=dict)
    latency_ms: int = 0
    cache_hit: bool = False
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.status not in ("ok", "uncertain", "unavailable", "disabled"):
            raise ValueError(f"invalid judgment status: {self.status!r}")
        frozen_answers = dict(self.answers)
        frozen_usage = {str(key): int(value) for key, value in self.usage.items()}
        object.__setattr__(self, "answers", frozen_answers)
        object.__setattr__(self, "usage", frozen_usage)

    def to_dict(self) -> dict[str, Any]:
        """JSON-only mapping for host RPC."""

        return {
            "status": self.status,
            "answers": {key: answer.to_dict() for key, answer in self.answers.items()},
            "purpose": self.purpose,
            "template_id": self.template_id,
            "template_version": self.template_version,
            "policy_version": self.policy_version,
            "provider": self.provider,
            "model": self.model,
            "calibrated": self.calibrated,
            "state_sha256": self.state_sha256,
            "usage": dict(self.usage),
            "latency_ms": self.latency_ms,
            "cache_hit": self.cache_hit,
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class BackendReply:
    """Transport-level reply: raw answers, usage, echoed model, request id."""

    answers: Mapping[str, Any]
    usage: Mapping[str, int]
    model: str
    request_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "answers", dict(self.answers))
        object.__setattr__(
            self, "usage", {str(k): int(v) for k, v in self.usage.items()}
        )
        if self.request_id is not None and not isinstance(self.request_id, str):
            raise ValueError("request_id must be a string or None")
