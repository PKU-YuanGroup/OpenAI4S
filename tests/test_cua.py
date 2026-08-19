"""Offline contracts for the managed CUA cloud-desktop integration module.

Everything here exercises ``openai4s.cua`` directly against fakes: credential
validation and brokering, the Streamable HTTP runtime config with its
just-in-time Bearer provider, the fixed six-tool discovery surface, in-band
auth-failure detection, reflected-credential redaction, and the fixed-shape
ping/observe projections.  No network, no real key.
"""

from __future__ import annotations

import json

import pytest

from openai4s import cua

FAKE_KEY = "cua-test-key-123456"


class FakeStore:
    def __init__(self, secrets: dict[str, str] | None = None) -> None:
        self.secret_settings = dict(secrets or {})
        self.scopes: dict[str, str] = {}

    def get_secret_setting(self, key):
        return self.secret_settings.get(key, "")

    def set_secret_setting(self, key, value, *, scope):
        self.secret_settings[key] = value
        self.scopes[key] = scope
        return f"secret://test/{scope}/{key}"

    def connector_env(self, connector):
        env = connector.get("env")
        return dict(env) if isinstance(env, dict) else {}


def _managed_connector(**extra) -> dict:
    return {
        "connector_id": cua.CONNECTOR_ID,
        "name": "CUA Cloud Desktop",
        "command": cua.managed_connector_command(),
        "enabled": True,
        **extra,
    }


def _result(
    *,
    is_error: bool = False,
    text: str = "",
    structured: dict | None = None,
    content: list | None = None,
) -> dict:
    """The manager's normalized ``{"is_error","text","raw"}`` result shape."""

    raw: dict = {}
    if content is not None:
        raw["content"] = content
    elif text:
        raw["content"] = [{"type": "text", "text": text}]
    if structured is not None:
        raw["structuredContent"] = structured
    return {"is_error": is_error, "text": text, "raw": raw}


# --- credential validation and brokering -----------------------------------


def test_save_cua_api_key_validates_normalizes_and_brokers_with_cua_scope():
    store = FakeStore()

    with pytest.raises(ValueError, match="must be a string"):
        cua.save_cua_api_key(store, 12345678)
    with pytest.raises(ValueError, match="required"):
        cua.save_cua_api_key(store, "   ")
    with pytest.raises(ValueError, match="at least 8"):
        cua.save_cua_api_key(store, "short-1")
    with pytest.raises(ValueError, match="too long"):
        cua.save_cua_api_key(store, "k" * 8_193)
    for bad in ("bad\rkey-123", "bad\nkey-123", "bad\x00key-123"):
        with pytest.raises(ValueError, match="invalid character"):
            cua.save_cua_api_key(store, bad)
    assert store.secret_settings == {}, "no rejected value may reach the broker"

    cua.save_cua_api_key(store, f"  {FAKE_KEY}  ")
    assert store.secret_settings[cua.CUA_API_KEY_SETTING] == FAKE_KEY
    assert store.scopes[cua.CUA_API_KEY_SETTING] == "cua"
    assert cua.resolve_cua_api_key(store) == FAKE_KEY


def test_resolver_is_the_dedicated_secret_and_never_the_llm_or_plan_key():
    """The CUA server refuses Ark plan keys in-band, so no key sharing."""

    store = FakeStore(
        secrets={
            "llm_api_key": "ark-key-must-not-be-sent",
            "agent_plan_key": "plan-key-must-not-be-sent",
        }
    )
    assert cua.resolve_cua_api_key(store) == ""
    assert cua.credential_state(store) == {"key_configured": False}

    cua.save_cua_api_key(store, FAKE_KEY)
    assert cua.resolve_cua_api_key(store) == FAKE_KEY
    assert cua.credential_state(store) == {"key_configured": True}


def test_credential_state_rejects_a_stored_but_invalid_key():
    # Written around `save_cua_api_key` (an import, an old release): the
    # projection must report what the outbound resolver would actually send.
    store = FakeStore(secrets={cua.CUA_API_KEY_SETTING: "r"})
    assert cua.credential_state(store) == {"key_configured": False}


