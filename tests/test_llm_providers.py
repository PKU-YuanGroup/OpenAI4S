"""Offline unit tests for the multi-provider, multimodal LLM client.

No network: `llm.transport.post_json` is monkeypatched to capture the outbound URL,
payload and headers and to return a canned per-wire response. This lets us
assert, deterministically, that:

 * each provider selects the right wire (openai / anthropic / gemini),
 * base_url + model resolve from the provider registry (or config override),
 * text and image content parts translate into each wire's own schema,
 * text-only providers reject image parts with a clear LLMError,
 * the auth headers differ per wire (Bearer / x-api-key / x-goog-api-key).
"""
import pytest

from openai4s import llm
from openai4s.config import LLMConfig

# --- a capturing fake transport ------------------------------------------


class _Capture:
    """Replaces transport.post_json and returns a canned wire response."""

    def __init__(self):
        self.url = None
        self.payload = None
        self.headers = None

    def __call__(self, url, payload, headers, timeout):
        self.url, self.payload, self.headers = url, payload, headers
        # Return a minimal valid body for whichever wire is in play.
        if "/chat/completions" in url:
            return {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {},
            }
        if "/v1/messages" in url:
            return {
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {},
            }
        if ":generateContent" in url:
            return {
                "candidates": [
                    {"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}
                ],
                "usageMetadata": {},
            }
        raise AssertionError(f"unexpected url {url!r}")


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    """These tests assert each provider's REGISTRY-default URL/model. A pinned
    deployment (e.g. an Ark daemon) sets the generic `OPENAI4S_LLM_*` env vars
    in .env, which legitimately override every provider — so clear them here so
    the unit tests exercise the defaults, not the local deployment's overrides."""
    for k in (
        "OPENAI4S_LLM_PROVIDER",
        "OPENAI4S_LLM_BASE_URL",
        "OPENAI4S_LLM_MODEL",
        "OPENAI4S_LLM_API_KEY",
        "OPENAI4S_LLM_REASONING_EFFORT",
        "OPENAI4S_LLM_TIMEOUT",
    ):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def cap(monkeypatch):
    c = _Capture()
    monkeypatch.setattr(llm.transport, "post_json", c)
    return c


def _cfg(provider, **kw):
    kw.setdefault("api_key", "test-key")
    return LLMConfig(provider=provider, **kw)


# --- registry / resolution -----------------------------------------------


@pytest.mark.parametrize(
    "provider,wire,vision",
    [
        ("ark", "openai", True),
        ("chatgpt", "openai", True),
        ("openai_responses", "responses", False),
        ("claude", "anthropic", True),
        ("gemini", "gemini", True),
    ],
)
def test_registry_wire_and_vision(provider, wire, vision):
    spec = llm.provider_spec(provider)
    assert spec["wire"] == wire
    assert llm.supports_vision(provider) is vision


def test_unknown_provider_raises():
    with pytest.raises(llm.LLMError):
        llm.provider_spec("no-such-model")


def test_config_resolves_provider_defaults(monkeypatch):
    # No generic OPENAI4S_LLM_* overrides in play for this construction.
    for var in (
        "OPENAI4S_LLM_MODEL",
        "OPENAI4S_LLM_BASE_URL",
        "OPENAI4S_CLAUDE_MODEL",
        "OPENAI4S_CLAUDE_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    c = LLMConfig(provider="claude", api_key="k")
    assert c.model == "claude-sonnet-4-5"
    assert c.base_url == "https://api.anthropic.com"


def test_per_provider_env_override(monkeypatch):
    monkeypatch.setenv("OPENAI4S_GEMINI_MODEL", "gemini-2.5-pro")
    monkeypatch.setenv("OPENAI4S_GEMINI_API_KEY", "gkey")
    c = LLMConfig(provider="gemini")
    assert c.model == "gemini-2.5-pro"
    assert c.api_key == "gkey"


# --- missing key ----------------------------------------------------------


def test_missing_api_key_raises(monkeypatch):
    for var in (
        "OPENAI4S_LLM_API_KEY",
        "OPENAI4S_ARK_API_KEY",
        "ARK_API_KEY",
        "DOUBAO_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    c = LLMConfig(provider="ark", api_key="")
    with pytest.raises(llm.LLMError):
        llm.chat([{"role": "user", "content": "hi"}], c)


def test_loopback_openai_endpoint_can_run_without_fake_api_key(cap):
    c = LLMConfig(
        provider="chatgpt",
        api_key="",
        base_url="http://127.0.0.1:11434/v1",
        model="qwen3:8b",
    )

    llm.chat([{"role": "user", "content": "hi"}], c)

    assert cap.url == "http://127.0.0.1:11434/v1/chat/completions"
    assert "Authorization" not in cap.headers


def test_loopback_endpoint_does_not_inherit_unverified_vendor_tool_support(cap):
    c = LLMConfig(
        provider="chatgpt",
        api_key="",
        base_url="http://127.0.0.1:1234/v1",
        model="local-model",
    )
    tools = [
        {
            "name": "external_write",
            "description": "must not be sent without a capability override",
            "parameters": {"type": "object", "properties": {}},
        }
    ]

    llm.chat([{"role": "user", "content": "hi"}], c, tools=tools)

    assert "tools" not in cap.payload
    assert (
        llm.get_model_capabilities(
            "chatgpt", "local-model", base_url=c.base_url
        ).tool_calling
        is False
    )


# --- wire selection + auth headers ---------------------------------------


def test_openai_wire_url_and_auth(cap):
    c = _cfg("ark")
    llm.chat([{"role": "user", "content": "hi"}], c)
    assert cap.url == "https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions"
    assert cap.headers["Authorization"] == "Bearer test-key"
    assert cap.payload["model"] == "doubao-seed-2.0-pro"


def test_anthropic_wire_url_and_auth(cap):
    c = _cfg("claude")
    llm.chat(
        [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
        ],
        c,
    )
    assert cap.url == "https://api.anthropic.com/v1/messages"
    assert cap.headers["x-api-key"] == "test-key"
    assert cap.headers["anthropic-version"] == llm.ANTHROPIC_VERSION
    # system message is hoisted to a top-level field, not in messages
    assert cap.payload["system"] == "be brief"
    assert all(m["role"] != "system" for m in cap.payload["messages"])


def test_gemini_wire_url_and_auth(cap):
    c = _cfg("gemini")
    llm.chat(
        [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "prior"},
            {"role": "user", "content": "hi"},
        ],
        c,
    )
    assert cap.url.endswith("/v1beta/models/gemini-2.5-flash:generateContent")
    assert cap.headers["x-goog-api-key"] == "test-key"
    assert cap.payload["systemInstruction"]["parts"][0]["text"] == "sys"
    # assistant role maps to "model"
    roles = [c_["role"] for c_ in cap.payload["contents"]]
    assert roles == ["model", "user"]


def test_config_base_url_override(cap):
    c = _cfg("chatgpt", base_url="https://proxy.local/v1")
    llm.chat([{"role": "user", "content": "hi"}], c)
    assert cap.url == "https://proxy.local/v1/chat/completions"


# --- multimodal content translation --------------------------------------

_IMG_MSG = [
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "what is this?"},
            {"type": "image", "url": "https://ex.com/a.png"},
            {"type": "image", "data": "QUJD", "mime": "image/jpeg"},
        ],
    }
]


