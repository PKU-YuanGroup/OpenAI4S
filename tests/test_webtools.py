"""Tests for openai4s.webtools — HTML → markdown conversion and the
multi-engine web_search (engine fallback, dedup, scholarly identifier routing).
All search tests stub `_http_get`, so nothing touches the network.

Also keeps regression coverage for the arXiv /abs/ bug where
`_html_to_markdown` dropped the abstract (bare text inside
<blockquote class="abstract">) and the author list (<a> links inside
<div class="authors">), returning only the title plus page boilerplate.
"""

import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

from openai4s import webtools
from openai4s.webtools import _html_to_markdown

pytest.importorskip("bs4")

_FIXTURES = Path(__file__).parent / "fixtures"


def _arxiv_abs_html() -> str:
    # `.html.capture`, not `.html`: CodeQL's JavaScript extractor analyses
    # `.html` files, and this byte-exact arXiv page contains a CDN <script>
    # tag, so as `.html` it raised a permanent `js/functionality-from-
    # untrusted-source` alert on a file that is parsed and never executed.
    # The extension is the whole fix -- the bytes are untouched.
    return (_FIXTURES / "arxiv_abs_2503.06687.html.capture").read_text(encoding="utf-8")


def test_arxiv_abs_keeps_abstract():
    md = _html_to_markdown(_arxiv_abs_html())
    # The full abstract prose, which lives as bare text inside
    # <blockquote class="abstract mathjax">, must survive the conversion.
    assert "Function in natural systems arises from one-dimensional sequences" in md
    assert "unified generative foundation model" in md


def test_arxiv_abs_keeps_authors():
    md = _html_to_markdown(_arxiv_abs_html())
    # Author names sit in <a> links inside <div class="authors">; the old code
    # early-returned on <a> and lost them.
    assert "Gongbo Zhang" in md
    assert "Tao Qin" in md


def test_arxiv_abs_keeps_title():
    md = _html_to_markdown(_arxiv_abs_html())
    assert "UniGenX" in md


def test_blockquote_text_emitted():
    html = "<html><body><blockquote>hello from a quote</blockquote></body></html>"
    assert "hello from a quote" in _html_to_markdown(html)


def test_div_with_only_inline_children_emitted():
    # A div holding a label span + anchors and bare text (the arXiv authors
    # shape) should emit as one line rather than being dropped.
    html = (
        "<html><body><div class='authors'>"
        "<span>Authors:</span>"
        "<a href='/x'>Ada Lovelace</a>, <a href='/y'>Alan Turing</a>"
        "</div></body></html>"
    )
    md = _html_to_markdown(html)
    assert "Ada Lovelace" in md
    assert "Alan Turing" in md


# --------------------------------------------------------------------------- #
#  web_search — offline (stubbed _http_get)
# --------------------------------------------------------------------------- #
def _stub_http(monkeypatch, handler):
    """Replace webtools._http_get with `handler(url) -> body_str`; a handler that
    returns None or raises simulates that endpoint failing/empty. Also disables
    the retry pause so the tests stay fast."""
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
<div class="result web-result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fb">Result B</a>
  <a class="result__snippet">Snippet B.</a>
</div>
"""

_BING_HTML = """
<ol id="b_results">
  <li class="b_algo"><h2><a href="https://bing-hit.example/x">Bing Hit</a></h2>
    <div class="b_caption"><p>Bing snippet text.</p></div></li>
