"""Offline Ollama model, credential, web-service and Host routing contracts."""

from __future__ import annotations

import dataclasses
import io
import json
import urllib.error

import pytest

from openai4s import egress, llm, webtools
from openai4s.config import Config, LLMConfig
from openai4s.host_dispatch import HostDispatcher
from openai4s.ollama_web import (
    MAX_RESPONSE_BYTES,
    OllamaWebError,
    OllamaWebService,
    _NoRedirect,
    resolve_web_key,
    save_web_config,
)

pytestmark = pytest.mark.stubbed_backend
SECRET = "ollama-synthetic-credential-for-offline-tests"
SEARCH = {
    "results": [
        {"title": "Paper", "url": "https://arxiv.org/abs/1234", "content": "Evidence"}
    ]
}
FETCH = {"title": "Paper", "content": "Evidence", "links": ["https://arxiv.org/"]}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for name in (
        "OLLAMA_API_KEY",
        "OPENAI4S_OLLAMA_API_KEY",
        "OPENAI4S_LLM_API_KEY",
        "OPENAI4S_LLM_BASE_URL",
        "OPENAI4S_LLM_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(webtools, "guard_url", lambda _url: None)


class Response(io.BytesIO):
    status = 200

    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode())
        self.headers = {}


class Opener:
    def __init__(self, payload=SEARCH):
        self.payload = payload
        self.requests = []
        self.error = None
        self.rotate = lambda: None

    def open(self, request, **_kwargs):
        self.requests.append(request)
        self.rotate()
        if self.error:
            raise self.error
        return Response(self.payload)


def test_ollama_defaults_native_key_tools_and_replay(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", SECRET)
    cfg = LLMConfig(provider="ollama")
    captured = []

    def post(url, payload, headers, _timeout):
        captured.append((url, payload, headers))
        return {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "web_search",
                                    "arguments": '{"query":"science"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 8, "completion_tokens": 4},
        }

    monkeypatch.setattr(llm.transport, "post_json", post)
    tool = {
        "name": "web_search",
        "description": "Search",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }
    reply = llm.chat(
        [{"role": "user", "content": "search"}], cfg, tools=[tool], tool_choice="auto"
    )
    url, payload, headers = captured[0]
    assert cfg.api_key == SECRET
    assert cfg.model == "gemma4:31b"
    assert url == "https://ollama.com/v1/chat/completions"
    assert headers["Authorization"] == f"Bearer {SECRET}"
    assert "tool_choice" not in payload and "parallel_tool_calls" not in payload
    assert payload["tools"][0]["function"]["name"] == "web_search"
    assert reply["tool_calls"][0]["name"] == "web_search"
    llm.chat(
        [
            reply["assistant_message"],
            {"role": "tool", "tool_call_id": "call-1", "content": "evidence"},
        ],
        cfg,
    )
    assert captured[1][1]["messages"][0]["tool_calls"][0]["id"] == "call-1"

    llm.chat(
        [{"role": "user", "content": "no tools"}], cfg, tools=[tool], tool_choice="none"
    )
    assert "tools" not in captured[-1][1]
    with pytest.raises(llm.LLMError, match="forced tool_choice"):
        llm.chat([], cfg, tools=[tool], tool_choice="web_search")

    cfg.base_url = "http://127.0.0.1:11434/v1"
    cfg.model = "gemma4:e2b"
    llm.chat([{"role": "user", "content": "local"}], cfg)
    assert "Authorization" not in captured[-1][2]


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.0.0.1:11434/v1",
        "https://proxy.example/v1",
        "https://ollama.com.evil.example/v1",
        "http://ollama.com/v1",
        "https://ollama.com/v1?token=x",
        "https://user@ollama.com/v1",
        "https://ollama.com:0/v1",
        "https://ollama.com/v1#fragment",
        "https://olla\nma.com/v1",
    ],
)
def test_proxy_or_local_credentials_never_donate_to_web(endpoint):
    assert (
        resolve_web_key(
            LLMConfig(
                provider="ollama",
                base_url=endpoint,
                api_key=SECRET,
            )
        )
        == ""
    )


def test_cloud_credential_and_dedicated_environment_resolution(monkeypatch):
    cfg = LLMConfig(provider="ollama", api_key=SECRET)
    assert resolve_web_key(cfg) == SECRET
    cfg.provider = "chatgpt"
    assert resolve_web_key(cfg) == ""
    monkeypatch.setenv("OLLAMA_API_KEY", "dedicated-ollama-test-key")
    assert resolve_web_key(cfg) == "dedicated-ollama-test-key"


