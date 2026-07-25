"""Every error response carries a stable code and the request's correlation id.

Errors were `{"error": "<prose>"}` plus a status. A client that must branch on
behaviour had only two options: match on English, which couples it to wording
nobody thinks of as an interface and breaks the first time a message is
improved; or match on status, which is too coarse — four genuinely different
failures share 400 here, and a client that retried "invalid cursor" the way it
retries "rate limited" would be looping on a request that can never succeed.

The enrichment is deliberately **additive**. `error` keeps the human message it
always had, so existing consumers — including this repo's own `app.js`, which
reads `j.error` — are untouched.

Success bodies are *not* wrapped in a `{data: …}` envelope. That was considered
and declined: it would churn every route and every consumer to relocate
information that is already unambiguous, and a half-finished reshape shows up as
a silently broken screen rather than a test failure. What a contract needs from
the success side is a documented, stable shape per route, which the inventory
test now enforces.
"""
import json

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server.gateway import GatewayError, _error_code_for


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


class _Recorder:
    """Drives the REAL `Handler._json` and reads back what it put on the wire.

    This used to be a hand-written copy of the enrichment, which meant every
    assertion below tested the copy. Deleting the enrichment from gateway.py
    left the whole module green -- the exact second-copy hazard
    `errors.py::gateway_error_payload` documents, and the reason the enrichment
    could diverge from the captured contract without any test objecting.

    So: build the real handler, stub only `_send` (the byte sink), and assert
    on the JSON the dispatcher actually serialised.
    """

    def __init__(self, tmp_path, correlation_id="req-1"):
        cfg = Config(
            data_dir=tmp_path,
            llm=LLMConfig(provider="deepseek", api_key="test-key"),
            max_turns=3,
        )
        runner = gateway_mod.SessionRunner(cfg, _Hub())
        handler_class = gateway_mod.make_handler(cfg, _Hub(), runner)
        self._handler = object.__new__(handler_class)
        self._handler._correlation_id = correlation_id
        self._handler._send = self._capture
        self.sent = None

    def _capture(self, code, body, ctype, extra=None):
        self.sent = (json.loads(body.decode("utf-8")), code)

    def json(self, obj, code=200):
        self._handler._json(obj, code)
        return self.sent[0]


@pytest.fixture
def recorder(tmp_path):
    return _Recorder(tmp_path)


# --------------------------------------------------------------------------
# the code taxonomy
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,expected",
    [
        (400, "bad_request"),
        (401, "unauthorized"),
        (403, "forbidden"),
        (404, "not_found"),
        (409, "conflict"),
        (413, "payload_too_large"),
        (423, "locked"),
        (429, "rate_limited"),
        (500, "internal_error"),
        (503, "unavailable"),
    ],
)
def test_each_status_maps_to_a_stable_code(status, expected):
    assert _error_code_for(status) == expected


def test_an_unmapped_server_status_is_still_an_internal_error():
    """A code must exist for every error, or a client's exhaustive match has a
    hole exactly where it is least able to cope."""
    assert _error_code_for(507) == "internal_error"
    assert _error_code_for(418) == "error"


# --------------------------------------------------------------------------
# enrichment
# --------------------------------------------------------------------------


def test_an_error_gains_a_code_status_and_request_id(recorder):
    out = recorder.json({"error": "nope"}, 404)
    assert out["code"] == "not_found"
    assert out["status"] == 404
    assert out["request_id"] == "req-1"


def test_the_human_message_is_preserved(recorder):
    """Additive: an existing client reading `j.error` must not notice."""
    out = recorder.json({"error": "connector not found"}, 404)
    assert out["error"] == "connector not found"


def test_an_explicit_code_wins_over_the_status_default(recorder):
    """Several distinct failures share 400; the point of a code is telling them
    apart."""
    out = recorder.json({"error": "bad cursor", "code": "invalid_cursor"}, 400)
    assert out["code"] == "invalid_cursor"


def test_extra_diagnostic_fields_survive(recorder):
    out = recorder.json({"error": "not found", "path": "/x"}, 404)
    assert out["path"] == "/x"


def test_success_responses_are_untouched(recorder):
    """Wrapping success bodies would churn every route and consumer to relocate
    information that is already unambiguous."""
    payload = {"projects": [1, 2]}
    out = recorder.json(payload, 200)
    assert out == payload
    assert "code" not in out and "request_id" not in out


def test_a_2xx_body_that_happens_to_contain_error_is_not_rewritten(recorder):
    """A successful response describing a prior failure — a job result, say —
    is data, not an error envelope."""
    body = {"error": "the remote job failed", "status": "failed"}
    out = recorder.json(dict(body), 200)
    assert out == body


def test_a_non_dict_error_body_is_left_alone(recorder):
    assert recorder.json(["a"], 400) == ["a"]


def test_a_missing_correlation_id_is_null_not_empty(tmp_path):
    """`null` says "not recorded"; "" reads as a real id that happens to be
    blank, and a log search for it silently matches nothing."""
    out = _Recorder(tmp_path, correlation_id="").json({"error": "x"}, 500)
    assert out["request_id"] is None


# --------------------------------------------------------------------------
# GatewayError
# --------------------------------------------------------------------------


def test_gateway_error_carries_an_optional_code():
    err = GatewayError(400, "bad cursor", "invalid_cursor")
    assert (err.code, err.message, err.error_code) == (
        400,
        "bad cursor",
        "invalid_cursor",
    )


def test_gateway_error_without_a_code_falls_back_to_the_status_default():
    err = GatewayError(404, "gone")
    assert err.error_code is None
    assert _error_code_for(err.code) == "not_found"


def test_the_four_distinct_400s_have_distinct_codes():
    """The concrete reason status alone is insufficient: a client retrying
    "invalid cursor" the way it retries a transient failure would loop on a
    request that can never succeed."""
    from pathlib import Path

    source = Path("openai4s/server/gateway.py").read_text()
    for code in (
        "malformed_json",
        "invalid_body_type",
        "invalid_cursor",
        "invalid_limit",
    ):
        assert f'"{code}"' in source, code


def test_the_envelope_is_json_serialisable(recorder):
    out = recorder.json({"error": "x"}, 500)
    assert json.loads(json.dumps(out))["code"] == "internal_error"


def test_the_dispatcher_itself_enriches_not_a_copy_of_it(recorder, monkeypatch):
    """The regression that made every other test in this module vacuous.

    The enrichment had two definitions: the dispatcher's, and a hand-written
    mirror in this file. The assertions all ran against the mirror, so removing
    the dispatcher's copy left the module green -- and that is how the
    dispatcher's shape came to differ from the one frozen in
    docs/response-schemas.json without any test objecting.

    Stubbing the shared projection must therefore remove the envelope. If the
    dispatcher ever grows its own inline copy again, the stub stops having any
    effect and this test fails -- which is the only way a second definition
    announces itself before the captured contract drifts.
    """
    monkeypatch.setattr(
        gateway_mod, "_public_failure", lambda payload, status, request_id: payload
    )
    out = recorder.json({"error": "nope"}, 404)
    assert "code" not in out and "request_id" not in out