</ol>
"""


def test_search_uses_ddg_first(monkeypatch):
    _stub_http(monkeypatch, lambda url: _DDG_HTML if "duckduckgo" in url else None)
    out = webtools.web_search("CRISPR base editing", num_results=5)
    assert out["source"] == "duckduckgo"
    assert out["count"] == 2
    assert out["results"][0]["url"] == "https://example.org/a"
    assert "Snippet A" in out["results"][0]["snippet"]


def test_search_falls_back_to_bing(monkeypatch):
    # DDG html + lite fail/empty; Bing answers. Mojeek would too but Bing is first.
    def handler(url):
        if "bing.com/search" in url:
            return _BING_HTML
        return None  # every DDG endpoint "fails"

    _stub_http(monkeypatch, handler)
    out = webtools.web_search("some query", num_results=5)
    assert out["source"] == "bing"
    assert out["results"][0]["url"] == "https://bing-hit.example/x"
    assert "Bing snippet" in out["results"][0]["snippet"]


def test_search_dedupes_by_normalized_url(monkeypatch):
    dupes = """
    <div class="result"><a class="result__a" href="https://ex.com/p?utm_source=x">One</a>
      <a class="result__snippet">s1</a></div>
    <div class="result"><a class="result__a" href="https://ex.com/p/">Two</a>
      <a class="result__snippet">s2</a></div>
    """
    _stub_http(monkeypatch, lambda url: dupes if "duckduckgo" in url else None)
    out = webtools.web_search("q", num_results=8)
    # both differ only by a trailing slash + a utm_ param → one result
    assert out["count"] == 1


def test_search_filters_ad_links(monkeypatch):
    ads = """
    <div class="result"><a class="result__a" href="https://duckduckgo.com/y.js?ad=1">Ad</a>
      <a class="result__snippet">promoted</a></div>
    <div class="result"><a class="result__a" href="https://real.example/x">Real</a>
      <a class="result__snippet">organic</a></div>
    """
    _stub_http(monkeypatch, lambda url: ads if "duckduckgo" in url else None)
    out = webtools.web_search("q")
    urls = [r["url"] for r in out["results"]]
    assert urls == ["https://real.example/x"]


def test_search_retries_with_simplified_query(monkeypatch):
    calls = []

    def handler(url):
        calls.append(url)
        # only answer when the query has been simplified (quotes/site: dropped)
        if "duckduckgo" in url and "site%3A" not in url and "%22" not in url:
            return _DDG_HTML
        return None

    _stub_http(monkeypatch, handler)
    out = webtools.web_search('"exact long phrase that fails" site:nope.example')
    assert out["count"] == 2
    assert "simplified query" in out.get("note", "")


def test_search_all_engines_empty(monkeypatch):
    _stub_http(monkeypatch, lambda url: None)
    out = webtools.web_search("nothing matches")
    assert out["count"] == 0
    assert "no results" in out["note"]


def test_search_lite_snippet_parsed(monkeypatch):
    lite = """
    <table>
    <tr><td><a class="result-link" href="https://lite.example/a">Lite A</a></td></tr>
    <tr><td class="result-snippet">Lite snippet A.</td></tr>
    <tr><td><a class="result-link" href="https://lite.example/b">Lite B</a></td></tr>
    <tr><td class="result-snippet">Lite snippet B.</td></tr>
    </table>
    """

    # html + bing fail, lite answers
    def handler(url):
        if "lite.duckduckgo" in url:
            return lite
        return None

    _stub_http(monkeypatch, handler)
    out = webtools.web_search("q")
    assert out["source"] == "duckduckgo-lite"
    assert out["results"][0]["url"] == "https://lite.example/a"
    assert out["results"][0]["snippet"] == "Lite snippet A."


def test_search_routes_doi_to_crossref(monkeypatch):
    payload = json.dumps(
        {
            "message": {
                "title": ["A Great Paper"],
                "author": [{"given": "Ada", "family": "Lovelace"}],
                "container-title": ["Nature"],
                "issued": {"date-parts": [[2021]]},
                "abstract": "<p>We show something important.</p>",
                "URL": "https://doi.org/10.1000/xyz123",
            }
        }
    )
    seen = {}

    def handler(url):
        seen["url"] = url
        if urlparse(url).hostname == "api.crossref.org":
            return payload
        return "<div class='result'></div>"  # engines would return nothing useful

    _stub_http(monkeypatch, handler)
    out = webtools.web_search("please find doi 10.1000/xyz123 for me")
    assert out["source"] == "crossref"
    assert out["results"][0]["title"] == "A Great Paper"
    assert "Ada Lovelace" in out["results"][0]["snippet"]
    assert "Nature" in out["results"][0]["snippet"]
    assert urlparse(seen["url"]).hostname == "api.crossref.org"


def test_search_routes_arxiv_id(monkeypatch):
    atom = """
    <feed><entry>
      <id>http://arxiv.org/abs/2503.06687v1</id>
      <title>UniGenX: a unified model</title>
      <summary>Function in natural systems arises from sequences.</summary>
    </entry></feed>
    """

    def handler(url):
        if urlparse(url).hostname == "export.arxiv.org":
            return atom
        return None

    _stub_http(monkeypatch, handler)
    out = webtools.web_search("arxiv 2503.06687 unigenx")
    assert out["source"] == "arxiv"
    assert "UniGenX" in out["results"][0]["title"]
    assert out["results"][0]["url"].endswith("2503.06687v1")


def test_search_empty_query():
    assert webtools.web_search("   ")["count"] == 0


def test_norm_url_strips_tracking_and_slash():
    a = webtools._norm_url("HTTPS://Example.com/Path/?utm_source=x&gclid=1#frag")
    b = webtools._norm_url("https://example.com/Path")
    assert a == b


def test_search_respects_overall_timeout_budget(monkeypatch):
    # Every engine "hangs" ~8s then fails. With a 20s whole-call budget the sweep
    # must bail once the budget is spent — NOT walk all four engines twice and
    # then run a second (simplified) sweep. A fake monotonic clock keeps this
    # deterministic (no real waiting).
    clock = {"t": 1000.0}
    calls = {"n": 0}
    monkeypatch.setattr(webtools.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(
        webtools.time, "sleep", lambda s=0: clock.__setitem__("t", clock["t"] + s)
    )

    def fake(url, *, timeout=30.0, headers=None, _max_redirects=5):
        calls["n"] += 1
        clock["t"] += 8.0  # this engine stalls for 8s, then fails
        raise RuntimeError("hang")

    monkeypatch.setattr(webtools, "_http_get", fake)
    out = webtools.web_search("some query with no identifier", timeout=20.0)
    assert out["count"] == 0
    # 20s / ~8s per engine → at most 3 requests before the budget forces a bail;
    # the old per-request-only cap would have issued up to 16.
    assert calls["n"] <= 3
