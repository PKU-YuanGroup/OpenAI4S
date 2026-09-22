"""doctor._judgment: disabled is informational; never prints the key."""

from __future__ import annotations

import json

import pytest

from openai4s import doctor, egress
from openai4s.config import Config, LLMConfig
from openai4s.judgment.disclosure import CAPABILITIES, DISCLOSURE_VERSION
from openai4s.judgment.settings import (
    ENV_API_KEY,
    SECRET_NAME,
    SECRET_SCOPE,
    TYPESAFE_HOST,
    update,
)
from openai4s.store import get_store

_JUDGMENT_ENV = (
    "OPENAI4S_EXPERIMENTAL_JUDGMENT",
    "OPENAI4S_JUDGMENT_SKILL_SUGGEST",
    "OPENAI4S_JUDGMENT_LITERATURE",
    "OPENAI4S_JUDGMENT_TEXT_FEATURES",
    "OPENAI4S_JUDGMENT_SAFETY_SHADOW",
    "OPENAI4S_JUDGMENT_TASK_MODE_SHADOW",
    "OPENAI4S_JUDGMENT_PROVIDER",
    ENV_API_KEY,
)

_TEST_KEY = "judgment-doctor-key-never-echo"


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _JUDGMENT_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("OPENAI4S_EGRESS", raising=False)
    egress.reset_grants()
    yield
    egress.reset_grants()


def _cfg(tmp_path):
    return Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )


def _by_name(cfg):
    return {c["name"]: c for c in doctor.report(cfg)["checks"]}


def test_default_off_is_informational(tmp_path):
    check = _by_name(_cfg(tmp_path))["judgment"]
    assert check["status"] == doctor.OK
    assert check["detail"].startswith("disabled")
    assert check["remedy"] == ""
    assert check["facts"]["enabled"] is False
    assert _TEST_KEY not in json.dumps(check)


def test_enabled_without_key_warns(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_EXPERIMENTAL_JUDGMENT", "1")
    check = _by_name(_cfg(tmp_path))["judgment"]
    assert check["status"] == doctor.WARN
    assert "no TypeSafe API key" in check["detail"]
    assert check["facts"]["key_configured"] is False
    assert _TEST_KEY not in json.dumps(check)
    assert _TEST_KEY not in doctor.render(doctor.report(_cfg(tmp_path)))


def test_enabled_with_key_passes(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_EXPERIMENTAL_JUDGMENT", "1")
    monkeypatch.setenv(ENV_API_KEY, _TEST_KEY)
    cfg = _cfg(tmp_path)
    check = _by_name(cfg)["judgment"]
    assert check["status"] == doctor.OK
    assert check["facts"]["key_configured"] is True
    blob = json.dumps(check) + doctor.render(doctor.report(cfg))
    assert _TEST_KEY not in blob


def test_allowlist_without_grant_includes_blocked_message(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_EXPERIMENTAL_JUDGMENT", "1")
    monkeypatch.setenv(ENV_API_KEY, _TEST_KEY)
    monkeypatch.setenv("OPENAI4S_EGRESS", "allowlist")
    check = _by_name(_cfg(tmp_path))["judgment"]
    assert check["status"] == doctor.WARN
    assert TYPESAFE_HOST in check["detail"]
    assert check["remedy"] == egress.blocked_message(TYPESAFE_HOST)
    assert "host.request_network_access" in check["remedy"]
    assert _TEST_KEY not in json.dumps(check)
    assert _TEST_KEY not in check["remedy"]


def test_ui_enabled_store_key_is_not_echoed(tmp_path):
    cfg = _cfg(tmp_path)
    store = get_store(cfg.db_path)
    update(
        store,
        {
            "enabled": True,
            "acknowledge": {
                "version": DISCLOSURE_VERSION,
                "capabilities": list(CAPABILITIES),
            },
            "api_key": _TEST_KEY,
        },
    )
    assert store.get_secret_setting(SECRET_NAME, scope=SECRET_SCOPE) == _TEST_KEY
    check = _by_name(cfg)["judgment"]
    assert check["facts"]["key_configured"] is True
    blob = json.dumps(doctor.report(cfg)) + doctor.render(doctor.report(cfg))
    assert _TEST_KEY not in blob
    assert store.get_setting(SECRET_NAME) != _TEST_KEY


def test_llm_backend_reports_main_model_without_typesafe_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_EXPERIMENTAL_JUDGMENT", "1")
    monkeypatch.setenv("OPENAI4S_JUDGMENT_PROVIDER", "llm")
    cfg = _cfg(tmp_path)
    cfg.llm.model = "configured-science-model"
    check = doctor._judgment(cfg)
    assert check.status == doctor.OK
    assert check.detail == "enabled (llm/configured-science-model)"
    assert check.facts["key_configured"] is True


def test_llm_backend_reports_its_own_egress_host(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_EXPERIMENTAL_JUDGMENT", "1")
    monkeypatch.setenv("OPENAI4S_JUDGMENT_PROVIDER", "llm")
    monkeypatch.setenv("OPENAI4S_EGRESS", "allowlist")
    cfg = _cfg(tmp_path)
    cfg.llm.base_url = "https://judgment-model.example/v1"
    check = doctor._judgment(cfg)
    assert check.status == doctor.WARN
    assert "judgment-model.example" in check.detail
    assert check.remedy == egress.blocked_message("judgment-model.example")
    assert TYPESAFE_HOST not in check.detail


def test_llm_backend_reads_configuration_saved_in_models(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_EXPERIMENTAL_JUDGMENT", "1")
    monkeypatch.setenv("OPENAI4S_JUDGMENT_PROVIDER", "llm")
    cfg = _cfg(tmp_path)
    cfg.llm.api_key = ""
    store = get_store(cfg.db_path)
    store.set_setting("llm_provider", "chatgpt")
    store.set_setting("llm_model", "saved-science-model")
    store.set_setting("llm_base_url", "https://saved-model.example/v1")
    store.set_secret_setting("llm_api_key", _TEST_KEY, scope="llm")
    check = doctor._judgment(cfg)
    assert check.status == doctor.OK
    assert check.detail == "enabled (llm/saved-science-model)"
    assert check.facts["key_configured"] is True
    assert _TEST_KEY not in json.dumps(check.public())
