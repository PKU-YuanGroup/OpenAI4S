"""Placeholder API-key filtering — is_placeholder_api_key + LLMConfig.__post_init__."""
from openai4s.config import LLMConfig, is_placeholder_api_key


def test_is_placeholder_api_key_matches_template_stubs():
    assert is_placeholder_api_key("your-api-key-here")
    assert is_placeholder_api_key("  Your-API-Key-Here  ")  # case/space-insensitive
    assert is_placeholder_api_key("changeme")
    assert is_placeholder_api_key("")
    assert is_placeholder_api_key(None)
    assert not is_placeholder_api_key("sk-real-0123456789")
    # the offline suite's fake key (tests/conftest.py) must stay "configured"
    assert not is_placeholder_api_key("test-key")


def test_post_init_drops_placeholder_from_env(monkeypatch):
    monkeypatch.delenv("OPENAI4S_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI4S_LLM_API_KEY", "your-api-key-here")
    assert LLMConfig(provider="deepseek").api_key == ""


def test_post_init_drops_placeholder_passed_explicitly():
    assert LLMConfig(provider="deepseek", api_key="your_api_key_here").api_key == ""


def test_placeholder_env_does_not_shadow_native_key(monkeypatch):
    # a .env copied verbatim from .env.example must not mask a real key the
    # user already exported for other tools — the placeholder is dropped
    # BEFORE the provider-native fallback (OPENAI_API_KEY & co) runs
    monkeypatch.delenv("OPENAI4S_CHATGPT_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI4S_LLM_API_KEY", "your-api-key-here")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-native-real")
    assert LLMConfig(provider="chatgpt").api_key == "sk-native-real"
