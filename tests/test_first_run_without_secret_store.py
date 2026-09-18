"""First-run setup on a host with no secure credential store.

`auto` fails closed where there is no keychain, no libsecret and no DPAPI,
which is every headless Linux server and every container that was not given
an environment backend. That refusal is correct. What was wrong is how far it
reached: the wizard's status read, its Skip button and every profile
activation read the broker unguarded, so each of them answered
`internal error`, and a user on such a host could neither finish setup nor
dismiss it. Saving a key there still has to be refused, but by name.

The broker is made to find no backend at all, which is what `auto` sees on
those hosts; everything above it is the real gateway. Marked
`stubbed_backend`, so none of these responses is captured as a route
contract.
"""

from __future__ import annotations

import io
import json

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.security import secret_broker
from openai4s.server import errors
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth

pytestmark = pytest.mark.stubbed_backend


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_SECRET_STORE", "auto")
    monkeypatch.delenv("OPENAI4S_SECRET_ENV", raising=False)
    monkeypatch.setattr(secret_broker, "_system_backends", lambda: [])
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key=""),
        max_turns=1,
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_class = gateway_mod.make_handler(cfg, _Hub(), runner)
    token = local_auth.read_token(tmp_path) or ""

    def call(method, path, body=None):
        handler = object.__new__(handler_class)
        handler._correlation_id = "req-no-store"
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

    # The premise, checked rather than assumed: the broker really refuses.
    with pytest.raises(secret_broker.SecretStoreUnavailable):
        runner.store.secrets
    return call


def test_the_wizard_loads_and_skips(api):
    status = api("GET", "/onboarding")
    assert status["code"] == 200, status
    # The conftest's environment key still counts; only the broker is gone.
    assert isinstance(status["body"]["has_api_key"], bool)

    skipped = api("POST", "/onboarding/complete", {"skip": True})
    assert skipped["code"] == 200, skipped
    assert skipped["body"]["complete"] is True


def test_a_keyless_profile_activates(api):
    """Activation reads the DataPro credential before and after the switch.

    The second activation starts from an active `ark` profile on Volcengine's
    endpoint, which is the branch that reads `llm_api_key` rather than the
    dedicated Agent Plan Key.
    """
    for name, provider in (("ark", "ark"), ("openai", "chatgpt")):
        created = api("POST", "/model-profiles", {"name": name, "provider": provider})
        assert created["code"] == 201, created
        activated = api("POST", f"/model-profiles/{created['body']['id']}/activate")
        assert activated["code"] == 200, activated
        assert activated["body"]["active_id"] == created["body"]["id"]


def test_saving_a_key_is_refused_by_name(api):
    result = api(
        "POST",
        "/model-profiles",
        {"name": "keyed", "provider": "chatgpt", "api_key": "sk-test-not-stored"},
    )
    assert result["code"] == 503, result
    assert result["body"]["code"] == "secret_store_unavailable"
    assert result["body"]["error"] == errors.SECRET_STORE_UNAVAILABLE_MESSAGE
    assert result["body"]["request_id"], "the refusal carries no request id"
    assert "sk-test-not-stored" not in json.dumps(result)


def test_the_refusal_never_quotes_the_backend():
    """The broker's own text can carry a backend's self-test output."""
    leaked = "keychain present but unusable: /Users/someone/Library/Keychains"
    body, status = errors.public_exception(
        secret_broker.SecretStoreUnavailable(leaked), surface="test"
    )
    assert status == 503
    assert body["code"] == "secret_store_unavailable"
    assert "/Users/someone" not in json.dumps(body)
    # Every other unknown exception keeps the generic answer.
    body, status = errors.public_exception(RuntimeError(leaked), surface="test")
    assert (status, body["error"]) == (500, errors.INTERNAL_ERROR_MESSAGE)
