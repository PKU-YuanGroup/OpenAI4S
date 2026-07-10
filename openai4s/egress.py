"""Outbound domain allowlist — the host-stamped network egress fence.

The policy is an **outbound domain allowlist**: science APIs (NCBI, UniProt,
RCSB, EBI, OpenAlex, CrossRef, arXiv), package indexes (PyPI, conda, CRAN,
Bioconductor, npm) and data repositories (GEO, SRA, ENA, CELLxGENE) are reachable;
everything else returns a proxy 403 and the agent must call
``request_network_access(domain=...)`` to widen the fence.

openai4s does not ship an OS-level sandbox (Seatbelt/bubblewrap) — that is a
separate, infra-heavy subsystem — so this module is a **best-effort, code-as-action
fence** enforced at the host-tool boundary (``host.web_fetch`` / ``host.web_search``
and, statically, ``host.bash``). It is:

* **host-stamped** — the allowlist is owned by the host process; the agent can only
  *request* a widening through the gated ``request_network_access`` tool, which the
  permission broker routes to the user. The agent cannot widen it unilaterally.
* **fail-open when unconfigured** — the mode defaults to ``off``
  (``OPENAI4S_EGRESS``), so nothing changes for an install that relies on
  "networking is ON". Set ``OPENAI4S_EGRESS=allowlist`` to enforce.

Matching is suffix-based: an allowlist entry ``ncbi.nlm.nih.gov`` also authorizes
``eutils.ncbi.nlm.nih.gov`` and ``sra-download.ncbi.nlm.nih.gov`` (subdomains), but
never ``evilncbi.nlm.nih.gov`` (the boundary dot is required).
"""
from __future__ import annotations

import os
import re
import threading
import urllib.parse

# --------------------------------------------------------------------------- #
#  Canonical allowlist — the single source of truth for both enforcement and
#  the Customize → Network display in the gateway. Entries are BASE domains;
#  subdomains match by suffix (see `_host_matches`), so eutils./sra-download./
#  rest. hosts are covered without listing every one.
# --------------------------------------------------------------------------- #
EGRESS_GROUPS: list[dict] = [
    {
        "name": "Scientific databases",
        "enabled": True,
        "domains": [
            "ncbi.nlm.nih.gov",  # NCBI (+ E-utilities, GEO, SRA subdomains)
            "ebi.ac.uk",  # EBI (+ ENA, Europe PMC subdomains)
            "uniprot.org",  # UniProt (+ rest.uniprot.org)
            "rcsb.org",  # RCSB PDB (+ files./data. subdomains)
            "ensembl.org",  # Ensembl (+ rest.ensembl.org)
            "openalex.org",  # OpenAlex (+ api.openalex.org)
            "crossref.org",  # CrossRef (+ api.crossref.org)
        ],
    },
    {
        "name": "Literature & preprints",
        "enabled": True,
        "domains": [
            "arxiv.org",  # arXiv (+ export.arxiv.org)
            "biorxiv.org",  # bioRxiv / medRxiv (+ api.biorxiv.org)
            "pubmed.ncbi.nlm.nih.gov",
            "europepmc.org",
            "semanticscholar.org",  # (+ api.semanticscholar.org)
        ],
    },
    {
        "name": "Package indexes",
        "enabled": True,
        "domains": [
            "pypi.org",
            "files.pythonhosted.org",  # PyPI
            "anaconda.org",
            "repo.anaconda.com",  # conda
            "conda.anaconda.org",
            "conda-forge.org",
            "r-project.org",  # CRAN (cran./cloud.)
            "bioconductor.org",  # Bioconductor
            "npmjs.org",
            "npmjs.com",
            "registry.npmjs.org",  # npm
            "github.com",
            "raw.githubusercontent.com",  # source installs
            "codeload.github.com",
            "objects.githubusercontent.com",
        ],
    },
    {
        "name": "Data repositories",
        "enabled": True,
        "domains": [
            "ftp.ncbi.nlm.nih.gov",  # GEO / SRA downloads
            "sra-download.ncbi.nlm.nih.gov",
            "trace.ncbi.nlm.nih.gov",
            "cellxgene.cziscience.com",  # CELLxGENE (+ datasets./api.)
            "ftp.ebi.ac.uk",  # ENA / ArrayExpress
        ],
    },
    {
        "name": "Web search",
        "enabled": True,
        "domains": ["duckduckgo.com", "html.duckduckgo.com", "lite.duckduckgo.com"],
    },
]


class EgressBlocked(RuntimeError):
    """Raised when an outbound request targets a domain outside the allowlist
    (allowlist mode only). Carries a proxy-403-style message the agent can
    recover from by calling ``host.request_network_access``."""


# Domains widened at runtime via an approved request_network_access call. Process
# -scoped (not persisted); the permission broker persists the *approval rule*, so
# a re-request after a daemon restart re-grants without re-prompting. Guarded by a
# lock because host.bash / host.web_fetch can run on background cell threads.
_RUNTIME_GRANTS: set[str] = set()
_LOCK = threading.Lock()


