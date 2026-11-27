"""SpecStream search in openai4s.webtools: offline, with the opener stubbed."""

import io
import json
import urllib.error

import pytest

from openai4s import webtools

_URL = "https://api.specstream.ai/v1/web-search"
_SPECSTREAM_JSON = json.dumps(
    {
        "data": {
            "query": "q",
            "answer": None,
            "sources": [
                {
                    "url": "https://ss-hit.example/a",
                    "title": "Hit A",
                    "site": "ss-hit.example",
                    "snippet": "  " + "x" * 700,
                },
                {"url": "https://ss-hit.example/b", "title": "", "snippet": "b"},
                {"title": "no url"},
                "not a source",
            ],
        }
    }
)


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def opener(monkeypatch):
    """The no-redirect opener, stubbed; records each request and replays
    ``answers`` (bytes, or an exception to raise) in order."""
    from openai4s import egress

    calls = {"requests": [], "guarded": [], "answers": []}
    monkeypatch.setattr(
        egress, "check_url", lambda url: calls["guarded"].append(("egress", url))
    )
    monkeypatch.setattr(
        webtools, "_guard_url", lambda url: calls["guarded"].append(("ssrf", url))
    )

    class _Opener:
        def open(self, req, timeout=None):
            calls["requests"].append(req)
            answer = calls["answers"].pop(0)
            if isinstance(answer, BaseException):
                raise answer
            return _Resp(answer)

    monkeypatch.setattr(webtools, "_no_redirect_opener", lambda: _Opener())
    return calls


def test_specstream_posts_the_key_as_bearer_and_maps_sources(monkeypatch, opener):
    monkeypatch.setenv("SPECSTREAM_API_KEY", "ss-test")
    opener["answers"].append(_SPECSTREAM_JSON.encode())
    out = webtools._specstream_search("CRISPR base editing", 5, 5.0)
    req = opener["requests"][0]
    assert req.full_url == "https://api.specstream.ai/v1/web-search"
    assert req.get_method() == "POST"
    assert req.get_header("Authorization") == "Bearer ss-test"
    assert json.loads(req.data) == {"q": "CRISPR base editing", "num": 5}
    assert opener["guarded"] == [
        ("egress", "https://api.specstream.ai/v1/web-search"),
        ("ssrf", "https://api.specstream.ai/v1/web-search"),
    ]
    assert out == [
        {"title": "Hit A", "url": "https://ss-hit.example/a", "snippet": "x" * 500},
        {
            "title": "https://ss-hit.example/b",
            "url": "https://ss-hit.example/b",
            "snippet": "b",
        },
    ]


def test_specstream_without_a_key_sends_nothing(monkeypatch, opener):
    monkeypatch.delenv("SPECSTREAM_API_KEY", raising=False)
    assert webtools._specstream_search("q", 5, 5.0) == []
    out = webtools.web_search("q", engines=("specstream",))
    assert out["count"] == 0
    assert opener["requests"] == []
    # Skipped, not tried: no engine is named as reached.
    assert "(none reached)" in out["note"]


def test_web_search_answers_from_specstream_when_it_is_first(monkeypatch, opener):
    monkeypatch.setenv("SPECSTREAM_API_KEY", "ss-test")
    opener["answers"].append(_SPECSTREAM_JSON.encode())
    out = webtools.web_search("CRISPR base editing", engines=("specstream", "builtin"))
    assert out["source"] == "specstream"
    assert out["count"] == 2


def test_a_refused_key_is_named_in_the_note_and_the_next_engine_runs(
    monkeypatch, opener
):
    monkeypatch.setenv("SPECSTREAM_API_KEY", "ss-wrong")
    body = json.dumps({"error": {"code": "invalid_token", "message": "key refused"}})
    opener["answers"].append(
        urllib.error.HTTPError(
            "https://api.specstream.ai/v1/web-search",
            401,
            "Unauthorized",
            {},
            io.BytesIO(body.encode()),
        )
    )
    tried = []

    def builtin(query, num_results, deadline):
        tried.append(query)
        return [], "", None

    monkeypatch.setattr(webtools, "_builtin_search", builtin)
    out = webtools.web_search("q", engines=("specstream", "builtin"))
    assert tried == ["q"]
    assert out["count"] == 0
    assert "SpecStream: HTTP 401: key refused" in out["note"]
    assert "ss-wrong" not in out["note"]


def _refusal(code, body):
    return urllib.error.HTTPError(_URL, code, "refused", {}, body)


def _builtin_finds_nothing(monkeypatch):
    monkeypatch.setattr(webtools, "_builtin_search", lambda *_args: ([], "", None))


