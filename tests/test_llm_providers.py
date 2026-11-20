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

import copy
import json

import pytest

from openai4s import llm
from openai4s.agent.actions import CodeCell
from openai4s.agent.engine import AgentEngine
from openai4s.agent.models import ExecutionOutcome
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
    assert cap.payload["model"] == "doubao-seed-2.1-pro"


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


def test_a_delta_callback_does_not_refuse_a_blocking_wire(cap):
    """The Web turn always passes `on_delta`.

    It was forwarded as a hard `streaming` requirement, and Gemini is declared
    non-streaming, so every Web turn on a Gemini profile raised
    `CapabilityError` before any request left. The adapter takes the callback
    and returns the whole reply; that is the documented behaviour.
    """
    deltas: list[str] = []

    out = llm.chat(
        [{"role": "user", "content": "hi"}], _cfg("gemini"), on_delta=deltas.append
    )

    assert out["content"] == "ok"
    assert cap.url.endswith(":generateContent")
    assert deltas == []


_LOOKUP = {
    "name": "lookup",
    "description": "Look up a fact.",
    "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
}


def _run_cell_requests():
    """The third and fourth requests of an Engine run whose model promises
    Python with a lookup twice, then answers the run_cell-only call with a
    run_cell call: what each wire is handed, built by the real Engine. The
    fourth offers the catalogue with run_cell beside it, so the tools it
    declares are returned with it."""

    requests = []
    beside = []

    class Model:
        def complete(self, messages, on_delta, *, only_tools=None, extra_tools=None):
            index = len(requests)
            requests.append((copy.deepcopy(list(messages)), only_tools))
            beside.append(extra_tools)
            if only_tools is not None:
                name, arguments = "run_cell", {"language": "python", "code": "print(2)"}
            else:
                name, arguments = "lookup", {"q": str(index)}
            call = {
                "id": f"call_{index}",
                "wire_id": f"call_{index}",
                "name": name,
                "ordinal": 0,
                "raw_arguments": json.dumps(arguments),
                "arguments": arguments,
                "parse_error": None,
                "provider_meta": {},
            }
            return {"content": "现在用 Python 读取 Excel。", "tool_calls": [call]}

    class Executor:
        def execute(self, action, reply, state):
            if isinstance(action, CodeCell):
                # What every executor answers a cell with.
                return ExecutionOutcome(
                    ({"role": "user", "content": "[Observation]\n2"},),
                    observation="[Observation]\n2",
                )
            return ExecutionOutcome(
                tuple(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "wire_id": call.wire_id,
                        "name": call.name,
                        "content": "found",
                        "is_error": False,
                    }
                    for call in action.calls
                )
            )

    AgentEngine(Model(), Executor(), max_turns=4).run(
        [{"role": "user", "content": "read demo.xlsx"}]
    )
    (third, offered), (fourth, again) = requests[2], requests[3]
    assert [tool["name"] for tool in offered] == ["run_cell"] and again is None
    assert beside[2] is None and [tool["name"] for tool in beside[3]] == ["run_cell"]
    return third, offered, (fourth, [_LOOKUP, *beside[3]])


def _no_forced_choice(provider, payload):
    """``auto`` or no choice at all: a fenced cell in the text still counts."""
    if provider == "claude":
        assert payload.get("tool_choice", {"type": "auto"})["type"] == "auto"
    elif provider == "gemini":
        mode = payload.get("toolConfig", {}).get("functionCallingConfig", {})
        assert mode.get("mode", "AUTO") == "AUTO"
    else:
        assert payload.get("tool_choice", "auto") == "auto"


@pytest.mark.parametrize("provider", ["ark", "chatgpt", "claude", "gemini"])
def test_the_run_cell_call_and_its_answer_are_well_formed_on_every_wire(cap, provider):
    """The Agent's fallback call offers run_cell alone, with the default tool
    choice; the next call offers the catalogue with run_cell beside it and
    answers run_cell's call id with the cell's observation as a tool result."""
    third, offered, (fourth, declared) = _run_cell_requests()

    llm.chat(third, _cfg(provider), tools=offered)
    payload = cap.payload
    _no_forced_choice(provider, payload)
    if provider in ("ark", "chatgpt"):
        names = [tool["function"]["name"] for tool in payload["tools"]]
    elif provider == "claude":
        names = [tool["name"] for tool in payload["tools"]]
    else:
        names = [tool["name"] for tool in payload["tools"][0]["functionDeclarations"]]
    assert names == ["run_cell"]

    llm.chat(fourth, _cfg(provider), tools=declared)
    payload = cap.payload
    _no_forced_choice(provider, payload)
    if provider in ("ark", "chatgpt"):
        names = [tool["function"]["name"] for tool in payload["tools"]]
    elif provider == "claude":
        names = [tool["name"] for tool in payload["tools"]]
    else:
        names = [tool["name"] for tool in payload["tools"][0]["functionDeclarations"]]
    assert names == ["lookup", "run_cell"]
    if provider in ("ark", "chatgpt"):
        call, answer = payload["messages"][-2:]
        assert call["tool_calls"][0]["function"]["name"] == "run_cell"
        assert call["tool_calls"][0]["id"] == answer["tool_call_id"] == "call_2"
        assert (answer["role"], answer["content"]) == ("tool", "[Observation]\n2")
    elif provider == "claude":
        call, answer = payload["messages"][-2:]
        (use,) = [part for part in call["content"] if part["type"] == "tool_use"]
        (result,) = answer["content"]
        assert (use["name"], use["id"]) == ("run_cell", "call_2")
        assert (result["type"], result["tool_use_id"]) == ("tool_result", "call_2")
        assert result["content"] == "[Observation]\n2"
    else:
        call, answer = payload["contents"][-2:]
        (use,) = [
            part["functionCall"] for part in call["parts"] if "functionCall" in part
        ]
        (result,) = [part["functionResponse"] for part in answer["parts"]]
        assert (use["name"], use["id"]) == ("run_cell", "call_2")
        assert (result["name"], result["id"]) == ("run_cell", "call_2")
        assert result["response"] == {"output": "[Observation]\n2"}


