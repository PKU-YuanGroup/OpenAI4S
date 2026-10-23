"""Exact model-capability receipts: three-state evidence and overlay adoption.

The regression this file exists for is P0-01: a second probe that observes
``unknown``/``false`` updated storage and the UI while the in-process positive
overlay from the first probe stayed adopted, so the next request still declared
tools the model had just failed to use.  The tests below therefore assert the
*adapter request* the next call would put on the wire, not only the overlay
cache: a decision that disagrees with the displayed receipt is the bug.
"""

from __future__ import annotations

import io
import json
import sqlite3
import threading

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.endpoint_identity import endpoint_sha256, normalize_endpoint
from openai4s.llm import chat as llm_chat
from openai4s.llm.capabilities import (
    CAPABILITY_PROBE_TOOL,
    PROBE_VERSION,
    adopted_receipts,
    clear_capability_overrides,
    drop_receipt_overlays,
    effective_capabilities,
    get_model_capabilities,
    set_capability_override,
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

#: A loopback endpoint is the case the report measured: the catalog is
#: deliberately conservative there, so a positive probe is the *only* thing
#: that can turn tool calling on -- and withdrawing it is observable.
LOCAL_BASE = "http://127.0.0.1:11434/v1"
DEMO_TOOL = {
    "name": "demo_tool",
    "description": "a demo tool",
    "input_schema": {"type": "object", "properties": {}},
}


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


def _wire_tool_reply():
    """OpenAI-wire reply carrying the schema-valid probe tool call."""
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_probe",
                            "type": "function",
                            "function": {
                                "name": CAPABILITY_PROBE_TOOL["name"],
                                "arguments": json.dumps({"ok": True}),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
    }


def _wire_text_reply(text="hello"):
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1},
    }


def _wire_stream(delta=True):
    """OpenAI-wire SSE events: one terminated stream, optionally with a delta."""
    piece = {"delta": {"content": "pong"} if delta else {}, "finish_reason": "stop"}
    return [{"choices": [piece]}]


class _Wire:
    """The urllib hop, replaced by a recorder that answers in wire format.

    The real facade, client, capability resolver and provider adapter all run;
    only the last hop is faked.  ``requests`` is what the adapter built and
    would have sent, which is what "the next request decision" means.
    """

    def __init__(self, answer=None, stream_events=None):
        self.requests: list[dict] = []
        self.answer = answer or (lambda _payload: _wire_text_reply())
        self.stream_events = stream_events or (lambda _payload: _wire_stream())

    def install(self, monkeypatch):
        monkeypatch.setattr("openai4s.llm._post_json", self.post_json)
        monkeypatch.setattr("openai4s.llm._post_sse", self.post_sse)

    def post_json(self, url, payload, headers, timeout, **_context):
        self.requests.append({"url": url, "payload": payload, "stream": False})
        return self.answer(payload)

    def post_sse(self, url, payload, headers, timeout, on_event, **_context):
        self.requests.append({"url": url, "payload": payload, "stream": True})
        for event in self.stream_events(payload):
            on_event(event)


def _local_cfg():
    return LLMConfig(
        provider="chatgpt",
        api_key="sk-local-tests",
        model="lab-model",
        base_url=LOCAL_BASE,
    )


def _next_request(wire, *, tools=None):
    """Drive one real ``chat()`` call and return the adapter payload it built."""
    before = len(wire.requests)
    llm_chat([{"role": "user", "content": "hi"}], _local_cfg(), tools=tools)
    assert len(wire.requests) == before + 1
    return wire.requests[-1]["payload"]


def _tool_names(payload):
    return [entry["function"]["name"] for entry in payload.get("tools") or ()]


@pytest.fixture(autouse=True)
def _clear_overlays():
    clear_capability_overrides()
    drop_receipt_overlays()
    yield
    clear_capability_overrides()
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


def _service_with_clock(tmp_path, ticks):
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    store = get_store(cfg.db_path)
    service = ModelProfileService(
        store,
        cfg,
        providers=provider_specs,
        clock_ms=lambda: next(ticks),
    )
    return store, service


def _positive_probe(service, profile_id, wire, monkeypatch):
    """One positive probe that adopts both fields through the real wire path."""
    wire.install(monkeypatch)
    result = service.probe(profile_id)
    assert result["capability_receipt"]["native_tool_call"] == EVIDENCE_TRUE
    assert result["capability_receipt"]["streaming"] == EVIDENCE_TRUE
    return result