def test_search_maps_snippets_clamps_count_and_checks_policy(monkeypatch):
    checked = []
    monkeypatch.setattr(egress, "check_url", checked.append)
    opener = Opener()
    result = OllamaWebService(lambda: SECRET, opener=opener).search(
        "科学 research",
        num_results=50,
    )
    request = opener.requests[0]
    assert request.full_url == "https://ollama.com/api/web_search"
    assert request.get_header("Authorization") == f"Bearer {SECRET}"
    assert json.loads(request.data) == {"query": "科学 research", "max_results": 10}
    assert checked == ["https://ollama.com/api/web_search"]
    assert result == {
        "query": "科学 research",
        "count": 1,
        "source": "ollama",
        "results": [
            {
                "title": "Paper",
                "url": "https://arxiv.org/abs/1234",
                "snippet": "Evidence",
            }
        ],
    }


def test_fetch_screens_all_prose_and_checks_target(monkeypatch):
    checked = []
    monkeypatch.setattr(egress, "check_url", checked.append)
    opener = Opener(FETCH)
    result = OllamaWebService(lambda: SECRET, opener=opener).fetch(
        "https://arxiv.org/",
        max_chars=12,
    )
    assert checked == ["https://arxiv.org/", "https://ollama.com/api/web_fetch"]
    assert json.loads(opener.requests[0].data) == {"url": "https://arxiv.org/"}
    assert result["content"] == "Paper\n\nEvide"
    assert result["truncated"] is True
    assert result["source"] == "ollama"
    assert "title" not in result and "links" not in result
    assert len(result["raw_sha256"]) == 64


@pytest.mark.parametrize("extra", [{}, {"links": None}, {"links": []}])
def test_fetch_accepts_pages_without_outgoing_links(extra):
    opener = Opener({"title": "Page", "content": "Text", **extra})
    result = OllamaWebService(lambda: SECRET, opener=opener).fetch("https://ollama.com")
    assert result["content"] == "Page\n\nText"


def test_fetch_keeps_relative_and_contact_links_in_screened_content():
    opener = Opener(
        {
            "title": "Page",
            "content": "Text",
            "links": ["/about", "mailto:research@example.org"],
        }
    )
    result = OllamaWebService(lambda: SECRET, opener=opener).fetch("https://ollama.com")
    assert result["content"] == "Page\n\nText\n\n/about\nmailto:research@example.org"
    assert len(opener.requests) == 1


@pytest.mark.parametrize("kind", ["network", "egress", "ssrf"])
def test_blocked_fetch_never_sends(monkeypatch, kind):
    opener = Opener(FETCH)
    if kind == "network":
        monkeypatch.setattr(webtools, "network_allowed", lambda: False)
        error = webtools.NetworkDisabled
    elif kind == "egress":
        error = egress.EgressBlocked
        monkeypatch.setattr(
            egress, "check_url", lambda _u: (_ for _ in ()).throw(error("denied"))
        )
    else:
        error = webtools.SSRFBlocked
        monkeypatch.setattr(
            webtools, "guard_url", lambda _u: (_ for _ in ()).throw(error("denied"))
        )
    with pytest.raises(error):
        OllamaWebService(lambda: SECRET, opener=opener).fetch("https://arxiv.org/")
    assert opener.requests == []


@pytest.mark.parametrize("status", [301, 302, 307, 401, 403, 429, 500])
def test_http_errors_never_publish_reflected_keys(status):
    opener = Opener()
    opener.error = urllib.error.HTTPError(
        "https://ollama.com",
        status,
        SECRET,
        {},
        io.BytesIO(SECRET.encode()),
    )
    with pytest.raises(OllamaWebError) as failure:
        OllamaWebService(lambda: SECRET, opener=opener).search("test")
    assert SECRET not in str(failure.value)
    assert str(status) in str(failure.value)
    assert _NoRedirect().redirect_request(None, None, None, None, None, None) is None


