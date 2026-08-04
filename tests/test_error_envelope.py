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


def test_the_envelope_never_destroys_a_field_the_route_set(recorder):
    """`POST /frames/<id>/recovery/actions/restart_fresh` answers a failed
    action with its whole domain result and HTTP 409, and that result carries
    its own `status` ("failed", "partial", ...). The envelope used to overwrite
    it with the integer 409, destroying the only copy.

    The HTTP status survives deferral -- it is on the status line and `code`
    names it. The domain value has no second copy, so the envelope defers, the
    way it has always deferred to a route-supplied `code`.
    """
    out = recorder.json({"error": "recovery failed", "status": "partial"}, 409)
    assert out["status"] == "partial"
    assert out["code"] == "conflict"
    assert out["error"] == "recovery failed"


def test_a_route_that_sets_no_status_still_gets_the_http_one(recorder):
    out = recorder.json({"error": "nope"}, 503)
    assert out["status"] == 503


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


def test_the_decision_route_answers_a_refusal_with_its_mapped_status(tmp_path):
    """The hop the other tests cannot see: that the route *uses* the table.

    `tests/test_permissions.py` asserts the codes and the status map agree, and
    that the card honours `output_committed`. Deleting the route's projection
    entirely -- so every refusal goes back to HTTP 200 `{ok: false}` -- left all
    of them green. Measured, then covered here.

    Driven through `response_capture._probe_handler`, which is the in-tree way
    to build a handler complete enough for `_api`; `_Recorder` above only ever
    needed `_json`.
    """
    import openai4s.permissions as perms
    from openai4s.server import gateway as gateway_mod
    from openai4s.server import response_capture

    cfg = Config(data_dir=tmp_path, llm=LLMConfig(provider="deepseek", api_key="k"))
    cfg.ensure_dirs()
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_class = gateway_mod.make_handler(cfg, _Hub(), runner)
    store = runner.store
    frame_id = store.new_frame(kind="turn", project_id="default")

    cases = [
        ({"code": "decision_id_required"}, 400),
        ({"code": "decision_not_found"}, 404),
        ({"code": "decision_in_flight"}, 409),
        ({"code": "decision_expired"}, 410),
        ({"code": "decision_continuation_failed", "output_committed": True}, 500),
        ({}, 400),  # no code at all still leaves 2xx behind
    ]

    recorder = response_capture.Recorder()
    original = getattr(perms, "_BROKER", None)
    try:
        for extra, expected_status in cases:
            resolution = {"ok": False, "error": "refused", **extra}

            class _Broker:
                def resolve_result(self, *a, **k):
                    return dict(resolution)

            perms._BROKER = _Broker()
            path = f"/frames/{frame_id}/decision"
            handler = response_capture._probe_handler(
                recorder,
                handler_class,
                "POST",
                path,
                r"/frames/([^/]+)/decision",
                {},
                None,
                {"decision_id": "d-1", "allow": True},
            )
            seen = {}
            handler._json = lambda payload, code=200, **k: seen.update(
                body=payload, code=code
            )
            handler._api("POST", path)

            assert seen.get("code") == expected_status, (extra, seen.get("code"))
            assert seen["body"].get("ok") is False
    finally:
        perms._BROKER = original
