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
    explain_model_capabilities,
    get_model_capabilities,
    install_receipt_overlay,
    replace_receipt_overlay,
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


def _explain():
    """The published next-request projection for the local test configuration."""
    return explain_model_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE)


def _restart(service):
    """Close this process's Store, forget overlays, and open the same DB again.

    The reopened service is what a daemon restart would resolve from the durable
    receipts, so comparing its projection with the pre-restart one is the
    restart-consistency assertion M1 asks for.
    """
    db_path = service.store.db_path
    service.store.close()
    drop_receipt_overlays()
    reopened = get_store(db_path)
    return reopened, ModelProfileService(
        reopened, service.cfg, providers=provider_specs
    )


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

    assert (
        get_model_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE).tool_calling
        is True
    )
    assert _explain()["tool_calling"] == {"value": True, "source": "probe"}
    sent = _next_request(wire, tools=[DEMO_TOOL])
    assert _tool_names(sent) == ["demo_tool"]

    state["probe"] = "unknown"
    second = service.probe(profile_id)
    receipt = second["capability_receipt"]
    assert receipt["native_tool_call"] == EVIDENCE_UNKNOWN
    assert receipt["streaming"] == EVIDENCE_UNKNOWN
    assert receipt["stale"] is False
    assert adopted_receipts(profile_id=profile_id) == []

    withdrawn = get_model_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE)
    assert withdrawn.tool_calling is False
    assert _explain()["tool_calling"] == {"value": False, "source": "catalog"}
    sent = _next_request(wire, tools=[DEMO_TOOL])
    assert "tools" not in sent, "a withdrawn promotion must not survive on the wire"

    # Restart consistency: the durable latest observation is the unknown one, so
    # a daemon restart must not resurrect the withdrawn promotion.
    before = _explain()
    store, service = _restart(service)
    assert _explain() == before
    assert adopted_receipts(profile_id=profile_id) == []
    latest = store.model_capability_receipts.latest_for_profile(profile_id)
    assert latest["native_tool_call"] == EVIDENCE_UNKNOWN
    assert latest["streaming"] == EVIDENCE_UNKNOWN


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

    before = _explain()
    store, service = _restart(service)
    assert _explain() == before


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
    assert adopted[0]["streaming"] == EVIDENCE_TRUE

    caps = get_model_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE)
    assert caps.tool_calling is False, "the tool promotion is withdrawn"
    assert caps.streaming is True, "the still-positive streaming evidence remains"
    projection = _explain()
    assert projection["tool_calling"] == {"value": False, "source": "catalog"}
    assert projection["streaming"]["value"] is True
    sent = _next_request(wire, tools=[DEMO_TOOL])
    assert "tools" not in sent

    before = _explain()
    store, service = _restart(service)
    assert _explain() == before


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
    assert receipt["native_tool_call"] != EVIDENCE_FALSE, "network failure is unknown"
    assert receipt["streaming"] != EVIDENCE_FALSE
    assert adopted_receipts(profile_id=profile_id) == []
    assert (
        get_model_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE).tool_calling
        is False
    )
    sent = _next_request(wire, tools=[DEMO_TOOL])
    assert "tools" not in sent

    before = _explain()
    store, service = _restart(service)
    assert _explain() == before


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
    assert _explain()["tool_calling"] == {"value": True, "source": "probe"}

    def _unavailable(**_kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(store.model_capability_receipts, "put", _unavailable)
    with pytest.raises(sqlite3.OperationalError):
        service.probe(profile_id)

    # The durable write failed before any effective-evidence change: the
    # previously stored promotion is still the decision, on the wire too.
    assert _explain()["tool_calling"] == {"value": True, "source": "probe"}
    assert adopted_receipts(profile_id=profile_id)
    sent = _next_request(wire, tools=[DEMO_TOOL])
    assert _tool_names(sent) == ["demo_tool"]

    before = _explain()
    store, service = _restart(service)
    assert _explain() == before


def test_a_probe_that_finishes_after_an_edit_is_persisted_but_not_adopted(
    tmp_path, monkeypatch
):
    """Revision race: a probe may outlive the profile revision it started on."""
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
    # ...reported as an observation that no longer describes the profile...
    assert results[0]["capability_receipt"]["stale"] is True
    # ...and it did not rewrite the decision for either configuration.
    assert adopted_receipts(profile_id=profile_id) == []
    assert _explain()["tool_calling"] == {"value": False, "source": "catalog"}
    assert (
        get_model_capabilities(
            "chatgpt", "other-model", base_url=LOCAL_BASE
        ).tool_calling
        is False
    )

    before = _explain()
    store, service = _restart(service)
    assert _explain() == before


def test_shared_endpoint_latest_observation_wins_and_deletion_restores_survivor(
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
    assert _explain()["tool_calling"] == {"value": True, "source": "probe"}

    # Same endpoint, model and wire: the latest observation is B's unknown, so
    # the shared identity stops promoting tools even though A observed them.
    state["probe"] = "unknown"
    service.probe(second["id"])
    assert adopted_receipts() == []
    assert _explain()["tool_calling"] == {"value": False, "source": "catalog"}

    before = _explain()
    store, service = _restart(service)
    assert _explain() == before, "a restart must agree with the running process"

    # Deleting the configuration that made the newer unknown observation leaves
    # A's earlier receipt as the newest still-valid evidence for the key, so it
    # becomes effective again -- exactly what a restart would now read.
    service.delete(second["id"])
    assert _explain()["tool_calling"] == {"value": True, "source": "probe"}
    sent = _next_request(wire, tools=[DEMO_TOOL])
    assert _tool_names(sent) == ["demo_tool"]

    before = _explain()
    store, service = _restart(service)
    assert _explain() == before


def test_profile_deletion_cannot_restore_a_snapshot_older_than_a_concurrent_probe(
    tmp_path, monkeypatch
):
    store, service = _service(tmp_path)
    first = _create_profile(service, provider="chatgpt", base_url=LOCAL_BASE)
    second = _create_profile(service, provider="chatgpt", base_url=LOCAL_BASE)
    service._persist_receipt(
        first,
        reachable=True,
        native_tool_call=EVIDENCE_TRUE,
        streaming=EVIDENCE_UNKNOWN,
        observed_at=1,
    )
    service._persist_receipt(
        second,
        reachable=False,
        native_tool_call=EVIDENCE_UNKNOWN,
        streaming=EVIDENCE_UNKNOWN,
        observed_at=2,
    )
    selected = threading.Event()
    probe_started = threading.Event()
    probe_done = threading.Event()
    errors = []
    read = service._valid_receipts

    def hold_snapshot():
        rows = read()
        selected.set()
        assert probe_started.wait(10)
        # Before the fix the probe commits here, then deletion republishes
        # these old rows. With the shared lock the probe waits until this
        # bounded hold ends and publishes after the rebuild instead.
        probe_done.wait(0.5)
        return rows

    monkeypatch.setattr(service, "_valid_receipts", hold_snapshot)

    def deleting():
        try:
            service.delete(second["id"])
        except BaseException as error:
            errors.append(error)

    def probing():
        try:
            assert selected.wait(10)
            probe_started.set()
            service._persist_receipt(
                first,
                reachable=False,
                native_tool_call=EVIDENCE_UNKNOWN,
                streaming=EVIDENCE_UNKNOWN,
                observed_at=3,
            )
        except BaseException as error:
            errors.append(error)
        finally:
            probe_done.set()

    threads = [threading.Thread(target=deleting), threading.Thread(target=probing)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert _explain()["tool_calling"] == {"value": False, "source": "catalog"}
    before = _explain()
    store._load_capability_receipt_overlays()
    assert _explain() == before


def test_receipt_adoption_cannot_outlive_concurrent_profile_deletion(
    tmp_path, monkeypatch
):
    store, service = _service(tmp_path)
    created = _create_profile(service, provider="chatgpt", base_url=LOCAL_BASE)
    adopting = threading.Event()
    deleting = threading.Event()
    deleted = threading.Event()
    errors = []

    def hold_adoption(row):
        adopting.set()
        assert deleting.wait(10)
        deleted.wait(0.5)
        return replace_receipt_overlay(row)

    monkeypatch.setattr(
        "openai4s.server.model_profiles.replace_receipt_overlay", hold_adoption
    )

    def probing():
        try:
            service._persist_receipt(
                created,
                reachable=True,
                native_tool_call=EVIDENCE_TRUE,
                streaming=EVIDENCE_UNKNOWN,
                observed_at=1,
            )
        except BaseException as error:
            errors.append(error)

    def remove():
        try:
            assert adopting.wait(10)
            deleting.set()
            service.delete(created["id"])
        except BaseException as error:
            errors.append(error)
        finally:
            deleted.set()

    threads = [threading.Thread(target=probing), threading.Thread(target=remove)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert adopted_receipts() == []
    assert _explain()["tool_calling"] == {"value": False, "source": "catalog"}
    before = _explain()
    store._load_capability_receipt_overlays()
    assert _explain() == before


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
    # The evidence is stored, but F11 gives the explicit override priority and
    # `explain` must say so.
    assert adopted_receipts(profile_id=profile_id)
    assert (
        get_model_capabilities("chatgpt", "lab-model", base_url=LOCAL_BASE).tool_calling
        is False
    )
    assert _explain()["tool_calling"] == {
        "value": False,
        "source": "explicit_override",
    }
    sent = _next_request(wire, tools=[DEMO_TOOL])
    assert "tools" not in sent

    before = _explain()
    store, service = _restart(service)
    assert _explain() == before


def test_explain_and_public_profile_share_the_request_resolver(tmp_path):
    store, service = _service(tmp_path)
    created = _create_profile(service)
    projection = created["effective_capabilities"]
    # B.3: exactly the two probe-capable fields.
    assert set(projection) == {"tool_calling", "streaming"}
    caps = get_model_capabilities("openai_responses", "lab-model")
    for field in ("tool_calling", "streaming"):
        entry = projection[field]
        assert set(entry) == {"value", "source"}
        assert isinstance(entry["value"], bool)
        assert entry["source"] in {"catalog", "explicit_override", "probe"}
        assert entry["value"] is bool(getattr(caps, field))
    assert projection == explain_model_capabilities("openai_responses", "lab-model")
    assert projection["tool_calling"] == {"value": True, "source": "catalog"}


def test_a_modelless_profile_keys_its_receipt_on_the_model_the_next_request_names(
    tmp_path, monkeypatch
):
    """The receipt identity took a modelless profile's model from the adapter's
    default while `effective_capabilities` read `provider_specs`. The two tables
    agree today only by coincidence; when they differ, positive evidence must
    still reach the projection the next request decides from."""
    specs = {name: dict(spec) for name, spec in provider_specs().items()}
    specs["chatgpt"]["model"] = "spec-default-model"
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    store = get_store(cfg.db_path)
    service = ModelProfileService(store, cfg, providers=lambda: specs)
    created = _create_profile(
        service, provider="chatgpt", model="", base_url=LOCAL_BASE
    )
    assert created["effective_capabilities"]["tool_calling"]["value"] is False

    def _chat(messages, cfg, **kwargs):
        if kwargs.get("on_delta") is not None:
            return _stream_reply(kwargs["on_delta"])
        return _valid_tool_reply()

    monkeypatch.setattr("openai4s.llm.chat", _chat)
    result = service.probe(created["id"])
    assert result["capability_receipt"]["native_tool_call"] == EVIDENCE_TRUE
    assert result["capability_receipt"]["stale"] is False
    profile = service._current_profile(created["id"])
    assert service.effective_capabilities(profile)["tool_calling"] == {
        "value": True,
        "source": "probe",
    }


def test_public_profile_publishes_null_for_an_undispatchable_protocol(tmp_path):
    store, service = _service(tmp_path)

    def add_unknown_protocol(profiles):
        profiles.append(
            {
                "id": "mp-unknown-protocol",
                "name": "legacy import",
                "provider": "not_a_wire",
                "base_url": "",
                "model": "whatever",
                "api_key": "",
                "revision": 1,
            }
        )

    store.mutate_model_profiles(add_unknown_protocol)
    profile = next(
        item
        for item in store.list_model_profiles()
        if item.get("id") == "mp-unknown-protocol"
    )
    projected = service.public_profile(profile)
    assert projected["readiness"]["state"] == "unsupported"
    assert projected["effective_capabilities"] is None


def test_install_receipt_overlay_keeps_its_positive_only_semantics():
    drop_receipt_overlays()
    receipt = {
        "endpoint": LOCAL_BASE,
        "model": "lab-model",
        "wire": "openai",
        "profile_id": "mp-install",
        "revision": 1,
        "native_tool_call": EVIDENCE_TRUE,
        "streaming": EVIDENCE_UNKNOWN,
        "observed_at": 1_000,
        "receipt_id": "install-1",
        "receipt_sha256": "install-positive",
    }
    assert install_receipt_overlay(receipt) is True
    assert adopted_receipts(profile_id="mp-install")

    non_positive = dict(
        receipt,
        native_tool_call=EVIDENCE_UNKNOWN,
        streaming=EVIDENCE_UNKNOWN,
        observed_at=2_000,
        receipt_id="install-2",
        receipt_sha256="install-unknown",
    )
    # Unchanged M1 semantics: install never withdraws; replace is the only
    # path that turns a non-positive observation into a removal.
    assert install_receipt_overlay(non_positive) is False
    assert adopted_receipts(profile_id="mp-install")
    drop_receipt_overlays()


def test_replace_receipt_overlay_orders_observations_per_f10():
    drop_receipt_overlays()
    base = {
        "endpoint": LOCAL_BASE,
        "model": "lab-model",
        "wire": "openai",
        "profile_id": "mp-order",
        "revision": 1,
        "receipt_sha256": "order",
    }

    def receipt(observed_at, native, receipt_id):
        return dict(
            base,
            observed_at=observed_at,
            native_tool_call=native,
            streaming=EVIDENCE_UNKNOWN,
            receipt_id=receipt_id,
        )

    assert replace_receipt_overlay(receipt(2_000, EVIDENCE_TRUE, "r-2000")) is True
    # An older observation that finishes later must not replace newer evidence.
    assert replace_receipt_overlay(receipt(1_000, EVIDENCE_UNKNOWN, "r-1000")) is True
    assert adopted_receipts(profile_id="mp-order")[0]["receipt_id"] == "r-2000"
    # F10 tie-break: same observed_at, the larger receipt_id is the later
    # observation.
    assert replace_receipt_overlay(receipt(2_000, EVIDENCE_TRUE, "r-2000a")) is True
    assert adopted_receipts(profile_id="mp-order")[0]["receipt_id"] == "r-2000a"
    # ...so the smaller id at the same observed_at is not newer and is ignored.
    assert replace_receipt_overlay(receipt(2_000, EVIDENCE_TRUE, "r-2000")) is True
    assert adopted_receipts(profile_id="mp-order")[0]["receipt_id"] == "r-2000a"
    # A newer non-positive observation withdraws the adopted promotion.
    assert replace_receipt_overlay(receipt(3_000, EVIDENCE_UNKNOWN, "r-3000")) is False
    assert adopted_receipts(profile_id="mp-order") == []
    drop_receipt_overlays()
