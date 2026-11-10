"""Provider-neutral behavior-policy adapter for external rollout samplers."""

from __future__ import annotations

import inspect
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Protocol, Sequence

from openai4s.agent.models import Message, ModelReply


class PolicyProvider(Protocol):
    """External sampler contract owned by the trainer or model service."""

    def generate(
        self,
        observation: tuple[Message, ...],
        *,
        tools: Sequence[Mapping[str, object]] = (),
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

    def sample(
        self,
        observation: tuple[Message, ...],
        *,
        tools: Sequence[Mapping[str, object]] = (),
    ) -> ModelReply:
        detached_tools = tuple(deepcopy(dict(tool)) for tool in tools)
        raw = _generate(
            self.provider,
            observation,
            detached_tools,
            self.config,
        )
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


def _generate(
    provider: PolicyProvider,
    observation: tuple[Message, ...],
    tools: Sequence[Mapping[str, object]],
    config: Mapping[str, object],
) -> ModelReply | Mapping[str, object]:
    """Call modern providers while retaining the original two-argument seam."""

    generate = provider.generate
    try:
        parameters = inspect.signature(generate).parameters
    except (TypeError, ValueError):
        parameters = {}
    tool_parameter = parameters.get("tools")
    config_parameter = parameters.get("config") or parameters.get("sampling_config")
    args: list[object] = [observation]
    kwargs: dict[str, object] = {}
    if tool_parameter is not None:
        if tool_parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
            args.append(tools)
        else:
            kwargs[tool_parameter.name] = tools
    elif any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    ):
        kwargs["tools"] = tools
    if config_parameter is not None:
        if config_parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
            args.append(config)
        else:
            kwargs[config_parameter.name] = config
    elif any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    ):
        kwargs["config"] = config
    else:
        # Preserve the original adapter contract for providers whose second
        # argument was named positionally but is hidden from introspection.
        kwargs["config"] = config
    return generate(*args, **kwargs)


__all__ = ["PolicyAdapter", "PolicyProvider"]