def test_second_probe_unknown_withdraws_the_previous_positive_promotion(
    tmp_path, monkeypatch
):
    store, service = _service(tmp_path)
    created = _create_profile(
        service, provider="chatgpt", base_url=LOCAL_BASE, model="lab-model"
    )
    profile_id = created["id"]
    state = {"probe": "positive"}

    def answer(payload):
        if state["probe"] == "positive" and payload.get("tools"):
            return _wire_tool_reply()
        return _wire_text_reply()

    def events(_payload):
        return _wire_stream(delta=state["probe"] == "positive")

    wire = _Wire(answer=answer, stream_events=events)
    _positive_probe(service, profile_id, wire, monkeypatch)

    promoted = get_model_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE)
    assert promoted.tool_calling is True
    assert effective_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE)[
        "tool_calling"
    ] == {"value": True, "source": "probe"}
    sent = _next_request(wire, tools=[DEMO_TOOL])
    assert _tool_names(sent) == [
        "demo_tool"
    ], "the promoted decision is the request the wire must see"

    state["probe"] = "unknown"
    second = service.probe(profile_id)
    receipt = second["capability_receipt"]
    assert receipt["native_tool_call"] == EVIDENCE_UNKNOWN
    assert receipt["streaming"] == EVIDENCE_UNKNOWN
    assert receipt["stale"] is False
    assert adopted_receipts(profile_id=profile_id) == []

    withdrawn = get_model_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE)
    assert withdrawn.tool_calling is False
    projection = effective_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE)
    assert projection["tool_calling"] == {"value": False, "source": "catalog"}
    sent = _next_request(wire, tools=[DEMO_TOOL])
    assert "tools" not in sent, "a withdrawn promotion must not survive on the wire"

    # ``public_receipt`` keeps the latest observation semantics: display says
    # unknown, and unknown is not the same claim as "does not support tools".
    listed = service.public_profile(store.list_model_profiles()[0])
    assert listed["capability_receipt"]["native_tool_call"] == EVIDENCE_UNKNOWN
    assert listed["effective_capabilities"]["tool_calling"]["value"] is False


def test_second_probe_protocol_false_withdraws_the_previous_positive_promotion(
    tmp_path, monkeypatch
):
    store, service = _service(tmp_path)
    created = _create_profile(
        service, provider="chatgpt", base_url=LOCAL_BASE, model="lab-model"
    )
    profile_id = created["id"]
    state = {"probe": "positive"}

    def refusal():
        return TransportError(
            "tools not supported",
            provider="chatgpt",
            status=400,
            error_code="unsupported_parameter",
        )

    def answer(payload):
        if state["probe"] == "positive":
            return _wire_tool_reply() if payload.get("tools") else _wire_text_reply()
        if state["probe"] == "false":
            raise refusal()
        return _wire_text_reply()

    def events(_payload):
        if state["probe"] == "positive":
            return _wire_stream()
        if state["probe"] == "false":
            raise refusal()
        return _wire_stream()

    wire = _Wire(answer=answer, stream_events=events)
    _positive_probe(service, profile_id, wire, monkeypatch)
    assert (
        get_model_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE).tool_calling
        is True
    )

    state["probe"] = "false"
    second = service.probe(profile_id)
    state["probe"] = "settled"
    receipt = second["capability_receipt"]
    assert receipt["native_tool_call"] == EVIDENCE_FALSE
    assert receipt["streaming"] == EVIDENCE_FALSE
    assert adopted_receipts(profile_id=profile_id) == []
    assert (
        get_model_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE).tool_calling
        is False
    )
    sent = _next_request(wire, tools=[DEMO_TOOL])
    assert "tools" not in sent


def test_one_still_positive_field_keeps_only_that_evidence(tmp_path, monkeypatch):
    store, service = _service(tmp_path)
    created = _create_profile(
        service, provider="chatgpt", base_url=LOCAL_BASE, model="lab-model"
    )
    profile_id = created["id"]
    state = {"probe": "positive"}

    def answer(payload):
        if state["probe"] == "positive" and payload.get("tools"):
            return _wire_tool_reply()
        return _wire_text_reply()

    # The second probe streams normally but never calls the tool.
    wire = _Wire(answer=answer, stream_events=lambda _payload: _wire_stream())
    _positive_probe(service, profile_id, wire, monkeypatch)
    assert (
        get_model_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE).tool_calling
        is True
    )

    state["probe"] = "partial"
    second = service.probe(profile_id)
    receipt = second["capability_receipt"]
    assert receipt["native_tool_call"] == EVIDENCE_UNKNOWN
    assert receipt["streaming"] == EVIDENCE_TRUE
    adopted = adopted_receipts(profile_id=profile_id)
    assert len(adopted) == 1
    assert adopted[0]["native_tool_call"] == EVIDENCE_UNKNOWN

    caps = get_model_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE)
    assert caps.tool_calling is False
    assert caps.streaming is True
    projection = effective_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE)
    assert projection["tool_calling"] == {"value": False, "source": "catalog"}
    # The catalogue already supports streaming, so positive evidence confirms it
    # without becoming the layer the decision comes from.
    assert projection["streaming"] == {"value": True, "source": "catalog"}
    sent = _next_request(wire, tools=[DEMO_TOOL])
    assert "tools" not in sent


