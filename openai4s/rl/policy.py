"""Provider-neutral behavior-policy adapter for external rollout samplers."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping, Protocol

from openai4s.agent.models import Message, ModelReply


class PolicyProvider(Protocol):
    """External sampler contract owned by the trainer or model service."""

    def generate(
        self,
        observation: tuple[Message, ...],
        *,
        config: Mapping[str, object],
    ) -> ModelReply | Mapping[str, object]: ...


@dataclass(frozen=True)
class PolicyAdapter:
    """Normalize one provider response without inventing behavior telemetry."""

    provider: PolicyProvider
    policy_name: str
    policy_version: str
    config: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _text("policy_name", self.policy_name)
        _text("policy_version", self.policy_version)
        if not isinstance(self.config, Mapping):
            raise TypeError("policy config must be a mapping")
        object.__setattr__(self, "config", dict(self.config))

    def sample(self, observation: tuple[Message, ...]) -> ModelReply:
        raw = self.provider.generate(observation, config=self.config)
        reply = raw if isinstance(raw, ModelReply) else ModelReply.from_mapping(raw)
        extra = dict(reply.extra)
        # The adapter is the authority for these join keys. A provider reply
        # cannot relabel itself after it has been admitted by the caller.
        extra["policy_name"] = self.policy_name
        extra["policy_version"] = self.policy_version
        extra["sampling_config"] = dict(self.config)
        # This flag describes evidence actually returned by the behavior
        # provider. A missing logprob stays missing; no second model is called
        # to estimate it after execution.
        extra.setdefault(
            "behavior_logprobs_available",
            reply.extra.get("action_logprob") is not None
            or reply.extra.get("token_logprobs") is not None,
        )
        return replace(reply, extra=extra)


def _text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


__all__ = ["PolicyAdapter", "PolicyProvider"]
