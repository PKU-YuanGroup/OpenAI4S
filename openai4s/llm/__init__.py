"""Pure-stdlib multi-provider LLM client.

The package facade preserves the original ``openai4s.llm`` surface while the
wire implementations live in focused provider modules.
"""

from __future__ import annotations

from typing import Any

import openai4s.llm.transport as transport
from openai4s.config import LLMConfig

from .capabilities import (
    SUPPORTED_WIRES,
    CapabilityCacheInfo,
    CapabilityError,
    CostMetadata,
    ModelCapabilities,
    ProviderCapabilities,
    UsageMapping,
    bind_provider_registry,
    calculate_usage_cost_usd,
    capability_cache_info,
    clear_capability_cache,
    clear_capability_overrides,
    get_model_capabilities,
    get_provider_capabilities,
    model_capabilities,
    normalize_usage,
    provider_capabilities,
    set_capability_override,
    validate_model_request,
)
from .catalog import (
    ARK_PLAN_MODELS,
    ModelPreset,
    model_presets,
    register_model_preset,
    unregister_model_preset,
)
from .client import chat as _client_chat
from .client import supports_vision
from .models import LLMError, TransportError, parse_retry_after
from .providers.anthropic import _ANTHROPIC_VERSION
from .registry import (
    PROVIDERS,
    provider_spec,
    provider_specs,
    register_provider,
    unregister_provider,
)

ANTHROPIC_VERSION = _ANTHROPIC_VERSION

__all__ = [
    "ARK_PLAN_MODELS",
    "CapabilityCacheInfo",
    "CapabilityError",
    "CostMetadata",
    "LLMError",
    "ModelCapabilities",
    "ModelPreset",
    "PROVIDERS",
    "ProviderCapabilities",
    "SUPPORTED_WIRES",
    "TransportError",
    "UsageMapping",
    "parse_retry_after",
    "bind_provider_registry",
    "calculate_usage_cost_usd",
    "capability_cache_info",
    "chat",
    "clear_capability_cache",
    "clear_capability_overrides",
    "get_model_capabilities",
    "get_provider_capabilities",
    "model_capabilities",
    "model_presets",
    "normalize_usage",
    "provider_spec",
    "provider_capabilities",
    "provider_specs",
    "register_model_preset",
    "register_provider",
    "set_capability_override",
    "supports_vision",
    "unregister_model_preset",
    "unregister_provider",
    "validate_model_request",
]


def _post_json(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    """Compatibility hook forwarding to the package transport."""
    return transport.post_json(url, payload, headers, timeout)


def _post_sse(url: str, payload: dict, headers: dict, timeout: float, on_event) -> None:
    """Compatibility hook forwarding to the package transport."""
    return transport.post_sse(url, payload, headers, timeout, on_event)


def chat(
    messages: list[dict[str, Any]],
    cfg: LLMConfig,
    *,
    max_tokens: int | None = None,
    temperature: float | None = None,
    stop: list[str] | None = None,
    on_delta=None,
    tools: list[Any] | tuple[Any, ...] | None = None,
    tool_choice: Any = None,
    parallel_tool_calls: bool | None = None,
) -> dict[str, Any]:
    """One blocking chat-completion call against the configured provider.

    ``_post_json`` and ``_post_sse`` deliberately remain facade globals so the
    existing offline transport injection contract continues to work after the
    module-to-package split.
    """
    return _client_chat(
        messages,
        cfg,
        max_tokens=max_tokens,
        temperature=temperature,
        stop=stop,
        on_delta=on_delta,
        tools=tools,
        tool_choice=tool_choice,
        parallel_tool_calls=parallel_tool_calls,
        post_json=_post_json,
        post_sse=_post_sse,
    )
