"""Contracts for extensible provider/model catalogs and profile services."""

from __future__ import annotations

import itertools

import pytest

from openai4s import llm
from openai4s.config import Config, LLMConfig
from openai4s.server.model_profiles import (
    ModelProfileError,
    ModelProfileService,
    migrate_provider_alias,
)
from openai4s.store import get_store


def test_supported_wires_match_the_shipped_transport_dispatch():
    from openai4s.llm.providers import _WIRE_DISPATCH

    assert llm.SUPPORTED_WIRES == frozenset(_WIRE_DISPATCH)


def test_custom_provider_registration_validates_and_routes(monkeypatch):
    captured = {}

    def post_json(url, payload, headers, timeout):
        captured.update(url=url, payload=payload, headers=headers, timeout=timeout)
        return {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {},
        }

    monkeypatch.setattr(llm.transport, "post_json", post_json)
    try:
        capabilities = llm.register_provider(
            "lab_openai",
            wire="openai",
            base_url="http://127.0.0.1:11434/v1/",
            model="science-model",
            tool_calling=False,
            context_window_tokens=16_384,
            max_output_tokens=2_048,
        )
        assert capabilities.provider == "lab_openai"
        assert capabilities.local_endpoint is True
        assert capabilities.tool_calling is False
        assert llm.provider_specs()["lab_openai"]["model"] == "science-model"

        result = llm.chat(
            [{"role": "user", "content": "hello"}],
            LLMConfig(provider="lab_openai"),
        )
        assert result["content"] == "ok"
        assert captured["url"] == "http://127.0.0.1:11434/v1/chat/completions"

        with pytest.raises(llm.CapabilityError, match="already registered"):
            llm.register_provider(
                "lab_openai",
                wire="openai",
                base_url="http://localhost:11434/v1",
                model="other",
            )
        with pytest.raises(llm.CapabilityError, match="built-in provider"):
            llm.register_provider(
                "chatgpt",
                wire="openai",
                base_url="https://example.test/v1",
                model="replacement",
                replace=True,
            )
    finally:
        llm.unregister_provider("lab_openai")

    assert "lab_openai" not in llm.PROVIDERS
    with pytest.raises(llm.CapabilityError, match="built-in provider"):
        llm.unregister_provider("claude")


def test_hyphenated_provider_uses_shell_safe_environment_prefix(monkeypatch):
    monkeypatch.setenv("OPENAI4S_LAB_OPENAI_API_KEY", "env-key")
    try:
        llm.register_provider(
            "lab-openai",
            wire="openai",
            base_url="https://example.test/v1",
            model="lab-model",
        )
        config = LLMConfig(provider="lab-openai")
        assert config.api_key == "env-key"
        assert config.model == "lab-model"
    finally:
        llm.unregister_provider("lab-openai")


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"provider": "Bad Name"}, "provider must start"),
        ({"wire": "invented"}, "unsupported wire"),
        ({"base_url": "not-a-url"}, "absolute http"),
        ({"base_url": "https://user:secret@example.test/v1"}, "credentials"),
        ({"model": ""}, "model must"),
        ({"streaming": "yes"}, "streaming must be a boolean"),
    ],
)
def test_custom_provider_registration_rejects_invalid_metadata(kwargs, message):
    values = {
        "provider": "invalid_probe",
        "wire": "openai",
        "base_url": "https://example.test/v1",
        "model": "model",
    }
    values.update(kwargs)
    with pytest.raises(llm.CapabilityError, match=message):
        llm.register_provider(**values)
    assert "invalid_probe" not in llm.PROVIDERS


def test_model_presets_are_extensible_and_builtins_are_immutable():
    before = llm.model_presets()
    assert llm.ARK_PLAN_MODELS == tuple(
        (preset.model, preset.label) for preset in before if preset.provider == "ark"
    )
    try:
        preset = llm.register_model_preset(
            "lab_openai",
            "science-model",
            "Science model",
            profile_name="Lab · Science model",
        )
        assert preset in llm.model_presets("lab_openai")
        with pytest.raises(llm.CapabilityError, match="already registered"):
            llm.register_model_preset("lab_openai", "science-model", "Duplicate")
    finally:
        llm.unregister_model_preset("lab_openai", "science-model")
    assert llm.model_presets() == before
    with pytest.raises(llm.CapabilityError, match="built-in model preset"):
        llm.unregister_model_preset("ark", "doubao-seed-2.0-pro")


def _service(tmp_path, *, provider="ark"):
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider=provider, api_key="test-key"),
    )
    store = get_store(cfg.db_path)
    ids = (f"mp-test-{index}" for index in itertools.count())
    service = ModelProfileService(
        store,
        cfg,
        providers=llm.provider_specs,
        id_factory=lambda: next(ids),
    )
    return store, service


def test_profile_service_seeds_catalog_once_without_exposing_keys(tmp_path):
    store, service = _service(tmp_path)
    payload, selected_model = service.profiles_payload()
    assert len(payload["profiles"]) == len(llm.model_presets())
    assert payload["known_providers"] == sorted(llm.PROVIDERS)
    assert selected_model == "doubao-seed-2.0-pro"
    assert all("api_key" not in profile for profile in payload["profiles"])
    assert any(profile["has_api_key"] for profile in payload["profiles"])

    first_ids = [profile["id"] for profile in store.list_model_profiles()]
    repeated, selected_again = service.profiles_payload()
    assert [profile["id"] for profile in repeated["profiles"]] == first_ids
    assert selected_again is None


def test_profile_service_crud_activation_and_header_projection(tmp_path):
    store, service = _service(tmp_path)
    with pytest.raises(ModelProfileError, match="name required"):
        service.create({})
    created = service.create(
        {
            "name": "Local",
            "provider": "chatgpt",
            "model": "lab-model",
            "api_key": "your-api-key-here",
        }
    )
    assert created["has_api_key"] is False

    payload, effective = service.activate(created["id"])
    assert payload == {"ok": True, "active_id": created["id"]}
    assert effective == "lab-model"
    assert store.get_setting("llm_api_key") == ""

    public, selected = service.edit(
        created["id"], {"model": "lab-model-v2", "api_key": "secret"}
    )
    assert public["has_api_key"] is True
    assert selected == "lab-model-v2"
    assert store.get_setting("llm_model") == "lab-model-v2"
    assert "secret" not in repr(public)

    header = service.models_payload("lab-model-v2")
    model_ids = [item["id"] for item in header["models"]["default"]]
    assert model_ids[0] == "lab-model-v2"
    assert len(model_ids) == len(set(model_ids))

    service.delete(created["id"])
    assert store.get_setting("active_model_profile") == ""
    with pytest.raises(ModelProfileError, match="profile not found"):
        service.activate(created["id"])


def test_provider_alias_migration_is_idempotent(tmp_path):
    store, _service_instance = _service(tmp_path)
    store.set_setting("llm_provider", "doubao")
    store.set_setting("llm_base_url", "")
    store.set_model_profiles([{"id": "legacy", "provider": "doubao", "base_url": ""}])
    for _ in range(2):
        migrate_provider_alias(store, llm.provider_specs(), old="doubao", new="ark")
    assert store.get_setting("llm_provider") == "ark"
    assert store.get_setting("llm_base_url") == llm.PROVIDERS["ark"]["base_url"]
    assert store.list_model_profiles() == [
        {
            "id": "legacy",
            "provider": "ark",
            "base_url": llm.PROVIDERS["ark"]["base_url"],
        }
    ]
