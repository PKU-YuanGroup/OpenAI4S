"""Web first-run onboarding: redacted GET, complete POST, zero outbound."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

from openai4s.config import Config, LLMConfig
from openai4s.onboarding import OnboardingService
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth, onboarding_routes, team_policy
from openai4s.server.model_discovery import LocalModelDiscoveryService


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


def _secret_hits(payload, secret: str) -> int:
    blob = json.dumps(payload) if not isinstance(payload, str) else payload
    return blob.count(secret)


def _api(tmp_path):
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=1,
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_class = gateway_mod.make_handler(cfg, _Hub(), runner)
    token = local_auth.read_token(tmp_path) or ""

    def call(method, path, body=None, *, identity=None):
        handler = object.__new__(handler_class)
        handler._correlation_id = "req-1"
        sent: dict = {}
        handler._send = (
            lambda code, payload, ctype, extra=None, security=None: sent.update(
                code=code, body=json.loads(payload.decode("utf-8"))
            )
        )
        handler.command = method
        handler.path = f"/api/v1{path}"
        raw = json.dumps(body or {}).encode("utf-8")
        handler.headers = {
            "Content-Length": str(len(raw)) if body is not None else "0",
            "Content-Type": "application/json",
            local_auth.TOKEN_HEADER: token,
        }
        handler.rfile = io.BytesIO(raw if body is not None else b"")
        if identity is not None:
            handler._team_identity = identity
        handler._route(method)
        return sent

    return runner, call


def test_get_onboarding_is_redacted_and_contacts_nobody(tmp_path, monkeypatch):
    secret = "sk-fake-onboarding-must-not-leak-9f3a"
    monkeypatch.setattr(
        "openai4s.llm.chat",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("GET contacted LLM")),
    )
    monkeypatch.setattr(
        LocalModelDiscoveryService,
        "discover",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("GET refreshed the local catalogue")
        ),
    )

    runner, call = _api(tmp_path)
    created = call(
        "POST",
        "/model-profiles",
        {
            "name": "first",
            "provider": "openai_responses",
            "api_key": secret,
            "model": "gpt-4o",
        },
    )
    assert created["code"] == 201, created

    result = call("GET", "/onboarding")
    assert result["code"] == 200, result
    body = result["body"]
    assert body["outbound"] == 0
    assert body["contacted"] is False
    assert body["network"]["contacted"] is False
    assert body["environment"]["network_contacted"] is False
    assert body["local_model_catalog"]["probed"] == 0
    assert body["local_model_catalog"]["contacted"] is False
    assert body["local_model_catalog"]["background_refresh"] is False
    assert body["has_api_key"] is True
    assert isinstance(body["has_api_key"], bool)
    assert "data_dir" not in body
    assert _secret_hits(body, secret) == 0
    assert secret not in json.dumps(body)


def test_get_onboarding_before_test_has_zero_outbound(tmp_path, monkeypatch):
    """The quantified gate: Test 前 outbound=0."""
    outbound = {"n": 0}

    def _count(*_a, **_k):
        outbound["n"] += 1
        raise AssertionError("onboarding GET made an outbound call")

    monkeypatch.setattr("openai4s.llm.chat", _count)
    monkeypatch.setattr("openai4s.llm.transport.post_json", _count)
    monkeypatch.setattr("openai4s.llm.transport.post_sse", _count)

    _runner, call = _api(tmp_path)
    body = call("GET", "/onboarding")["body"]
    assert outbound["n"] == 0
    assert body["outbound"] == 0


def test_complete_marks_first_run_without_a_provider_call(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "openai4s.llm.chat",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("complete contacted")),
    )
    runner, call = _api(tmp_path)
    result = call("POST", "/onboarding/complete", {"skip": True})
    assert result["code"] == 200, result
    assert result["body"]["complete"] is True
    assert runner.store.get_setting("onboarding_complete") == "1"
    assert result["body"]["outbound"] == 0


def test_complete_is_instance_config_and_members_are_refused():
    assert team_policy.is_instance_config("POST", "/onboarding/complete")
    assert not team_policy.is_instance_config("GET", "/onboarding")

    handler = SimpleNamespace(
        _team_identity=SimpleNamespace(is_admin=False, user_id="bob"),
        sent=None,
    )

    def _json(body, status=200):
        handler.sent = (status, body)

    handler._json = _json
    handler._body = lambda: {"skip": True}

    owned = onboarding_routes.handle(
        handler,
        "POST",
        "/onboarding/complete",
        {},
        store=SimpleNamespace(),
        cfg=SimpleNamespace(),
        model_profiles=SimpleNamespace(),
        model_discovery=SimpleNamespace(),
    )
    assert owned is True
    assert handler.sent[0] == 403
    assert handler.sent[1]["code"] == "admin_only"


def test_web_status_never_returns_the_key(tmp_path):
    from openai4s.llm.registry import provider_specs
    from openai4s.store import get_store

    secret = "sk-fake-web-status-canary-aabbcc"
    cfg = Config(data_dir=tmp_path / "data", llm=LLMConfig(provider="chatgpt"))
    store = get_store(cfg.db_path)
    service = OnboardingService(cfg, store, provider_specs())
    service.configure(provider="chatgpt", api_key=secret)
    payload = service.web_status()
    assert payload["has_api_key"] is True
    assert _secret_hits(payload, secret) == 0
    assert "api_key" not in payload


def test_diagnostics_bundle_does_not_include_the_key(tmp_path):
    """key 在 GET/DOM/日志/diagnostics 命中数 0 — diagnostics half."""
    from openai4s.diagnostics import redact_text
    from openai4s.llm.registry import provider_specs
    from openai4s.store import get_store

    secret = "sk-diag-canary-1122334455"
    cfg = Config(data_dir=tmp_path / "data", llm=LLMConfig(provider="chatgpt"))
    store = get_store(cfg.db_path)
    service = OnboardingService(cfg, store, provider_specs())
    service.configure(provider="chatgpt", api_key=secret)
    dumped = redact_text(json.dumps(service.web_status()))
    assert dumped.count(secret) == 0


def test_catalog_candidates_are_listed_without_opening_sockets():
    service = LocalModelDiscoveryService()
    payload = service.catalog()
    assert payload["probed"] == 0
    assert payload["contacted"] is False
    assert payload["background_refresh"] is False
    assert payload["mutated_settings"] is False
    assert len(payload["endpoints"]) == len(service.endpoints)
