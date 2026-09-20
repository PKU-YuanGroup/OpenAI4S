"""
Literature-review helpers. Auto-loaded into the python kernel by the host
when the skill loads:

 verify_dois, crossref_lookup, search_openalex, expand_citations,
 extract_dois, style_pass, screen_passages, check_claims

Module top level is definition-only (functions, imports, literal constants) so
the sidecar AST gate accepts it; everything that touches the network or the
host runtime happens inside a function body.

Ported verbatim from openai4s's bundled `literature-review/kernel.py`
(origin=openai4s) — see /Users/.../.openai4s/skills/literature-review/.
Network access goes through `host.web_fetch`, never `urllib.request`: a
request made from inside a cell with urllib is subject to neither the egress
allowlist nor the SSRF guard. `urllib.parse` is still used, for quoting only.
"""

import difflib
import json
import re
import time
import unicodedata
import urllib.parse

DOI_PATTERN = r"10\.\d{4,9}/[^\s\"'`\]\}—–&|]+"


def lr_sdk():
    """Rebind-proof SDK handle — see pdf-explore/kernel.py:pdf_sdk."""
    import host

    return host


def litrev_contact() -> str | None:
    """User contact email for polite-pool API headers; None if unavailable/declined."""
    # get_user_email returns the address as a plain str and raises
    # host.ContactEmailUnavailable (ContactEmailDeclined is a subclass)
    # when there is no address. The bare `except Exception` deliberately
    # also covers host-call failures (this helper runs in the analysis
    # kernel, where the host refuses the call outright): the polite-pool
    # `mailto:` UA suffix is best-effort, never worth failing a fetch.
    try:
        r = lr_sdk().get_user_email()
    except Exception:
        return None
    return (r or None) if isinstance(r, str) else None


def litrev_get(url: str, timeout: float = 15) -> dict | None:
    """GET `url` and JSON-decode. One 2s retry on HTTP 429; None on any error."""
    c = litrev_contact()
    ua = "ClaudeScience-literature-review/1.0" + (f" (mailto:{c})" if c else "")
    ua = ua.encode("ascii", "ignore").decode("ascii")
    for attempt in (0, 1):
        try:
            # Through the Host, not urllib. A request made with urllib from
            # inside a cell is subject to neither the egress allowlist nor the
            # SSRF guard, so this skill's traffic used to go around the fence
            # the product builds for exactly this. `user_agent` exists on the
            # Host call so the polite-pool identity below survives the move.
            response = lr_sdk().web_fetch(
                url, format="json", timeout=timeout, user_agent=ua
            )
            if response.get("error"):
                # 429 arrives as a soft error string; the one retry is still
                # worth taking before giving up on a rate limit.
                if "429" in str(response["error"]) and attempt == 0:
                    time.sleep(2)
                    continue
                return None
            return json.loads(response.get("content") or "null")
        except Exception:
            return None
    return None


def quote_doi_path(doi: str) -> str:
    """URL-encode a DOI path; unquote each segment first so a pre-encoded
    %28 stays single-encoded (caller may pass either form)."""
    return "/".join(
        urllib.parse.quote(urllib.parse.unquote(seg), safe="") for seg in doi.split("/")
    )


def crossref_year(m: dict) -> int | None:
    """Safely extract the publication year from a CrossRef `message` record."""
    dp = (m.get("published") or {}).get("date-parts") or [[None]]
    return (dp[0] or [None])[0]


def litrev_head(url: str, timeout: float = 10) -> int | None:
    """HEAD `url` WITHOUT following redirects; return the origin server's own
    status (so doi.org returns 302 for a registered DOI and 404 for an
    unregistered one — not the publisher's status). One 2s retry on 429.
    Returns None only when no status could be obtained (connection/timeout)."""
    c = litrev_contact()
    ua = (
        ("ClaudeScience-literature-review/1.0" + (f" (mailto:{c})" if c else ""))
        .encode("ascii", "ignore")
        .decode("ascii")
    )

    for attempt in (0, 1):
        try:
            # `host.web_fetch(method="HEAD")` deliberately does not follow
            # redirects, which is the whole point here: doi.org's own 302 means
            # the DOI is registered, while the publisher's status behind it may
            # be a 403 paywall for a DOI that certainly exists.
            probe = lr_sdk().web_fetch(
                url, method="HEAD", timeout=timeout, user_agent=ua
            )
            if probe.get("error"):
                return None
            status = int(probe.get("status") or 0)
            if status == 429 and attempt == 0:
                time.sleep(2)
                continue
            # 0 means no status could be obtained at all -- reported as None,
            # the same "unknown" this function has always returned for a dead
            # connection, and distinct from a 404.
            return status or None
        except Exception:
            return None
    return None


