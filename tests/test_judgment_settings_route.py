"""Gateway settings surface for the experimental judgment layer."""

from __future__ import annotations

import json
import logging
import os

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.judgment.disclosure import CAPABILITIES, DISCLOSURE_VERSION
from openai4s.judgment.flags import (
    SETTING_BY_CAPABILITY,
    SETTING_DISCLOSURE_ACK,
    SETTING_MASTER,
)
from openai4s.judgment.settings import ENV_API_KEY, SECRET_NAME
from openai4s.server import gateway as gateway_mod
from openai4s.server.errors import GatewayError
from openai4s.store import get_store

_JUDGMENT_ENV = (
    "OPENAI4S_EXPERIMENTAL_JUDGMENT",
    "OPENAI4S_JUDGMENT_SKILL_SUGGEST",
    "OPENAI4S_JUDGMENT_LITERATURE",
    "OPENAI4S_JUDGMENT_TEXT_FEATURES",
    "OPENAI4S_JUDGMENT_SAFETY_SHADOW",
    "OPENAI4S_JUDGMENT_TASK_MODE_SHADOW",
    "OPENAI4S_JUDGMENT_PROVIDER",
    "OPENAI4S_JUDGMENT_TIMEOUT_S",
    ENV_API_KEY,
)

_TEST_KEY = "judgment-test-key-never-echo"


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None

    def has_subscriber(self, root_frame_id):
        return False

    def drop_frame(self, root_frame_id):
        return None


@pytest.fixture(autouse=True)
def _clear_judgment_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _JUDGMENT_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("OPENAI4S_EGRESS", raising=False)


def _cfg(tmp_path):
    return Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )


def _handler(tmp_path):
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    handler = object.__new__(handler_cls)
    replies: list[tuple[int, dict]] = []
    handler._query = lambda: {}
    handler._json = lambda obj, code=200: replies.append((code, obj))
    handler._body = lambda: {}
    return handler, replies, cfg


def _assert_status_shape(body: dict) -> None:
    assert body["experimental"] is True
    assert "key_configured" in body
    assert isinstance(body["key_configured"], bool)
    assert body["provider"] == "typesafe"
    assert body["model"] == "jev-1.13.0"
    assert set(body["effective"]) == {"master", *CAPABILITIES}
    for item in body["effective"].values():
        assert set(item) == {"enabled", "source"}
        assert isinstance(item["enabled"], bool)
        assert isinstance(item["source"], str)
    disclosure = body["disclosure"]
    assert disclosure["version"] == DISCLOSURE_VERSION
    assert set(disclosure["capabilities"]) == set(CAPABILITIES)
    for name in CAPABILITIES:
        assert set(disclosure["capabilities"][name]) == {"zh", "en"}
    assert set(disclosure["facts"]) == {"en", "zh"}
    assert isinstance(disclosure["acked"], bool)
    assert isinstance(disclosure["acknowledged_capabilities"], list)
    egress = body["egress"]
    assert set(egress) == {"host", "mode", "domain_allowed", "remediation"}
    assert egress["host"] == "api.typesafe.ai"
    assert egress["mode"] in ("off", "allowlist")
    assert isinstance(egress["domain_allowed"], bool)


def _assert_no_secret(payload: object, sentinel: str = _TEST_KEY) -> None:
    dumped = json.dumps(payload, ensure_ascii=False)
    assert sentinel not in dumped
    assert "api_key" not in dumped or sentinel not in dumped


