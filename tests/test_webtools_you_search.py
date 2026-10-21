"""You.com search in openai4s.webtools — offline (stubbed _http_get / opener).

Kept out of test_webtools.py on purpose: that module skips itself when bs4 is
missing, and no CI job installs bs4, so tests placed there never run in CI.
Nothing here needs bs4 — the DuckDuckGo fallthrough only checks `source`, which
the regex parser in `_ddg_html` produces as well.
"""

import io
import json

import pytest

from openai4s import webtools


@pytest.fixture(autouse=True)
def _no_inherited_backoff(monkeypatch):
    # The 429 backoff is process-wide; every test starts without one and
    # monkeypatch puts the value back after the test that sets it.
    monkeypatch.setattr(webtools, "_you_backoff_until", 0.0)


def _stub_http(monkeypatch, handler):
    """Replace webtools._http_get with `handler(url) -> body_str`; None or a
    raise simulates that endpoint failing. Also disables the retry pause."""
    monkeypatch.setattr(webtools.time, "sleep", lambda *_a, **_k: None)

    def fake(url, *, timeout=30.0, headers=None, _max_redirects=5):
        body = handler(url)
        if body is None:
            raise RuntimeError("simulated failure")
        return body.encode("utf-8"), url, "text/html"

    monkeypatch.setattr(webtools, "_http_get", fake)


_DDG_HTML = """
<div class="result web-result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fa">Result A</a>
  <a class="result__snippet">Snippet A about CRISPR.</a>
</div>
"""

_YOU_JSON = json.dumps(
    {
        "results": {
            "web": [
                {
                    "url": "https://you-hit.example/a",
                    "title": "You Hit",
                    "description": "desc",
                    "snippets": ["first snippet", "second snippet"],
                },
                {
                    "url": "https://you-hit.example/b",
                    "title": "B",
                    "description": "only desc",
                },
            ]
        }
    }
)


def test_search_uses_keyless_you_before_scrapers(monkeypatch):
    monkeypatch.delenv("YDC_API_KEY", raising=False)
    seen = []

    def handler(url):
        seen.append(url)
        if url.startswith("https://api.you.com/v1/agents/search?"):
            return _YOU_JSON
        return _DDG_HTML if "duckduckgo" in url else None

    _stub_http(monkeypatch, handler)
    out = webtools.web_search("CRISPR base editing", num_results=5)
    assert out["source"] == "you.com"
    assert out["count"] == 2
    assert out["results"][0]["snippet"] == "first snippet second snippet"
    assert out["results"][1]["snippet"] == "only desc"
    assert "count=5" in seen[0]
    assert not any("duckduckgo" in u for u in seen)


def test_you_search_sends_integration_user_agent(monkeypatch):
    monkeypatch.delenv("YDC_API_KEY", raising=False)
    captured = {}

    def fake(url, *, timeout=30.0, headers=None, _max_redirects=5):
        captured["headers"] = headers
        return _YOU_JSON.encode(), url, "application/json"

    monkeypatch.setattr(webtools, "_http_get", fake)
    webtools._you_search("q", 3, 5.0)
    assert captured["headers"]["User-Agent"] == (
        "youdotcom-integration/pku-yuangroup-openai4s"
    )


def test_you_search_failure_falls_through_to_scrapers(monkeypatch):
    monkeypatch.delenv("YDC_API_KEY", raising=False)
    # api.you.com "fails" (e.g. 429 past the free quota) → DDG answers.
    _stub_http(monkeypatch, lambda url: _DDG_HTML if "duckduckgo" in url else None)
    out = webtools.web_search("CRISPR base editing", num_results=5)
    assert out["source"] == "duckduckgo"


def test_a_switched_off_you_is_never_asked(monkeypatch):
    seen = []

    def handler(url):
        seen.append(url)
        return _DDG_HTML if "duckduckgo" in url else None

    _stub_http(monkeypatch, handler)
    out = webtools.web_search("q", engines=("tavily", "builtin"))
    assert out["source"] == "duckduckgo"
    assert not any("api.you.com" in u for u in seen)


def test_you_search_with_key_uses_keyed_post(monkeypatch):
    from openai4s import egress

    monkeypatch.setenv("YDC_API_KEY", "ydc-test")
    captured = {}
    guarded = []
    monkeypatch.setattr(
        egress, "check_url", lambda url: guarded.append(("egress", url))
    )
    monkeypatch.setattr(
        webtools, "_guard_url", lambda url: guarded.append(("ssrf", url))
    )

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Opener:
        def open(self, req, timeout=None):
            captured["req"] = req
            return _Resp(_YOU_JSON.encode())

    # The no-redirect opener, not urlopen: a followed 3xx would carry X-API-Key.
    monkeypatch.setattr(webtools, "_no_redirect_opener", lambda: _Opener())
    out = webtools._you_search("q", 1, 5.0)
    req = captured["req"]
    assert req.full_url == "https://ydc-index.io/v1/search"
    assert req.get_method() == "POST"
    assert req.get_header("X-api-key") == "ydc-test"
    assert json.loads(req.data) == {"query": "q", "count": 1}
    assert len(out) == 1
    assert guarded == [
        ("egress", "https://ydc-index.io/v1/search"),
        ("ssrf", "https://ydc-index.io/v1/search"),
    ]


