"""TypeSafeBackend against the in-process loopback fake. Offline; no live API."""

from __future__ import annotations

import logging
import os
import time
import urllib.request
from contextlib import contextmanager
from typing import Any, Iterator

import pytest

from harness.providers.typesafe_fake import start_fake
from openai4s.http_deadline import HTTPExchangeDeadline
from openai4s.judgment.port import BackendError
from openai4s.judgment.types import Answer, Choice, Noul, Score
from openai4s.judgment.typesafe import ENDPOINT, MAX_RESPONSE_BYTES, TypeSafeBackend

SECRET = "ts-w1a-secret-NEVER-LEAK-7f2c"
STORE_SECRET = "ts-w1a-store-secret-NEVER-LEAK-aa91"

NOUL = Noul(instructions="Does state.text convey urgency?")
CHOICE = Choice(
    instructions="Which team should handle state.text?",
    options={"billing": "Payments", "technical": "Bugs", "sales": "Pricing"},
)
SCORE = Score(
    instructions="How frustrated is the customer in state.text?",
    levels=("Calm", "Frustrated", "Very angry"),
)


class _Store:
    def __init__(self, key: str = "") -> None:
        self.key = key
        self.calls = 0

    def get_secret_setting(self, key: str, *, scope: str | None = None) -> str:
        del key, scope
        self.calls += 1
        return self.key


def _assert_secret_absent(*parts: object, secret: str = SECRET) -> None:
    for part in parts:
        text = part if isinstance(part, str) else str(part)
        assert secret not in text
        assert STORE_SECRET not in text


@contextmanager
def running_backend(
    monkeypatch: pytest.MonkeyPatch,
    *,
    secret: str | None = SECRET,
    store_key: str = "",
    **fake_opts: Any,
) -> Iterator[tuple[TypeSafeBackend, str, list[dict[str, Any]], _Store]]:
    captures: list[dict[str, Any]] = fake_opts.pop("captures", None) or []
    fake_opts["captures"] = captures
    url, stop = start_fake(port=0, **fake_opts)
    store = _Store(store_key)
    try:
        monkeypatch.setenv("OPENAI4S_JUDGMENT_FAKE_ENDPOINT", url)
        if secret is None:
            monkeypatch.delenv("OPENAI4S_TYPESAFE_API_KEY", raising=False)
        else:
            monkeypatch.setenv("OPENAI4S_TYPESAFE_API_KEY", secret)
        backend = TypeSafeBackend(lambda: store)
        yield backend, url, captures, store
    finally:
        stop()


def _evaluate(
    backend: TypeSafeBackend,
    *,
    questions: dict[str, Any] | None = None,
    timeout: float = 3.0,
    state: object = "Help! My payouts have been failing for 3 days.",
) -> Any:
    return backend.evaluate(
        state=state,
        questions=questions or {"is_urgent": NOUL},
        model="jev-1.13.0",
        timeout=timeout,
    )


def test_three_question_types_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    with running_backend(monkeypatch) as (backend, _url, _captures, _store):
        reply = _evaluate(
            backend,
            questions={
                "is_urgent": NOUL,
                "department": CHOICE,
                "frustration": SCORE,
            },
        )
    assert reply.fake is True
    assert reply.model == "jev-1.13.0"
    assert reply.usage["input_tokens"] >= 0
    assert reply.usage["output_tokens"] >= 0
    urgent = reply.answers["is_urgent"]
    assert isinstance(urgent, Answer)
    assert urgent.kind == "noul"
    assert urgent.value == 0.5
    department = reply.answers["department"]
    assert department.kind == "choice"
    assert department.value == "billing"
    assert department.probabilities["billing"] == pytest.approx(0.6)
    frustration = reply.answers["frustration"]
    assert frustration.kind == "score"
    assert frustration.value == 1.0


@pytest.mark.parametrize(
    ("fail", "code"),
    [
        ("401:1", "auth"),
        ("422:1", "invalid_request"),
        ("500:1", "unavailable"),
        ("529:1", "overloaded"),
    ],
)
def test_http_status_mapping(
    monkeypatch: pytest.MonkeyPatch,
    fail: str,
    code: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="openai4s.judgment.typesafe")
    timeout = 0.3 if code in {"overloaded"} else 3.0
    retry_after = "5" if code == "overloaded" else "0"
    with running_backend(monkeypatch, fail=fail, retry_after=retry_after) as (
        backend,
        _url,
        _captures,
        _store,
    ):
        with pytest.raises(BackendError) as caught:
            _evaluate(backend, timeout=timeout)
    assert caught.value.code == code
    _assert_secret_absent(caught.value, caught.value.message, caplog.text)


def test_429_retries_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="openai4s.judgment.typesafe")
    with running_backend(monkeypatch, fail="429:2", retry_after="0") as (
        backend,
        _url,
        captures,
        _store,
    ):
        reply = _evaluate(backend)
    assert reply.answers["is_urgent"].value == 0.5
    assert len(captures) == 3
    _assert_secret_absent(caplog.text, reply.model)


def test_429_exceeds_budget(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="openai4s.judgment.typesafe")
    started = time.monotonic()
    with running_backend(monkeypatch, fail="429:20", retry_after="5") as (
        backend,
        _url,
        _captures,
        _store,
    ):
        with pytest.raises(BackendError) as caught:
            _evaluate(backend, timeout=0.4)
    assert caught.value.code == "rate_limited"
    assert time.monotonic() - started < 1.5
    _assert_secret_absent(caught.value, caplog.text)