def test_get_allowlist_reports_blocked_remediation(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_EGRESS", "allowlist")
    handler, replies, _cfg = _handler(tmp_path)
    handler._api("GET", "/experimental/judgment")
    code, body = replies.pop()
    assert code == 200
    _assert_status_shape(body)
    assert body["egress"]["mode"] == "allowlist"
    assert body["egress"]["domain_allowed"] is False
    assert isinstance(body["egress"]["remediation"], str)
    assert "host.request_network_access" in body["egress"]["remediation"]
    _assert_no_secret(body)


def test_get_default_status_is_off(tmp_path):
    handler, replies, _cfg = _handler(tmp_path)
    handler._api("GET", "/experimental/judgment")
    code, body = replies.pop()
    assert code == 200
    _assert_status_shape(body)
    assert body["key_configured"] is False
    assert body["effective"]["master"] == {"enabled": False, "source": "default"}
    for name in CAPABILITIES:
        assert body["effective"][name]["enabled"] is False
        assert body["effective"][name]["source"] == "master_off"
    assert body["disclosure"]["acked"] is False
    _assert_no_secret(body)


def test_put_api_key_sets_configured_and_never_echoes(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    handler, replies, cfg = _handler(tmp_path)
    handler._body = lambda: {"api_key": _TEST_KEY}
    handler._api("PUT", "/experimental/judgment")
    code, body = replies.pop()
    assert code == 200
    _assert_status_shape(body)
    assert body["key_configured"] is True
    _assert_no_secret(body)
    _assert_no_secret(caplog.text)
    assert ENV_API_KEY not in os.environ
    store = get_store(cfg.db_path)
    stored_row = store.get_setting(SECRET_NAME) or ""
    assert _TEST_KEY not in stored_row
    assert store.get_secret_setting(SECRET_NAME, scope="judgment") == _TEST_KEY

    handler._api("GET", "/experimental/judgment")
    _, again = replies.pop()
    assert again["key_configured"] is True
    _assert_no_secret(again)


def test_put_clear_api_key(tmp_path):
    handler, replies, _cfg = _handler(tmp_path)
    handler._body = lambda: {"api_key": _TEST_KEY}
    handler._api("PUT", "/experimental/judgment")
    assert replies.pop()[1]["key_configured"] is True
    handler._body = lambda: {"clear_api_key": True}
    handler._api("PUT", "/experimental/judgment")
    code, body = replies.pop()
    assert code == 200
    assert body["key_configured"] is False
    _assert_no_secret(body)


def test_patch_is_accepted(tmp_path):
    handler, replies, _cfg = _handler(tmp_path)
    handler._body = lambda: {"enabled": False}
    handler._api("PATCH", "/experimental/judgment")
    code, body = replies.pop()
    assert code == 200
    assert body["effective"]["master"]["source"] in {"setting", "default"}
    _assert_status_shape(body)


def test_unknown_field_is_400(tmp_path):
    handler, replies, _cfg = _handler(tmp_path)
    handler._body = lambda: {"not_a_field": True}
    with pytest.raises(GatewayError) as raised:
        handler._api("PUT", "/experimental/judgment")
    assert raised.value.code == 400
    assert "unknown field" in raised.value.message
    assert replies == []


def test_type_error_is_400(tmp_path):
    handler, replies, _cfg = _handler(tmp_path)
    handler._body = lambda: {"enabled": "yes"}
    with pytest.raises(GatewayError) as raised:
        handler._api("PUT", "/experimental/judgment")
    assert raised.value.code == 400
    assert "boolean" in raised.value.message


@pytest.mark.parametrize(
    "invalid",
    [
        {"enabled": "yes"},
        {"capabilities": {"skill_suggest": True, "text_features": "yes"}},
        {"capabilities": {"skill_suggest": True, "unknown": True}},
        {"clear_api_key": "yes"},
        {"clear_api_key": None},
        {"api_key": 123},
        {"clear_api_key": True, "api_key": _TEST_KEY},
        {
            "acknowledge": {
                "version": DISCLOSURE_VERSION,
                "capabilities": [],
                "provider": "unknown",
            }
        },
    ],
)
def test_rejected_update_preserves_flags_ack_and_key(tmp_path, invalid):
    handler, replies, cfg = _handler(tmp_path)
    store = get_store(cfg.db_path)
    store.set_secret_setting(SECRET_NAME, _TEST_KEY, scope="judgment")
    body = {
        "enabled": True,
        "capabilities": {"skill_suggest": True},
        "acknowledge": {
            "version": DISCLOSURE_VERSION,
            "capabilities": ["skill_suggest"],
        },
        **invalid,
    }
    handler._body = lambda: body
    with pytest.raises(GatewayError) as raised:
        handler._api("PUT", "/experimental/judgment")
    assert raised.value.code == 400
    assert replies == []
    for key in (
        SETTING_MASTER,
        SETTING_DISCLOSURE_ACK,
        *SETTING_BY_CAPABILITY.values(),
    ):
        assert store.get_setting(key) is None
    assert store.get_secret_setting(SECRET_NAME, scope="judgment") == _TEST_KEY


def test_wrong_disclosure_version_is_rejected(tmp_path):
    handler, replies, _cfg = _handler(tmp_path)
    handler._body = lambda: {
        "acknowledge": {
            "version": "1999-01-01",
            "capabilities": ["skill_suggest"],
        }
    }
    with pytest.raises(GatewayError) as raised:
        handler._api("PUT", "/experimental/judgment")
    assert raised.value.code == 400
    assert raised.value.error_code == "disclosure_version_mismatch"
    handler._api("GET", "/experimental/judgment")
    assert replies.pop()[1]["disclosure"]["acked"] is False


def test_ui_enable_without_ack_is_no_disclosure(tmp_path):
    handler, replies, _cfg = _handler(tmp_path)
    handler._body = lambda: {"enabled": True}
    handler._api("PUT", "/experimental/judgment")
    code, body = replies.pop()
    assert code == 200
    assert body["effective"]["master"] == {
        "enabled": False,
        "source": "no_disclosure",
    }
    for name in CAPABILITIES:
        assert body["effective"][name]["enabled"] is False
        assert body["effective"][name]["source"] == "master_off"


def test_env_false_keeps_ui_from_enabling(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_EXPERIMENTAL_JUDGMENT", "0")
    handler, replies, _cfg = _handler(tmp_path)
    handler._body = lambda: {
        "enabled": True,
        "acknowledge": {
            "version": DISCLOSURE_VERSION,
            "capabilities": list(CAPABILITIES),
        },
    }
    handler._api("PUT", "/experimental/judgment")
    code, body = replies.pop()
    assert code == 200
    assert body["effective"]["master"] == {"enabled": False, "source": "env_off"}
    for name in CAPABILITIES:
        assert body["effective"][name]["source"] == "master_off"


def test_acknowledge_then_enable_opens_ui_path(tmp_path):
    handler, replies, _cfg = _handler(tmp_path)
    handler._body = lambda: {
        "enabled": True,
        "capabilities": {"skill_suggest": True},
        "acknowledge": {
            "version": DISCLOSURE_VERSION,
            "capabilities": ["skill_suggest"],
        },
    }
    handler._api("PUT", "/experimental/judgment")
    code, body = replies.pop()
    assert code == 200
    assert body["effective"]["master"] == {"enabled": True, "source": "setting"}
    assert body["effective"]["skill_suggest"] == {
        "enabled": True,
        "source": "setting",
    }
    assert body["disclosure"]["acked"] is True
    assert body["disclosure"]["acknowledged_capabilities"] == ["skill_suggest"]


def test_llm_status_and_acknowledgment_follow_actual_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_JUDGMENT_PROVIDER", "llm")
    handler, replies, cfg = _handler(tmp_path)
    cfg.llm.model = "science-model"
    cfg.llm.base_url = "https://provider.example/v1"
    handler._body = lambda: {
        "enabled": True,
        "capabilities": {"skill_suggest": True},
        "acknowledge": {
            "version": DISCLOSURE_VERSION,
            "capabilities": ["skill_suggest"],
        },
    }
    handler._api("PUT", "/experimental/judgment")
    code, body = replies.pop()
    assert code == 200
    assert body["provider"] == "llm"
    assert body["model"] == cfg.llm.model
    assert body["key_configured"] is True
    assert body["egress"]["host"] == "provider.example"
    assert "TypeSafe" not in body["disclosure"]["facts"]["en"]
    assert body["disclosure"]["acked"] is False
    assert body["disclosure"]["acknowledged_capabilities"] == []
    assert body["effective"]["master"]["source"] == "no_disclosure"

    handler._body = lambda: {
        "acknowledge": {
            "version": DISCLOSURE_VERSION,
            "provider": "llm",
            "capabilities": ["skill_suggest"],
        }
    }
    handler._api("PUT", "/experimental/judgment")
    code, body = replies.pop()
    assert code == 200
    assert body["effective"]["master"]["enabled"] is True
    assert body["effective"]["skill_suggest"]["enabled"] is True
    assert body["disclosure"]["acked"] is True
    assert body["disclosure"]["acknowledged_capabilities"] == ["skill_suggest"]


def test_llm_status_does_not_require_a_typesafe_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_JUDGMENT_PROVIDER", "llm")
    handler, replies, cfg = _handler(tmp_path)
    cfg.llm.api_key = ""
    cfg.llm.provider = "chatgpt"
    cfg.llm.base_url = "http://127.0.0.1:11434/v1"
    handler._api("GET", "/experimental/judgment")
    code, body = replies.pop()
    assert code == 200
    assert body["key_configured"] is True
    assert body["egress"]["host"] == "127.0.0.1"


def test_unknown_main_model_provider_does_not_break_status(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_JUDGMENT_PROVIDER", "llm")
    handler, replies, cfg = _handler(tmp_path)
    cfg.llm.api_key = ""
    cfg.llm.provider = "missing-provider"
    handler._api("GET", "/experimental/judgment")
    code, body = replies.pop()
    assert code == 200
    assert body["key_configured"] is False


def test_llm_status_reads_current_models_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_JUDGMENT_PROVIDER", "llm")
    handler, replies, cfg = _handler(tmp_path)
    cfg.llm.api_key = ""
    store = get_store(cfg.db_path)
    store.set_setting("llm_provider", "chatgpt")
    store.set_setting("llm_model", "configured-science-model")
    store.set_setting("llm_base_url", "https://configured-model.example/v1")
    store.set_secret_setting("llm_api_key", "configured-test-key", scope="llm")
    handler._api("GET", "/experimental/judgment")
    code, body = replies.pop()
    assert code == 200
    assert body["model"] == "configured-science-model"
    assert body["egress"]["host"] == "configured-model.example"
    assert body["key_configured"] is True
    assert cfg.llm.api_key == ""


@pytest.mark.stubbed_backend
def test_llm_probe_reads_current_models_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_JUDGMENT_PROVIDER", "llm")
    monkeypatch.setenv("OPENAI4S_EXPERIMENTAL_JUDGMENT", "1")
    handler, replies, cfg = _handler(tmp_path)
    cfg.llm.api_key = ""
    store = get_store(cfg.db_path)
    store.set_setting("llm_provider", "chatgpt")
    store.set_setting("llm_model", "configured-science-model")
    store.set_setting("llm_base_url", "https://configured-model.example/v1")
    store.set_secret_setting("llm_api_key", "configured-test-key", scope="llm")
    calls = []

    def chat(messages, llm_cfg, **kwargs):
        calls.append(llm_cfg)
        return {
            "content": json.dumps({"answers": {"alive": {"noul": 0.9}}}),
            "model": llm_cfg.model,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

    monkeypatch.setattr("openai4s.llm.chat", chat)
    handler._api("POST", "/experimental/judgment/test")
    code, body = replies.pop()
    assert code == 200
    assert body["status"] == "ok"
    assert body["model"] == "configured-science-model"
    assert len(calls) == 1
    assert calls[0].provider == "chatgpt"
    assert calls[0].base_url == "https://configured-model.example/v1"
    assert calls[0].api_key == "configured-test-key"
    assert cfg.llm.api_key == ""


def test_post_test_returns_public_fields(tmp_path):
    handler, replies, _cfg = _handler(tmp_path)
    handler._api("POST", "/experimental/judgment/test")
    code, body = replies.pop()
    assert code == 200
    assert set(body) == {"status", "error_code", "latency_ms", "model"}
    assert body["status"] in {"ok", "uncertain", "unavailable", "disabled"}
    assert isinstance(body["latency_ms"], int)
    _assert_no_secret(body)


def test_post_test_reports_a_non_null_error_code(tmp_path, monkeypatch):
    """Unstubbed, and deliberately in the state that fills ``error_code``.

    Added by MERGE-W1. The frozen response shape is whatever the offline suite
    elicits, and until this test existed the only unstubbed call hit the
    default-off path, where ``error_code`` is null. The route can plainly
    return a string, so freezing ``"type": "null"`` would have described the
    prober rather than the route -- and the first real deployment answer would
    have violated its own contract.
    """

    monkeypatch.setenv("OPENAI4S_EXPERIMENTAL_JUDGMENT", "1")
    monkeypatch.delenv(ENV_API_KEY, raising=False)
    handler, replies, _cfg_obj = _handler(tmp_path)
    handler._api("POST", "/experimental/judgment/test")
    code, body = replies.pop()
    assert code == 200
    assert body["status"] == "unavailable"
    assert body["error_code"] == "unconfigured"
    _assert_no_secret(body)


@pytest.mark.stubbed_backend
def test_post_test_uses_stubbed_service(tmp_path, monkeypatch):
    def fake_probe(cfg, store):
        return {
            "status": "ok",
            "error_code": None,
            "latency_ms": 17,
            "model": "jev-1.13.0",
        }

    monkeypatch.setattr("openai4s.judgment.settings.probe_connection", fake_probe)
    handler, replies, _cfg = _handler(tmp_path)
    handler._api("POST", "/experimental/judgment/test")
    code, body = replies.pop()
    assert code == 200
    assert body == {
        "status": "ok",
        "error_code": None,
        "latency_ms": 17,
        "model": "jev-1.13.0",
    }


def test_delete_judgment_is_method_not_allowed(tmp_path):
    handler, replies, _cfg = _handler(tmp_path)
    with pytest.raises(GatewayError) as raised:
        handler._api("DELETE", "/experimental/judgment")
    assert raised.value.code == 405
