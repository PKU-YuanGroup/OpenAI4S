"""Typed transport errors and the bounded retry built on them.

Every transport failure used to collapse into ``LLMError(f"LLM HTTP {code}:
{detail}")``. The status, the headers, and with them ``Retry-After`` were gone
by the time any caller saw it, so a 429 was indistinguishable from a 401
without parsing English — and nothing retried. The repo's own golden trace
recorded that as `rate_limit_single_attempt`.

The retry is deliberately narrow, and the two halves matter equally:

  * a whole-response POST that failed with a retryable status committed
    nothing, so replaying it is safe;
  * a stream that already handed events to the caller has committed output,
    so replaying it would duplicate what the user has seen — no status makes
    that safe.

Tests inject `sleep` rather than actually sleeping; the delay is asserted as a
value, which is also the only way to pin the Retry-After/backoff precedence.
"""
import io
import urllib.error

import pytest

from openai4s.llm.models import TransportError, parse_retry_after, status_is_retryable
from openai4s.llm.transport import post_json, post_sse


class _Recorder:
    """Records sleeps instead of taking them."""

    def __init__(self):
        self.slept = []

    def __call__(self, seconds):
        self.slept.append(seconds)


def _http_error(code, body=b"{}", headers=None):
    return urllib.error.HTTPError(
        url="https://x.invalid/v1",
        code=code,
        msg="err",
        hdrs=headers or {},
        fp=io.BytesIO(body),
    )


class _Resp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# --------------------------------------------------------------------------
# the error carries its evidence
# --------------------------------------------------------------------------


def test_http_error_preserves_status_headers_and_retry_after(monkeypatch):
    err = _http_error(
        429,
        b'{"error":{"code":"rate_limit_exceeded"}}',
        {"Retry-After": "3", "x-request-id": "req-77"},
    )
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(err)
    )
    with pytest.raises(TransportError) as e:
        post_json("https://x.invalid/v1", {}, {}, 5, provider="ark", max_attempts=1)
    exc = e.value
    assert exc.status == 429
    assert exc.retry_after == 3.0
    assert exc.request_id == "req-77"
    assert exc.error_code == "rate_limit_exceeded"
    assert exc.retryable is True
    assert exc.provider == "ark"
    assert exc.is_rate_limit is True


def test_transport_error_is_still_an_llm_error():
    """Existing `except LLMError` handlers across the codebase must keep
    working — the typing is additive, not a migration."""
    from openai4s.llm.models import LLMError

    assert issubclass(TransportError, LLMError)


def test_to_dict_is_loggable_and_omits_the_body():
    err = TransportError("boom", provider="p", status=500, retryable=True)
    d = err.to_dict()
    assert d["status"] == 500
    assert d["retryable"] is True
    assert "body" not in d


# --------------------------------------------------------------------------
# retryability classification
# --------------------------------------------------------------------------


@pytest.mark.parametrize("code", [408, 425, 429, 500, 502, 503, 504])
def test_transient_statuses_are_retryable(code):
    assert status_is_retryable(code) is True


@pytest.mark.parametrize("code", [400, 401, 403, 404, 409, 422])
def test_client_errors_are_not_retryable(code):
    """Retrying a 401 just spends the user's rate limit to fail again."""
    assert status_is_retryable(code) is False


def test_auth_failure_raises_immediately(monkeypatch):
    calls = []

    def urlopen(*a, **k):
        calls.append(1)
        raise _http_error(401, b'{"error":"bad key"}')

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    sleeper = _Recorder()
    with pytest.raises(TransportError) as e:
        post_json("https://x.invalid", {}, {}, 5, sleep=sleeper)
    assert e.value.retryable is False
    assert len(calls) == 1, "a 401 must not be retried"
    assert sleeper.slept == []


# --------------------------------------------------------------------------
# Retry-After parsing
# --------------------------------------------------------------------------


def test_retry_after_seconds_form():
    assert parse_retry_after("7") == 7.0


def test_retry_after_http_date_form():
    # RFC 9110 permits either form; providers use both.
    assert parse_retry_after(
        "Wed, 21 Oct 2015 07:28:05 GMT", now=1445412425.0  # == that instant
    ) == pytest.approx(60.0, abs=1)


def test_retry_after_in_the_past_is_not_negative():
    """A date already elapsed means 'now', not 'sleep a negative amount'."""
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:05 GMT", now=2e9) == 0.0


@pytest.mark.parametrize("value", [None, "", "  ", "soon", "not-a-date"])
def test_unparseable_retry_after_is_none(value):
    assert parse_retry_after(value) is None


# --------------------------------------------------------------------------
# the retry loop
# --------------------------------------------------------------------------


def test_429_is_retried_and_recovers(monkeypatch):
    """The headline fix, at the transport level."""
    attempts = []

    def urlopen(*a, **k):
        attempts.append(1)
        if len(attempts) == 1:
            raise _http_error(429, b"{}", {"Retry-After": "2"})
        return _Resp(b'{"ok":true}')

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    sleeper = _Recorder()
    out = post_json("https://x.invalid", {}, {}, 5, sleep=sleeper)
    assert out == {"ok": True}
    assert len(attempts) == 2


