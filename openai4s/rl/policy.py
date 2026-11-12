"""Provider-neutral behavior-policy adapter for external rollout samplers."""

from __future__ import annotations

import asyncio
import inspect
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence, cast

from openai4s.agent.models import Message, ModelReply


class PolicyProvider(Protocol):
    """External sampler contract owned by the trainer or model service."""

    def generate(
        self,
        observation: tuple[Message, ...],
        *,
        tools: Sequence[Mapping[str, object]] = (),
        config: Mapping[str, object],
    ) -> (
        ModelReply | Mapping[str, object] | Awaitable[ModelReply | Mapping[str, object]]
    ): ...


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
        if inspect.isawaitable(raw):
            close = getattr(raw, "close", None)
            if callable(close):
                close()
            raise TypeError(
                "policy provider returned an awaitable; use PolicyAdapter.sample_async()"
            )
        reply = _normalize_reply(raw)
        return self._decorate(reply)

    async def sample_async(
        self,
        observation: tuple[Message, ...],
        *,
        tools: Sequence[Mapping[str, object]] = (),
    ) -> ModelReply:
        """Sample from a sync or async provider without changing join keys.

        Async rollout workers may use a provider backed by an inference server
        while local tests and small trainers keep a synchronous ``generate``.
        A provider that returns an awaitable is only awaited here; the Engine
        still owns the subsequent action execution and ledger writes.
        """

        detached_tools = tuple(deepcopy(dict(tool)) for tool in tools)
        if inspect.iscoroutinefunction(getattr(self.provider, "generate", None)):
            raw = _generate(self.provider, observation, detached_tools, self.config)
            raw = await raw if inspect.isawaitable(raw) else raw
        else:
            raw = await asyncio.to_thread(
                _generate, self.provider, observation, detached_tools, self.config
            )
            if inspect.isawaitable(raw):
                raw = await raw
        return self._decorate(_normalize_reply(raw))

    def _decorate(self, reply: ModelReply) -> ModelReply:
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


def _reply_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    """Accept common trainer names while keeping ModelReply provider-neutral."""

    normalized = dict(value)
    if "content" not in normalized and isinstance(normalized.get("text"), str):
        normalized["content"] = normalized["text"]
    if "token_logprobs" not in normalized and "logprobs" in normalized:
        normalized["token_logprobs"] = normalized["logprobs"]
    return normalized


def _normalize_reply(value: ModelReply | Mapping[str, object]) -> ModelReply:
    if isinstance(value, ModelReply):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("policy provider must return ModelReply or a mapping")
    return ModelReply.from_mapping(_reply_mapping(value))


def _generate(
    provider: PolicyProvider,
    observation: tuple[Message, ...],
    tools: Sequence[Mapping[str, object]],
    config: Mapping[str, object],
) -> ModelReply | Mapping[str, object] | Awaitable[ModelReply | Mapping[str, object]]:
    """Call modern providers while retaining the original two-argument seam."""

    generate = provider.generate
    try:
        parameters: Mapping[str, inspect.Parameter] = inspect.signature(
            generate
        ).parameters
    except (TypeError, ValueError):
        parameters = {}
    tool_parameter = parameters.get("tools")
    config_parameter = parameters.get("config") or parameters.get("sampling_config")
    args: list[Any] = [observation]
    kwargs: dict[str, Any] = {}
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
    elif sum(
        parameter.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        for parameter in parameters.values()
    ) > len(args):
        # Some older adapters used an unnamed ``settings``/``options``
        # positional parameter.  Pass the config by position when that slot
        # exists, while leaving true one-argument providers untouched.
        args.append(config)
    # Signature inspection is the compatibility boundary: the Protocol keeps
    # the modern shape typed, while this cast permits legacy providers with a
    # positional tools/config slot to remain supported at runtime.
    callable_generate = cast(Callable[..., Any], generate)
    return callable_generate(*args, **kwargs)


__all__ = ["PolicyAdapter", "PolicyProvider"]
