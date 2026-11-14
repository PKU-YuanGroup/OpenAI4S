"""Offline tests for explicit endpoint model-list discovery."""

from __future__ import annotations

import http.client
import io
import json
from urllib.error import HTTPError, URLError

import pytest

from openai4s.server.endpoint_models import EndpointModelError, EndpointModelFetcher


class _Response:
    def __init__(self, payload):
        self.raw = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, limit):
        return self.raw[:limit]


class _Opener:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        payload = self.payloads.pop(0)
        if isinstance(payload, BaseException):
            raise payload
        return _Response(payload)


def _fetch(provider, base_url, payload, api_key="secret"):
    opener = _Opener([payload])
    result = EndpointModelFetcher(opener=opener).fetch(
        provider=provider, base_url=base_url, api_key=api_key
    )
    return result, opener.calls[0][0]


def test_openai_models_use_bearer_and_fill_first_model():
    result, request = _fetch(
        "chatgpt",
        "https://api.example/v1",
        {"data": [{"id": "gpt-custom"}, {"id": "gpt-other"}]},
    )
    assert result["models"] == ["gpt-custom", "gpt-other"]
    assert result["default_model"] == "gpt-custom"
    assert request.full_url == "https://api.example/v1/models"
    assert request.get_header("Authorization") == "Bearer secret"


def test_protocol_specific_model_routes_and_auth_headers():
    _, anthropic = _fetch(
        "claude", "https://api.example", {"data": [{"id": "claude-x"}]}
    )
    assert anthropic.full_url == "https://api.example/v1/models?limit=1000"
    assert anthropic.get_header("X-api-key") == "secret"
    assert anthropic.get_header("Anthropic-version") == "2023-06-01"

    _, gemini = _fetch(
        "gemini",
        "https://generativelanguage.googleapis.com",
        {"models": [{"name": "models/gemini-x"}]},
    )
    assert gemini.full_url.endswith("/v1beta/models?pageSize=1000")
    assert gemini.get_header("X-goog-api-key") == "secret"


def test_ollama_falls_back_to_native_tags_route():
    opener = _Opener(
        [
            HTTPError(
                "https://ollama.example/v1/models", 404, "missing", {}, io.BytesIO()
            ),
            {"models": [{"name": "llama3.2"}]},
        ]
    )
    result = EndpointModelFetcher(opener=opener).fetch(
        provider="ollama", base_url="https://ollama.example/v1", api_key=""
    )
    assert result["models"] == ["llama3.2"]
    assert opener.calls[1][0].full_url == "https://ollama.example/api/tags"


def test_invalid_or_empty_endpoint_results_are_safe_failures():
    with pytest.raises(EndpointModelError, match="absolute http"):
        EndpointModelFetcher(opener=_Opener([])).fetch(
            provider="chatgpt", base_url="https://example/v1?key=leak", api_key="secret"
        )

    with pytest.raises(EndpointModelError, match="no model ids"):
        EndpointModelFetcher(opener=_Opener([{"data": []}])).fetch(
            provider="chatgpt", base_url="https://example/v1", api_key="secret"
        )


def test_response_is_bounded_before_model_parsing():
    opener = _Opener([{"data": [{"id": "x" * 2000}]}])
    with pytest.raises(EndpointModelError, match="too much"):
        EndpointModelFetcher(opener=opener, max_response_bytes=1024).fetch(
            provider="chatgpt", base_url="https://example/v1", api_key="secret"
        )


def _raising(error):
    return EndpointModelFetcher(opener=_Opener([error]))


@pytest.mark.parametrize(
    "error",
    [
        http.client.BadStatusLine("SSH-2.0-OpenSSH_9.6"),
        http.client.IncompleteRead(b""),
        URLError("refused"),
        TimeoutError("timed out"),
    ],
    ids=["non-http-service", "incomplete-read", "url-error", "timeout"],
)
def test_every_transport_failure_is_a_bounded_error(error):
    """`http.client.HTTPException` is not an `OSError`: a base URL pointed at
    a non-HTTP port raised `BadStatusLine` straight through to the gateway's
    catch-all, a 500 the wizard could not tell from a daemon bug."""
    with pytest.raises(EndpointModelError, match="could not reach") as raised:
        _raising(error).fetch(
            provider="chatgpt", base_url="http://127.0.0.1:22/v1", api_key="k"
        )
    assert raised.value.status_code == 502


@pytest.mark.parametrize(
    "base_url", ["http://127.0.0.1:abc/v1", "http://127.0.0.1:99999/v1"]
)
def test_an_invalid_port_is_the_users_400_before_any_request(base_url):
    opener = _Opener([])
    with pytest.raises(EndpointModelError, match="valid port") as raised:
        EndpointModelFetcher(opener=opener).fetch(
            provider="chatgpt", base_url=base_url, api_key="k"
        )
    assert raised.value.status_code == 400
    assert opener.calls == []


