"""Independent Ollama web credentials through real settings routes and Store."""

from __future__ import annotations

import dataclasses
import json
import os
from types import SimpleNamespace

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.ollama_web import (
    WEB_API_KEY_SETTING,
    resolve_web_key,
    save_web_config,
    web_config_status,
)
from openai4s.server import gateway
from openai4s.server.errors import GatewayError
from openai4s.server.model_profiles import ModelProfileService
from openai4s.store import get_store

KEY = "synthetic-ollama-web-credential"


class Hub:
    def emitter(self, _frame):
        return lambda _event: None

    def broadcast(self, *_args):
        pass


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI4S_OLLAMA_API_KEY", raising=False)


@pytest.fixture
def settings(tmp_path):
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="unrelated-model-credential"),
    )
    runner = gateway.SessionRunner(cfg, Hub(), start_idle_sweeper=False)
    handler = object.__new__(gateway.make_handler(cfg, Hub(), runner))
    replies = []
    handler._query = lambda: {}
    handler._json = lambda payload, code=200: replies.append((code, payload))

    def call(method="GET", body=None):
        handler._body = lambda: body if body is not None else {}
        handler._api(method, "/ollama-web/config")
        code, payload = replies.pop()
        assert code == 200
        assert set(payload) == {"key_configured", "available", "source"}
        assert KEY not in json.dumps(payload)
        return payload

    yield cfg, runner.store, handler, call
    runner.close()


def test_save_replace_clear_is_secret_only_and_leaves_model_untouched(settings, caplog):
    cfg, store, _handler, call = settings
    model_before = dataclasses.asdict(cfg.llm)
    profiles_before = store.list_model_profiles()
    environment_before = dict(os.environ)
    assert call() == {"key_configured": False, "available": False, "source": ""}
    assert call("PUT", {"api_key": "  " + KEY + "  "}) == {
        "key_configured": True,
        "available": True,
        "source": "saved",
    }
    assert call()["key_configured"]
    reference = store.get_setting(WEB_API_KEY_SETTING)
    assert reference.startswith("secret://")
    assert KEY not in reference
    assert resolve_web_key(cfg.llm, store) == KEY
    call("PUT", {"api_key": KEY + "-rotated"})
    assert resolve_web_key(cfg.llm, store) == KEY + "-rotated"
    assert call("PUT", {"clear_api_key": True}) == {
        "key_configured": False,
        "available": False,
        "source": "",
    }
    assert not store.secrets.get(reference)
    assert not resolve_web_key(cfg.llm, store)
    assert dataclasses.asdict(cfg.llm) == model_before
    assert store.list_model_profiles() == profiles_before
    assert dict(os.environ) == environment_before
    assert KEY not in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"api_key": None},
        {"api_key": 10},
        {"api_key": ""},
        {"api_key": "short"},
        {"api_key": "placeholder"},
        {"api_key": "long-credential\ninjection"},
        {"api_key": "x" * 8193},
        {"api_key": KEY, "clear_api_key": True},
        {"api_key": KEY, "model": "another-model"},
        {"clear_api_key": "true"},
        {"clear_api_key": False},
    ],
)
def test_invalid_save_preserves_existing_key(settings, body):
    cfg, store, _handler, call = settings
    save_web_config(store, {"api_key": KEY})
    with pytest.raises(GatewayError) as error:
        call("PUT", body)
    assert error.value.code == 400
    assert KEY not in error.value.message
    assert resolve_web_key(cfg.llm, store) == KEY


def test_members_can_read_status_but_cannot_replace_or_clear(settings):
    _cfg, store, handler, call = settings
    save_web_config(store, {"api_key": KEY})
    handler._team_identity = SimpleNamespace(is_admin=False, user_id="member")
    assert call()["key_configured"]
    for body in ({"api_key": KEY + "-other"}, {"clear_api_key": True}):
        with pytest.raises(GatewayError) as error:
            call("PUT", body)
        assert error.value.code == 403
        assert error.value.error_code == "admin_only"
    assert store.get_secret_setting(WEB_API_KEY_SETTING) == KEY


def test_persists_on_reopen_and_stays_scoped_to_its_store(tmp_path):
    cfg = LLMConfig(provider="claude", api_key="another-model-key")
    path = tmp_path / "first.db"
    first = get_store(path)
    second = get_store(tmp_path / "second.db")
    try:
        save_web_config(first, {"api_key": KEY})
        assert resolve_web_key(cfg, first) == KEY
        assert resolve_web_key(cfg, second) == ""
        first.close()
        first = get_store(path)
        assert resolve_web_key(cfg, first) == KEY
        save_web_config(first, {"clear_api_key": True})
        assert not resolve_web_key(cfg, first)
    finally:
        first.close()
        second.close()


def test_saved_key_wins_over_cloud_profile_and_environment(settings, monkeypatch):
    cfg, store, _handler, call = settings
    monkeypatch.setenv("OLLAMA_API_KEY", "environment-ollama-key")
    cfg.llm = LLMConfig(provider="ollama", api_key="model-ollama-key")
    assert call()["source"] == "model"
    call("PUT", {"api_key": KEY})
    assert resolve_web_key(cfg.llm, store) == KEY
    assert call("PUT", {"clear_api_key": True})["source"] == "model"
    cfg.llm = LLMConfig(provider="chatgpt", api_key="unrelated-model-key")
    assert call() == {
        "key_configured": False,
        "available": True,
        "source": "environment",
    }
    assert web_config_status(cfg.llm, store)["source"] == "environment"


def test_status_follows_profile_activation_and_clear_fallback(settings):
    cfg, store, _handler, call = settings
    profiles = ModelProfileService(store, cfg, providers=lambda: gateway.PROVIDERS)
    cloud = profiles.create(
        {"name": "Ollama cloud", "provider": "ollama", "api_key": KEY}
    )
    other = profiles.create(
        {"name": "Other model", "provider": "claude", "api_key": "other-key"}
    )
    profiles.activate(cloud["id"])
    assert call() == {"key_configured": False, "available": True, "source": "model"}
    call("PUT", {"api_key": KEY + "-independent"})
    assert call("PUT", {"clear_api_key": True})["source"] == "model"
    # Boot configuration must not outlive the profile the user selected.
    cfg.llm = LLMConfig(provider="ollama", api_key=KEY + "-boot")
    profiles.activate(other["id"])
    assert call() == {"key_configured": False, "available": False, "source": ""}


def test_injected_web_key_resolves_without_a_settings_row(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_SECRET_STORE", "env")
    monkeypatch.setenv("OPENAI4S_SECRET_ENV", "1")
    variable = "OPENAI4S_SECRET_OLLAMA_WEB_OLLAMA_WEB_API_KEY"
    monkeypatch.setenv(variable, KEY)
    cfg = LLMConfig(provider="deepseek", api_key="unrelated-key")
    store = get_store(tmp_path / "injected.db")
    try:
        assert store.get_setting(WEB_API_KEY_SETTING) is None
        assert resolve_web_key(cfg, store) == KEY
        assert web_config_status(cfg, store) == {
            "key_configured": False,
            "available": True,
            "source": "environment",
        }
        monkeypatch.setenv(variable, KEY + "-rotated")
        assert resolve_web_key(cfg, store) == KEY + "-rotated"
        monkeypatch.delenv(variable)
        assert not resolve_web_key(cfg, store)
    finally:
        store.close()