def test_a_positive_probe_never_rewrites_an_explicit_override(tmp_path, monkeypatch):
    store, service = _service(tmp_path)
    created = _create_profile(
        service, provider="chatgpt", base_url=LOCAL_BASE, model="lab-model"
    )
    profile_id = created["id"]
    set_capability_override("chatgpt", model="lab-model", tool_calling=False)

    wire = _Wire(
        answer=lambda payload: (
            _wire_tool_reply() if payload.get("tools") else _wire_text_reply()
        ),
        stream_events=lambda _payload: _wire_stream(),
    )
    result = _positive_probe(service, profile_id, wire, monkeypatch)
    assert result["capability_receipt"]["native_tool_call"] == EVIDENCE_TRUE
    # The evidence is stored, but an explicit configuration override is never
    # silently rewritten by it.
    assert adopted_receipts(profile_id=profile_id)
    caps = get_model_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE)
    assert caps.tool_calling is False
    projection = effective_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE)
    assert projection["tool_calling"] == {"value": False, "source": "explicit_override"}
    sent = _next_request(wire, tools=[DEMO_TOOL])
    assert "tools" not in sent


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
def test_a_failed_second_probe_withdraws_promotion_but_stays_unknown(
    tmp_path, monkeypatch, error
):
    store, service = _service(tmp_path)
    created = _create_profile(
        service, provider="chatgpt", base_url=LOCAL_BASE, model="lab-model"
    )
    profile_id = created["id"]
    state = {"probe": "positive"}

    def answer(payload):
        if state["probe"] == "positive":
            return _wire_tool_reply() if payload.get("tools") else _wire_text_reply()
        if state["probe"] == "failed":
            raise error
        return _wire_text_reply()

    def events(_payload):
        if state["probe"] == "positive":
            return _wire_stream()
        if state["probe"] == "failed":
            raise error
        return _wire_stream()

    wire = _Wire(answer=answer, stream_events=events)
    _positive_probe(service, profile_id, wire, monkeypatch)
    assert (
        get_model_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE).tool_calling
        is True
    )

    state["probe"] = "failed"
    second = service.probe(profile_id)
    state["probe"] = "settled"
    receipt = second["capability_receipt"]
    assert receipt["native_tool_call"] == EVIDENCE_UNKNOWN
    assert receipt["streaming"] == EVIDENCE_UNKNOWN
    assert receipt["native_tool_call"] != EVIDENCE_FALSE
    assert adopted_receipts(profile_id=profile_id) == []
    assert (
        get_model_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE).tool_calling
        is False
    )
    sent = _next_request(wire, tools=[DEMO_TOOL])
    assert "tools" not in sent


def test_switching_the_endpoint_drops_the_old_endpoints_promotion(
    tmp_path, monkeypatch
):
    store, service = _service(tmp_path)
    created = _create_profile(
        service, provider="chatgpt", base_url=LOCAL_BASE, model="lab-model"
    )
    profile_id = created["id"]
    wire = _Wire(
        answer=lambda payload: (
            _wire_tool_reply() if payload.get("tools") else _wire_text_reply()
        ),
        stream_events=lambda _payload: _wire_stream(),
    )
    _positive_probe(service, profile_id, wire, monkeypatch)
    assert (
        get_model_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE).tool_calling
        is True
    )

    other = "http://127.0.0.1:11435/v1"
    edited, _selected = service.edit(profile_id, {"base_url": other})
    assert edited["revision"] != created["revision"]
    assert edited["capability_receipt"]["stale"] is True
    assert (
        get_model_capabilities("chatgpt", "lab-model", base_url=other).tool_calling
        is False
    )
    # The old identity keeps no promotion either: the edit invalidates it
    # precisely instead of leaving a stale cache hit behind.
    assert (
        get_model_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE).tool_calling
        is False
    )

    before = len(wire.requests)
    llm_chat(
        [{"role": "user", "content": "hi"}],
        LLMConfig(
            provider="chatgpt",
            api_key="sk-local-tests",
            model="lab-model",
            base_url=other,
        ),
        tools=[DEMO_TOOL],
    )
    assert len(wire.requests) == before + 1
    assert "tools" not in wire.requests[-1]["payload"]