def test_the_ollama_fallback_maps_an_invalid_body_too():
    """The fallback request ran inside the `except HTTPError` handler, where
    the sibling JSON clause could not catch its decode error."""

    class _Html(_Response):
        def __init__(self):
            self.raw = b"<html>not a model api</html>"

    class _Fallback(_Opener):
        def open(self, request, timeout):
            self.calls.append((request, timeout))
            if len(self.calls) == 1:
                raise HTTPError(request.full_url, 404, "missing", {}, io.BytesIO())
            return _Html()

    with pytest.raises(EndpointModelError, match="invalid JSON"):
        EndpointModelFetcher(opener=_Fallback([])).fetch(
            provider="ollama", base_url="http://127.0.0.1:11434/v1", api_key=""
        )


def test_only_string_ids_become_candidates():
    result, _ = _fetch(
        "chatgpt",
        "https://example/v1",
        {"data": [{"id": {"nested": 1}}, {"id": True}, {"id": 0, "name": "real"}]},
    )
    assert result["models"] == ["real"]


def test_gemini_keeps_only_models_that_generate_content():
    result, _ = _fetch(
        "gemini",
        "https://generativelanguage.googleapis.com",
        {
            "models": [
                {
                    "name": "models/embedding-001",
                    "supportedGenerationMethods": ["embedContent"],
                },
                {
                    "name": "models/gemini-chat",
                    "supportedGenerationMethods": ["generateContent"],
                },
            ]
        },
    )
    assert result["models"] == ["gemini-chat"]


def test_the_protocol_default_is_preferred_when_the_endpoint_serves_it():
    opener = _Opener([{"data": [{"id": "whisper-1"}, {"id": "gpt-5"}]}] * 2)
    fetcher = EndpointModelFetcher(opener=opener)
    served = fetcher.fetch(
        provider="chatgpt",
        base_url="https://example/v1",
        api_key="k",
        preferred_model="gpt-5",
    )
    assert served["default_model"] == "gpt-5"
    absent = fetcher.fetch(
        provider="chatgpt",
        base_url="https://example/v1",
        api_key="k",
        preferred_model="gpt-absent",
    )
    assert absent["default_model"] == "whisper-1"


@pytest.mark.parametrize(
    ("provider", "base_url", "listed"),
    [
        (
            "claude",
            "https://api.anthropic.com/v1",
            "https://api.anthropic.com/v1/v1/models?limit=1000",
        ),
        (
            "gemini",
            "https://generativelanguage.googleapis.com/v1beta",
            "https://generativelanguage.googleapis.com/v1beta/v1beta/models?pageSize=1000",
        ),
    ],
)
def test_the_listing_route_carries_the_prefix_dispatch_adds(provider, base_url, listed):
    """Dispatch always appends `/v1/messages` / `/v1beta/models/...`. A listing
    that tolerated the prefix in the base URL succeeded for a base the saved
    profile then 404ed on."""
    _, request = _fetch(provider, base_url, {"data": [{"id": "m"}]})
    assert request.full_url == listed


def test_an_auth_refusal_names_a_missing_key_as_missing():
    def refusal():
        return HTTPError("https://example/v1/models", 401, "no", {}, io.BytesIO())

    with pytest.raises(EndpointModelError, match="requires an API key"):
        _raising(refusal()).fetch(
            provider="chatgpt", base_url="https://example/v1", api_key=""
        )
    with pytest.raises(EndpointModelError, match="rejected the API key"):
        _raising(refusal()).fetch(
            provider="chatgpt", base_url="https://example/v1", api_key="k"
        )


def test_a_refused_redirect_is_named_as_one():
    error = HTTPError("http://example/v1/models", 301, "moved", {}, io.BytesIO())
    with pytest.raises(EndpointModelError, match="redirected"):
        _raising(error).fetch(
            provider="chatgpt", base_url="http://example/v1", api_key="k"
        )


@pytest.mark.parametrize("key", ["sk-abc\ndef", "sk-密钥"])
def test_a_key_that_cannot_travel_in_a_header_is_a_400(key):
    opener = _Opener([])
    with pytest.raises(EndpointModelError, match="cannot be sent") as raised:
        EndpointModelFetcher(opener=opener).fetch(
            provider="chatgpt", base_url="https://example/v1", api_key=key
        )
    assert raised.value.status_code == 400
    assert opener.calls == []


