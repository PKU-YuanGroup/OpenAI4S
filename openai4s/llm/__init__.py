"""Pure-stdlib multi-provider LLM client.

The package facade preserves the original ``openai4s.llm`` surface while the
wire implementations live in focused provider modules.
"""

from __future__ import annotations

from typing import Any

import openai4s.llm.transport as transport
from openai4s.config import LLMConfig

from .client import ARK_PLAN_MODELS, PROVIDERS
from .client import chat as _client_chat
from .client import provider_spec, supports_vision
from .models import LLMError
from .providers.anthropic import _ANTHROPIC_VERSION

ANTHROPIC_VERSION = _ANTHROPIC_VERSION

__all__ = [
    "ARK_PLAN_MODELS",
    "LLMError",
    "PROVIDERS",
    "chat",
    "provider_spec",
    "supports_vision",
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