def test_rotation_redacts_sent_and_current_credentials():
    current = [SECRET]
    rotated = SECRET + "-rotated"
    opener = Opener(
        {
            "results": [
                {
                    "title": SECRET,
                    "content": rotated,
                    "url": "https://arxiv.org/",
                }
            ]
        }
    )
    opener.rotate = lambda: current.__setitem__(0, rotated)
    service = OllamaWebService(lambda: current[0], opener=opener)
    result = service.search(SECRET)
    assert SECRET not in json.dumps(result)
    assert "rotated" not in json.dumps(result)
    service.search("another")
    assert opener.requests[1].get_header("Authorization") == f"Bearer {rotated}"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"error": SECRET},
        {"results": {}},
        {"results": [None]},
        {"results": [{"title": 3, "content": "text", "url": "https://arxiv.org/"}]},
        {
            "results": [
                {"title": "text", "content": "text", "url": "file:///etc/passwd"}
            ]
        },
        {
            "results": [
                {
                    "title": "text",
                    "content": "x" * MAX_RESPONSE_BYTES,
                    "url": "https://arxiv.org/",
                }
            ]
        },
    ],
)
def test_invalid_or_oversized_responses_fail(payload):
    with pytest.raises(OllamaWebError):
        OllamaWebService(lambda: SECRET, opener=Opener(payload)).search("test")


def test_host_native_and_sdk_routes_share_ollama_and_refresh_config(
    tmp_path, monkeypatch
):
    dispatcher = HostDispatcher(
        Config(
            data_dir=tmp_path,
            llm=LLMConfig(provider="ollama", api_key=SECRET),
        ),
        frame_id="ollama-test",
    )
    opener = Opener()
    dispatcher._ollama_web_service._opener = opener
    try:
        assert dispatcher("web_search", [{"query": "research"}])["source"] == "ollama"
        opener.payload = FETCH
        assert (
            dispatcher("web_fetch", [{"url": "https://arxiv.org/"}])["source"]
            == "ollama"
        )
        monkeypatch.setattr(webtools, "web_fetch", lambda *a, **kw: {"direct": kw})
        for spec in (
            {"format": "html"},
            {"format": "json"},
            {"method": "HEAD"},
            {"user_agent": "Research contact"},
        ):
            assert "direct" in dispatcher._m_web_fetch(
                {"url": "https://arxiv.org/", **spec}
            )
        dispatcher.cfg = dataclasses.replace(
            dispatcher.cfg,
            llm=LLMConfig(provider="chatgpt", api_key="different-key"),
        )
        assert not dispatcher._ollama_web_service.configured()
        assert "direct" in dispatcher._m_web_fetch({"url": "https://arxiv.org/"})
        assert not hasattr(dispatcher, "_m_fetch_web")
    finally:
        dispatcher.store.close()


def test_ollama_egress_origin_is_exact(monkeypatch):
    monkeypatch.setenv("OPENAI4S_EGRESS", "allowlist")
    egress.check_url("https://ollama.com/api/web_search")
    with pytest.raises(egress.EgressBlocked):
        egress.check_url("https://untrusted.ollama.com/api/web_search")


def test_existing_host_uses_independent_key_after_save_rotate_and_model_switch(
    tmp_path,
):
    dispatcher = HostDispatcher(
        Config(
            data_dir=tmp_path,
            llm=LLMConfig(provider="deepseek", api_key="unrelated-model-key"),
        ),
        frame_id="ollama-independent",
    )
    opener = Opener()
    dispatcher._ollama_web_service._opener = opener
    try:
        assert not dispatcher._ollama_web_service.configured()
        save_web_config(dispatcher.store, {"api_key": SECRET})
        assert dispatcher("web_search", [{"query": "research"}])["source"] == "ollama"
        assert opener.requests[-1].get_header("Authorization") == f"Bearer {SECRET}"
        dispatcher.cfg = dataclasses.replace(
            dispatcher.cfg,
            llm=LLMConfig(provider="claude", api_key="unrelated-claude-key"),
        )
        save_web_config(dispatcher.store, {"api_key": SECRET + "-rotated"})
        opener.payload = FETCH
        assert (
            dispatcher("web_fetch", [{"url": "https://arxiv.org/"}])["source"]
            == "ollama"
        )
        assert (
            opener.requests[-1].get_header("Authorization")
            == f"Bearer {SECRET}-rotated"
        )
        save_web_config(dispatcher.store, {"clear_api_key": True})
        assert not dispatcher._ollama_web_service.configured()
    finally:
        dispatcher.store.close()
