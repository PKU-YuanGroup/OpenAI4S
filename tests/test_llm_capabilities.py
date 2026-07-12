"""Provider capability catalogue and canonical usage accounting contracts."""

from __future__ import annotations

import pytest

from openai4s import llm


@pytest.fixture(autouse=True)
def _clean_capability_overrides():
    llm.clear_capability_overrides()
    yield
    llm.clear_capability_overrides()


def test_legacy_registry_and_immutable_provider_catalog_stay_aligned():
    for name, spec in llm.PROVIDERS.items():
        capabilities = llm.get_provider_capabilities(name)
        assert capabilities.provider == name
        assert capabilities.wire == spec["wire"]
        assert capabilities.default_base_url == spec["base_url"]
        assert capabilities.default_model == spec["model"]
        assert capabilities.vision is spec["vision"]
        assert capabilities.context_limit == capabilities.context_window_tokens
        assert capabilities.output_limit == capabilities.max_output_tokens

    # Public callers historically customize this mutable dict.  Capability
    # snapshots are independent immutable records, not shared nested state.
    assert isinstance(llm.PROVIDERS, dict)
    with pytest.raises((AttributeError, TypeError)):
        llm.get_provider_capabilities("ark").vision = False


def test_model_resolution_marks_custom_and_local_endpoints():
    default = llm.get_model_capabilities("chatgpt", "gpt-5")
    assert default.custom_endpoint is False
    assert default.local_endpoint is False
    assert default.usable_context_tokens == (
        default.context_window_tokens - default.max_output_tokens
    )

    local = llm.get_model_capabilities(
        "chatgpt", "lab-model", base_url="http://127.0.0.1:11434/v1/"
    )
    assert local.model == "lab-model"
    assert local.endpoint == "http://127.0.0.1:11434/v1"
    assert local.custom_endpoint is True
    assert local.local_endpoint is True
    assert local.context_window_tokens is None
    assert local.max_output_tokens is None
    assert local.tool_calling is False
    assert local.parallel_tool_calls is False
    assert local.strict_tool_schema is False
    assert local.vision is False
    assert local.cost.source == "local endpoint; capabilities unknown"


def test_legacy_registry_edits_and_new_provider_are_reflected(monkeypatch):
    monkeypatch.setitem(llm.PROVIDERS["ark"], "vision", False)
    monkeypatch.setitem(llm.PROVIDERS["ark"], "model", "ark-deployment-model")
    assert llm.supports_vision("ark") is False
    edited = llm.get_model_capabilities("ark")
    assert edited.vision is False
    assert edited.model == "ark-deployment-model"

    monkeypatch.setitem(
        llm.PROVIDERS,
        "local_openai",
        {
            "wire": "openai",
            "base_url": "http://localhost:11434/v1",
            "model": "science-local",
            "vision": False,
            "context_window_tokens": 16_384,
            "max_output_tokens": 4_096,
        },
    )
    local = llm.get_model_capabilities("local_openai")
    assert local.provider == "local_openai"
    assert local.context_window_tokens == 16_384
    assert local.max_output_tokens == 4_096
    assert local.custom_endpoint is True
    assert local.local_endpoint is True

    # Mutation is part of the cache key, so no explicit cache clear is needed.
    monkeypatch.setitem(llm.PROVIDERS["local_openai"], "vision", True)
    assert llm.get_model_capabilities("local_openai").vision is True


def test_exact_model_override_invalidates_cache_without_changing_provider():
    before = llm.capability_cache_info()
    first = llm.get_model_capabilities("ark", "lab-model")
    second = llm.get_model_capabilities("ark", "lab-model")
    cached = llm.capability_cache_info()
    assert first is second
    assert cached.hits > before.hits

    llm.set_capability_override(
        "ark",
        model="lab-model",
        context_window_tokens=32_000,
        max_output_tokens=8_000,
        audio=True,
        cost={"input_per_million": 0.25, "source": "deployment"},
    )
    overridden = llm.get_model_capabilities("ark", "lab-model")
    default_model = llm.get_model_capabilities("ark")
    assert overridden is not first
    assert overridden.context_window_tokens == 32_000
    assert overridden.max_output_tokens == 8_000
    assert overridden.audio is True
    assert overridden.cost.input_per_million == 0.25
    assert default_model.context_window_tokens == 262_144
    assert llm.capability_cache_info().generation > cached.generation