# --- runtime config ---------------------------------------------------------


def test_runtime_config_is_streamable_http_with_lazy_bearer_provider():
    store = FakeStore()
    config = cua.connector_runtime_config(store, _managed_connector())

    assert config["transport"] == "streamable_http"
    assert config["url"] == cua.ENDPOINT
    assert config["cache_scope"] == f"store:{id(store)}"
    assert config["timeout"] == cua.REQUEST_TIMEOUT_SECONDS
    assert callable(config["headers_provider"])

    # No credential yet: the provider fails at send time, not config time.
    with pytest.raises(cua.CUACredentialError, match="not configured"):
        config["headers_provider"]()

    cua.save_cua_api_key(store, FAKE_KEY)
    assert config["headers_provider"]() == {"Authorization": f"Bearer {FAKE_KEY}"}

    # Just-in-time resolution: rotation is visible without a new config.
    rotated = "cua-rotated-key-654321"
    cua.save_cua_api_key(store, rotated)
    assert config["headers_provider"]() == {"Authorization": f"Bearer {rotated}"}

    # The config object itself never carries the credential.
    static = {k: v for k, v in config.items() if k != "headers_provider"}
    assert FAKE_KEY not in json.dumps(static)
    assert rotated not in json.dumps(static)


def test_headers_provider_refuses_a_stored_but_invalid_key():
    store = FakeStore(secrets={cua.CUA_API_KEY_SETTING: "bad\x00key-123"})
    config = cua.connector_runtime_config(store, _managed_connector())
    with pytest.raises(cua.CUACredentialError, match="invalid"):
        config["headers_provider"]()


def test_runtime_config_keeps_stdio_shape_for_custom_rows():
    """A user-controlled row cannot become the authenticated HTTP transport."""

    store = FakeStore(secrets={cua.CUA_API_KEY_SETTING: FAKE_KEY})
    connector = {
        "connector_id": "my-cua",
        "command": ["python", "server.py"],
        "args": ["--stdio"],
        "env": {"TOKEN": "test"},
        "cwd": "/tmp/work",
    }
    config = cua.connector_runtime_config(store, connector)
    assert config == {
        "command": ["python", "server.py"],
        "args": ["--stdio"],
        "env": {"TOKEN": "test"},
        "cwd": "/tmp/work",
    }
    assert FAKE_KEY not in json.dumps(config)


# --- fixed discovery surface -------------------------------------------------


def test_tool_descriptors_are_the_fixed_six_and_mutation_safe():
    descriptors = cua.tool_descriptors()
    assert [d["name"] for d in descriptors] == list(cua.TOOL_NAMES)
    assert len(cua.TOOL_NAMES) == 6
    for descriptor in descriptors:
        assert set(descriptor) == {"name", "description", "inputSchema"}
        assert descriptor["description"].strip()
        assert descriptor["inputSchema"]["type"] == "object"
        assert descriptor["inputSchema"]["additionalProperties"] is False

    # Callers get copies: corrupting one reply cannot poison later discovery.
    descriptors[0]["inputSchema"]["properties"]["injected"] = {"type": "string"}
    descriptors[0]["name"] = "not_cua_ping"
    fresh = cua.tool_descriptors()
    assert fresh[0]["name"] == "cua_ping"
    assert "injected" not in fresh[0]["inputSchema"]["properties"]


def test_observed_input_schemas_pin_the_probe_facts():
    by_name = {d["name"]: d["inputSchema"] for d in cua.tool_descriptors()}
    assert by_name["cua_ping"]["properties"] == {}
    assert by_name["cua_delegate"]["required"] == ["objective", "wait_ms"]
    assert by_name["cua_watch"]["required"] == ["invocation_id", "wait_ms"]
    assert by_name["cua_answer"]["required"] == ["invocation_id", "answer", "wait_ms"]
    assert by_name["cua_cancel"]["required"] == ["invocation_id"]
    assert by_name["cua_observe"]["required"] == [
        "invocation_id",
        "include_screenshot",
    ]
    # observe accepts an explicit null invocation for the default environment.
    assert by_name["cua_observe"]["properties"]["invocation_id"]["type"] == [
        "string",
        "null",
    ]


