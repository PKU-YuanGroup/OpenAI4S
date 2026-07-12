"""Provider registry and provider-neutral chat orchestration."""

from __future__ import annotations

from typing import Any

from openai4s.config import LLMConfig

from .capabilities import (
    bind_provider_registry,
    get_model_capabilities,
    legacy_provider_specs,
    normalize_usage,
    validate_model_request,
)
from .messages import _is_parts
from .models import LLMError
from .providers import _WIRE_DISPATCH
from .tooling import _canonical_tool_specs

# Keep the original mutable registry shape as a compatibility facade.  The
# immutable, queryable source of truth lives in ``llm.capabilities``.
PROVIDERS: dict[str, dict[str, Any]] = legacy_provider_specs()
bind_provider_registry(PROVIDERS)

# Model ids served by the Ark plan/v3 gateway (all share the `ark` provider's
# endpoint + key). Surfaced as ready-to-pick model profiles in Customize → Models.
ARK_PLAN_MODELS: tuple[tuple[str, str], ...] = (
    ("doubao-seed-2.0-pro", "Doubao Seed 2.0 Pro"),
    ("doubao-seed-2.0-code", "Doubao Seed 2.0 Code"),
    ("doubao-seed-2.0-lite", "Doubao Seed 2.0 Lite"),
    ("doubao-seed-2.0-mini", "Doubao Seed 2.0 Mini"),
    ("glm-5.2", "GLM 5.2"),
    ("kimi-k2.7-code", "Kimi K2.7 Code"),
    ("deepseek-v4-pro", "DeepSeek V4 Pro"),
    ("deepseek-v4-flash", "DeepSeek V4 Flash"),
    ("minimax-m3", "MiniMax M3"),
    ("minimax-m2.7", "MiniMax M2.7"),
    ("kimi-k2.6", "Kimi K2.6"),
)


def provider_spec(name: str) -> dict[str, Any]:
    spec = PROVIDERS.get(name.lower())
    if spec is None:
        raise LLMError(
            f"unknown provider {name!r}; known: {', '.join(sorted(PROVIDERS))}"
        )
    return spec


def supports_vision(provider: str) -> bool:
    # Resolve through the capability layer so a deployment override is honored;
    # keep provider_spec's historical LLMError for unknown names.
    provider_spec(provider)
    return get_model_capabilities(provider).vision


def _guard_vision(provider: str, messages: list[dict], *, capabilities=None) -> None:
    """Raise a clear error if image parts are sent to a text-only provider."""
    if (capabilities or get_model_capabilities(provider)).vision:
        return
    for message in messages:
        if _is_parts(message.get("content")) and any(
            part.get("type") == "image" for part in message["content"]
        ):
            raise LLMError(
                f"provider {provider!r} has no vision support; image parts are "
                f"only accepted by: "
                f"{', '.join(name for name in PROVIDERS if PROVIDERS[name]['vision'])}"
            )


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
    post_json,
    post_sse,
) -> dict[str, Any]:
    """Route one normalized request through the configured provider adapter."""
    spec = provider_spec(cfg.provider)
    base = cfg.base_url or spec["base_url"]
    model = cfg.model or spec["model"]
    capabilities = get_model_capabilities(cfg.provider, model, base_url=base)
    if not cfg.api_key and not capabilities.local_endpoint:
        raise LLMError(
            f"no API key configured for provider {cfg.provider!r}: set the "
            f"OPENAI4S_{cfg.provider.upper()}_API_KEY (or generic OPENAI4S_LLM_API_KEY) "
            f"environment variable, or add it to a .env file at the repo root. "
            f"See .env.example."
        )
    _guard_vision(cfg.provider, messages, capabilities=capabilities)
    validate_model_request(
        cfg.provider,
        model,
        base_url=base,
        parallel_tool_calls=bool(parallel_tool_calls),
        vision=any(
            _is_parts(message.get("content"))
            and any(part.get("type") == "image" for part in message["content"])
            for message in messages
        ),
        streaming=on_delta is not None,
        max_output_tokens=max_tokens,
    )
    wire = spec["wire"]
    caller = _WIRE_DISPATCH[wire]
    # Local auto-discovery establishes only OpenAI wire compatibility. Until a
    # deployment/model capability override explicitly enables tool calling,
    # keep that request on the Code-as-Action path instead of sending an
    # unsupported schema and failing the whole turn.
    canonical_tools = _canonical_tool_specs(tools) if capabilities.tool_calling else []
    if canonical_tools and not capabilities.strict_tool_schema:
        canonical_tools = [
            {**declaration, "strict": False} for declaration in canonical_tools
        ]
    effective_parallel = parallel_tool_calls
    if canonical_tools and effective_parallel is None:
        effective_parallel = capabilities.parallel_tool_calls
    if not canonical_tools:
        effective_parallel = None
    transport_args = {"post_sse": post_sse}
    if wire == "openai":
        transport_args["post_json"] = post_json
    elif wire in ("anthropic", "gemini"):
        transport_args = {"post_json": post_json}
    reply = caller(
        messages,
        cfg,
        base,
        model,
        max_tokens,
        temperature,
        stop,
        on_delta=on_delta,
        tools=canonical_tools,
        tool_choice=tool_choice,
        parallel_tool_calls=effective_parallel,
        **transport_args,
    )
    reply["usage"] = normalize_usage(reply.get("usage"), capabilities.usage_mapping)
    return reply
