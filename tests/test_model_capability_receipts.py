"""Exact model-capability receipts: three-state evidence and overlay adoption."""

from __future__ import annotations

import io
import json

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.endpoint_identity import endpoint_sha256, normalize_endpoint
from openai4s.llm.capabilities import (
    CAPABILITY_PROBE_TOOL,
    PROBE_VERSION,
    adopted_receipts,
    drop_receipt_overlays,
    get_model_capabilities,
    tool_call_matches_schema,
)
from openai4s.llm.models import TransportError
from openai4s.llm.registry import provider_specs
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth
from openai4s.server.model_profiles import ModelProfileService
from openai4s.storage.model_capability_receipts import (
    EVIDENCE_FALSE,
    EVIDENCE_TRUE,
    EVIDENCE_UNKNOWN,
)
from openai4s.store import get_store


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


@pytest.fixture(autouse=True)
def _clear_overlays():
    drop_receipt_overlays()
    yield
    drop_receipt_overlays()


def _service(tmp_path):
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    store = get_store(cfg.db_path)
    return store, ModelProfileService(store, cfg, providers=provider_specs)


def _create_profile(service, **fields):
    body = {
        "name": fields.get("name", "P"),
        "provider": fields.get("provider", "openai_responses"),
        "base_url": fields.get("base_url", "https://api.example.invalid/v1"),
        "model": fields.get("model", "lab-model"),
        "api_key": fields.get("api_key", "sk-test-receipts-aabbcc"),
    }
    return service.create(body)


def _valid_tool_reply():
    return {
        "content": "",
        "tool_calls": [
            {
                "name": CAPABILITY_PROBE_TOOL["name"],
                "arguments": {"ok": True},
                "parse_error": None,
            }
        ],
        "finish_reason": "tool_calls",
    }


def _stream_reply(on_delta):
    if on_delta is not None:
        on_delta("pong")
    return {"content": "pong", "tool_calls": [], "finish_reason": "stop"}


def test_endpoint_sha256_is_stable_across_trailing_slash_and_userinfo():
    a = endpoint_sha256("https://h.example/v1/")
    b = endpoint_sha256("https://user:secret@h.example/v1")
    assert a == b
    assert "secret" not in a
    assert (
        normalize_endpoint("https://user:secret@h.example/v1/")
        == "https://h.example/v1"
    )


def test_schema_valid_tool_call_is_the_only_true_native_evidence():
    assert tool_call_matches_schema(
        {"name": "openai4s_capability_probe", "arguments": {"ok": True}},
        CAPABILITY_PROBE_TOOL,
    )
    assert not tool_call_matches_schema(
        {"name": "openai4s_capability_probe", "arguments": {"ok": "yes"}},
        CAPABILITY_PROBE_TOOL,
    )
    assert not tool_call_matches_schema(
        {
            "name": "openai4s_capability_probe",
            "arguments": {"ok": True},
            "parse_error": "invalid JSON",
        },
        CAPABILITY_PROBE_TOOL,
    )


