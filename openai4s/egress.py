"""Outbound domain allowlist — the host-stamped network egress fence.

The policy is an **outbound domain allowlist**: science APIs (NCBI, UniProt,
RCSB, EBI, OpenAlex, CrossRef, arXiv), package indexes (PyPI, conda, CRAN,
Bioconductor, npm) and data repositories (GEO, SRA, ENA, CELLxGENE) are reachable;
everything else returns a proxy 403 and the agent must call
``request_network_access(domain=...)`` to widen the fence.

This module is the **host-stamped outbound allowlist** enforced at the Host
tool boundary (``host.web_fetch`` / ``host.web_search`` and, at capability
issuance, ``host.bash``). The OS kernel sandbox (Seatbelt/bubblewrap in
``openai4s.security.sandbox``) is a separate layer: it denies raw sockets from
the scientific worker so these Host tools remain the intended network path.
A Skill ``capabilities.network`` manifest describes intended destinations; it
never calls ``grant_domain`` and never widens this allowlist. It is:

* **host-stamped** — the allowlist is owned by the host process; the agent can only
  *request* a widening through the gated ``request_network_access`` tool, which the
  permission broker routes to the user. The agent cannot widen it unilaterally.
* **fail-open when unconfigured** — the mode defaults to ``off``
  (``OPENAI4S_EGRESS``), so nothing changes for an install that relies on
  "networking is ON". Set ``OPENAI4S_EGRESS=allowlist`` to enforce.

Matching is suffix-based by default: an allowlist entry ``ncbi.nlm.nih.gov`` also
authorizes ``eutils.ncbi.nlm.nih.gov`` and ``sra-download.ncbi.nlm.nih.gov``
(subdomains), but never ``evilncbi.nlm.nih.gov`` (the boundary dot is required).
Credential-bearing managed integrations opt into exact-host matching instead.
"""

from __future__ import annotations

import os
import re
import threading
import urllib.parse
from collections.abc import Mapping
from typing import Any