def verify_dois(dois: list[str]) -> dict[str, dict]:
    """Resolve each DOI against CrossRef, with a doi.org HEAD fallback for
    DataCite/mEDRA/arXiv DOIs. Returns {doi: {ok, title?, year?, journal?,
    retracted?, registry?, error?}} where:
    ok=True — resolves (CrossRef hit, or doi.org 2xx/3xx);
    ok=False — does NOT resolve (doi.org 404; likely fabricated or typo);
    ok=None — could not be verified (network/transient/5xx); do not flag as
    fabricated.
    `retracted` is True/False only on a CrossRef hit; None when the registry
    is non-CrossRef or the lookup was unverified."""
    out: dict[str, dict] = {}
    for d in dois:
        d = d.strip()
        # No registration agency uses `.`/`..`/empty path segments in a DOI
        # suffix; reject up-front so a server/CDN that dot-segment-normalizes
        # can't make a fabricated identifier appear to resolve. Decode the WHOLE
        # string first then split, so encoded `..` (`%2E%2E`) and encoded
        # slashes carrying `..` (`a%2F..%2Fb`) both surface as a `..` segment.
        segs = urllib.parse.unquote(d).split("/")
        if any(seg in ("", ".", "..") for seg in segs[1:]):
            out[d] = {"ok": False, "error": "dot-segment in DOI"}
            continue
        enc = quote_doi_path(d)
        j = litrev_get(f"https://api.crossref.org/works/{enc}")
        time.sleep(0.06)
        if j and "message" in j:
            m = j["message"]
            title = (m.get("title") or [""])[0]
            upd = [u.get("type", "") for u in (m.get("update-to") or [])]
            retracted = (
                any("retract" in t.lower() for t in upd)
                or str(m.get("subtype") or "").lower() == "retraction"
                or title.upper().startswith("RETRACTED")
            )
            out[d] = {
                "ok": True,
                "title": title,
                "year": crossref_year(m),
                "journal": (m.get("container-title") or [""])[0],
                "retracted": retracted,
                "registry": "crossref",
            }
            continue
        # CrossRef miss OR transient — doi.org is the authoritative resolver
        # across all registration agencies, so its verdict decides ok.
        code = litrev_head(f"https://doi.org/{enc}")
        if code is not None and 200 <= code < 400:
            out[d] = {"ok": True, "registry": "non-crossref", "retracted": None}
        elif code == 404:
            out[d] = {"ok": False}
        else:
            out[d] = {"ok": None, "error": "unverified (network)", "retracted": None}
    return out


def crossref_lookup(ref_string: str) -> dict | None:
    """Find a DOI from a free-text citation (author/title/year). Returns the
    top CrossRef match as {doi, title, year, score} or None. Use when you have
    a citation's details but not its DOI — this is the alternative to guessing."""
    q = urllib.parse.quote(ref_string)
    j = litrev_get(f"https://api.crossref.org/works?query.bibliographic={q}&rows=1")
    items = (j or {}).get("message", {}).get("items", [])
    if not items:
        return None
    m = items[0]
    return {
        "doi": m.get("DOI"),
        "title": (m.get("title") or [""])[0],
        "year": crossref_year(m),
        "score": m.get("score"),
    }


def search_openalex(query: str, n: int = 10, filters: str = "") -> list[dict]:
    """Search OpenAlex (open scholarly index, ~250M works). Returns up to n
    hits as [{doi, title, year, cited_by, venue, oa_url}]. `filters` is an
    OpenAlex filter string, e.g. 'from_publication_date:2022-01-01'."""
    q = urllib.parse.quote(query)
    flt = f"&filter={filters}" if filters else ""
    c = litrev_contact()
    mailto = f"&mailto={urllib.parse.quote(c)}" if c else ""
    j = litrev_get(
        f"https://api.openalex.org/works?search={q}&per-page={min(n, 25)}"
        f"&sort=cited_by_count:desc{flt}{mailto}"
    )
    out = []
    for w in (j or {}).get("results", [])[:n]:
        loc = w.get("primary_location") or {}
        venue = ((loc.get("source") or {}) or {}).get("display_name")
        out.append(
            {
                "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
                "title": w.get("title"),
                "year": w.get("publication_year"),
                "cited_by": w.get("cited_by_count"),
                "venue": venue,
                "oa_url": (w.get("open_access") or {}).get("oa_url"),
            }
        )
    return out


