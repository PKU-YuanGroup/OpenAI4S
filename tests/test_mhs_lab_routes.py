"""The Lab panel's routes through the real gateway, with OpenMHS turned off.

`OPENAI4S_OPENMHS=off` makes the lab unavailable at request time whether or
not a checkout is importable, so these responses are the same on a developer
machine with the `third_party/OpenMHS` submodule and in CI without it. No
service is replaced: the shapes recorded here are the real ones.
"""

from __future__ import annotations

import io
import json

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth


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
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_OPENMHS", "off")
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
        handler._correlation_id = "req-lab"
        sent: dict = {}
        handler._send = (
            lambda code, payload, ctype, extra=None, security=None: sent.update(
                code=code, body=json.loads(payload.decode("utf-8")) if payload else None
            )
        )
        handler._send_static_bytes = (
            lambda code, payload, ctype, extra, security: sent.update(
                code=code, body=json.loads(payload.decode("utf-8"))
            )
        )
        handler.command = method
        handler.path = f"/api/v1{path}"
        raw = json.dumps(body).encode("utf-8") if body is not None else b""
        handler.headers = {
            "Content-Length": str(len(raw)),
            "Content-Type": "application/json",
            "X-Request-Id": "req-lab",
            local_auth.TOKEN_HEADER: token,
        }
        handler.rfile = io.BytesIO(raw)
        handler._route(method)
        return sent

    yield call
    runner.close()


def test_status_reports_how_to_set_up_openmhs(api):
    sent = api("GET", "/mhs-lab/status")
    assert sent["code"] == 200
    body = sent["body"]
    assert body["available"] is False
    assert body["setup"] == "POST /api/v1/mhs-lab/setup"
    assert body["openmhs"]["checkout"] is None
    assert body["openmhs"]["pinned_ref"]


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/mhs-lab/activity", None),
        ("GET", "/mhs-lab/results", None),
        ("GET", "/mhs-lab/verify", None),
        ("GET", "/mhs-lab/card", None),
        ("GET", "/mhs-lab/market?q=pump", None),
        ("POST", "/mhs-lab/devices", {"package": "labs/tiagocoutinho__julabo"}),
        ("DELETE", "/mhs-lab/devices/julabo", None),
        ("POST", "/mhs-lab/start", {}),
        ("POST", "/mhs-lab/stop", {}),
        ("POST", "/mhs-lab/estop", {"endpoint": "all"}),
        ("POST", "/mhs-lab/reset", {"endpoint": "assay"}),
    ],
)
def test_every_other_route_says_the_lab_is_unavailable(api, method, path, body):
    sent = api(method, path, body)
    assert sent["code"] == 404
    assert sent["body"]["code"] == "mhs_lab_unavailable"


def test_setup_needs_discovery_on_and_an_explicit_confirmation(api, monkeypatch):
    sent = api("POST", "/mhs-lab/setup", {"confirm": True})
    assert sent["code"] == 409
    assert sent["body"]["code"] == "mhs_lab_disabled"
    monkeypatch.setenv("OPENAI4S_OPENMHS", "/nonexistent/openmhs")
    sent = api("POST", "/mhs-lab/setup", {})
    assert sent["code"] == 400
    assert sent["body"]["code"] == "confirm_required"