# --------------------------------------------------------------------------- #
#  Canonical allowlist — the single source of truth for both enforcement and
#  the Customize → Network display in the gateway. Entries are BASE domains by
#  default; subdomains match by suffix (see `_host_matches`), so eutils./
#  sra-download./rest. hosts are covered without listing every one. Managed
#  authenticated origins opt into exact matching below.
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
            "string-db.org",  # STRING protein association networks
            "bindingdb.org",  # BindingDB protein-ligand affinities
            "crossref.org",  # CrossRef (+ api.crossref.org)
        ],
    },
    {
        "name": "Literature & preprints",
        "enabled": True,
        "domains": [
            "arxiv.org",  # arXiv (+ export.arxiv.org)
            "biorxiv.org",  # bioRxiv / medRxiv (+ api.biorxiv.org)
            "doi.org",  # DOI resolver used by literature-review HEAD probes
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
            "zenodo.org",  # Zenodo public dataset metadata
        ],
    },
    {
        "name": "Professional datasets",
        "enabled": True,
        # Exact host, not the Volcengine parent domain or a child host: this
        # authenticated connector must not widen the general outbound fence.
        "exact": True,
        "domains": ["datapro.hqd.cn-beijing.volces.com"],
    },
    {
        "name": "Web search",
        "enabled": True,
        "exact_domains": ["open.feedcoopapi.com", "ydc-index.io", "api.tavily.com"],
        "domains": [
            # Exact authenticated Doubao Search upstream.  Keep this narrower
            # than feedcoopapi.com so the Agent Plan credential cannot be sent
            # to sibling services through an allowlist match.
            "open.feedcoopapi.com",
            # You.com: keyless tier, and the exact host that receives YDC_API_KEY.
            "api.you.com",
            "ydc-index.io",
            # Tavily's fixed endpoint; it receives the Tavily key.
            "api.tavily.com",
            "bing.com",
            "duckduckgo.com",
            "html.duckduckgo.com",
            "lite.duckduckgo.com",
            "mojeek.com",
        ],
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


def exact_builtin_domains() -> frozenset[str]:
    """Built-ins whose authorization does not extend to child hosts.

    Most catalog entries intentionally cover a service's documented
    subdomains. Credential-bearing managed integrations are different: their
    request code uses one fixed origin, so the policy must name that origin
    exactly rather than granting an attacker-controlled child label too.
    """

    exact: set[str] = set()
    for group in EGRESS_GROUPS:
        if group.get("exact"):
            exact.update(_norm(d) for d in group.get("domains", []))
        exact.update(_norm(d) for d in group.get("exact_domains", []))
    return frozenset(exact)


def granted_domains() -> frozenset[str]:
    """Domains widened at runtime via approved request_network_access calls."""
    with _LOCK:
        return frozenset(_RUNTIME_GRANTS)


def domain_in_allowlist(host_or_url: str) -> bool:
    """Whether a host is in the built-in or explicitly granted catalog.

    Unlike :func:`domain_allowed`, this answer does not depend on whether
    allowlist enforcement is currently enabled.  The distinction matters for
    WSL Fake-IP DNS compatibility: accepting a synthetic 198.18.0.0/15 answer
    is safe only for a domain the host already knows or the user explicitly
    granted, even when the general egress policy is otherwise ``off``.
    """

    host = domain_of(host_or_url)
    if not host:
        return False
    exact = exact_builtin_domains()
    if host in exact:
        return True
    runtime = granted_domains()
    allowed = (builtin_domains() - exact) | (runtime - exact)
    return any(_host_matches(host, item) for item in allowed)


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
    request fail on its own rather than mis-block).

    The unparseable case is kept here rather than inherited from
    :func:`domain_in_allowlist`, which is deliberately fail-*closed*. Callers
    of this function read a False as "an existing hard policy refuses this",
    and `permissions.py` turns that into a Guardian hard deny plus a durable
    audit row. A scheme-bearing target with no host (``file:///abs/path``) is
    not an egress decision at all, so answering it with a denial names a
    policy that never issued."""
    if egress_mode() != "allowlist":
        return True
    if not domain_of(host_or_url):
        return True
    return domain_in_allowlist(host_or_url)


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
    redirect is still caught.

    When a ``host_only`` Skill is bound to the current frame, the destination
    must also be in that Skill's declared domain list. The extra constraint
    only narrows; it never grants a host the allowlist would refuse.
    """
    try:
        from openai4s.server.skill_network_admission import constrain_check_url

        blocked_by_skill = constrain_check_url(url)
    except (
        Exception
    ):  # noqa: BLE001 - missing admission must not fail open on a bound skill
        blocked_by_skill = None
        # If a host_only Skill is bound and the extra check cannot run, refuse.
        try:
            from openai4s.server import skill_network_admission as _sna

            if _sna.active_host_only_domains() is not None:
                raise EgressBlocked(
                    "skill host_only domain constraint unavailable; request blocked"
                )
        except EgressBlocked:
            raise
        except Exception:  # noqa: BLE001
            blocked_by_skill = None
    if blocked_by_skill:
        raise EgressBlocked(
            f"skill host_only manifest does not declare destination {blocked_by_skill!r}"
        )
    if egress_mode() != "allowlist":
        return
    host = domain_of(url)
    if host and not domain_allowed(host):
        raise EgressBlocked(blocked_message(host))


# Match explicit http(s) URLs; stop at shell metacharacters/quotes/whitespace so a
# `curl https://x.org/a && rm y` only extracts the URL, not the rest of the line.
_URL_RE = re.compile(r"https?://[^\s'\"|;>)`]+", re.I)


def command_domains(command: str) -> list[str]:
    """The unique http(s) URL-domains named in a shell command, policy-free.

    Pure extraction (no mode/allowlist consultation) so the kernel-local
    `host.bash` can collect the domains in the worker and ask the HOST for the
    verdict — the runtime grants (`request_network_access`) and the live
    `OPENAI4S_EGRESS` toggle exist only in the host process."""
    out: list[str] = []
    for m in _URL_RE.finditer(command or ""):
        host = domain_of(m.group(0))
        if host and host not in out:
            out.append(host)
    return out


def scan_command(command: str) -> str | None:
    """Best-effort static egress check for host.bash: return the first http(s)
    URL-domain in `command` that the allowlist would block, else None.

    This is defense-in-depth only and is not the Skill-network boundary.
    Static URL patterns in a command produce an audit-oriented hint here;
    Cell admission and shell capability issuance enforce Skill manifests
    against the measured OS sandbox (``openai4s.security.sandbox``) plus
    this allowlist. It catches the common
    `curl`/`wget`/`pip install <url>`/`git clone https://…` shapes."""
    if egress_mode() != "allowlist":
        return None
    for host in command_domains(command):
        if not domain_allowed(host):
            return host
    return None


EGRESS_BOUNDARY_UNAVAILABLE = "egress_boundary_unavailable"

_BOUNDARY_REASON = (
    "OPENAI4S_EGRESS=allowlist admits a new Cell only when this kernel's OS "
    "sandbox has proven it blocks raw network: macOS Seatbelt or Linux "
    "bubblewrap, with enforced true, self-test passed, and network_policy "
    "blocked. OPENAI4S_KERNEL_SANDBOX=auto admits the Cell when that "
    "self-test passes; OPENAI4S_KERNEL_SANDBOX=enforce refuses to start "
    "instead of degrading. Set OPENAI4S_EGRESS=off to admit Cells without "
    "that proof. Remote kernels are always refused under allowlist."
)

_BOUNDARY_REMEDY = (
    "Use macOS Seatbelt or Linux bubblewrap. OPENAI4S_KERNEL_SANDBOX=auto "
    "admits a Cell when the self-test reports enforced, self_test_passed, "
    "and network_policy blocked; set OPENAI4S_KERNEL_SANDBOX=enforce to "
    "refuse to start instead of degrading.",
    "Set OPENAI4S_EGRESS=off to admit Cells without a proven raw-network block.",
    "Remote kernels are refused for as long as OPENAI4S_EGRESS=allowlist.",
)

_SANDBOX_PROJECTION_KEYS = (
    "mode",
    "state",
    "backend",
    "enforced",
    "self_test_passed",
    "network_policy",
)


class EgressBoundaryUnavailable(RuntimeError):
    """Allowlist mode cannot prove this kernel blocks raw network."""

    stable_reason = _BOUNDARY_REASON
    stable_remedy = _BOUNDARY_REMEDY

    def __init__(self, decision: Mapping[str, Any]) -> None:
        payload = dict(decision)
        code = str(payload.get("code") or EGRESS_BOUNDARY_UNAVAILABLE)
        reason = str(payload.get("reason") or self.stable_reason)
        payload["code"] = code
        payload["reason"] = reason
        self.code = code
        self.decision = payload
        super().__init__(f"{code}: {reason}")


def cell_admission_refusal(sandbox_status: Any) -> dict[str, Any] | None:
    """Refuse a new Cell unless allowlist mode has a proven raw-network block.

    ``None`` means the Cell may proceed: egress is off, or ``boundary_holds``
    is true. Any other posture returns a decision dict and does not raise.
    """

    if egress_mode() != "allowlist":
        return None
    from openai4s.security.sandbox import boundary_holds

    if boundary_holds(sandbox_status):
        return None
    return _boundary_decision(sandbox_status)


def _project_sandbox(sandbox_status: Any) -> dict[str, Any]:
    projected = {key: None for key in _SANDBOX_PROJECTION_KEYS}
    values: Mapping[str, Any] | None
    if isinstance(sandbox_status, Mapping):
        values = sandbox_status
    elif sandbox_status is not None and hasattr(sandbox_status, "to_dict"):
        raw = sandbox_status.to_dict()
        values = raw if isinstance(raw, Mapping) else None
    else:
        values = None
    if values is None:
        return projected
    for key in _SANDBOX_PROJECTION_KEYS:
        if key in values:
            projected[key] = values[key]
    return projected


def _boundary_decision(sandbox_status: Any) -> dict[str, Any]:
    return {
        "code": EGRESS_BOUNDARY_UNAVAILABLE,
        "reason": _BOUNDARY_REASON,
        "egress_mode": egress_mode(),
        "sandbox": _project_sandbox(sandbox_status),
        "remedy": list(_BOUNDARY_REMEDY),
    }


# Host-wrapped forms. The code token alone is not enough: a Cell can write it
# into a worker's stderr, and that tail is glued onto
# ``kernel worker exited unexpectedly:``.
_KERNEL_BOOTSTRAP_REFUSAL_PREFIX = (
    "kernel bootstrap failed: " + EGRESS_BOUNDARY_UNAVAILABLE + ":"
)
_R_BOOTSTRAP_REFUSAL_PREFIX = (
    "R kernel unavailable: R kernel bootstrap failed: "
    + EGRESS_BOUNDARY_UNAVAILABLE
    + ":"
)


def _exception_chain(exc: BaseException):
    """Yield ``exc`` and each ``__cause__`` / ``__context__``, cause first.

    A context that ``raise ... from`` suppressed is not followed, the same
    rule the interpreter uses when it prints the chain.
    """

    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        current = stack.pop()
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)
        yield current
        context = None if current.__suppress_context__ else current.__context__
        cause = current.__cause__
        if isinstance(context, BaseException):
            stack.append(context)
        if isinstance(cause, BaseException):
            stack.append(cause)