def expand_citations(doi: str, n_backward: int = 50, n_forward: int = 15) -> dict:
    """One citation-graph step in both directions via OpenAlex.
    `references` is the backward step — the paper's own bibliography (outgoing
    citations), via `filter=cited_by:<id>`, sorted most-cited first.
    `cited_by` is the forward step — papers that cite this one (incoming
    citations), via `filter=cites:<id>`. Each entry is {doi, title, year,
    cited_by}. Three OpenAlex requests total; returns empty lists when the DOI
    is unknown to OpenAlex or the list endpoint is rate-limited."""
    c = litrev_contact()
    mailto = f"&mailto={urllib.parse.quote(c)}" if c else ""
    enc = quote_doi_path(doi)
    work = litrev_get(f"https://api.openalex.org/works/doi:{enc}?select=id{mailto}")
    work_id = ((work or {}).get("id") or "").rsplit("/", 1)[-1]
    if not work_id:
        return {"references": [], "cited_by": []}

    def _rows(results: list) -> list[dict]:
        out = []
        for w in results or []:
            out.append(
                {
                    "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
                    "title": w.get("title"),
                    "year": w.get("publication_year"),
                    "cited_by": w.get("cited_by_count"),
                }
            )
        return out

    def _list(filter_expr: str, n: int) -> list[dict]:
        j = litrev_get(
            f"https://api.openalex.org/works?filter={filter_expr}"
            f"&select=doi,title,publication_year,cited_by_count"
            f"&sort=cited_by_count:desc&per-page={min(n, 100)}{mailto}"
        )
        return _rows((j or {}).get("results", []))

    return {
        "references": _list(f"cited_by:{work_id}", n_backward),
        "cited_by": _list(f"cites:{work_id}", n_forward),
    }


def html_decode(s: str) -> str:
    """Minimal HTML entity decode for DOI extraction (lt/gt/amp/nbsp/slash)."""
    for a, b in (
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&amp;", "&"),
        ("&nbsp;", " "),
        ("&#x2F;", "/"),
        ("&#47;", "/"),
    ):
        s = s.replace(a, b)
    return s


def extract_dois(text: str) -> list[str]:
    """Pull every DOI-looking string from `text` (for feeding to verify_dois).
    HTML-decoded, balanced-paren SICI, `</`-truncated, markdown/punct-stripped."""
    decoded = html_decode(text)
    out: set[str] = set()
    for m in re.findall(DOI_PATTERN, decoded):
        d = m.split("</")[0]
        if d.count("<") != d.count(">"):
            d = d.split("<")[0]
        # Strip trailing markdown/punct chars. The old regex `(?:\*\*|__|[...])+$`
        # was ambiguous (`*`/`_` sat in both the alternation and the class) and
        # backtracked catastrophically on long `**` runs; str.rstrip over the same
        # character set is exactly equivalent and unconditionally linear.
        d = d.rstrip("_]*>`,;:")
        if d.endswith("."):
            d = d[:-1]
        while d.endswith(")") and d.count("(") < d.count(")"):
            d = d[:-1]
        if len(d) > 8:
            out.add(d)
    return sorted(out)