def test_retry_after_overrides_the_computed_backoff(monkeypatch):
    """When the server says how long to wait, guessing is worse than obeying."""
    state = []

    def urlopen(*a, **k):
        state.append(1)
        if len(state) == 1:
            raise _http_error(429, b"{}", {"Retry-After": "2"})
        return _Resp(b"{}")

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    sleeper = _Recorder()
    post_json("https://x.invalid", {}, {}, 5, sleep=sleeper)
    assert sleeper.slept == [2.0]


def test_backoff_is_jittered_when_no_retry_after(monkeypatch):
    """Without jitter every client rate-limited at the same instant returns at
    the same instant and re-collides."""
    state = []

    def urlopen(*a, **k):
        state.append(1)
        if len(state) < 3:
            raise _http_error(503)
        return _Resp(b"{}")

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    sleeper = _Recorder()
    post_json("https://x.invalid", {}, {}, 5, sleep=sleeper)
    assert len(sleeper.slept) == 2
    # Full jitter: within (0, backoff], never the bare deterministic value.
    assert all(0 <= s <= 8 for s in sleeper.slept)


def test_attempts_are_bounded(monkeypatch):
    calls = []

    def urlopen(*a, **k):
        calls.append(1)
        raise _http_error(503)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    with pytest.raises(TransportError):
        post_json("https://x.invalid", {}, {}, 5, max_attempts=3, sleep=_Recorder())
    assert len(calls) == 3


def test_retry_budget_stops_an_absurd_retry_after(monkeypatch):
    """A provider may advertise a 300s Retry-After. Honouring it inside a turn
    would read as a hang, so the budget refuses — and says why."""

    def urlopen(*a, **k):
        raise _http_error(429, b"{}", {"Retry-After": "300"})

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    sleeper = _Recorder()
    with pytest.raises(TransportError) as e:
        post_json("https://x.invalid", {}, {}, 5, retry_budget=30, sleep=sleeper)
    assert "budget" in str(e.value)
    assert sleeper.slept == [], "must not sleep past the budget"


def test_cancellation_is_honored_between_attempts(monkeypatch):
    """A user's Stop must not wait out a backoff."""

    def urlopen(*a, **k):
        raise _http_error(503)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    with pytest.raises(TransportError) as e:
        post_json(
            "https://x.invalid",
            {},
            {},
            5,
            should_cancel=lambda: True,
            sleep=_Recorder(),
        )
    assert "cancelled" in str(e.value)


def test_connection_error_is_retryable(monkeypatch):
    """Never reached the server, so nothing was committed."""
    state = []

    def urlopen(*a, **k):
        state.append(1)
        if len(state) == 1:
            raise urllib.error.URLError("connection refused")
        return _Resp(b'{"ok":1}')

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    assert post_json("https://x.invalid", {}, {}, 5, sleep=_Recorder()) == {"ok": 1}


# --------------------------------------------------------------------------
# streams: the no-retry-after-output rule
# --------------------------------------------------------------------------


def test_sse_connect_failure_is_retried(monkeypatch):
    """Nothing was delivered yet, so a replay is safe."""
    state = []

    def urlopen(*a, **k):
        state.append(1)
        if len(state) == 1:
            raise _http_error(503)
        return iter([b'data: {"delta":"hi"}\n', b"\n"])

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    seen = []
    post_sse("https://x.invalid", {}, {}, 5, seen.append, sleep=_Recorder())
    assert seen == [{"delta": "hi"}]
    assert len(state) == 2


def test_sse_failure_after_committed_output_is_never_retried(monkeypatch):
    """The rule that keeps the retry honest: the caller has already seen these
    bytes, so replaying the request would emit them twice."""

    class _Stream:
        def __iter__(self):
            yield b'data: {"delta":"committed"}\n'
            yield b"\n"
            raise ConnectionResetError("stream died mid-flight")

        def close(self):
            pass

    calls = []

    def urlopen(*a, **k):
        calls.append(1)
        return _Stream()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    seen = []
    with pytest.raises(TransportError) as e:
        post_sse("https://x.invalid", {}, {}, 5, seen.append, sleep=_Recorder())
    assert e.value.output_committed is True
    assert e.value.retryable is False
    assert len(calls) == 1, "a committed stream must not be replayed"
    assert seen == [{"delta": "committed"}]


def test_sse_read_failure_before_any_event_is_retryable(monkeypatch):
    class _Stream:
        def __iter__(self):
            raise ConnectionResetError("died before any event")
            yield  # pragma: no cover

        def close(self):
            pass

    calls = []

    def urlopen(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            return _Stream()
        return iter([b'data: {"delta":"ok"}\n', b"\n"])

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    seen = []
    post_sse("https://x.invalid", {}, {}, 5, seen.append, sleep=_Recorder())
    assert seen == [{"delta": "ok"}]
