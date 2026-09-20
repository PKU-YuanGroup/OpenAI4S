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
import socket
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
    assert profile["readiness"]["checked_endpoint"] is False
    # Prose, not the state code repeated. The card renders `detail` verbatim,
    # and it read "needs_key" at the user until this branch was given the same
    # treatment as the `ready` and `unsupported` ones either side of it.
    assert profile["readiness"]["detail"] == "no credential resolves for this profile"


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
    assert result["body"]["tool_execution"] == 0
    assert result["body"]["outbound"] <= 2
    assert "capability_receipt" in result["body"]


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
        # What the transport actually raises. The stub used to throw a bare
        # `RuntimeError("401 invalid_api_key")` and the assertion then
        # text-matched that string -- a shape the product does not produce,
        # certifying a contract nothing serves. `transport.py` always sets
        # `status` and `error_code` on an HTTP failure, and those are the
        # fields the detail is now chosen from.
        from openai4s.llm.models import TransportError

        raise TransportError(
            "LLM HTTP 401: {'error': 'Incorrect API key'}",
            provider="openai_responses",
            status=401,
            error_code="invalid_api_key",
        )

    monkeypatch.setattr("openai4s.llm.chat", _fail)
    result = call("POST", f"/model-profiles/{profile_id}/probe")
    assert result["code"] == 200
    assert result["body"]["reachable"] is False
    # The cause is named, in the daemon's words rather than the provider's.
    assert "rejected the credential" in result["body"]["detail"]
    assert result["body"]["code"] == "probe_failed"


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


# --------------------------------------------------------------------------
# failures the real transport produces
# --------------------------------------------------------------------------
#
# These go through `openai4s.llm.transport` unstubbed: loopback servers for
# the answers, `urlopen` itself for the no-answer cases. Stubbing `chat` is
# how the network case slipped. The transport wraps a refused connection in a
# status-less `TransportError`, which is not an `OSError`, so the probe
# answered "internal error" to a wrong base URL and then sent its second
# request anyway. A stub that raised `OSError` would have passed.


@pytest.fixture
def no_backoff(monkeypatch):
    """Retries still happen; only the sleeps between them are skipped.

    ``monotonic`` is the real clock: the transport reads it for the logical
    call's total deadline, so a namespace carrying only ``sleep`` turned every
    connect failure into an ``AttributeError`` the probe reported as "internal
    error" -- the exact misreport these tests exist to catch.
    """
    import time as real_time
    import types

    from openai4s.llm import transport

    slept: list[float] = []
    monkeypatch.setattr(
        transport,
        "time",
        types.SimpleNamespace(sleep=slept.append, monotonic=real_time.monotonic),
    )
    return slept


def _profile_at(call, base_url):
    created = call(
        "POST",
        "/model-profiles",
        {
            "name": "loopback",
            "provider": "chatgpt",
            "api_key": "sk-test",
            "model": "gpt-test",
            "base_url": base_url,
        },
    )
    assert created["code"] == 201, created
    return created["body"]["id"]


def _answering(status, body, content_type):
    import threading
    from http.server import BaseHTTPRequestHandler

    from tests._ports import bound_gateway_server

    hits: list[str] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server naming
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            hits.append(self.path)
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            return None

    server, port = bound_gateway_server()
    server.RequestHandlerClass = _Handler
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port, hits


@pytest.mark.parametrize(
    "reason",
    [
        ConnectionRefusedError(61, "Connection refused"),
        socket.gaierror(8, "nodename nor servname provided, or not known"),
        TimeoutError(60, "Operation timed out"),
    ],
    ids=["refused", "unknown-host", "connect-timeout"],
)
def test_an_unreachable_endpoint_is_named_and_not_probed_twice(
    api, no_backoff, monkeypatch, reason
):
    """What `urlopen` raises when no HTTP answer comes back at all.

    Raised from `urlopen` itself so the transport's own wrapping runs; a real
    socket cannot stand in portably, because macOS lets a connect to a bound,
    non-listening port time out instead of refusing it.
    """
    import urllib.error

    from openai4s.llm import transport

    attempts: list[str] = []

    def _unreachable(request, **_kwargs):
        attempts.append(request.full_url)
        raise urllib.error.URLError(reason)

    # `_urlopen` is the transport's documented injectable open seam; the real
    # path goes through the shared deadline watchdog rather than
    # `urllib.request.urlopen`, so patching that module function stopped
    # intercepting anything.
    monkeypatch.setattr(transport, "_urlopen", _unreachable)
    runner, call = api
    profile_id = _profile_at(call, "http://127.0.0.1:9/v1")
    result = call("POST", f"/model-profiles/{profile_id}/probe")
    body = result["body"]
    assert result["code"] == 200
    assert body["reachable"] is False
    assert body["code"] == "probe_failed"
    assert "could not be reached" in body["detail"]
    assert "internal error" not in body["detail"]
    # The tool-call request never got an answer, so the streaming one is not
    # sent: each of them can wait out a full connect timeout and its retries.
    assert body["outbound"] == 1
    assert all("/chat/completions" in url for url in attempts), attempts
    # The transport still retried that one request.
    assert len(attempts) == transport.DEFAULT_MAX_ATTEMPTS


def test_a_rejected_request_names_its_status(api, no_backoff):
    runner, call = api
    server, port, hits = _answering(
        400,
        json.dumps({"error": {"message": "/Users/someone/secret", "code": "bad"}}),
        "application/json",
    )
    try:
        profile_id = _profile_at(call, f"http://127.0.0.1:{port}/v1")
        body = call("POST", f"/model-profiles/{profile_id}/probe")["body"]
    finally:
        server.shutdown()
        server.server_close()
    assert hits, "the probe never reached the loopback endpoint"
    assert body["reachable"] is False
    assert "rejected the probe request (HTTP 400)" in body["detail"]
    # The provider's own text stays out of the answer.
    assert "/Users/someone" not in json.dumps(body)


def test_a_web_page_is_not_mistaken_for_a_model_api(api, no_backoff):
    runner, call = api
    server, port, hits = _answering(
        200, "<html><body>welcome</body></html>", "text/html"
    )
    try:
        profile_id = _profile_at(call, f"http://127.0.0.1:{port}")
        body = call("POST", f"/model-profiles/{profile_id}/probe")["body"]
    finally:
        server.shutdown()
        server.server_close()
    assert hits, "the probe never reached the loopback endpoint"
    assert body["reachable"] is False
    assert "not like a model API" in body["detail"]
    assert "internal error" not in body["detail"]