def style_pass(draft: str, model: str | None = None) -> dict:
    """Deterministic prose lint. Returns {ok, issues:[{code,note}]} where each
    code is one of EMDASH/HONEST/PROCNOTE/PARENDOI/LONGHEAD/FLATSTRUCT.

    No LLM call by design: drafts routinely quote web/paper-retrieved
    third-party text, and a free-text fix hint the agent is instructed to
    apply would be an indirect-injection channel. The deterministic regex
    codes are the load-bearing checks. `model` is accepted and ignored."""
    del model
    issues: list[dict] = []
    w = len(draft.split()) or 1
    em = draft.count("—")
    if em > 6 and 1000 * em / w > 8:
        issues.append(
            {
                "code": "EMDASH",
                "note": f"{em} em-dashes ({1000*em/w:.0f}/1kw); replace most with comma/colon/period, keep at most one per paragraph",
            }
        )
    m = re.search(
        r"\b(the\s+|an?\s+)?honest(ly)?\s+(answer|summary|read|reading|look|perspective|assessment|appraisal|take|view)\b",
        draft,
        re.I,
    )
    if m:
        issues.append(
            {
                "code": "HONEST",
                "note": f"{m.group(0)!r}: drop the framing, write the sentence it was guarding",
            }
        )
    if re.search(
        r"(DOIs?\s+(were\s+)?verif|verified against (CrossRef|PubMed)|no retraction|current as of)",
        draft,
        re.I,
    ):
        issues.append(
            {"code": "PROCNOTE", "note": "process-narration line present; delete it"}
        )
    if re.search(r"\]\(https://doi\.org/[^)\s]*\([^)\s]*\)", draft):
        issues.append(
            {
                "code": "PARENDOI",
                "note": "DOI href contains literal ; URL-encode as %28 %29 so the markdown link survives simpler renderers",
            }
        )
    h2 = [ln for ln in draft.split("\n") if ln.startswith("## ")]
    long_h2 = [ln for ln in h2 if len(ln.split()) > 8]
    if len(long_h2) >= 2:
        issues.append(
            {
                "code": "LONGHEAD",
                "note": f"{len(long_h2)} headings read as sentences; shorten to <=6-word noun phrases",
            }
        )
    if len(h2) >= 7 and not any(ln.startswith("### ") for ln in draft.split("\n")):
        issues.append(
            {
                "code": "FLATSTRUCT",
                "note": f"{len(h2)} top-level sections, no subsections; group related ## under a parent and demote to ###",
            }
        )
    return {"ok": len(issues) == 0, "issues": issues}


# --- Semantic evidence check (experimental; host.judge / literature_check) ---

FUZZY_MIN = 0.9
CLAIM_TEMPLATE_VERSION = "1"
SCREEN_TEMPLATE_VERSION = "1"
DEFAULT_WINDOW = 400

_STRAIGHT_QUOTES = {
    "\u201c": '"',
    "\u201d": '"',
    "\u201e": '"',
    "\u201f": '"',
    "\u00ab": '"',
    "\u00bb": '"',
    "\u2018": "'",
    "\u2019": "'",
    "\u201a": "'",
    "\u201b": "'",
}
_HYPHENS = (
    "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\u2043" "\ufe58\ufe63\uff0d\u00ad\u2011"
)
_HYPHEN_TABLE = str.maketrans({ch: "-" for ch in _HYPHENS})
_KEYWORD_RE = re.compile(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]+")
_STOP = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "and",
        "or",
        "to",
        "in",
        "on",
        "for",
        "with",
        "is",
        "are",
        "was",
        "were",
        "be",
        "this",
        "that",
        "it",
        "as",
        "by",
        "from",
        "at",
        "we",
        "our",
    }
)
_QUANTITY_RE = re.compile(
    r"(?<![A-Za-z0-9.])"
    r"(?P<num>[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?|"
    r"[+-]?\.\d+(?:[eE][+-]?\d+)?)"
    r"(?:\s*(?P<unit>%|percent|pct|[A-Za-zµμ°Å][A-Za-zµμ°Å/\-]{0,12}))?",
)
_YEAR_MIN = 1900
_YEAR_MAX = 2100


def normalize_literature_text(text: str) -> str:
    """Unicode NFKC, fold quotes/hyphens, collapse whitespace."""

    folded, _index_map = _fold_map(text)
    return folded


