"""`web_fetch` keeps to a host's published request rate and retries one 429.

The bug this pins (usability review ENV-4): four parallel fetches of
`eutils.ncbi.nlm.nih.gov` and the fourth came back `HTTP Error 429: Too Many
Requests`. NCBI allows three requests a second without a key; `web_fetch`
neither paced itself nor tried again, so the model was left to "look it up
later".

No test here sleeps or opens a socket: the throttle's clock and sleep are
injected, and the opener is a stub.
"""

from __future__ import annotations

import email.message
import io
import sys
import threading
import urllib.error

import pytest

from openai4s import webtools

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed"


class _Time:
    """A clock that only moves when something sleeps on it."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _max_starts_in_any_window(starts: list[float], window: float) -> int:
    return max(
        sum(1 for other in starts if start <= other < start + window)
        for start in starts
    )


# --------------------------------------------------------------------------
# pacing
# --------------------------------------------------------------------------


def test_the_fourth_request_in_a_second_waits_for_the_next_one():
    fake = _Time()
    throttle = webtools.HostThrottle(clock=fake.clock, sleep=fake.sleep)

    starts = []
    for _ in range(7):
        throttle.wait(EUTILS)
        starts.append(fake.now)

    assert fake.sleeps == [1.0, 1.0]
    assert starts == [0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 2.0]
    assert _max_starts_in_any_window(starts, 1.0) == 3


def test_a_host_with_no_published_limit_is_never_delayed():
    fake = _Time()
    throttle = webtools.HostThrottle(clock=fake.clock, sleep=fake.sleep)

    for _ in range(50):
        assert throttle.wait("https://rest.uniprot.org/uniprotkb/search") == 0.0

    assert fake.sleeps == []


def test_parallel_callers_queue_instead_of_waking_together():
    """Each caller reserves its own slot under the lock: twelve threads asking
    at the same instant are spread over four seconds, three to a second."""
    delays: list[float] = []
    lock = threading.Lock()

    def record(seconds: float) -> None:
        with lock:
            delays.append(seconds)

    throttle = webtools.HostThrottle(clock=lambda: 0.0, sleep=record)
    threads = [
        threading.Thread(target=throttle.wait, args=(EUTILS,)) for _ in range(12)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # Three start at once and sleep nothing; the other nine wait 1, 2 and 3 s.
    assert sorted(delays) == [1.0] * 3 + [2.0] * 3 + [3.0] * 3


# --------------------------------------------------------------------------
# one retry after a 429
# --------------------------------------------------------------------------


class _Body(io.BytesIO):
    headers = {"Content-Type": "application/json"}

    def geturl(self) -> str:
        return EUTILS


def _too_many(retry_after: str | None) -> urllib.error.HTTPError:
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(
        EUTILS, 429, "Too Many Requests", headers, io.BytesIO(b"")
    )


@pytest.fixture
def stubbed(monkeypatch):
    """A scripted opener and a fake clock under the real fetch path."""
    fake = _Time()
    script: list[object] = []
    opened: list[str] = []

    class _Opener:
        def open(self, request, timeout=None):
            opened.append(request.full_url)
            answer = script.pop(0)
            if isinstance(answer, BaseException):
                raise answer
            return answer

    monkeypatch.setenv("OPENAI4S_ALLOW_NETWORK", "1")
    monkeypatch.setitem(sys.modules, "requests", None)
    monkeypatch.setattr(webtools, "_guard_url", lambda _url: None)
    monkeypatch.setattr(webtools, "_no_redirect_opener", lambda: _Opener())
    monkeypatch.setattr(
        webtools,
        "_throttle",
        webtools.HostThrottle(clock=fake.clock, sleep=fake.sleep),
    )
    return fake, script, opened


@pytest.mark.stubbed_backend
def test_a_429_is_retried_once_after_what_the_server_asked_for(stubbed):
    fake, script, opened = stubbed
    script[:] = [_too_many("2"), _Body(b'{"count": 1}')]

    result = webtools.web_fetch(EUTILS, fmt="json")

    assert '"count": 1' in result["content"]
    assert len(opened) == 2
    assert fake.sleeps == [2.0]


@pytest.mark.stubbed_backend
def test_a_429_without_retry_after_waits_the_default(stubbed):
    fake, script, opened = stubbed
    script[:] = [_too_many(None), _Body(b"{}")]

    webtools.web_fetch(EUTILS, fmt="json")

    assert fake.sleeps == [webtools.RETRY_AFTER_DEFAULT_S]
    assert len(opened) == 2


@pytest.mark.stubbed_backend
def test_a_second_429_is_the_callers_answer(stubbed):
    """Bounded: one retry, then the 429 it would have had anyway."""
    fake, script, opened = stubbed
    script[:] = [_too_many("1"), _too_many("1"), _Body(b"{}")]

    with pytest.raises(urllib.error.HTTPError) as caught:
        webtools.web_fetch(EUTILS, fmt="json")

    assert caught.value.code == 429
    assert len(opened) == 2
    assert fake.sleeps == [1.0]


@pytest.mark.stubbed_backend
def test_a_long_retry_after_is_not_waited_out(stubbed):
    fake, script, opened = stubbed
    script[:] = [_too_many("600"), _Body(b"{}")]

    with pytest.raises(urllib.error.HTTPError):
        webtools.web_fetch(EUTILS, fmt="json")

    assert len(opened) == 1
    assert fake.sleeps == []


@pytest.mark.stubbed_backend
def test_the_retry_does_not_spend_the_redirect_budget(stubbed):
    fake, script, opened = stubbed
    script[:] = [_too_many("1"), _Body(b"{}")]

    body, _final, _ctype = webtools._http_get(EUTILS, _max_redirects=0)

    assert body == b"{}"
    assert len(opened) == 2


@pytest.mark.stubbed_backend
def test_four_fetches_of_ncbi_are_paced_rather_than_refused(stubbed):
    """The reviewed session, end to end: the fourth request waits a second
    for its slot instead of being sent into a 429."""
    fake, script, opened = stubbed
    script[:] = [_Body(b"{}") for _ in range(4)]

    for _ in range(4):
        webtools.web_fetch(EUTILS, fmt="json")

    assert len(opened) == 4
    assert fake.sleeps == [1.0]