def test_openai_image_translation(cap):
    llm.chat(_IMG_MSG, _cfg("chatgpt"))
    parts = cap.payload["messages"][0]["content"]
    kinds = [p["type"] for p in parts]
    assert kinds == ["text", "image_url", "image_url"]
    assert parts[1]["image_url"]["url"] == "https://ex.com/a.png"
    assert parts[2]["image_url"]["url"].startswith("data:image/jpeg;base64,QUJD")


def test_anthropic_image_translation(cap):
    llm.chat(_IMG_MSG, _cfg("claude"))
    parts = cap.payload["messages"][0]["content"]
    assert parts[0]["type"] == "text"
    assert parts[1]["source"] == {"type": "url", "url": "https://ex.com/a.png"}
    assert parts[2]["source"]["type"] == "base64"
    assert parts[2]["source"]["media_type"] == "image/jpeg"
    assert parts[2]["source"]["data"] == "QUJD"


def test_gemini_image_translation(cap):
    llm.chat(_IMG_MSG, _cfg("gemini"))
    parts = cap.payload["contents"][0]["parts"]
    assert parts[0] == {"text": "what is this?"}
    assert parts[1]["file_data"]["file_uri"] == "https://ex.com/a.png"
    assert parts[2]["inline_data"]["mime_type"] == "image/jpeg"
    assert parts[2]["inline_data"]["data"] == "QUJD"


# --- vision guard ---------------------------------------------------------


@pytest.mark.parametrize("provider", ["ark", "chatgpt", "claude", "gemini"])
def test_vision_provider_accepts_images(cap, provider):
    # Should not raise; the fake transport returns a canned body.
    out = llm.chat(_IMG_MSG, _cfg(provider))
    assert out["content"] == "ok"


# --- normalized return ----------------------------------------------------


@pytest.mark.parametrize("provider", ["ark", "chatgpt", "claude", "gemini"])
def test_normalized_return_shape(cap, provider):
    out = llm.chat([{"role": "user", "content": "hi"}], _cfg(provider))
    assert set(out) >= {"content", "reasoning", "usage", "finish_reason", "raw"}
    assert out["content"] == "ok"
