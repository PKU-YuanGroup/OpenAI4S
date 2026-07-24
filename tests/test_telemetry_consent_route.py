"""The Customize toggle, at the HTTP boundary.

Consent is off by default and turned on only by a person acting on this
install, so the route is the whole product surface for step six: a GET that
reports state and a PUT that grants or revokes. The route does not send -- it
records -- and the properties that matter are the ones the consent module
already guarantees, checked here through the gateway to prove the wiring
carries them: a fresh install reads disabled, granting mints an id, revoking
destroys it, and re-granting is a new identity.
"""
from __future__ import annotations

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.telemetry import consent as consent_mod


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None

    def has_subscriber(self, root_frame_id):
        return False

    def drop_frame(self, root_frame_id):
        return None


@pytest.fixture
def gw(tmp_path):
    config = Config(
        data_dir=tmp_path, llm=LLMConfig(provider="deepseek", api_key="test-key")
    )
    runner = gateway_mod.SessionRunner(config, _Hub(), start_idle_sweeper=False)
    handler = object.__new__(gateway_mod.make_handler(config, _Hub(), runner))
    return runner, handler


def _call(handler, method, body=None):
    replies: list = []
    handler._query = lambda: {}
    handler._body = lambda: body or {}
    handler._json = lambda value, code=200: replies.append((code, value))
    handler._send = lambda *a, **k: replies.append(("send",))
    handler._api(method, "/telemetry/consent")
    return replies[-1]


def test_a_fresh_install_reports_disabled(gw):
    _runner, handler = gw
    code, body = _call(handler, "GET")
    assert code == 200
    assert body == {"enabled": False, "env_locked": False}


def test_the_put_grants_and_the_get_reflects_it(gw):
    _runner, handler = gw
    assert _call(handler, "PUT", {"enabled": True})[1]["enabled"] is True
    assert _call(handler, "GET")[1]["enabled"] is True


def test_granting_through_the_route_records_a_real_identity(gw):
    runner, handler = gw
    _call(handler, "PUT", {"enabled": True})

    active = consent_mod.read(runner.store)
    assert active is not None and len(active.install_id) == 32


def test_revoking_through_the_route_destroys_the_identity(gw):
    runner, handler = gw
    _call(handler, "PUT", {"enabled": True})
    _call(handler, "PUT", {"enabled": False})

    assert consent_mod.read(runner.store) is None
    assert _call(handler, "GET")[1]["enabled"] is False


def test_re_granting_through_the_route_is_a_new_identity(gw):
    runner, handler = gw
    _call(handler, "PUT", {"enabled": True})
    first = consent_mod.read(runner.store).install_id
    _call(handler, "PUT", {"enabled": False})
    _call(handler, "PUT", {"enabled": True})
    second = consent_mod.read(runner.store).install_id

    assert first != second


def test_an_environment_veto_is_reported_so_the_toggle_is_not_a_lie(gw, monkeypatch):
    """A toggle the user can flip while the environment forces it off would be
    a control that silently does nothing. The route says env_locked so the UI
    can disable it."""
    runner, handler = gw
    _call(handler, "PUT", {"enabled": True})
    monkeypatch.setenv(consent_mod.ENV_VAR, "0")

    code, body = _call(handler, "GET")
    assert body == {"enabled": False, "env_locked": True}


def test_the_route_is_in_the_contract_inventory():
    from openai4s.server.contract import http_routes

    assert "/telemetry/consent" in http_routes()


# --------------------------------------------------------------------------
# a privacy boundary refuses an ambiguous request
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["false", "true", "0", "1", 0, 1, {}, {"a": 1}, [], ["x"], None],
)
def test_a_non_boolean_enabled_is_rejected(gw, value):
    """The regression.

    `bool()` maps the *string* "false", any non-empty dict, and any non-empty
    list onto True — so a form serialiser that sends `"false"`, or a client
    that did not read the contract closely, granted telemetry consent while
    asking to revoke it. Resolving an ambiguous privacy request in the
    permissive direction is the one direction that must never be the default.
    """
    _runner, handler = gw
    code, body = _call(handler, "PUT", {"enabled": value})
    assert code == 400
    assert "boolean" in body["error"]


def test_a_rejected_request_changes_nothing(gw):
    _runner, handler = gw
    _call(handler, "PUT", {"enabled": True})
    _call(handler, "PUT", {"enabled": "false"})
    assert _call(handler, "GET")[1]["enabled"] is True, "a 400 is not a revoke"


def test_a_missing_enabled_is_rejected_rather_than_read_as_revoke(gw):
    _runner, handler = gw
    code, _body = _call(handler, "PUT", {})
    assert code == 400


def test_real_booleans_still_work(gw):
    _runner, handler = gw
    assert _call(handler, "PUT", {"enabled": True})[1]["enabled"] is True
    assert _call(handler, "PUT", {"enabled": False})[1]["enabled"] is False


def test_a_corrupt_record_does_not_make_the_get_fail(gw):
    """`int(...)` on a non-numeric schema_version escaped `read()` entirely,
    past its own "a malformed row is no consent" contract — so the GET
    answered 500 and `grant()` could not repair the row either, because it
    calls `read()` first and inherited the same exception."""
    runner, handler = gw
    runner.store.set_setting(
        consent_mod.CONSENT_KEY,
        '{"install_id": "' + "a" * 32 + '", "granted_at": 1, "schema_version": "v2"}',
    )
    code, body = _call(handler, "GET")
    assert code == 200
    assert body["enabled"] is False


def test_granting_repairs_a_corrupt_record(gw):
    """The other half: a user who clicks the toggle must be able to get out of
    the broken state without editing the database."""
    runner, handler = gw
    runner.store.set_setting(
        consent_mod.CONSENT_KEY,
        '{"install_id": "' + "b" * 32 + '", "granted_at": 1, "schema_version": []}',
    )
    assert _call(handler, "PUT", {"enabled": True})[1]["enabled"] is True
    recorded = consent_mod.read(runner.store)
    assert recorded is not None and recorded.install_id != "b" * 32