def _fold_map(text: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    index_map: list[int] = []
    prev_space = False
    source = str(text or "")
    for orig_i, raw in enumerate(source):
        mapped = _STRAIGHT_QUOTES.get(raw, raw)
        mapped = mapped.translate(_HYPHEN_TABLE)
        for ch in unicodedata.normalize("NFKC", mapped):
            if ch.isspace():
                if prev_space or not chars:
                    continue
                chars.append(" ")
                index_map.append(orig_i)
                prev_space = True
                continue
            prev_space = False
            chars.append(ch)
            index_map.append(orig_i)
    if chars and chars[-1] == " ":
        chars.pop()
        index_map.pop()
    return "".join(chars), index_map


def _span_from_map(index_map: list[int], start: int, end: int) -> dict[str, int] | None:
    if start < 0 or end <= start or end > len(index_map):
        return None
    orig_start = index_map[start]
    orig_end = index_map[end - 1] + 1
    return {"start": int(orig_start), "end": int(orig_end)}


def _fuzzy_locate(
    haystack: str, needle: str, min_ratio: float
) -> tuple[float, int, int] | None:
    n = len(needle)
    h = len(haystack)
    if n == 0 or h == 0:
        return None
    sm = difflib.SequenceMatcher(None, haystack, needle, autojunk=False)
    block = sm.find_longest_match(0, h, 0, n)
    if block.size <= 0:
        return None
    start = max(0, block.a - block.b)
    best: tuple[float, int, int] | None = None
    lo = max(0, start - max(1, n // 5))
    hi = min(h, start + max(1, n // 5) + 1)
    for s in range(lo, hi):
        e = min(h, s + n)
        if e - s < max(1, int(n * 0.8)):
            continue
        ratio = difflib.SequenceMatcher(
            None, haystack[s:e], needle, autojunk=False
        ).ratio()
        if best is None or ratio > best[0]:
            best = (ratio, s, e)
    if best is None or best[0] < min_ratio:
        return None
    return best


def _keywords(text: str) -> list[str]:
    out: list[str] = []
    for match in _KEYWORD_RE.finditer(text):
        token = match.group(0)
        folded = token.casefold() if token.isascii() else token
        if folded in _STOP:
            continue
        out.append(folded)
    return out


def _keyword_window(
    haystack: str, claim: str, window: int
) -> tuple[float, int, int] | None:
    tokens = _keywords(claim)
    if not tokens or not haystack:
        return None
    win = max(8, int(window))
    step = max(1, win // 4)
    best: tuple[float, int, int] | None = None
    hay_fold = haystack.casefold()
    length = len(haystack)
    for start in range(0, max(1, length - win + 1), step):
        end = min(length, start + win)
        chunk = hay_fold[start:end]
        hits = 0
        for token in tokens:
            needle = token.casefold() if token.isascii() else token
            if needle and needle in chunk:
                hits += 1
        score = hits / float(len(tokens))
        if best is None or score > best[0]:
            best = (score, start, end)
    if length <= win:
        chunk = hay_fold
        hits = sum(
            1
            for token in tokens
            if (token.casefold() if token.isascii() else token) in chunk
        )
        score = hits / float(len(tokens))
        best = (score, 0, length)
    if best is None or best[0] <= 0:
        return None
    return best


def locate_claim(
    source: str,
    *,
    quote: str | None = None,
    claim: str = "",
    window: int = DEFAULT_WINDOW,
) -> dict | None:
    """Locate a quote or claim keywords in ``source``. None if not found.

    Returns ``{span, match, section}`` where ``match`` is ``exact`` or
    ``fuzzy:<score>`` and ``section`` is the original-text window.
    """

    folded, index_map = _fold_map(source)
    if not folded:
        return None
    quote_text = str(quote).strip() if quote else ""
    if quote_text:
        needle, _ = _fold_map(quote_text)
        if not needle:
            return None
        found = folded.find(needle)
        if found >= 0:
            span = _span_from_map(index_map, found, found + len(needle))
            if span is None:
                return None
            return {
                "span": span,
                "match": "exact",
                "section": source[span["start"] : span["end"]],
            }
        fuzzy = _fuzzy_locate(folded, needle, FUZZY_MIN)
        if fuzzy is None:
            return None
        ratio, start, end = fuzzy
        span = _span_from_map(index_map, start, end)
        if span is None:
            return None
        return {
            "span": span,
            "match": f"fuzzy:{ratio:.4f}",
            "section": source[span["start"] : span["end"]],
        }
    claim_fold, _ = _fold_map(claim)
    located = _keyword_window(folded, claim_fold, window)
    if located is None:
        return None
    _score, start, end = located
    span = _span_from_map(index_map, start, end)
    if span is None:
        return None
    pad = max(0, int(window) // 8)
    orig_start = max(0, span["start"] - pad)
    orig_end = min(len(source), span["end"] + pad)
    return {
        "span": {"start": orig_start, "end": orig_end},
        "match": "exact",
        "section": source[orig_start:orig_end],
    }


def _norm_unit(unit: str) -> str:
    text = (unit or "").strip().lower()
    text = text.replace("µ", "u").replace("μ", "u")
    text = text.replace("percentage", "%").replace("percent", "%").replace("pct", "%")
    text = re.sub(r"\s+", "", text)
    return text


def extract_quantities(text: str) -> list[dict]:
    """Pull ``{value, unit, raw}`` number+unit pairs from ``text``."""

    folded, _ = _fold_map(text)
    out: list[dict] = []
    seen: set[tuple[float, str]] = set()
    for match in _QUANTITY_RE.finditer(folded):
        raw_num = match.group("num") or ""
        unit = _norm_unit(match.group("unit") or "")
        compact = raw_num.replace(",", "")
        try:
            value = float(compact)
        except ValueError:
            continue
        if (
            not unit
            and compact.isdigit()
            and _YEAR_MIN <= int(compact) <= _YEAR_MAX
            and len(compact) == 4
        ):
            continue
        key = (value, unit)
        if key in seen:
            continue
        seen.add(key)
        out.append({"value": value, "unit": unit, "raw": match.group(0)})
    return out


def compare_quantities(claim: str, section: str) -> dict:
    """Compare number+unit pairs. ``mismatch`` is True on missing value or unit clash."""

    claim_qs = extract_quantities(claim)
    section_qs = extract_quantities(section)
    missing: list[dict] = []
    unit_conflicts: list[dict] = []
    for item in claim_qs:
        value = float(item["value"])
        unit = str(item["unit"])
        matched_value = [
            other for other in section_qs if _values_close(value, float(other["value"]))
        ]
        if not matched_value:
            missing.append(item)
            continue
        if unit:
            same_unit = [other for other in matched_value if other["unit"] == unit]
            if not same_unit:
                unit_conflicts.append(
                    {"claim": item, "section": [dict(other) for other in matched_value]}
                )
    return {
        "mismatch": bool(missing or unit_conflicts),
        "missing": missing,
        "unit_conflicts": unit_conflicts,
        "claim": claim_qs,
        "section": section_qs,
    }


def _values_close(left: float, right: float) -> bool:
    scale = max(abs(left), abs(right), 1.0)
    return abs(left - right) <= max(1e-9, 1e-6 * scale)


def _as_passage(item: object) -> dict:
    if isinstance(item, str):
        return {"text": item, "source_version_id": None, "locator": None}
    if not isinstance(item, dict):
        return {"text": str(item or ""), "source_version_id": None, "locator": None}
    return {
        "text": str(item.get("text") or ""),
        "source_version_id": item.get("source_version_id"),
        "locator": item.get("locator"),
    }


def _noul_p(payload: dict, key: str) -> float | None:
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        return None
    answer = answers.get(key)
    if not isinstance(answer, dict):
        return None
    if answer.get("kind") not in (None, "noul"):
        raw = answer.get("noul")
    else:
        raw = answer.get("value")
        if raw is None:
            raw = answer.get("noul")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _choice_payload(payload: dict) -> tuple[str | None, dict | None, float | None]:
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        return None, None, None
    answer = answers.get("relation")
    if not isinstance(answer, dict):
        return None, None, None
    relation = answer.get("value") or answer.get("choice")
    relation_s = str(relation) if relation else None
    probs = answer.get("probabilities")
    probabilities = dict(probs) if isinstance(probs, dict) else None
    confidence = answer.get("confidence")
    try:
        conf_f = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        conf_f = None
    return relation_s, probabilities, conf_f


def _judge(template: str, state: dict, **params: object) -> dict:
    try:
        result = lr_sdk().judge(template, state, **params)
    except Exception as exc:
        return {
            "status": "unavailable",
            "error_code": "unavailable",
            "answers": {},
            "error": str(exc) or type(exc).__name__,
        }
    if isinstance(result, dict) and result.get("error") and "status" not in result:
        return {
            "status": "unavailable",
            "error_code": "invalid_request",
            "answers": {},
            "error": str(result.get("error")),
        }
    if not isinstance(result, dict):
        return {
            "status": "unavailable",
            "error_code": "invalid_response",
            "answers": {},
        }
    return result


def screen_passages(
    question: str, passages: list, *, hypothesis: str | None = None
) -> list[dict]:
    """Score each passage with ``literature.screen``. Contradictions are kept."""

    hypo = str(hypothesis).strip() if hypothesis else ""
    rows: list[dict] = []
    for item in passages or []:
        passage = _as_passage(item)
        state = {
            "question": str(question or ""),
            "passage": passage["text"],
        }
        params: dict = {}
        if hypo:
            state["hypothesis"] = hypo
            params["has_hypothesis"] = True
        judged = _judge("literature.screen", state, **params)
        status = str(judged.get("status") or "unavailable")
        relevant = _noul_p(judged, "relevant")
        has_evidence = _noul_p(judged, "has_evidence")
        contradicts = _noul_p(judged, "contradicts_hypothesis") if hypo else None
        rows.append(
            {
                "text": passage["text"],
                "source_version_id": passage["source_version_id"],
                "locator": passage["locator"],
                "status": status,
                "relevant": relevant,
                "has_evidence": has_evidence,
                "contradicts_hypothesis": contradicts,
                "probabilities": {
                    "relevant": relevant,
                    "has_evidence": has_evidence,
                    "contradicts_hypothesis": contradicts,
                },
                "template_version": judged.get("template_version")
                or SCREEN_TEMPLATE_VERSION,
                "error_code": judged.get("error_code"),
            }
        )
    return rows


def _empty_claim_row(
    *,
    claim_id: object,
    source_id: object,
    version_id: object,
    status: str,
    span: dict | None = None,
    match: str | None = None,
    relation: str | None = None,
    probabilities: dict | None = None,
    confidence: float | None = None,
    numeric: dict | None = None,
    error_code: object = None,
    template_version: object = None,
) -> dict:
    return {
        "claim_id": claim_id,
        "source_id": source_id,
        "version_id": version_id,
        "span": span,
        "match": match,
        "relation": relation,
        "probabilities": probabilities or {},
        "confidence": confidence,
        "template_version": template_version or CLAIM_TEMPLATE_VERSION,
        "status": status,
        "numeric": numeric,
        "error_code": error_code,
    }


def _relation_status(relation: str | None, judge_status: str) -> str:
    if judge_status == "disabled":
        return "disabled"
    if judge_status == "unavailable":
        return "uncertain"
    if judge_status == "uncertain":
        return "uncertain"
    if relation == "supports":
        return "verified"
    if relation == "contradicts":
        return "contradicted"
    if relation == "insufficient":
        return "unsupported"
    return "uncertain"


def check_claims(
    claims: list, sources: dict, *, window: int = DEFAULT_WINDOW
) -> list[dict]:
    """Locate quotes, compare numbers in code, then ask ``literature.claim``."""

    source_map = sources if isinstance(sources, dict) else {}
    rows: list[dict] = []
    for raw in claims or []:
        if not isinstance(raw, dict):
            continue
        claim_id = raw.get("claim_id")
        source_id = raw.get("source_id")
        claim_text = str(raw.get("claim") or "")
        quote = raw.get("quote")
        source = source_map.get(source_id) if source_id is not None else None
        if not isinstance(source, dict):
            source = {"text": str(source or "")} if source else {}
        source_text = str(source.get("text") or "")
        version_id = source.get("version_id")
        located = locate_claim(
            source_text,
            quote=str(quote) if quote else None,
            claim=claim_text,
            window=window,
        )
        if located is None:
            rows.append(
                _empty_claim_row(
                    claim_id=claim_id,
                    source_id=source_id,
                    version_id=version_id,
                    status="not_found_needs_review",
                )
            )
            continue
        span = located.get("span") or {}
        pad = max(0, int(window))
        try:
            match_start = int(span.get("start", 0))
            match_end = int(span.get("end", 0))
        except (TypeError, ValueError):
            match_start, match_end = 0, 0
        sec_start = max(0, match_start - pad)
        sec_end = min(len(source_text), match_end + pad)
        section = source_text[sec_start:sec_end]
        numeric = compare_quantities(claim_text, section)
        judged = _judge(
            "literature.claim",
            {"claim": claim_text, "section": section},
        )
        judge_status = str(judged.get("status") or "unavailable")
        relation, probabilities, confidence = _choice_payload(judged)
        if judge_status == "disabled":
            status = "disabled"
        elif numeric.get("mismatch"):
            status = "numeric_mismatch"
        else:
            status = _relation_status(relation, judge_status)
        rows.append(
            _empty_claim_row(
                claim_id=claim_id,
                source_id=source_id,
                version_id=version_id,
                status=status,
                span=located.get("span"),
                match=located.get("match"),
                relation=relation,
                probabilities=probabilities,
                confidence=confidence,
                numeric=numeric,
                error_code=judged.get("error_code"),
                template_version=judged.get("template_version")
                or CLAIM_TEMPLATE_VERSION,
            )
        )
    return rows