def test_positive_probe_persists_receipt_and_relaxes_overlay(tmp_path, monkeypatch):
    store, service = _service(tmp_path)
    created = _create_profile(service)
    profile_id = created["id"]
    calls = {"n": 0, "tools_executed": 0}

    def _chat(messages, cfg, **kwargs):
        calls["n"] += 1
        assert calls["n"] <= 2
        if kwargs.get("on_delta") is not None:
            return _stream_reply(kwargs["on_delta"])
        assert kwargs.get("tools")
        return _valid_tool_reply()

    monkeypatch.setattr("openai4s.llm.chat", _chat)

    import openai4s.tools.base as tools_base

    original_execute = tools_base.Tool.execute

    def _count_execute(self, *args, **kwargs):
        calls["tools_executed"] += 1
        return original_execute(self, *args, **kwargs)

    monkeypatch.setattr(tools_base.Tool, "execute", _count_execute)

    result = service.probe(profile_id)
    assert result["contacted"] is True
    assert result["reachable"] is True
    assert result["outbound"] <= 2
    assert result["tool_execution"] == 0
    assert calls["n"] <= 2
    assert calls["tools_executed"] == 0
    receipt = result["capability_receipt"]
    assert receipt["native_tool_call"] == EVIDENCE_TRUE
    assert receipt["streaming"] == EVIDENCE_TRUE
    assert receipt["stale"] is False
    assert receipt["native_completion"] is True
    assert adopted_receipts(profile_id=profile_id, revision=created["revision"])

    caps = get_model_capabilities(
        "openai_responses",
        "lab-model",
        base_url="https://api.example.invalid/v1",
    )
    assert caps.tool_calling is True
    assert caps.streaming is True

    # Restart: drop process overlays, reopen the same database.
    db_path = store.db_path
    store.close()
    drop_receipt_overlays()
    assert adopted_receipts(profile_id=profile_id) == []
    reopened = get_store(db_path)
    loaded = reopened.model_capability_receipts.get_exact(
        profile_id=profile_id,
        revision=int(created["revision"] or 0),
        endpoint_sha256=endpoint_sha256("https://api.example.invalid/v1"),
        model="lab-model",
        wire="responses",
        probe_version=PROBE_VERSION,
    )
    assert loaded is not None
    assert loaded["native_tool_call"] == EVIDENCE_TRUE
    assert adopted_receipts(profile_id=profile_id, revision=created["revision"])


def test_positive_receipt_relaxes_local_conservative_defaults(tmp_path, monkeypatch):
    store, service = _service(tmp_path)
    created = _create_profile(
        service,
        provider="chatgpt",
        model="lab-model",
        base_url="http://127.0.0.1:11434/v1",
    )
    before = get_model_capabilities(
        "chatgpt", "lab-model", base_url="http://127.0.0.1:11434/v1"
    )
    assert before.tool_calling is False

    def _chat(messages, cfg, **kwargs):
        if kwargs.get("on_delta") is not None:
            return _stream_reply(kwargs["on_delta"])
        return _valid_tool_reply()

    monkeypatch.setattr("openai4s.llm.chat", _chat)
    result = service.probe(created["id"])
    assert result["capability_receipt"]["native_tool_call"] == EVIDENCE_TRUE
    after = get_model_capabilities(
        "chatgpt", "lab-model", base_url="http://127.0.0.1:11434/v1"
    )
    assert after.tool_calling is True
    assert after.streaming is True


def test_unknown_probe_does_not_enable_native_completion(tmp_path, monkeypatch):
    store, service = _service(tmp_path)
    created = _create_profile(
        service,
        provider="chatgpt",
        model="lab-model",
        base_url="http://127.0.0.1:11434/v1",
    )
    before = get_model_capabilities(
        "chatgpt", "lab-model", base_url="http://127.0.0.1:11434/v1"
    )
    assert before.tool_calling is False

    def _chat(messages, cfg, **kwargs):
        return {"content": "hello", "tool_calls": [], "finish_reason": "stop"}

    monkeypatch.setattr("openai4s.llm.chat", _chat)
    result = service.probe(created["id"])
    receipt = result["capability_receipt"]
    assert receipt["native_tool_call"] == EVIDENCE_UNKNOWN
    assert receipt["streaming"] == EVIDENCE_UNKNOWN
    assert receipt["native_completion"] is False
    assert adopted_receipts(profile_id=created["id"]) == []
    after = get_model_capabilities(
        "chatgpt", "lab-model", base_url="http://127.0.0.1:11434/v1"
    )
    assert after.tool_calling is False


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("timed out"),
        TransportError(
            "401", provider="chatgpt", status=401, error_code="invalid_api_key"
        ),
        TransportError(
            "boom", provider="chatgpt", status=503, error_code="server_error"
        ),
    ],
)
def test_timeout_auth_and_5xx_are_unknown_never_false(tmp_path, monkeypatch, error):
    store, service = _service(tmp_path)
    created = _create_profile(service)
    monkeypatch.setattr(
        "openai4s.llm.chat",
        lambda *_a, **_k: (_ for _ in ()).throw(error),
    )
    result = service.probe(created["id"])
    receipt = result["capability_receipt"]
    assert receipt["native_tool_call"] != EVIDENCE_FALSE
    assert receipt["streaming"] != EVIDENCE_FALSE
    assert receipt["native_tool_call"] == EVIDENCE_UNKNOWN
    assert result["outbound"] <= 2
    assert result["tool_execution"] == 0