def test_shared_endpoint_latest_observation_wins_and_deletion_keeps_other_evidence(
    tmp_path, monkeypatch
):
    store, service = _service(tmp_path)
    first = _create_profile(
        service,
        name="first",
        provider="chatgpt",
        base_url=LOCAL_BASE,
        model="lab-model",
        api_key="sk-shared-a",
    )
    second = _create_profile(
        service,
        name="second",
        provider="chatgpt",
        base_url=LOCAL_BASE,
        model="lab-model",
        api_key="sk-shared-b",
    )
    state = {"probe": "positive"}

    def answer(payload):
        if state["probe"] == "positive" and payload.get("tools"):
            return _wire_tool_reply()
        return _wire_text_reply()

    wire = _Wire(
        answer=answer,
        stream_events=lambda _payload: _wire_stream(delta=state["probe"] == "positive"),
    )
    _positive_probe(service, first["id"], wire, monkeypatch)
    assert (
        get_model_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE).tool_calling
        is True
    )

    # Same endpoint, model and wire: the latest observation of that exact
    # identity replaces the earlier positive one, whichever config produced it.
    state["probe"] = "unknown"
    service.probe(second["id"])
    assert adopted_receipts() == []
    assert (
        get_model_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE).tool_calling
        is False
    )

    state["probe"] = "positive"
    service.probe(first["id"])
    assert (
        get_model_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE).tool_calling
        is True
    )

    # Deleting the config that made the *older* observation must not clear the
    # newer evidence owned by a still-current config; a restart would keep it.
    service.delete(second["id"])
    assert (
        get_model_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE).tool_calling
        is True
    )
    sent = _next_request(wire, tools=[DEMO_TOOL])
    assert _tool_names(sent) == ["demo_tool"]


def test_a_late_receipt_cannot_delete_newer_evidence(tmp_path, monkeypatch):
    ticks = iter([1_000, 2_000, 3_000])
    store, service = _service_with_clock(tmp_path, ticks)
    created = _create_profile(
        service, provider="chatgpt", base_url=LOCAL_BASE, model="lab-model"
    )
    profile_id = created["id"]

    started = threading.Event()
    release = threading.Event()

    def answer(payload):
        if threading.current_thread().name == "late-probe":
            if payload.get("tools"):
                started.set()
                assert release.wait(10), "the late probe was never released"
            return _wire_text_reply()
        if payload.get("tools"):
            return _wire_tool_reply()
        return _wire_text_reply()

    def events(_payload):
        return _wire_stream(delta=threading.current_thread().name != "late-probe")

    wire = _Wire(answer=answer, stream_events=events)
    wire.install(monkeypatch)

    errors: list[BaseException] = []

    def run_late_probe():
        try:
            service.probe(profile_id)
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion
            errors.append(exc)

    late = threading.Thread(target=run_late_probe, name="late-probe")
    late.start()
    assert started.wait(10)

    newer = service.probe(profile_id)
    assert newer["capability_receipt"]["native_tool_call"] == EVIDENCE_TRUE
    release.set()
    late.join(10)
    assert not late.is_alive()
    assert errors == []

    assert (
        get_model_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE).tool_calling
        is True
    )
    adopted = adopted_receipts(profile_id=profile_id)
    assert len(adopted) == 1
    assert int(adopted[0]["observed_at"]) == 3_000
    assert adopted[0]["receipt_sha256"] == newer["capability_receipt"]["receipt_sha256"]


def test_persistence_failure_does_not_change_effective_capability(
    tmp_path, monkeypatch
):
    store, service = _service(tmp_path)
    created = _create_profile(
        service, provider="chatgpt", base_url=LOCAL_BASE, model="lab-model"
    )
    profile_id = created["id"]
    wire = _Wire(
        answer=lambda payload: (
            _wire_tool_reply() if payload.get("tools") else _wire_text_reply()
        ),
        stream_events=lambda _payload: _wire_stream(),
    )
    _positive_probe(service, profile_id, wire, monkeypatch)
    assert (
        get_model_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE).tool_calling
        is True
    )

    def _unavailable(**_kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(store.model_capability_receipts, "put", _unavailable)
    with pytest.raises(sqlite3.OperationalError):
        service.probe(profile_id)

    # Persistence failed before any effective-evidence change: the previously
    # stored promotion is still the decision, and it is still on the wire.
    assert (
        get_model_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE).tool_calling
        is True
    )
    assert adopted_receipts(profile_id=profile_id)
    sent = _next_request(wire, tools=[DEMO_TOOL])
    assert _tool_names(sent) == ["demo_tool"]


