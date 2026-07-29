"""Whether a user can find out that their model configuration is wrong.

`ModelProfileService` draws a careful line. `readiness` answers "is this
configured" from local state alone, so opening Customize costs nothing and
cannot spend anyone's API quota; `probe` contacts the endpoint once, because a
human pressed something. Both are implemented, tested, and served — `readiness`
rides on every profile in `GET /model-profiles`, and `POST
/model-profiles/{id}/probe` is routed.

Neither had a call site. The Models tab derived its own status from
`has_api_key` — a binary that carries no reason and cannot express
`unsupported`, the state of a profile naming a protocol this build does not
dispatch (an imported config, or one carried over from a build that offered a
provider this one dropped). Such a profile has a key, so the row showed it as
configured and it failed only when a turn used it.

Scope, stated honestly: `needs_model` is unreachable for the five protocols
Customize offers, because every one of them has a default model. It is checked
here anyway since the branch exists and a future protocol without a default
would land on it.

The larger half is the probe. Nothing could reach it, so the answer to "is my
endpoint actually reachable" was to send a message and find out.

The probe stays a button. A readiness card that probed on render is exactly the
implicit outbound call the service's own docstring refuses, and the route is
POST so a prefetch cannot spend the user's quota for them.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth
from openai4s.server.model_profiles import ModelProfileService

APP_JS = Path("openai4s/server/webui/app.js").read_text(encoding="utf-8")


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


@pytest.fixture
def api(tmp_path):
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
        handler._send = lambda code, payload, ctype, extra=None: sent.update(
            code=code, body=json.loads(payload.decode("utf-8"))
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

    return runner, call


# --------------------------------------------------------------------------
# the payload the row needs
# --------------------------------------------------------------------------


def test_readiness_rides_on_every_listed_profile(api):
    """It is computed and serialised on every row already — the gap was
    entirely that nothing displayed it."""
    runner, call = api
    created = call(
        "POST",
        "/model-profiles",
        {"name": "keyless", "provider": "openai_responses"},
    )
    assert created["code"] == 201, created
    listed = call("GET", "/model-profiles")["body"]
    profile = next(p for p in listed["profiles"] if p["name"] == "keyless")
    assert profile["readiness"]["state"] == "needs_key"
    assert profile["readiness"]["detail"]
    assert profile["readiness"]["checked_endpoint"] is False


def test_a_configured_profile_reads_ready_without_being_contacted(api):
    """ "Ready" is a claim about local configuration only. If it ever came to
    mean "the endpoint answered", opening Customize would be making calls."""
    runner, call = api
    call(
        "POST",
        "/model-profiles",
        {
            "name": "full",
            "provider": "openai_responses",
            "api_key": "sk-test",
            "model": "gpt-4o",
        },
    )
    listed = call("GET", "/model-profiles")["body"]
    profile = next(p for p in listed["profiles"] if p["name"] == "full")
    assert profile["readiness"]["state"] == "ready"
    assert profile["readiness"]["checked_endpoint"] is False
    assert "not been contacted" in profile["readiness"]["detail"]


def test_needs_model_is_unreachable_for_the_protocols_on_offer(api):
    """Recorded rather than assumed. Every protocol Customize offers has a
    default model, so this branch cannot fire from the UI today — and a new
    protocol without one would make it live, which is when this test starts
    mattering rather than the day it was written."""
    from openai4s.llm.registry import provider_spec
    from openai4s.server.model_profiles import PROFILE_PROTOCOLS

    for protocol in PROFILE_PROTOCOLS:
        assert provider_spec(protocol).get("model"), protocol


def test_an_unsupported_protocol_is_named_as_such(api):
    """`has_api_key` is true here, so the old row showed this as configured."""
    runner, _call = api
    service = ModelProfileService(runner.store, runner.cfg, providers=lambda: {})
    state = service.readiness({"provider": "not-a-protocol", "model": "m"})
    assert state["state"] == "unsupported"


def test_readiness_costs_no_network(api, monkeypatch):
    """The property the whole split exists to protect. If listing profiles ever
    contacts a provider, opening Customize spends the user's quota."""
    import openai4s.llm as llm_mod

    def _boom(*_a, **_k):
        raise AssertionError("listing profiles made a provider call")

    monkeypatch.setattr(llm_mod, "chat", _boom)
    call = api[1]
    call(
        "POST",
        "/model-profiles",
        {
            "name": "p",
            "provider": "openai_responses",
            "api_key": "sk-test",
            "model": "gpt-4o",
        },
    )
    assert call("GET", "/model-profiles")["code"] == 200