def test_provider_override_validation_and_request_validation():
    with pytest.raises(llm.CapabilityError, match="unknown capability"):
        llm.set_capability_override("gemini", invented=True)
    with pytest.raises(llm.CapabilityError, match="positive integer"):
        llm.set_capability_override("gemini", max_output_tokens=0)
    with pytest.raises(llm.CapabilityError, match="does not support: streaming"):
        llm.validate_model_request("gemini", streaming=True)
    with pytest.raises(llm.CapabilityError, match="exceeds model limit"):
        llm.validate_model_request("gemini", max_output_tokens=100_000)

    resolved = llm.validate_model_request(
        "chatgpt", tool_calling=True, parallel_tool_calls=True, vision=True
    )
    assert resolved.provider == "chatgpt"


def test_chat_enforces_output_limit_before_transport():
    cfg = llm.LLMConfig(
        provider="gemini",
        api_key="test",
        model="gemini-2.5-pro",
    )
    with pytest.raises(llm.CapabilityError, match="exceeds model limit"):
        llm.chat(
            [{"role": "user", "content": "hello"}],
            cfg,
            max_tokens=100_000,
        )


@pytest.mark.parametrize(
    "provider,raw,expected",
    [
        (
            "chatgpt",
            {
                "prompt_tokens": 20,
                "completion_tokens": 9,
                "prompt_tokens_details": {"cached_tokens": 5},
                "completion_tokens_details": {"reasoning_tokens": 4},
            },
            (20, 9, 5, 0, 4, 29),
        ),
        (
            "claude",
            {
                "input_tokens": 12,
                "output_tokens": 7,
                "cache_read_input_tokens": 3,
                "cache_creation_input_tokens": 2,
            },
            (12, 7, 3, 2, 0, 19),
        ),
        (
            "gemini",
            {
                "promptTokenCount": 30,
                "candidatesTokenCount": 11,
                "cachedContentTokenCount": 6,
                "thoughtsTokenCount": 4,
                "totalTokenCount": 41,
            },
            (30, 11, 6, 0, 4, 41),
        ),
    ],
)
def test_usage_mapping_is_canonical_and_keeps_openai_aliases(provider, raw, expected):
    usage = llm.normalize_usage(raw, provider)
    input_tokens, output_tokens, cache_read, cache_write, reasoning, total = expected
    assert usage == {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read": cache_read,
        "cache_write": cache_write,
        "reasoning_tokens": reasoning,
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": total,
    }


def test_usage_normalization_tolerates_missing_and_invalid_counters():
    usage = llm.normalize_usage(
        {"prompt_tokens": "unknown", "completion_tokens": -3}, "chatgpt"
    )
    assert usage == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read": 0,
        "cache_write": 0,
        "reasoning_tokens": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }


def test_usage_cost_uses_only_explicit_prices_and_replaces_cache_subsets():
    usage = {
        "input_tokens": 1_000_000,
        "output_tokens": 100_000,
        "cache_read": 200_000,
        "cache_write": 100_000,
    }
    priced = llm.CostMetadata(
        input_per_million=2.0,
        output_per_million=8.0,
        cache_read_per_million=0.5,
        cache_write_per_million=3.0,
        source="deployment price table",
    )
    assert llm.calculate_usage_cost_usd(usage, priced) == 2.6
    assert llm.calculate_usage_cost_usd(usage, llm.CostMetadata()) is None
    assert (
        llm.calculate_usage_cost_usd(
            usage,
            llm.CostMetadata(
                currency="EUR",
                input_per_million=2.0,
                output_per_million=8.0,
            ),
        )
        is None
    )
