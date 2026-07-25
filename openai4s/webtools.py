"""Networking tools for the agent — web_search + web_fetch.

These give the Code-as-Action agent the same online reach opencode's `websearch`
and `webfetch` tools provide, implemented over the stdlib + (optionally) requests
and BeautifulSoup, which the kernel ships preinstalled. No API key is required:
search walks a chain of keyless engines (DuckDuckGo → Bing → DuckDuckGo lite →
Mojeek) with scholarly fast paths (a DOI resolves via Crossref, an arXiv id via
the arXiv API); fetch downloads a URL and converts HTML to readable markdown/text.

Networking can be globally gated by ``OPENAI4S_ALLOW_NETWORK`` (default on);
the daemon's Customize → Network panel flips this.
"""
from __future__ import annotations

import base64
import hashlib
import html as _html
import ipaddress
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def network_allowed() -> bool:
    return os.environ.get("OPENAI4S_ALLOW_NETWORK", "1") not in ("0", "false", "no")


class NetworkDisabled(RuntimeError):
    pass


class SSRFBlocked(RuntimeError):
    pass


def _require_network() -> None:
    if not network_allowed():
        raise NetworkDisabled(
            "networking is disabled (enable it in Customize → Network / set "
            "OPENAI4S_ALLOW_NETWORK=1)"
        )


def _host_is_private(host: str) -> bool:
    """True if `host` resolves to a loopback / private / link-local (incl. cloud
    metadata 169.254.169.254) / reserved address — anything an agent-controlled
    URL should not be able to reach (SSRF guard)."""
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return False  # let the request itself fail normally
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        ):
            return True
    return False


def _guard_url(url: str) -> None:
    if os.environ.get("OPENAI4S_ALLOW_PRIVATE_FETCH", "") in ("1", "true", "yes"):
        return  # explicit opt-in (e.g. fetching a local model endpoint)
    host = urllib.parse.urlparse(url).hostname or ""
    if _host_is_private(host):
        raise SSRFBlocked(
            f"refusing to fetch a private/loopback/metadata address: {host!r} "
            "(set OPENAI4S_ALLOW_PRIVATE_FETCH=1 to allow)"
        )