def test_the_probe_route_answers(api, monkeypatch):
    """The route existed and was reachable; nothing called it."""
    runner, call = api
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
    monkeypatch.setattr("openai4s.llm.chat", lambda *_a, **_k: None)
    result = call("POST", f"/model-profiles/{profile_id}/probe")
    assert result["code"] == 200
    assert result["body"]["reachable"] is True
    assert result["body"]["contacted"] is True


def test_an_unconfigured_profile_is_not_probed_at_all(api, monkeypatch):
    """Sending a request with no credential produces a 401 that reads like an
    endpoint fault rather than the missing key it is."""
    runner, call = api
    created = call(
        "POST", "/model-profiles", {"name": "keyless", "provider": "openai_responses"}
    )
    profile_id = created["body"]["id"]

    def _boom(*_a, **_k):
        raise AssertionError("an unconfigured profile was sent to the provider")

    monkeypatch.setattr("openai4s.llm.chat", _boom)
    body = call("POST", f"/model-profiles/{profile_id}/probe")["body"]
    assert body["reachable"] is False
    assert body["contacted"] is False


def test_a_provider_failure_comes_back_as_a_report_not_an_error(api, monkeypatch):
    """A 500 would tell the user their daemon is broken when their API key is."""
    runner, call = api
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

    def _fail(*_a, **_k):
        raise RuntimeError("401 invalid_api_key")

    monkeypatch.setattr("openai4s.llm.chat", _fail)
    result = call("POST", f"/model-profiles/{profile_id}/probe")
    assert result["code"] == 200
    assert result["body"]["reachable"] is False
    assert "invalid_api_key" in result["body"]["detail"]


# --------------------------------------------------------------------------
# the call sites
# --------------------------------------------------------------------------


def _models_tab() -> str:
    start = APP_JS.index("  profs.forEach(p => {")
    return APP_JS[start : APP_JS.index("\n  });", start)]


def test_the_row_reads_readiness_rather_than_re_deriving_it():
    """The defect. `has_api_key` cannot express three of the four states."""
    body = _models_tab()
    assert "p.readiness" in body
    assert 'rd.state !== "ready"' in body


def test_the_row_can_reach_the_probe():
    body = _models_tab()
    assert "/probe" in body
    assert 'method: "POST"' in body


def test_the_probe_is_behind_a_click():
    """The service refuses to probe on render for a reason: a page load must
    not spend the user's provider quota. A probe called during row
    construction, rather than from a handler, would reintroduce exactly that.
    """
    body = _models_tab()
    probe_at = body.index("/probe")
    handler_at = body.index("test.onclick")
    assert handler_at < probe_at, "the probe is not inside a click handler"


def test_the_probe_detail_is_treated_as_untrusted():
    """It is a provider's own error text, redacted but not authored here."""
    body = _models_tab()
    assert "publicText(r.detail" in body
    assert "publicText(rd.detail" in body


def test_reachable_is_not_reported_as_verified():
    """The server is careful that "the endpoint answered" is not "this will
    work". A UI string that promised more would undo that."""
    for key in ("cust.models.reachable", "cust.models.unreachable"):
        assert APP_JS.count(f'"{key}":') == 2
    start = APP_JS.index('"cust.models.reachable": "the endpoint')
    line = APP_JS[start : APP_JS.index("\n", start)]
    assert "verified" not in line.lower()


def test_every_new_string_is_in_both_languages():
    for key in (
        "cust.models.test",
        "cust.models.testing",
        "cust.models.reachable",
        "cust.models.unreachable",
    ):
        assert APP_JS.count(f'"{key}":') == 2, key
