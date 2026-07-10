"""Provider registry and provider-neutral chat orchestration."""

from __future__ import annotations

from typing import Any

from openai4s.config import LLMConfig

from .messages import _is_parts
from .models import LLMError
from .providers import _WIRE_DISPATCH
from .tooling import _canonical_tool_specs

PROVIDERS: dict[str, dict[str, Any]] = {
    # Volcengine Ark "plan" gateway (火山方舟) — one OpenAI-compatible endpoint that
    # fronts many model families (doubao / glm / kimi / deepseek / minimax). Pick
    # the concrete model per config/profile; the key + endpoint are shared.
    "ark": {
        "wire": "openai",
        "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3",
        "model": "doubao-seed-2.0-pro",
        "vision": True,
    },
    # Official first-party endpoints for the frontier labs.
    "chatgpt": {
        "wire": "openai",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-5",
        "vision": True,
    },
    "openai_responses": {
        "wire": "responses",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-5",
        # The Responses wire is currently text/tool only. Marking this false
        # prevents the adapter from silently flattening image parts.
        "vision": False,
    },
    "claude": {
        "wire": "anthropic",
        "base_url": "https://api.anthropic.com",
        "model": "claude-sonnet-4-5",
        "vision": True,
    },
    "gemini": {
        "wire": "gemini",
        "base_url": "https://generativelanguage.googleapis.com",
        "model": "gemini-2.5-flash",
        "vision": True,
    },
}

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
    return bool(provider_spec(provider).get("vision"))


def _guard_vision(provider: str, messages: list[dict]) -> None:
    """Raise a clear error if image parts are sent to a text-only provider."""
    if supports_vision(provider):
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
    if not cfg.api_key:
        raise LLMError(
            f"no API key configured for provider {cfg.provider!r}: set the "
            f"OPENAI4S_{cfg.provider.upper()}_API_KEY (or generic OPENAI4S_LLM_API_KEY) "
            f"environment variable, or add it to a .env file at the repo root. "
            f"See .env.example."
        )
    spec = provider_spec(cfg.provider)
    _guard_vision(cfg.provider, messages)
    base = cfg.base_url or spec["base_url"]
    model = cfg.model or spec["model"]
    wire = spec["wire"]
    caller = _WIRE_DISPATCH[wire]
    canonical_tools = _canonical_tool_specs(tools)
    transport_args = {"post_sse": post_sse}
    if wire == "openai":
        transport_args["post_json"] = post_json
    elif wire in ("anthropic", "gemini"):
        transport_args = {"post_json": post_json}
    return caller(
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
        parallel_tool_calls=parallel_tool_calls,
        **transport_args,
    )
