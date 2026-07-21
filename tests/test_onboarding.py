"""First-run setup stays deterministic, validated, and secret-free."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from openai4s.config import Config
from openai4s.onboarding import OnboardingService

PROVIDERS = {
    "alpha": {
        "wire": "openai",
        "base_url": "https://alpha.example/v1",
        "model": "alpha-1",
    },
    "beta": {
        "wire": "anthropic",
        "base_url": "https://beta.example",
        "model": "beta-2",
    },
}


class MemorySettings:
    """Stands in for the Store's settings surface.

    The secret accessors mirror what the real Store does under the plaintext
    backend — store the value, hand it straight back. Onboarding writes the
    user's API key through the broker now, so this double has to carry that
    part of the contract; the tests below assert the key does not leak into a
    *response*, which is orthogonal to where it is stored.
    """

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get_setting(self, key: str):
        return self.values.get(key)

    def set_setting(self, key: str, value: str) -> None:
        self.values[key] = value

    def get_secret_setting(self, key: str) -> str:
        return self.values.get(key) or ""

    def set_secret_setting(self, key: str, value: str, *, scope: str) -> str:
        self.values[key] = value
        return value


def service(tmp_path: Path) -> tuple[OnboardingService, MemorySettings]:
    settings = MemorySettings()
    cfg = Config(
        data_dir=tmp_path,
        llm=SimpleNamespace(
            provider="alpha",
            base_url="https://alpha.example/v1",
            model="alpha-1",
            api_key="",
        ),
    )
    return OnboardingService(cfg, settings, PROVIDERS), settings


def test_configure_uses_provider_defaults_and_returns_no_secret(tmp_path):
    onboarding, settings = service(tmp_path)

    result = onboarding.configure(provider="beta", api_key="super-secret-value")

    assert result.provider == "beta"
    assert result.model == "beta-2"
    assert result.base_url == "https://beta.example"
    assert result.has_api_key is True
    assert result.complete is True
    assert settings.values["llm_api_key"] == "super-secret-value"
    assert "super-secret-value" not in repr(result)
    assert "api_key" not in result.as_dict()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"provider": "missing"}, "unknown provider"),
        ({"provider": "alpha", "model": ""}, "model must not be empty"),
        (
            {"provider": "alpha", "base_url": "ftp://alpha.example"},
            "absolute http",
        ),
        (
            {"provider": "alpha", "base_url": "https://user:pass@alpha.example"},
            "must not contain credentials",
        ),
        ({"provider": "alpha", "api_key": "changeme"}, "placeholder"),
    ],
)
def test_configure_rejects_invalid_or_secret_bearing_inputs(tmp_path, kwargs, message):
    onboarding, _settings = service(tmp_path)
    if kwargs.get("model") == "":
        # An explicit empty model falls back by design; use a provider whose
        # declared default is also empty to exercise the validation boundary.
        onboarding.providers = {
            **PROVIDERS,
            "empty": {"base_url": "https://e", "model": ""},
        }
        kwargs = {"provider": "empty", "model": ""}
    with pytest.raises(ValueError, match=message):
        onboarding.configure(**kwargs)


def test_invalid_api_key_does_not_partially_write_model_settings(tmp_path):
    onboarding, settings = service(tmp_path)

    with pytest.raises(ValueError, match="placeholder"):
        onboarding.configure(provider="beta", api_key="changeme")

    assert settings.values == {}


def test_switching_provider_does_not_reuse_previous_provider_defaults(tmp_path):
    onboarding, settings = service(tmp_path)
    settings.values.update(
        {
            "llm_provider": "alpha",
            "llm_model": "alpha-custom",
            "llm_base_url": "https://custom.example/v1",
        }
    )

    assert onboarding.defaults("alpha")["model"] == "alpha-custom"
    assert onboarding.defaults("beta") == {
        "provider": "beta",
        "model": "beta-2",
        "base_url": "https://beta.example",
    }


def test_switching_provider_drops_the_previous_provider_key(tmp_path, monkeypatch):
    onboarding, settings = service(tmp_path)
    settings.values.update(
        {
            "llm_provider": "alpha",
            "llm_api_key": "alpha-secret",
        }
    )
    monkeypatch.delenv("OPENAI4S_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI4S_BETA_API_KEY", raising=False)

    result = onboarding.configure(provider="beta")

    assert settings.values["llm_api_key"] == ""
    assert result.provider == "beta"
    assert result.has_api_key is False


def test_clear_api_key_is_explicit(tmp_path):
    onboarding, settings = service(tmp_path)
    settings.values["llm_api_key"] = "keep-me"

    onboarding.configure(provider="alpha")
    assert settings.values["llm_api_key"] == "keep-me"

    result = onboarding.configure(provider="alpha", clear_api_key=True)
    assert settings.values["llm_api_key"] == ""
    assert result.has_api_key is False


def test_configure_preserves_stored_model_and_base_url_when_only_touching_key(tmp_path):
    onboarding, settings = service(tmp_path)
    # Operator previously customized model/base_url for the active provider.
    settings.values["llm_provider"] = "alpha"
    settings.values["llm_model"] = "alpha-custom"
    settings.values["llm_base_url"] = "https://corp.proxy/v1"

    # Non-interactive path passes model=None/base_url=None; it must not reset
    # the stored customization back to the provider spec defaults.
    result = onboarding.configure(provider="alpha", api_key="fresh-key")

    assert result.model == "alpha-custom"
    assert result.base_url == "https://corp.proxy/v1"
    assert settings.values["llm_model"] == "alpha-custom"
    assert settings.values["llm_base_url"] == "https://corp.proxy/v1"
    assert settings.values["llm_api_key"] == "fresh-key"