# --------------------------------------------------------------------------- #
#  low-level fetch
# --------------------------------------------------------------------------- #
def _http_get(
    url: str,
    *,
    timeout: float = 30.0,
    headers: dict | None = None,
    _max_redirects: int = 5,
) -> tuple[bytes, str, str]:
    """GET a URL, following redirects MANUALLY so the SSRF guard is applied to
    every hop (a public URL can 30x-redirect to a metadata/loopback target).
    Returns (body_bytes, final_url, content_type)."""
    _require_network()
    hdrs = {"User-Agent": _UA, "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    try:
        import requests  # type: ignore
    except ImportError:
        requests = None  # type: ignore

    cur = url
    from openai4s import egress

    for _hop in range(_max_redirects + 1):
        # Host-stamped outbound domain allowlist. No-op unless
        # OPENAI4S_EGRESS=allowlist; applied per hop so a public URL that
        # 30x-redirects to a non-allowlisted domain is still fenced. Checked
        # BEFORE the SSRF guard so a blocked domain short-circuits with a
        # proxy-403 soft error and zero DNS/network. SSRF still guards allowed
        # domains (a permitted host that resolves to a private/metadata IP).
        egress.check_url(cur)
        _guard_url(cur)
        if requests is not None:
            r = requests.get(cur, headers=hdrs, timeout=timeout, allow_redirects=False)
            if r.is_redirect and r.headers.get("Location"):
                cur = urllib.parse.urljoin(cur, r.headers["Location"])
                continue
            return r.content, r.url, r.headers.get("Content-Type", "")
        req = urllib.request.Request(cur, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                return (
                    resp.read(),
                    resp.geturl(),
                    resp.headers.get("Content-Type", ""),
                )
        except urllib.error.HTTPError as e:  # urllib follows redirects itself
            raise e
    raise RuntimeError("too many redirects")


# --------------------------------------------------------------------------- #
#  HTML -> text / markdown
# --------------------------------------------------------------------------- #
def _html_to_markdown(html_text: str) -> str:
    """Best-effort HTML → markdown. Uses BeautifulSoup when available; otherwise
    a compact regex stripper."""
    try:
        from bs4 import BeautifulSoup, NavigableString  # type: ignore
    except ImportError:
        return _strip_tags(html_text)

    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(
        [
            "script",
            "style",
            "noscript",
            "svg",
            "head",
            "nav",
            "footer",
            "form",
            "iframe",
        ]
    ):
        tag.decompose()

    parts: list[str] = []

    # Tags whose presence among a container's descendants means the container is
    # structural (recurse so those blocks format themselves) rather than a leaf
    # holding inline prose (emit its whole text).
    structural = [
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "p",
        "li",
        "pre",
        "blockquote",
        "ul",
        "ol",
        "table",
        "section",
        "article",
    ]

    def _walk(node) -> None:
        name = getattr(node, "name", None)
        if name is None:
            return
        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(name[1])
            parts.append("\n" + "#" * level + " " + node.get_text(" ", strip=True))
        elif name in ("p", "blockquote"):
            txt = node.get_text(" ", strip=True)
            if txt:
                parts.append("\n" + txt)
        elif name == "li":
            txt = node.get_text(" ", strip=True)
            if txt:
                parts.append("- " + txt)
        elif name in ("pre", "code"):
            txt = node.get_text("\n", strip=True)
            if txt:
                parts.append("\n```\n" + txt + "\n```")
        elif name in ("section", "article", "div", "span", "a", "td", "th", "dd", "dt"):
            # Containers/inline wrappers. If they enclose block-level content,
            # recurse so those blocks format themselves; otherwise emit the whole
            # node as one paragraph so bare text and <a> children aren't dropped
            # (e.g. the arXiv abstract <blockquote> text and <div class=authors>
            # author links, which the old code silently discarded).
            if node.find(structural) is not None:
                for child in node.children:
                    if isinstance(child, NavigableString):
                        stray = str(child).strip()
                        if stray:
                            parts.append("\n" + stray)
                    else:
                        _walk(child)
            else:
                txt = node.get_text(" ", strip=True)
                if txt:
                    parts.append("\n" + txt)
        else:
            for child in getattr(node, "children", []):
                _walk(child)

    body = soup.body or soup
    for child in getattr(body, "children", []):
        _walk(child)
    text = "\n".join(p for p in parts if p.strip())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:  # some pages nest oddly — fall back to a flat get_text
        text = soup.get_text("\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _strip_tags(html_text: str) -> str:
    text = re.sub(r"(?is)<(script|style|head|nav|footer|form).*?</\1>", " ", html_text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = _html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def web_fetch(
    url: str, fmt: str = "markdown", timeout: float = 30.0, max_chars: int = 20000
) -> dict:
    """Fetch a URL and return its content. fmt ∈ {markdown, text, html, json}."""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    body, final_url, ctype = _http_get(url, timeout=timeout)
    raw = body.decode("utf-8", errors="replace")
    is_html = ("html" in ctype.lower()) or bool(re.search(r"(?i)<html", raw[:2000]))
    if fmt == "html":
        content = raw
    elif fmt == "json" or "json" in ctype.lower():
        try:
            content = json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
        except Exception:  # noqa: BLE001
            content = raw
    elif is_html:
        content = _html_to_markdown(raw) if fmt == "markdown" else _strip_tags(raw)
    else:
        content = raw
    truncated = len(content) > max_chars
    return {
        "url": final_url,
        "content_type": ctype,
        "truncated": truncated,
        "content": content[:max_chars],
        # The response exactly as it came off the wire, before decoding and
        # before any reformatting. `content` above has been through
        # `decode(errors="replace")` — which maps every invalid byte sequence
        # onto the same U+FFFD — and, for JSON, through a load/dump round trip
        # that discards the original whitespace entirely. Two materially
        # different responses can produce identical `content`, so a hash taken
        # over `content` cannot answer "are these the same bytes we received".
        # These describe the complete body even when `content` is truncated.
        "raw_sha256": hashlib.sha256(body).hexdigest(),
        "raw_bytes": len(body),
    }


# --------------------------------------------------------------------------- #
#  web search (keyless, multi-engine + scholarly fast paths)
# --------------------------------------------------------------------------- #
_RETRY_PAUSE = 1.5  # seconds before the one retry pass (rate-limits are bursty)

_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>]+)", re.I)
_ARXIV_ID_RE = re.compile(r"\b(\d{4}\.\d{4,5})(v\d+)?\b")

# Redirector/ad links no engine result should surface.
_AD_URL_BITS = (
    "duckduckgo.com/y.js",
    "bing.com/aclick",
    "doubleclick.net",
    "googleadservices.com",
)
_TRACKING_PARAMS = {"fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "igshid"}


def _tavily_key() -> str:
    """Tavily API key from the environment. A key saved from the UI (global
    Search settings) is written to this env var — live by the Search settings
    endpoint and at daemon startup — so a UI-entered key works without .env."""
    return os.environ.get("OPENAI4S_TAVILY_API_KEY", "").strip()


def _tavily_search(query: str, num_results: int, timeout: float) -> list[dict]:
    """Authenticated Tavily search (https://tavily.com). Tried FIRST when a key
    is configured (env ``OPENAI4S_TAVILY_API_KEY`` or the UI Search setting),
    because the keyless scrapers below are increasingly bot-blocked /
    rate-limited. Returns [] (silent fall-through to the keyless chain) when the
    key is unset or anything errors, so search never hard-depends on the key.
    stdlib-only POST — no extra deps."""
    key = _tavily_key()
    if not key or timeout <= 0:
        return []
    body = json.dumps(
        {
            "query": query,
            "max_results": max(1, min(int(num_results), 20)),
            "search_depth": "basic",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=min(timeout, 15.0)) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - any failure → fall back to keyless engines
        return []
    out: list[dict] = []
    for r in payload.get("results") or []:
        url = (r.get("url") or "").strip()
        if not url:
            continue
        out.append(
            {
                "title": (r.get("title") or "").strip() or url,
                "url": url,
                "snippet": (r.get("content") or "").strip()[:500],
            }
        )
        if len(out) >= num_results:
            break
    return out


def web_search(query: str, num_results: int = 8, timeout: float = 20.0) -> dict:
    """Multi-engine web search. When ``OPENAI4S_TAVILY_API_KEY`` is set, an
    authenticated Tavily query is tried first (the keyless scrapers get
    bot-blocked). A DOI in the query is answered straight
    from Crossref and an arXiv id from the arXiv API (structured, reliable
    metadata); otherwise the engine chain DuckDuckGo → Bing → DuckDuckGo lite →
    Mojeek is walked until one returns hits, retrying once (and once more with a
    simplified query) if everything comes back empty. Results are deduplicated
    by normalized URL. `timeout` is the budget for the WHOLE call (every engine,
    the retry pass, and the fallback combined), so hanging endpoints can't blow
    past it. Returns {query, count, results:[{title,url,snippet}], source}."""
    _require_network()
    query = (query or "").strip()
    if not query:
        return {"query": query, "count": 0, "results": [], "note": "empty query"}
    # One wall-clock deadline shared across the entire call so a set of stalling
    # engines can never run the caller ~10x past `timeout` (each engine and the
    # retry/simplified passes draw down the same remaining budget).
    deadline = time.monotonic() + max(timeout, 1.0)
    routed = _identifier_route(query, deadline)
    if routed:
        source, results = routed
        return {
            "query": query,
            "count": len(results),
            "results": results[:num_results],
            "source": source,
        }
    # Authenticated engine first — the keyless scrapers below get bot-blocked;
    # silently falls through when the key is unset or the call errors.
    if _time_left(deadline) > 0:
        tav = _tavily_search(query, num_results, _req_timeout(deadline))
        if tav:
            return {
                "query": query,
                "count": len(tav),
                "results": tav[:num_results],
                "source": "tavily",
            }
    results, source = _engine_sweep(query, num_results, deadline)
    note = None
    if not results and _time_left(deadline) > 0:
        simplified = _simplify_query(query)
        if simplified and simplified.lower() != query.lower():
            results, source = _engine_sweep(simplified, num_results, deadline)
            if results:
                note = (
                    f"no hits for the full query — showing results for the "
                    f"simplified query {simplified!r}"
                )
    out: dict = {"query": query, "count": len(results), "results": results}
    if source:
        out["source"] = source
    if note:
        out["note"] = note
    if not results:
        out["note"] = (
            "no results from any engine (DuckDuckGo/Bing/Mojeek) — "
            "they may be rate-limiting; retry shortly with different "
            "terms, or use host.web_fetch on a known URL / a specific "
            "database API instead."
        )
    return out


# ---- budget helpers -------------------------------------------------------- #
def _time_left(deadline: float) -> float:
    return deadline - time.monotonic()


def _req_timeout(deadline: float) -> float:
    """Per-request cap: the smaller of the remaining call budget and 12s (so one
    stalled engine can't eat the whole budget on its own)."""
    return max(0.0, min(_time_left(deadline), 12.0))


# ---- scholarly identifier routing ----------------------------------------- #
def _identifier_route(query: str, deadline: float) -> tuple[str, list[dict]] | None:
    """If the query carries a scholarly identifier, resolve it via the matching
    structured API (far more reliable than scraping an engine for it):
    DOI → Crossref, arXiv id (when 'arxiv' appears in the query) → arXiv API.
    Returns (source, results) or None to fall through to the engines."""
    m = _DOI_RE.search(query)
    if m and _req_timeout(deadline) > 0:
        results = _crossref_lookup(m.group(1).rstrip(".,;)"), _req_timeout(deadline))
        if results:
            return "crossref", results
    if "arxiv" in query.lower():
        m = _ARXIV_ID_RE.search(query)
        if m and _req_timeout(deadline) > 0:
            results = _arxiv_lookup(
                m.group(1) + (m.group(2) or ""), _req_timeout(deadline)
            )
            if results:
                return "arxiv", results
    return None


def _crossref_lookup(doi: str, timeout: float) -> list[dict]:
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi)
    try:
        body, _f, _c = _http_get(url, timeout=timeout)
        msg = json.loads(body.decode("utf-8", errors="replace")).get("message") or {}
    except Exception:  # noqa: BLE001
        return []
    title = " ".join(msg.get("title") or []).strip()
    if not title:
        return []
    authors = ", ".join(
        f"{a.get('given', '')} {a.get('family', '')}".strip()
        for a in (msg.get("author") or [])[:6]
        if isinstance(a, dict)
    )
    container = " ".join(msg.get("container-title") or []).strip()
    year = ""
    try:
        year = str((msg.get("issued") or {}).get("date-parts", [[""]])[0][0] or "")
    except Exception:  # noqa: BLE001
        pass
    abstract = _strip_tags(msg.get("abstract") or "")
    head = " — ".join(b for b in (authors, container, year) if b)
    snippet = (head + (". " if head and abstract else "") + abstract)[:500]
    return [
        {
            "title": title,
            "url": msg.get("URL") or f"https://doi.org/{doi}",
            "snippet": snippet,
        }
    ]


def _arxiv_lookup(arxiv_id: str, timeout: float) -> list[dict]:
    url = "https://export.arxiv.org/api/query?id_list=" + urllib.parse.quote(arxiv_id)
    try:
        body, _f, _c = _http_get(url, timeout=timeout)
    except Exception:  # noqa: BLE001
        return []
    raw = body.decode("utf-8", errors="replace")
    out: list[dict] = []
    for entry in re.finditer(r"<entry>(.*?)</entry>", raw, re.S):
        e = entry.group(1)

        def _tag(name: str, e: str = e) -> str:
            m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", e, re.S)
            if not m:
                return ""
            return _html.unescape(re.sub(r"\s+", " ", m.group(1)).strip())

        title = _tag("title")
        # the API reports an unknown id as an <entry> titled "Error"
        if not title or title.lower() == "error":
            continue
        link_m = re.search(r"<id>\s*(https?://\S+?)\s*</id>", e)
        out.append(
            {
                "title": title,
                "url": link_m.group(1)
                if link_m
                else f"https://arxiv.org/abs/{arxiv_id}",
                "snippet": _tag("summary")[:500],
            }
        )
    return out


# ---- engine chain ---------------------------------------------------------- #
def _engine_sweep(
    query: str, num_results: int, deadline: float
) -> tuple[list[dict], str | None]:
    """Walk the engine chain until one returns hits; one whole-chain retry after
    a short pause (the keyless endpoints rate-limit in bursts). Every engine and
    the retry pause draw from the shared `deadline`, so the sweep stops as soon
    as the caller's overall budget is spent rather than plowing on."""
    for attempt in (0, 1):
        for name, fn in _ENGINES:
            eng_timeout = _req_timeout(deadline)
            if eng_timeout <= 0:
                return [], None
            try:
                results = _dedupe(fn(query, num_results, eng_timeout))
            except Exception:  # noqa: BLE001
                results = []
            if results:
                return results[:num_results], name
        # retry pass only when the pause itself still fits in the budget
        if attempt == 0:
            if _time_left(deadline) <= _RETRY_PAUSE:
                break
            time.sleep(_RETRY_PAUSE)
    return [], None


def _simplify_query(query: str) -> str:
    """Zero-hit fallback: drop exact-phrase quotes and site: filters, keep the
    first 8 tokens — over-constrained queries are the usual cause of no hits."""
    q = re.sub(r"[\"“”]", " ", query)
    q = re.sub(r"\bsite:\S+", " ", q)
    toks = [t for t in re.split(r"\s+", q) if t]
    return " ".join(toks[:8]).strip()


def _norm_url(url: str) -> str:
    """Canonical form for dedup: lowercase scheme/host, no fragment, no
    utm_*/click-id tracking params, no trailing slash."""
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    keep = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    return urllib.parse.urlunsplit(
        (
            (p.scheme or "https").lower(),
            p.netloc.lower(),
            p.path.rstrip("/") or "/",
            urllib.parse.urlencode(keep),
            "",
        )
    )


def _dedupe(results: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for r in results or []:
        url = (r.get("url") or "").strip()
        if not url or any(bit in url for bit in _AD_URL_BITS):
            continue
        key = _norm_url(url)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _ddg_unwrap(href: str) -> str:
    # DuckDuckGo wraps hits as /l/?uddg=<encoded target>
    if "uddg=" in href:
        try:
            q = urllib.parse.urlparse(href).query
            target = urllib.parse.parse_qs(q).get("uddg", [None])[0]
            if target:
                return urllib.parse.unquote(target)
        except Exception:  # noqa: BLE001
            pass
    if href.startswith("//"):
        return "https:" + href
    return href


def _bing_unwrap(href: str) -> str:
    # Bing sometimes wraps organic hits as /ck/a?...&u=a1<base64url target>
    if "bing.com/ck/" not in href:
        return href
    try:
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query)
        u = (q.get("u") or [""])[0]
        if u.startswith("a1"):
            pad = "=" * (-len(u[2:]) % 4)
            return base64.urlsafe_b64decode(u[2:] + pad).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        pass
    return href


def _ddg_html(query: str, num_results: int, timeout: float) -> list[dict]:
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    try:
        body, _final, _ct = _http_get(
            url, timeout=timeout, headers={"Referer": "https://duckduckgo.com/"}
        )
    except Exception:  # noqa: BLE001
        return []
    raw = body.decode("utf-8", errors="replace")
    out: list[dict] = []
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(raw, "html.parser")
        for res in soup.select(".result, .web-result"):
            a = res.select_one("a.result__a")
            if not a:
                continue
            snip_el = res.select_one(".result__snippet")
            out.append(
                {
                    "title": a.get_text(" ", strip=True),
                    "url": _ddg_unwrap(a.get("href", "")),
                    "snippet": snip_el.get_text(" ", strip=True) if snip_el else "",
                }
            )
            if len(out) >= num_results:
                break
    except ImportError:
        for m in re.finditer(r'result__a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', raw, re.S):
            href, title = m.group(1), _strip_tags(m.group(2))
            out.append({"title": title, "url": _ddg_unwrap(href), "snippet": ""})
            if len(out) >= num_results:
                break
    return out


def _bing_html(query: str, num_results: int, timeout: float) -> list[dict]:
    url = (
        "https://www.bing.com/search?q="
        + urllib.parse.quote(query)
        + "&count="
        + str(min(max(num_results, 1), 30))
    )
    try:
        body, _f, _c = _http_get(
            url, timeout=timeout, headers={"Accept-Language": "en-US,en;q=0.8"}
        )
    except Exception:  # noqa: BLE001
        return []
    raw = body.decode("utf-8", errors="replace")
    out: list[dict] = []
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(raw, "html.parser")
        for li in soup.select("li.b_algo"):
            a = li.select_one("h2 a")
            if not a or not a.get("href"):
                continue
            snip = li.select_one(".b_caption p") or li.select_one("p")
            out.append(
                {
                    "title": a.get_text(" ", strip=True),
                    "url": _bing_unwrap(a["href"]),
                    "snippet": snip.get_text(" ", strip=True) if snip else "",
                }
            )
            if len(out) >= num_results:
                break
    except ImportError:
        for m in re.finditer(
            r'<li class="b_algo".*?<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>'
            r"(.*?)</a></h2>(.*?)</li>",
            raw,
            re.S,
        ):
            snip_m = re.search(r"<p[^>]*>(.*?)</p>", m.group(3), re.S)
            out.append(
                {
                    "title": _strip_tags(m.group(2)).strip(),
                    "url": _bing_unwrap(_html.unescape(m.group(1))),
                    "snippet": _strip_tags(snip_m.group(1)).strip() if snip_m else "",
                }
            )
            if len(out) >= num_results:
                break
    return out


def _ddg_lite(query: str, num_results: int, timeout: float) -> list[dict]:
    url = "https://lite.duckduckgo.com/lite/?q=" + urllib.parse.quote(query)
    try:
        body, _f, _c = _http_get(
            url, timeout=timeout, headers={"Referer": "https://duckduckgo.com/"}
        )
    except Exception:  # noqa: BLE001
        return []
    raw = body.decode("utf-8", errors="replace")
    out: list[dict] = []
    anchors = list(
        re.finditer(
            r"<a[^>]+class=['\"]result-link['\"][^>]+href=['\"]([^'\"]+)['\"][^>]*>"
            r"(.*?)</a>",
            raw,
            re.S,
        )
    )
    for i, m in enumerate(anchors):
        # the snippet <td class="result-snippet"> sits between this link row
        # and the next result's link row
        seg_end = anchors[i + 1].start() if i + 1 < len(anchors) else len(raw)
        seg = raw[m.end() : seg_end]
        snip_m = re.search(
            r"<td[^>]*class=['\"]result-snippet['\"][^>]*>(.*?)</td>", seg, re.S
        )
        out.append(
            {
                "title": _strip_tags(m.group(2)).strip(),
                "url": _ddg_unwrap(_html.unescape(m.group(1))),
                "snippet": _strip_tags(snip_m.group(1)).strip() if snip_m else "",
            }
        )
        if len(out) >= num_results:
            break
    return out


def _mojeek_html(query: str, num_results: int, timeout: float) -> list[dict]:
    url = "https://www.mojeek.com/search?q=" + urllib.parse.quote(query)
    try:
        body, _f, _c = _http_get(url, timeout=timeout)
    except Exception:  # noqa: BLE001
        return []
    raw = body.decode("utf-8", errors="replace")
    out: list[dict] = []
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(raw, "html.parser")
        for li in soup.select("ul.results-standard li"):
            a = li.select_one("h2 a") or li.select_one("a.title")
            if not a or not a.get("href"):
                continue
            snip = li.select_one("p.s")
            out.append(
                {
                    "title": a.get_text(" ", strip=True),
                    "url": a["href"],
                    "snippet": snip.get_text(" ", strip=True) if snip else "",
                }
            )
            if len(out) >= num_results:
                break
    except ImportError:
        for m in re.finditer(
            r'<h2><a[^>]+href="([^"]+)"[^>]*>(.*?)</a></h2>\s*'
            r'(?:<p class="s">(.*?)</p>)?',
            raw,
            re.S,
        ):
            out.append(
                {
                    "title": _strip_tags(m.group(2)).strip(),
                    "url": _html.unescape(m.group(1)),
                    "snippet": _strip_tags(m.group(3) or "").strip(),
                }
            )
            if len(out) >= num_results:
                break
    return out


# Ordered by result quality (snippet richness) and rate-limit tolerance.
_ENGINES: tuple[tuple[str, object], ...] = (
    ("duckduckgo", _ddg_html),
    ("bing", _bing_html),
    ("duckduckgo-lite", _ddg_lite),
    ("mojeek", _mojeek_html),
)