def test_protocol_unsupported_is_the_only_false_path(tmp_path, monkeypatch):
    store, service = _service(tmp_path)
    created = _create_profile(service, provider="gemini", model="gemini-2.5-flash")

    def _chat(messages, cfg, **kwargs):
        raise TransportError(
            "tools not supported",
            provider="gemini",
            status=400,
            error_code="unsupported_parameter",
        )

    monkeypatch.setattr("openai4s.llm.chat", _chat)
    result = service.probe(created["id"])
    receipt = result["capability_receipt"]
    assert receipt["native_tool_call"] == EVIDENCE_FALSE
    # Gemini's adapter has no streaming transport: protocol-level false,
    # no second request required.
    assert receipt["streaming"] == EVIDENCE_FALSE
    assert result["outbound"] <= 2


def test_revision_change_drops_old_receipt_adoption(tmp_path, monkeypatch):
    store, service = _service(tmp_path)
    created = _create_profile(service)
    profile_id = created["id"]
    old_revision = created["revision"]

    def _chat(messages, cfg, **kwargs):
        if kwargs.get("on_delta") is not None:
            return _stream_reply(kwargs["on_delta"])
        return _valid_tool_reply()

    monkeypatch.setattr("openai4s.llm.chat", _chat)
    service.probe(profile_id)
    assert adopted_receipts(profile_id=profile_id, revision=old_revision)

    edited, _selected = service.edit(profile_id, {"model": "other-model"})
    assert edited["revision"] != old_revision
    assert adopted_receipts(profile_id=profile_id, revision=old_revision) == []
    assert edited["capability_receipt"]["stale"] is True


def test_receipt_never_contains_the_key(tmp_path, monkeypatch):
    secret = "sk-receipt-canary-zz99"
    store, service = _service(tmp_path)
    created = _create_profile(service, api_key=secret)
    monkeypatch.setattr(
        "openai4s.llm.chat",
        lambda *_a, **_k: _valid_tool_reply(),
    )
    result = service.probe(created["id"])
    blob = json.dumps(result)
    assert blob.count(secret) == 0
    listed = service.public_profile(store.list_model_profiles()[0])
    assert json.dumps(listed).count(secret) == 0


def test_gateway_probe_returns_capability_receipt(tmp_path, monkeypatch):
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=1,
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_class = gateway_mod.make_handler(cfg, _Hub(), runner)
    token = local_auth.read_token(tmp_path) or ""

    def call(method, path, body=None):
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
        handler._route(method)
        return sent

    created = call(
        "POST",
        "/model-profiles",
        {
            "name": "p",
            "provider": "openai_responses",
            "api_key": "sk-test",
            "model": "gpt-4o",
        },
    )
    profile_id = created["body"]["id"]

    def _chat(*_a, **kwargs):
        if kwargs.get("on_delta") is not None:
            return _stream_reply(kwargs["on_delta"])
        return _valid_tool_reply()

    monkeypatch.setattr("openai4s.llm.chat", _chat)
    result = call("POST", f"/model-profiles/{profile_id}/probe")
    assert result["code"] == 200
    assert result["body"]["tool_execution"] == 0
    assert result["body"]["outbound"] <= 2
    assert result["body"]["capability_receipt"]["native_tool_call"] == EVIDENCE_TRUE