def test_the_run_cell_call_and_its_answer_are_well_formed_on_responses(monkeypatch):
    sent = []

    def post_sse(url, payload, headers, timeout, on_event):
        sent.append(payload)
        output = [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "ok"}],
            }
        ]
        on_event(
            {
                "type": "response.completed",
                "response": {"status": "completed", "output": output, "usage": {}},
            }
        )

    monkeypatch.setattr(llm.transport, "post_sse", post_sse)
    third, offered, (fourth, declared) = _run_cell_requests()
    llm.chat(third, _cfg("openai_responses"), tools=offered)
    llm.chat(fourth, _cfg("openai_responses"), tools=declared)

    first, second = sent
    _no_forced_choice("openai_responses", first)
    assert [tool["name"] for tool in first["tools"]] == ["run_cell"]
    _no_forced_choice("openai_responses", second)
    assert [tool["name"] for tool in second["tools"]] == ["lookup", "run_cell"]
    call, answer = second["input"][-2:]
    assert (call["type"], call["name"], call["call_id"]) == (
        "function_call",
        "run_cell",
        "call_2",
    )
    assert (answer["type"], answer["call_id"]) == ("function_call_output", "call_2")
    assert answer["output"] == "[Observation]\n2"


def test_an_anthropic_none_choice_takes_no_other_field(cap):
    # The facade's "no tool calls" shape on the Anthropic wire, which rejects
    # ``disable_parallel_tool_use`` beside ``{"type": "none"}``.
    llm.chat(
        [{"role": "user", "content": "hi"}],
        _cfg("claude"),
        tools=[_LOOKUP],
        tool_choice="none",
        parallel_tool_calls=False,
    )
    assert cap.payload["tool_choice"] == {"type": "none"}


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


# --- per-model request parameters -----------------------------------------


def test_chatgpt_default_model_sends_max_completion_tokens_without_temperature(cap):
    # The default `gpt-5` 400s on `max_tokens` and on any non-default temperature.
    c = _cfg("chatgpt")
    llm.chat([{"role": "user", "content": "hi"}], c)
    assert cap.payload["model"] == "gpt-5"
    assert cap.payload["max_completion_tokens"] == c.max_tokens
    assert "max_tokens" not in cap.payload
    assert "temperature" not in cap.payload


@pytest.mark.parametrize(
    "model",
    [
        "gpt-5-mini",
        "gpt-5-2025-08-07",
        "gpt-5.1-codex",
        "gpt-5.2-pro",
        "o1",
        "o3-mini",
        "o4-mini-2025-04-16",
    ],
)
def test_openai_reasoning_models_drop_max_tokens_and_temperature(cap, model):
    llm.chat(
        [{"role": "user", "content": "hi"}],
        _cfg("chatgpt", model=model),
        max_tokens=1234,
        temperature=0.2,
    )
    assert cap.payload["max_completion_tokens"] == 1234
    assert "max_tokens" not in cap.payload
    assert "temperature" not in cap.payload