def egress_mode() -> str:
    """Current enforcement mode: ``"allowlist"`` or ``"off"`` (the default).

    Read fresh from the environment on every call (mirroring
    ``webtools.network_allowed``) so a UI/toggle or a test that flips
    ``OPENAI4S_EGRESS`` takes effect without rebuilding the config singleton.
    Any unrecognized value degrades to ``off`` (fail-open)."""
    val = (os.environ.get("OPENAI4S_EGRESS", "off") or "off").strip().lower()
    return (
        "allowlist"
        if val in ("allowlist", "allow_list", "on", "1", "enforce")
        else "off"
    )


def _norm(host: str) -> str:
    """Lowercase a bare hostname and strip a trailing dot / :port."""
    h = (host or "").strip().lower().rstrip(".")
    if h.startswith("[") and "]" in h:  # IPv6 literal [::1]:8080
        return h[1 : h.index("]")]
    return h.split(":", 1)[0]


def domain_of(target: str) -> str:
    """Extract the hostname from a URL, bare domain, or ``host:port`` string."""
    t = (target or "").strip()
    if not t:
        return ""
    if "://" not in t:
        t = "//" + t  # let urlsplit read it as a netloc rather than a path
    try:
        host = urllib.parse.urlsplit(t).hostname or ""
    except ValueError:
        return ""
    return _norm(host)


def _host_matches(host: str, allowed: str) -> bool:
    """True if `host` equals `allowed` or is a subdomain of it. The leading dot on
    the suffix check keeps `evilncbi.nlm.nih.gov` from matching `ncbi.nlm.nih.gov`."""
    return host == allowed or host.endswith("." + allowed)


def builtin_domains() -> frozenset[str]:
    """Flattened base domains from EGRESS_GROUPS."""
    return frozenset(_norm(d) for g in EGRESS_GROUPS for d in g.get("domains", []))


def granted_domains() -> frozenset[str]:
    """Domains widened at runtime via approved request_network_access calls."""
    with _LOCK:
        return frozenset(_RUNTIME_GRANTS)


def grant_domain(domain: str) -> str:
    """Widen the allowlist with `domain` (called AFTER the permission broker
    approves a request_network_access). Returns the normalized domain stored."""
    d = domain_of(domain) or _norm(domain)
    if d:
        with _LOCK:
            _RUNTIME_GRANTS.add(d)
    return d


def revoke_domain(domain: str) -> None:
    d = domain_of(domain) or _norm(domain)
    with _LOCK:
        _RUNTIME_GRANTS.discard(d)


def reset_grants() -> None:
    """Clear all runtime grants (test hook / a full network-lockdown reset)."""
    with _LOCK:
        _RUNTIME_GRANTS.clear()


def domain_allowed(host_or_url: str) -> bool:
    """Whether an outbound request to this host/URL is permitted.

    Fail-open: ``off`` mode → always True; an unparseable target → True (let the
    request fail on its own rather than mis-block)."""
    if egress_mode() != "allowlist":
        return True
    host = domain_of(host_or_url)
    if not host:
        return True
    allowed = builtin_domains() | granted_domains()
    return any(_host_matches(host, a) for a in allowed)


def blocked_message(domain: str) -> str:
    """The proxy-403-style soft error an agent sees for a blocked domain."""
    d = domain or "the target host"
    return (
        f"proxy 403: outbound network access to {d!r} is blocked by the egress "
        f"allowlist (OPENAI4S_EGRESS=allowlist). This domain is not on the "
        f"science / package-index / data-repository allowlist. Call "
        f"host.request_network_access(domain={d!r}) to ask the user to approve "
        f"widening it."
    )


def blocked_error(domain: str) -> dict:
    """Single-key soft-fail dict (openai4s host-tool contract)."""
    return {"error": blocked_message(domain)}


def check_url(url: str) -> None:
    """Raise EgressBlocked if `url`'s domain is outside the allowlist. No-op in
    ``off`` mode. Called per redirect hop by webtools so a public → blocked
    redirect is still caught."""
    if egress_mode() != "allowlist":
        return
    host = domain_of(url)
    if host and not domain_allowed(host):
        raise EgressBlocked(blocked_message(host))


# Match explicit http(s) URLs; stop at shell metacharacters/quotes/whitespace so a
# `curl https://x.org/a && rm y` only extracts the URL, not the rest of the line.
_URL_RE = re.compile(r"https?://[^\s'\"|;>)`]+", re.I)


def scan_command(command: str) -> str | None:
    """Best-effort static egress check for host.bash: return the first http(s)
    URL-domain in `command` that the allowlist would block, else None.

    This is defense-in-depth only — raw code in a cell (or an obfuscated shell
    command) is NOT sandboxed by this check; real enforcement is the OS-level
    egress fence, which openai4s does not ship. It catches the common
    `curl`/`wget`/`pip install <url>`/`git clone https://…` shapes."""
    if egress_mode() != "allowlist":
        return None
    for m in _URL_RE.finditer(command or ""):
        host = domain_of(m.group(0))
        if host and not domain_allowed(host):
            return host
    return None