def test_529_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    with running_backend(monkeypatch, fail="529:1", retry_after="0") as (
        backend,
        _url,
        captures,
        _store,
    ):
        reply = _evaluate(backend)
    assert reply.fake is True
    assert len(captures) == 2


def test_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    with running_backend(monkeypatch, latency_ms=400) as (backend, _url, _c, _s):
        with pytest.raises(BackendError) as caught:
            _evaluate(backend, timeout=0.1)
    assert caught.value.code == "timeout"
    _assert_secret_absent(caught.value)


def test_redirect_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    with running_backend(monkeypatch, redirect_to="http://127.0.0.1:9/steal") as (
        backend,
        _url,
        _c,
        _s,
    ):
        with pytest.raises(BackendError) as caught:
            _evaluate(backend)
    assert caught.value.code == "unavailable"
    _assert_secret_absent(caught.value)


def test_response_body_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    with running_backend(monkeypatch, oversize_bytes=MAX_RESPONSE_BYTES + 1) as (
        backend,
        _url,
        _c,
        _s,
    ):
        with pytest.raises(BackendError) as caught:
            _evaluate(backend)
    assert caught.value.code == "invalid_response"
    _assert_secret_absent(caught.value)


def test_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    with running_backend(monkeypatch, raw_body=b"not-json") as (backend, _url, _c, _s):
        with pytest.raises(BackendError) as caught:
            _evaluate(backend)
    assert caught.value.code == "invalid_response"
    _assert_secret_absent(caught.value)


def test_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI4S_TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI4S_JUDGMENT_FAKE_ENDPOINT", raising=False)
    backend = TypeSafeBackend(lambda: _Store(""))
    with pytest.raises(BackendError) as caught:
        _evaluate(backend)
    assert caught.value.code == "unconfigured"
    _assert_secret_absent(caught.value)


def test_env_key_wins_over_store(monkeypatch: pytest.MonkeyPatch) -> None:
    with running_backend(monkeypatch, secret=SECRET, store_key=STORE_SECRET) as (
        backend,
        _url,
        captures,
        store,
    ):
        _evaluate(backend)
    assert store.calls == 0
    assert captures[-1]["authorization"] == f"Bearer {SECRET}"
    assert "OPENAI4S_TYPESAFE_API_KEY" in os.environ


def test_store_key_used_when_env_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    with running_backend(monkeypatch, secret=None, store_key=SECRET) as (
        backend,
        _url,
        captures,
        store,
    ):
        _evaluate(backend)
        assert "OPENAI4S_TYPESAFE_API_KEY" not in os.environ
    assert store.calls == 1
    assert captures[-1]["authorization"] == f"Bearer {SECRET}"


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8862/v1/systemone",
        "http://10.0.0.1:80/v1/systemone",
        "https://127.0.0.1:443/v1/systemone",
        "https://127.0.0.1/v1/systemone",
    ],
)
def test_illegal_fake_endpoint_fails_closed(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setenv("OPENAI4S_JUDGMENT_FAKE_ENDPOINT", url)
    monkeypatch.setenv("OPENAI4S_TYPESAFE_API_KEY", SECRET)
    with pytest.raises(ValueError):
        TypeSafeBackend(lambda: _Store("x"))


def test_egress_allowlist_blocks_before_connect(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="openai4s.judgment.typesafe")
    monkeypatch.delenv("OPENAI4S_JUDGMENT_FAKE_ENDPOINT", raising=False)
    monkeypatch.setenv("OPENAI4S_EGRESS", "allowlist")
    monkeypatch.setenv("OPENAI4S_TYPESAFE_API_KEY", SECRET)
    opened: list[str] = []

    def _boom_open(self: HTTPExchangeDeadline, opener: Any, request: Any) -> Any:
        opened.append(getattr(request, "full_url", "") or "")
        raise AssertionError("must not connect")

    def _boom_urlopen(*_args: Any, **_kwargs: Any) -> Any:
        opened.append("urlopen")
        raise AssertionError("must not connect")

    monkeypatch.setattr(HTTPExchangeDeadline, "open", _boom_open)
    monkeypatch.setattr(urllib.request, "urlopen", _boom_urlopen)
    backend = TypeSafeBackend(lambda: _Store(""))
    with pytest.raises(BackendError) as caught:
        _evaluate(backend)
    assert caught.value.code == "egress_blocked"
    assert opened == []
    _assert_secret_absent(caught.value, caplog.text)
    assert ENDPOINT.startswith("https://api.typesafe.ai/")


def test_secret_absent_from_exceptions_logs_and_fake_log(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    log_path = tmp_path / "typesafe.jsonl"
    with running_backend(
        monkeypatch, fail="500:1", log_path=log_path, secret=SECRET
    ) as (backend, _url, _c, _s):
        with pytest.raises(BackendError) as caught:
            _evaluate(backend)
    logged = log_path.read_text(encoding="utf-8")
    assert caught.value.code == "unavailable"
    _assert_secret_absent(caught.value, caught.value.message, caplog.text, logged)
    assert "Authorization" not in logged
    assert "authorization" not in logged