def test_restart_after_a_withdrawing_probe_does_not_restore_positive_evidence(
    tmp_path, monkeypatch
):
    store, service = _service(tmp_path)
    created = _create_profile(
        service, provider="chatgpt", base_url=LOCAL_BASE, model="lab-model"
    )
    profile_id = created["id"]
    state = {"probe": "positive"}

    def answer(payload):
        if state["probe"] == "positive" and payload.get("tools"):
            return _wire_tool_reply()
        return _wire_text_reply()

    wire = _Wire(
        answer=answer,
        stream_events=lambda _payload: _wire_stream(delta=state["probe"] == "positive"),
    )
    _positive_probe(service, profile_id, wire, monkeypatch)
    state["probe"] = "unknown"
    service.probe(profile_id)
    assert adopted_receipts(profile_id=profile_id) == []

    db_path = store.db_path
    store.close()
    drop_receipt_overlays()
    reopened = get_store(db_path)
    assert adopted_receipts(profile_id=profile_id) == []
    assert (
        get_model_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE).tool_calling
        is False
    )
    # The durable latest receipt is the unknown one, so the process restart
    # cannot resurrect the withdrawn promotion.
    latest = reopened.model_capability_receipts.latest_for_profile(profile_id)
    assert latest["native_tool_call"] == EVIDENCE_UNKNOWN
    assert latest["streaming"] == EVIDENCE_UNKNOWN


def test_effective_capabilities_projection_is_the_request_resolver(tmp_path):
    store, service = _service(tmp_path)
    created = _create_profile(service)
    projection = created["effective_capabilities"]
    caps = get_model_capabilities("openai_responses", "lab-model")
    boolean_fields = (
        "tool_calling",
        "parallel_tool_calls",
        "strict_tool_schema",
        "vision",
        "audio",
        "reasoning",
        "streaming",
    )
    assert set(projection) == set(boolean_fields)
    for field in boolean_fields:
        entry = projection[field]
        assert set(entry) == {"value", "source"}
        assert isinstance(entry["value"], bool)
        assert entry["source"] in {"catalog", "explicit_override", "probe"}
        # One resolver: the projection and the request decision cannot disagree.
        assert entry["value"] is bool(getattr(caps, field))
    assert projection["tool_calling"] == {"value": True, "source": "catalog"}


def test_a_probe_that_finishes_after_an_edit_is_persisted_but_not_adopted(
    tmp_path, monkeypatch
):
    """The attribution check for a revision change: persistence, no decision."""
    ticks = iter([1_000, 2_000, 3_000])
    store, service = _service_with_clock(tmp_path, ticks)
    created = _create_profile(
        service, provider="chatgpt", base_url=LOCAL_BASE, model="lab-model"
    )
    profile_id = created["id"]
    started = threading.Event()
    release = threading.Event()

    def answer(payload):
        if payload.get("tools"):
            started.set()
            assert release.wait(10), "the in-flight probe was never released"
        return _wire_tool_reply() if payload.get("tools") else _wire_text_reply()

    wire = _Wire(answer=answer, stream_events=lambda _payload: _wire_stream())
    wire.install(monkeypatch)
    results: list[dict] = []
    errors: list[BaseException] = []

    def run_probe():
        try:
            results.append(service.probe(profile_id))
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion
            errors.append(exc)

    thread = threading.Thread(target=run_probe, name="edited-under-probe")
    thread.start()
    assert started.wait(10)

    edited, _selected = service.edit(profile_id, {"model": "other-model"})
    assert edited["revision"] != created["revision"]
    release.set()
    thread.join(10)
    assert not thread.is_alive()
    assert errors == []

    # The row was persisted with the identity the probe started on...
    latest = store.model_capability_receipts.latest_for_profile(profile_id)
    assert latest["revision"] == created["revision"]
    assert latest["native_tool_call"] == EVIDENCE_TRUE
    # ...and reported as an observation that no longer describes the profile.
    assert results[0]["capability_receipt"]["stale"] is True
    # ...but it did not rewrite the decision for either configuration.
    assert adopted_receipts(profile_id=profile_id) == []
    assert (
        get_model_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE).tool_calling
        is False
    )
    assert (
        get_model_capabilities(
            "chatgpt", "other-model", base_url=LOCAL_BASE
        ).tool_calling
        is False
    )