@pytest.mark.parametrize(
    "body",
    [
        "null",
        "[]",
        '{"results": ["x"]}',
        '{"results": {"web": ["x"]}}',
        '{"results": {"web": [{"url": 5}]}}',
    ],
)
def test_malformed_you_payload_falls_through_to_scrapers(monkeypatch, body):
    def handler(url):
        if url.startswith(webtools._YOU_FREE_URL):
            return body
        return _DDG_HTML if "duckduckgo" in url else None

    _stub_http(monkeypatch, handler)
    out = webtools.web_search("CRISPR base editing", num_results=5)
    assert out["source"] == "duckduckgo"


def test_you_search_ignores_a_string_snippets_field(monkeypatch):
    body = json.dumps(
        {
            "results": {
                "web": [
                    {
                        "url": "https://you-hit.example/a",
                        "description": "desc",
                        "snippets": "first snippet",
                    }
                ]
            }
        }
    )
    _stub_http(monkeypatch, lambda url: body)
    assert webtools._you_search("q", 3, 5.0)[0]["snippet"] == "desc"


def test_you_count_matches_results_when_none_are_asked_for(monkeypatch):
    _stub_http(
        monkeypatch,
        lambda url: _YOU_JSON if url.startswith(webtools._YOU_FREE_URL) else None,
    )
    out = webtools.web_search("CRISPR base editing", num_results=0)
    assert out["count"] == len(out["results"])


def test_requests_branch_asks_for_identity_encoding(monkeypatch):
    # `requests` hands back `response.raw` undecoded, so a gzip answer would
    # reach json.loads as compressed bytes.
    import sys
    import types

    captured = {}

    class _Response:
        is_redirect = False
        status_code = 200
        url = "https://api.you.com/v1/agents/search"
        headers = {"Content-Type": "application/json"}

        def __init__(self):
            self.raw = io.BytesIO(b"{}")

        def close(self):
            pass

    def request(method, url, *, headers, **_kwargs):
        captured["headers"] = headers
        return _Response()

    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(request=request))
    monkeypatch.setattr(webtools, "_guard_url", lambda _url: None)
    body, _url, _ctype = webtools._http_get("https://api.you.com/v1/agents/search")
    assert body == b"{}"
    assert captured["headers"]["Accept-Encoding"] == "identity"


def test_tavily_goes_through_the_same_guarded_post(monkeypatch):
    from openai4s import egress

    monkeypatch.setenv("OPENAI4S_TAVILY_API_KEY", "tvly-test")
    guarded, captured = [], {}
    monkeypatch.setattr(
        egress, "check_url", lambda url: guarded.append(("egress", url))
    )
    monkeypatch.setattr(
        webtools, "_guard_url", lambda url: guarded.append(("ssrf", url))
    )

    class _Opener:
        def open(self, req, timeout=None):
            captured["req"] = req
            body = {"results": [{"url": "https://t.example/a", "title": "T"}]}
            return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(webtools, "_no_redirect_opener", lambda: _Opener())
    out = webtools._tavily_search("q", 1, 5.0)
    assert [r["url"] for r in out] == ["https://t.example/a"]
    assert captured["req"].get_header("Authorization") == "Bearer tvly-test"
    assert guarded == [
        ("egress", "https://api.tavily.com/search"),
        ("ssrf", "https://api.tavily.com/search"),
    ]


def _rate_limited(retry_after=None):
    import urllib.error

    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return urllib.error.HTTPError(
        webtools._YOU_FREE_URL, 429, "Too Many", headers, None
    )


def test_a_429_skips_you_until_retry_after(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(webtools.time, "monotonic", lambda: clock["t"])
    calls = []

    def fake(url, *, timeout=30.0, headers=None, _max_redirects=5):
        calls.append(timeout)
        raise _rate_limited("120")

    monkeypatch.setattr(webtools, "_http_get", fake)
    assert webtools._you_search("q", 3, 12.0) == []
    assert calls == [webtools._YOU_TIMEOUT]  # a stall cannot take the full 12 s
    assert webtools._you_search("q", 3, 12.0) == []
    assert len(calls) == 1  # skipped: no doomed round trip
    clock["t"] += 121
    webtools._you_search("q", 3, 12.0)
    assert len(calls) == 2


def test_a_requests_shaped_429_also_backs_off_and_other_errors_do_not(monkeypatch):
    import types

    requests_429 = RuntimeError("429")
    requests_429.response = types.SimpleNamespace(status_code=429, headers={})
    errors = iter([RuntimeError("timeout"), requests_429])
    calls = []

    def fake(url, *, timeout=30.0, headers=None, _max_redirects=5):
        calls.append(url)
        raise next(errors)

    monkeypatch.setattr(webtools, "_http_get", fake)
    webtools._you_search("q", 3, 5.0)  # a plain failure: no backoff
    webtools._you_search("q", 3, 5.0)  # requests' 429: backoff starts
    webtools._you_search("q", 3, 5.0)
    assert len(calls) == 2