def test_a_key_the_api_quotes_back_is_scrubbed_from_the_note(monkeypatch, opener):
    monkeypatch.setenv("SPECSTREAM_API_KEY", "ss-live-secret")
    body = json.dumps({"error": {"message": "Invalid API key ss-live-secret"}})
    opener["answers"].append(_refusal(401, io.BytesIO(body.encode())))
    _builtin_finds_nothing(monkeypatch)
    out = webtools.web_search("q", engines=("specstream", "builtin"))
    assert "SpecStream: HTTP 401: Invalid API key [REDACTED]" in out["note"]
    assert "ss-live-secret" not in out["note"]


def test_a_key_with_a_line_break_is_refused_before_any_request(monkeypatch, opener):
    # http.client would refuse the header and quote all of it, key included.
    monkeypatch.setenv("SPECSTREAM_API_KEY", "ss-live-0123\nABCDEF")
    _builtin_finds_nothing(monkeypatch)
    out = webtools.web_search("q", engines=("specstream", "builtin"))
    assert opener["requests"] == []
    assert "SpecStream: SPECSTREAM_API_KEY contains a line break" in out["note"]
    assert "ss-live-0123" not in out["note"] and "ABCDEF" not in out["note"]


class _Body(io.BytesIO):
    """An error body that counts how much of it was read."""

    consumed = 0

    def read(self, size=-1):
        chunk = super().read(size)
        self.consumed += len(chunk)
        return chunk


def test_an_error_body_is_read_only_up_to_a_cap(monkeypatch, opener):
    monkeypatch.setenv("SPECSTREAM_API_KEY", "ss-test")
    body = _Body(json.dumps({"error": {"message": "A" * 2_000_000}}).encode())
    opener["answers"].append(_refusal(401, body))
    _builtin_finds_nothing(monkeypatch)
    out = webtools.web_search("q", engines=("specstream", "builtin"))
    assert body.consumed <= webtools._SPECSTREAM_ERROR_BODY_MAX
    assert "Failed: SpecStream: HTTP 401." in out["note"]
    # A long message that does fit is still cut to a short reason.
    long = json.dumps({"error": {"message": "B" * 5000}}).encode()
    reason = webtools._specstream_reason(_refusal(402, io.BytesIO(long)), "ss-test")
    assert reason.startswith("HTTP 402: BBB")
    assert len(reason) <= webtools._SPECSTREAM_REASON_MAX


def test_a_reply_without_sources_is_a_failure(monkeypatch, opener):
    monkeypatch.setenv("SPECSTREAM_API_KEY", "ss-test")
    opener["answers"].append(b'{"data": {}}')
    with pytest.raises(ValueError, match="no sources"):
        webtools._specstream_search("q", 5, 5.0)


def test_a_row_off_the_schema_is_skipped_not_fatal(monkeypatch, opener):
    monkeypatch.setenv("SPECSTREAM_API_KEY", "ss-test")
    sources = [
        {"url": "https://ok.example/a", "title": "A", "snippet": "a"},
        {"url": 12345, "title": "a number for a url"},
        {"url": "https://ok.example/b", "title": 7, "snippet": ["not", "text"]},
        {"url": "javascript:alert(1)", "title": "not http"},
    ]
    opener["answers"].append(json.dumps({"data": {"sources": sources}}).encode())
    assert webtools._specstream_search("q", 5, 5.0) == [
        {"title": "A", "url": "https://ok.example/a", "snippet": "a"},
        {"title": "https://ok.example/b", "url": "https://ok.example/b", "snippet": ""},
    ]


def test_specstream_is_an_exact_egress_host(monkeypatch):
    from openai4s import egress

    monkeypatch.setenv("OPENAI4S_EGRESS", "allowlist")
    assert egress.domain_allowed("api.specstream.ai")
    assert not egress.domain_allowed("child.api.specstream.ai")
    assert not egress.domain_allowed("specstream.ai")


def test_specstream_is_opt_in_and_counted_only_with_a_key(monkeypatch):
    assert "specstream" not in webtools.SEARCH_ENGINES
    assert "specstream" in webtools.AVAILABLE_SEARCH_ENGINES
    assert webtools._engine_hosts("specstream") == ("api.specstream.ai",)
    monkeypatch.delenv("SPECSTREAM_API_KEY", raising=False)
    runnable, _ = webtools._engines_within(("specstream",), ("api.specstream.ai",))
    assert runnable == []
    monkeypatch.setenv("SPECSTREAM_API_KEY", "ss-test")
    runnable, allowed = webtools._engines_within(
        ("specstream",), ("api.specstream.ai",)
    )
    assert runnable == allowed == ["specstream"]