@pytest.mark.stubbed_backend
def test_discovery_asks_where_and_as_whom_dispatch_would(tmp_path, monkeypatch):
    """A blank Base URL used the protocol's static default and a blank key sent
    nothing, so with `OPENAI4S_CHATGPT_BASE_URL` naming a proxy the wizard
    listed api.openai.com's models for a profile that dispatches to the proxy,
    and a key kept in the environment read as "the endpoint rejected the API
    key". Both now resolve through the rules dispatch uses -- and the
    environment key still never travels to a URL the user typed."""
    from openai4s.config import Config, LLMConfig
    from openai4s.llm import provider_specs
    from openai4s.server.model_profiles import ModelProfileService
    from openai4s.store import get_store

    monkeypatch.setenv("OPENAI4S_CHATGPT_BASE_URL", "https://proxy.example/v1")
    monkeypatch.setenv("OPENAI4S_CHATGPT_API_KEY", "sk-env")
    seen: list[dict] = []

    def _fetch_stub(self, **kwargs):
        seen.append(kwargs)
        return {"models": ["m"], "default_model": "m", "count": 1, "source": ""}

    monkeypatch.setattr(EndpointModelFetcher, "fetch", _fetch_stub)
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    service = ModelProfileService(get_store(cfg.db_path), cfg, providers=provider_specs)

    service.discover_models({"provider": "chatgpt"})
    assert seen[-1]["base_url"] == "https://proxy.example/v1"
    assert seen[-1]["api_key"] == "sk-env"
    assert seen[-1]["preferred_model"] == provider_specs()["chatgpt"]["model"]

    service.discover_models(
        {"provider": "chatgpt", "base_url": "https://elsewhere.example/v1"}
    )
    assert seen[-1]["base_url"] == "https://elsewhere.example/v1"
    assert seen[-1]["api_key"] == ""

    service.discover_models({"provider": "chatgpt", "api_key": "sk-typed"})
    assert seen[-1]["api_key"] == "sk-typed"


def test_a_missing_listing_route_is_tagged_so_callers_can_tell_it_apart():
    error = HTTPError("https://example/v1/models", 404, "no", {}, io.BytesIO())
    with pytest.raises(EndpointModelError, match="model-listing route") as raised:
        _raising(error).fetch(
            provider="ark", base_url="https://example/v1", api_key="k"
        )
    assert raised.value.reason == "no_listing_route"


def _discovery_service(tmp_path):
    from openai4s.config import Config, LLMConfig
    from openai4s.llm import provider_specs
    from openai4s.server.model_profiles import ModelProfileService
    from openai4s.store import get_store

    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    return ModelProfileService(get_store(cfg.db_path), cfg, providers=provider_specs)


@pytest.mark.stubbed_backend
def test_the_agent_plan_gateway_offers_the_plan_catalog(tmp_path, monkeypatch):
    """Volcengine's Agent Plan gateway, the `ark` default, has no `/models`
    route: a key it accepts gets a 404 there. First-run setup's "Fetch models"
    therefore failed for the default protocol every time. The plan's model set
    is the published table the Ark presets mirror, so that is offered instead,
    marked as the catalog rather than as what the endpoint listed."""
    from openai4s import llm
    from openai4s.server.endpoint_models import NO_LISTING_ROUTE

    for name in ("OPENAI4S_ARK_BASE_URL", "OPENAI4S_LLM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)

    def _no_route(self, **_kwargs):
        raise EndpointModelError(
            "the endpoint does not expose a model-listing route for this protocol",
            reason=NO_LISTING_ROUTE,
        )

    monkeypatch.setattr(EndpointModelFetcher, "fetch", _no_route)
    service = _discovery_service(tmp_path)

    result = service.discover_models({"provider": "ark", "api_key": "k"})

    presets = [p.model for p in llm.model_presets("ark") if p.model]
    assert result["source"] == "catalog"
    assert result["models"] == presets
    assert result["count"] == len(presets)
    assert result["default_model"] == llm.provider_specs()["ark"]["model"]
    assert result["default_model"] in presets


@pytest.mark.stubbed_backend
@pytest.mark.parametrize(
    "body",
    [
        # the platform (`/api/v3`) and Coding Plan gateways are not the plan
        {"provider": "ark", "base_url": "https://ark.cn-beijing.volces.com/api/v3"},
        {
            "provider": "ark",
            "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
        },
        # an ark-compatible proxy is someone else's endpoint
        {"provider": "ark", "base_url": "https://proxy.example/api/plan/v3"},
        # another protocol at the plan host keeps its own answer
        {
            "provider": "chatgpt",
            "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3",
        },
    ],
)
def test_only_the_agent_plan_gateway_falls_back_to_the_catalog(
    tmp_path, monkeypatch, body
):
    from openai4s.server.endpoint_models import NO_LISTING_ROUTE
    from openai4s.server.model_profiles import ModelProfileError

    def _no_route(self, **_kwargs):
        raise EndpointModelError("no route", reason=NO_LISTING_ROUTE)

    monkeypatch.setattr(EndpointModelFetcher, "fetch", _no_route)
    with pytest.raises(ModelProfileError, match="no route"):
        _discovery_service(tmp_path).discover_models({**body, "api_key": "k"})


@pytest.mark.stubbed_backend
def test_an_auth_refusal_at_the_agent_plan_gateway_is_not_papered_over(
    tmp_path, monkeypatch
):
    """Without an accepted key the gateway answers 401, not 404: that must stay
    the user's error, never become a catalog that looks like a success."""
    from openai4s.server.model_profiles import ModelProfileError

    def _refused(self, **_kwargs):
        raise EndpointModelError("the endpoint rejected the API key")

    monkeypatch.setattr(EndpointModelFetcher, "fetch", _refused)
    with pytest.raises(ModelProfileError, match="rejected the API key"):
        _discovery_service(tmp_path).discover_models(
            {"provider": "ark", "api_key": "k"}
        )