# --- in-band auth failure -----------------------------------------------------


def test_is_auth_error_matches_the_observed_401_reply():
    observed = (
        '{"error":"AuthError","message":"invalid api key",'
        '"status":401,"code":"Unauthorized"}'
    )
    assert cua.is_auth_error(_result(is_error=True, text=observed)) is True

    # Fallback: the text field lost, but the raw content block survives.
    assert (
        cua.is_auth_error(
            {
                "is_error": True,
                "text": "",
                "raw": {"content": [{"type": "text", "text": observed}]},
            }
        )
        is True
    )

    # Status alone is sufficient; a differently spelled error name is not
    # allowed to hide a 401.
    assert (
        cua.is_auth_error(
            _result(is_error=True, text='{"error":"Denied","status":401}')
        )
        is True
    )


@pytest.mark.parametrize(
    "result",
    [
        _result(is_error=False, text='{"error":"AuthError","status":401}'),
        _result(is_error=True, text="plain transport failure"),
        _result(is_error=True, text='{"error":"RateLimited","status":429}'),
        _result(is_error=True, text='{"status":"401"}'),
        _result(is_error=True, text='{"status":true}'),
        _result(is_error=True, text="[1, 2, 3]"),
        _result(is_error=True, text=""),
        "not a mapping",
        None,
    ],
)
def test_is_auth_error_rejects_everything_else(result):
    assert cua.is_auth_error(result) is False


# --- redaction ----------------------------------------------------------------


def test_redact_mcp_result_scrubs_echoes_and_rebuilds_the_trusted_skeleton():
    canary = "cua-canary-key-987654"
    result = _result(
        is_error=False,
        text=f"echo {canary}",
        structured={
            "ok": True,
            "value": canary,
            canary: "reflected-as-key",
        },
    )
    safe = cua.redact_mcp_result(result, canary)

    assert canary not in json.dumps(safe)
    assert safe["is_error"] is False
    assert safe["text"] == "echo [REDACTED]"
    assert safe["raw"]["content"] == [{"type": "text", "text": "echo [REDACTED]"}]
    structured = safe["raw"]["structuredContent"]
    assert structured["ok"] is True
    assert structured["value"] == "[REDACTED]"
    assert structured["[REDACTED]"] == "reflected-as-key"

    # An empty secret is a no-op, never an exception.
    assert cua.redact_mcp_result(result, "") == cua.redact_secret(result, "")


# --- fixed-shape projections ---------------------------------------------------


def test_ping_projection_reports_only_the_boolean_and_drops_upstream_fields():
    result = _result(
        structured={
            "ok": True,
            "server": {"name": "cua-skill", "version": "0.1.0"},
            "auth": {"authenticated": True, "org_id": "org-1"},
            "agent_hint": "untrusted upstream prose",
        }
    )
    assert cua.ping_projection(result) == {"ok": True}

    assert cua.ping_projection(_result(structured={"ok": "true"})) == {"ok": False}
    assert cua.ping_projection(_result(structured={})) == {"ok": False}
    assert cua.ping_projection({"is_error": True, "text": "", "raw": {}}) == {
        "ok": False
    }

    # Text-block fallback when the server omits structuredContent.
    assert cua.ping_projection(_result(text='{"ok": true}')) == {"ok": True}


def test_observe_projection_extracts_only_the_access_url():
    result = _result(
        structured={
            "environment": {"id": "env-1", "name": "desktop", "status": "running"},
            "access_url": "https://desktop.example/session-token",
            "screenshot": None,
            "agent_hint": "untrusted upstream prose",
        }
    )
    assert cua.observe_projection(result) == {
        "access_url": "https://desktop.example/session-token",
        "temporary": True,
    }

    assert cua.observe_projection(_result(structured={})) is None
    assert cua.observe_projection(_result(structured={"access_url": ""})) is None
    assert cua.observe_projection(_result(structured={"access_url": 42})) is None
    assert cua.observe_projection(
        _result(text='{"access_url": "https://desktop.example/from-text"}')
    ) == {"access_url": "https://desktop.example/from-text", "temporary": True}