def _is_typed_boundary(exc: BaseException) -> bool:
    """True for the boundary class or an exception carrying its stable code.

    ``PermissionError.code`` is how the CLI admission seam types a refusal
    without changing the exception class the caller already catches. The
    message text is not consulted.
    """

    if isinstance(exc, EgressBoundaryUnavailable):
        return True
    return getattr(exc, "code", None) == EGRESS_BOUNDARY_UNAVAILABLE


def _prefix_boundary_refusal(exc: BaseException) -> bool:
    """Exact host prefixes, and only while allowlist is the current mode."""

    if egress_mode() != "allowlist":
        return False
    text = str(exc)
    return text.startswith(_KERNEL_BOOTSTRAP_REFUSAL_PREFIX) or text.startswith(
        _R_BOOTSTRAP_REFUSAL_PREFIX
    )


def is_boundary_refusal(exc: BaseException) -> bool:
    """True when ``exc`` is a kernel-boundary refusal the host can trust.

    A typed ``EgressBoundaryUnavailable`` counts anywhere on the cause or
    context chain, because the watchdog re-raises that object and a relay
    may wrap it. The two bootstrap prefixes count only as the start of the
    outermost message, and only while ``egress_mode()`` is ``allowlist``.
    A substring of the code token does not count. An interrupt or exit
    (a ``BaseException`` that is not an ``Exception``) is that interrupt,
    even when a refusal sits on its context.
    """

    if not isinstance(exc, BaseException):
        return False
    if not isinstance(exc, Exception) and not _is_typed_boundary(exc):
        return False
    if any(_is_typed_boundary(item) for item in _exception_chain(exc)):
        return True
    return _prefix_boundary_refusal(exc)


def _carried_decision(exc: BaseException) -> dict[str, Any] | None:
    for current in _exception_chain(exc):
        if not _is_typed_boundary(current):
            continue
        decision = getattr(current, "decision", None)
        if isinstance(decision, dict):
            return dict(decision)
    return None


def boundary_refusal_decision(
    exc: BaseException,
    sandbox_status: Any = None,
) -> dict[str, Any] | None:
    """Stable decision for a recognized refusal, otherwise None.

    A typed refusal keeps the decision it was raised with. A prefix refusal
    names a bootstrap candidate that was never published, so a caller passes
    ``sandbox_status`` only when it holds that exact candidate; ``None``
    projects null sandbox fields. A posture that would pass admission cannot
    be the refused worker's, so it is not projected onto the refusal.
    """

    if not is_boundary_refusal(exc):
        return None
    carried = _carried_decision(exc)
    if carried is not None:
        return carried
    fresh = cell_admission_refusal(sandbox_status)
    if fresh is not None:
        return fresh
    return _boundary_decision(None)
