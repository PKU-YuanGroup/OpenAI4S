"""Provider registry and provider-neutral chat orchestration."""

from __future__ import annotations

from typing import Any

from openai4s.config import LLMConfig

from .capabilities import (
    get_model_capabilities,
    normalize_usage,
    validate_model_request,
)
from .catalog import ARK_PLAN_MODELS
from .messages import _is_parts
from .models import LLMError, MissingCredentialError
from .providers import _WIRE_DISPATCH
from .registry import PROVIDERS, provider_spec
from .tooling import _canonical_tool_specs
from .transport import CallState, bind_call_context


def supports_vision(provider: str) -> bool:
    # Resolve through the capability layer so a deployment override is honored;
    # keep provider_spec's historical LLMError for unknown names.
    provider_spec(provider)
    return get_model_capabilities(provider).vision


def supports_vision_for(cfg: LLMConfig) -> bool:
    """Vision for the exact provider+endpoint+model triple a call would use.

    ``supports_vision(provider)`` answers for the provider's *default* model at
    its *default* endpoint, which is not what a configured session sends. A
    session pinned to a text-only model on a vision-capable provider therefore
    passed a caller's pre-flight check and was then refused by ``_guard_vision``
    below -- which does resolve the triple -- turning a graceful text fallback
    into a failed turn. Resolved exactly as ``chat`` resolves it, so the
    pre-flight answer and the guard cannot disagree.
    """
    spec = provider_spec(cfg.provider)
    return get_model_capabilities(
        cfg.provider,
        cfg.model or spec["model"],
        base_url=cfg.base_url or spec["base_url"],
    ).vision


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
    should_cancel=None,
    post_json,
    post_sse,
    call_state: CallState | None = None,
) -> dict[str, Any]:
    """Route one normalized request through the configured provider adapter."""
    try:
        spec = provider_spec(cfg.provider)
        base = cfg.base_url or spec["base_url"]
        model = cfg.model or spec["model"]
        capabilities = get_model_capabilities(cfg.provider, model, base_url=base)
        if not cfg.api_key and not capabilities.local_endpoint:
            raise MissingCredentialError(
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
        canonical_tools = (
            _canonical_tool_specs(tools) if capabilities.tool_calling else []
        )
        if canonical_tools and not capabilities.strict_tool_schema:
            canonical_tools = [
                {**declaration, "strict": False} for declaration in canonical_tools
            ]
        effective_parallel = parallel_tool_calls
        if canonical_tools and effective_parallel is None:
            effective_parallel = capabilities.parallel_tool_calls
        if not canonical_tools:
            effective_parallel = None
        state = (
            call_state
            or getattr(should_cancel, "call_state", None)
            or CallState(
                should_cancel=should_cancel, total_timeout_s=cfg.total_timeout_s
            )
        )
        context = dict(
            provider=cfg.provider, should_cancel=state.should_cancel, call_state=state
        )
        bound_json = bind_call_context(post_json, **context)
        bound_sse = bind_call_context(post_sse, **context)
        # `responses` is SSE-only; `gemini` has no streaming adapter. The two wires
        # that stream *and* keep a blocking fallback need both transports.
        transport_args = {"post_sse": bound_sse}
        if wire in ("openai", "anthropic"):
            transport_args["post_json"] = bound_json
        elif wire == "gemini":
            transport_args = {"post_json": bound_json}
    except Exception as error:
        error.llm_not_started = True
        raise
    raw_usage = None
    if "post_json" in transport_args:
        send_json = transport_args["post_json"]

        def json_with_evidence(*args, **context):
            nonlocal raw_usage
            body = bind_call_context(send_json, **context)(*args)
            if isinstance(body, dict):
                raw_usage = body.get("usageMetadata" if wire == "gemini" else "usage")
            return body

        transport_args["post_json"] = json_with_evidence
    try:
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
    except Exception as error:
        error.llm_not_started = getattr(error, "llm_not_started", False) or bool(
            state.attempts and not state.sent
        )
        evidence = getattr(error, "usage", None)
        error.usage = normalize_usage(
            evidence if evidence is not None else raw_usage, capabilities.usage_mapping
        )
        raise
    reply["usage"] = normalize_usage(reply.get("usage"), capabilities.usage_mapping)
    return reply