@pytest.mark.parametrize(
    "model,default_effort",
    [
        ("gpt-5.1", "none"),
        ("gpt-5.1-2025-11-13", "none"),
        ("gpt-5.2", "none"),
        ("gpt-5.2-2025-12-11", "none"),
        ("gpt-5.4", "none"),
        ("gpt-5.4-2026-03-05", "none"),
        ("gpt-5.5", "medium"),
        ("gpt-5.6-sol", "medium"),
    ],
)
@pytest.mark.parametrize("effort", [None, "none", "low", "high"])
@pytest.mark.parametrize("temperature", [None, 0.0])
@pytest.mark.parametrize("stream", [False, True])
def test_openai_optional_reasoning_preserves_supported_sampling(
    cap, monkeypatch, model, default_effort, effort, temperature, stream
):
    if effort is not None:
        monkeypatch.setenv("OPENAI4S_LLM_REASONING_EFFORT", effort)

    def post_sse(url, payload, headers, timeout, on_event):
        cap(url, payload, headers, timeout)
        on_event({"choices": [{"delta": {"content": "ok"}}]})
        on_event({"choices": [{"delta": {}, "finish_reason": "stop"}]})

    monkeypatch.setattr(llm.transport, "post_sse", post_sse)
    monkeypatch.setenv("OPENAI4S_LLM_STREAM", "1")
    cfg = _cfg("chatgpt", model=model, temperature=0.3)
    result = llm.chat(
        [{"role": "user", "content": "hi"}],
        cfg,
        max_tokens=1234,
        temperature=temperature,
        on_delta=(lambda _: None) if stream else None,
    )

    assert result["content"] == "ok"
    assert cap.payload["max_completion_tokens"] == 1234
    assert "max_tokens" not in cap.payload
    assert cap.payload.get("reasoning_effort") == effort
    if (effort or default_effort) == "none":
        assert cap.payload["temperature"] == (
            cfg.temperature if temperature is None else temperature
        )
    else:
        assert "temperature" not in cap.payload


@pytest.mark.parametrize("model", ["gpt-5", "o3", "gpt-5.1-codex", "gpt-5.2-pro"])
def test_unsupported_none_effort_does_not_enable_sampling(cap, monkeypatch, model):
    monkeypatch.setenv("OPENAI4S_LLM_REASONING_EFFORT", "none")
    llm.chat([{"role": "user", "content": "hi"}], _cfg("chatgpt", model=model))
    assert "temperature" not in cap.payload
    assert cap.payload["reasoning_effort"] == "none"


@pytest.mark.parametrize(
    "provider,model",
    [
        ("chatgpt", "gpt-5-chat-latest"),
        ("chatgpt", "gpt-4o"),
        ("chatgpt", "gpt-4.1-mini"),
        ("ark", "doubao-seed-2.1-pro"),
        ("ark", "kimi-k3"),
        ("ark", "kimi-k2.8-preview"),
        ("ollama", "gpt-oss:20b"),
    ],
)
def test_openai_wire_chat_models_keep_max_tokens_and_temperature(cap, provider, model):
    llm.chat(
        [{"role": "user", "content": "hi"}],
        _cfg(provider, model=model),
        max_tokens=1234,
        temperature=0.2,
    )
    assert cap.payload["max_tokens"] == 1234
    assert cap.payload["temperature"] == 0.2
    assert "max_completion_tokens" not in cap.payload


@pytest.mark.parametrize(
    "model", ["kimi-k3.1-pro", "kimi-k3-1-pro-261024", "KIMI-K3.1-PRO"]
)
def test_kimi_k3_1_gets_no_temperature(cap, model):
    """Ark serves Kimi K3.1 with fixed sampling: any explicit temperature but 1
    is HTTP 400, so every turn of a kimi-k3.1-pro profile failed."""
    llm.chat(
        [{"role": "user", "content": "hi"}],
        _cfg("ark", model=model),
        max_tokens=1234,
        temperature=0.2,
    )
    assert "temperature" not in cap.payload
    assert cap.payload["max_tokens"] == 1234


@pytest.mark.parametrize(
    "model",
    [
        "claude-opus-4-7",
        "claude-opus-4-8",
        "claude-opus-5",
        "claude-opus-5-5",
        "claude-sonnet-5",
        "claude-sonnet-5-5",
        "claude-haiku-5-5",
        "claude-fable-5-1",
        "anthropic.claude-opus-5-5",
    ],
)
def test_anthropic_models_without_sampling_get_no_temperature(cap, model):
    # These models return 400 for any `temperature`, even an explicit one.
    llm.chat(
        [{"role": "user", "content": "hi"}],
        _cfg("claude", model=model),
        temperature=0.2,
    )
    assert "temperature" not in cap.payload
    assert cap.payload["max_tokens"]


@pytest.mark.parametrize(
    "model",
    [
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
        "claude-sonnet-4-20250514",
        "claude-opus-4-1-20250805",
        "claude-opus-4-5@20251101",
        "claude-3-5-sonnet-20241022",
    ],
)
def test_anthropic_models_with_sampling_keep_temperature(cap, model):
    llm.chat(
        [{"role": "user", "content": "hi"}],
        _cfg("claude", model=model),
        temperature=0.2,
    )
    assert cap.payload["temperature"] == 0.2


def test_anthropic_default_model_keeps_configured_temperature(cap):
    c = _cfg("claude")
    llm.chat([{"role": "user", "content": "hi"}], c)
    assert cap.payload["model"] == "claude-sonnet-4-5"
    assert cap.payload["temperature"] == c.temperature
