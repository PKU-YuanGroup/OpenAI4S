"""Is there a newer release, and what would have to be true for us to say so.

This module answers one question and refuses to answer it badly. Three
properties are load-bearing; each of them is a mechanism here rather than a
convention somewhere else.

**Failure is never "up to date."** A check that cannot reach pypi.org has not
learned that this install is current — it has learned nothing. Every failure
path in this module produces ``status="unknown"`` with a distinct ``reason``
drawn from `REASONS`, and `STATUSES` has exactly three members so the two
knowable answers cannot be reached by accident. An updater that reports "you
are on the latest version" because DNS failed is worse than one that reports
nothing, because the user stops looking.

**Nothing here names an outbound primitive.** `tests/test_egress_surface.py`
AST-walks every module under ``openai4s/`` for ``urlopen`` / ``Request`` /
``build_opener`` / ``create_connection`` and asserts the declared surface is
still thirteen modules. Every byte this module moves goes through
`openai4s.webtools`, which is already declared and which is the only code in
the tree that follows a redirect *manually* and re-applies
``egress.check_url()`` and ``guard_url()`` on every hop. That matters and is
not theoretical: both sources redirect (``pypi.org`` →
``files.pythonhosted.org``, ``github.com`` → ``objects.githubusercontent.com``),
so a client that follows redirects internally checks the first hop and trusts
the rest.

**One version oracle, two digest witnesses.** ``info.version`` from the
project document decides *which* version exists; the per-version document and
the release ``SHA256SUMS`` decide what it must hash to. This module does not
re-implement the agreement rule — it hands both maps to
`openai4s.update.verify.agree`, whose `Witnesses` cannot be constructed any
other way, so the ordering rule (a manifest digest is honoured only after the
wheel already agreed) cannot be skipped by a caller that has not read it.

Two smaller decisions, stated because they look like oversights:

* ``status`` is the coarse tri-state the surfaces render and ``reason`` is the
  precise frozen code. Splitting them is what lets "every failure renders as
  unknown" and "every failure is distinguishable" both be true. A caller that
  branches on prose is a caller that breaks when the prose improves.
* The ``OPENAI4S_UPDATE_SOURCE=offline`` pin short-circuits **before** the
  cache is read and before `resolved_source` is consulted. Reading a cache is
  not dialling, but a pinned check that answered from a cache would answer
  differently on two machines, and the whole point of the pin is that the test
  suite and the CI response prober get one deterministic answer.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Mapping

from openai4s.update import UpdateError, UpdateRefusal, verify

# --------------------------------------------------------------------------- #
#  where we read from
# --------------------------------------------------------------------------- #
#
# Templates, not concatenation, and never a host taken from a response body, a
# settings row, a tool argument or a model message. `OPENAI4S_UPDATE_INDEX`
# exists for a mirror and is validated exactly as strictly as the default.

#: The PyPI project name. Fixed: a distribution name resolved from anywhere
#: else is a dependency-confusion channel.
PROJECT = "openai4s"

DEFAULT_INDEX = "https://pypi.org"

_PYPI_PROJECT_URL = "{index}/pypi/{project}/json"
_PYPI_VERSION_URL = "{index}/pypi/{project}/{version}/json"
_RELEASE_SUMS_URL = (
    "https://github.com/PKU-YuanGroup/OpenAI4S/releases/download/v{version}/SHA256SUMS"
)

#: Hosts a per-file download URL from the PyPI document may point at. The URL
#: itself has to come from the body — a PyPI file URL carries a content hash
#: nobody can reconstruct — so the host is compared against a set decided here
#: instead. That is a different thing from taking a host from the body, and the
#: difference is the whole control: an index that answers with a download URL
#: on some other host gets that URL dropped, not followed.
ASSET_HOSTS: frozenset[str] = frozenset({"files.pythonhosted.org"})

#: The two knowable answers and the one honest non-answer. Three members, on
#: purpose: a fourth would be somebody's idea of a partial success.
STATUSES: tuple[str, ...] = ("update_available", "up_to_date", "unknown")

#: Why the status is what it is. Frozen, because the CLI maps these to exit
#: codes and the HTTP route puts them in a response body, so this tuple is the
#: contract and `detail` is prose nobody may parse. Every member except "ok"
#: resolves to ``status="unknown"``; `tests/test_update_discovery.py` asserts
#: that for each one by forcing it.
REASONS: tuple[str, ...] = (
    "ok",
    "offline_pin",
    "unparsable_running",
    "bad_index",
    "network_disabled",
    "egress_blocked",
    "ssrf_blocked",
    "unreachable",
    "timeout",
    "http_error",
    "too_large",
    "malformed",
    "yanked",
    "witness_disagreement",
    "witness_missing",
    "unknown",
)

#: Where an answer came from. "none" means no read was attempted at all.
SOURCES: tuple[str, ...] = ("pypi", "offline", "none")

#: A known answer is good for six hours. A failure is retried in fifteen
#: minutes: caching "the network was down" for six hours would turn one blip
#: into an afternoon of silence, and the cache exists to spare pypi.org a
#: request, not to spare the user an answer.
SUCCESS_TTL_MS = 6 * 60 * 60 * 1000
FAILURE_RETRY_MS = 15 * 60 * 1000

_OFFLINE_DETAIL = (
    "OPENAI4S_UPDATE_SOURCE=offline: the release sources were not contacted, "
    "so whether a newer version exists is unknown"
)

_TRUE = frozenset({"1", "true", "yes", "on"})

#: `<64 lowercase hex><whitespace>[*]<name>` — coreutils' own two formats. A
#: digest in any other spelling does not match and the line is dropped, which
#: is the safe direction: a name the manifest then fails to describe is caught
#: by `verify.agree` as a missing witness rather than compared loosely.
_SUMS_LINE = re.compile(r"^([0-9a-f]{64})\s+\*?(\S+)$")


# --------------------------------------------------------------------------- #
#  private failures, so one classifier handles every path
# --------------------------------------------------------------------------- #


class _Offline(UpdateError):
    """The pinned no-network source was asked to read something."""


class _Malformed(UpdateError):
    """A document was reachable but is not the document we expected."""


class _AllYanked(UpdateError):
    """Every file of the newest version has been withdrawn."""


# --------------------------------------------------------------------------- #
#  the source seam
# --------------------------------------------------------------------------- #


class ReleaseSource:
    """The three reads discovery makes, as one replaceable object.

    A seam rather than a monkeypatch of `webtools`: a test that replaces the
    transport replaces the guards with it, and the guards are the reason the
    transport is `webtools` in the first place.
    """

    def project(self, index: str, *, timeout: float) -> dict:
        raise NotImplementedError

    def release(self, index: str, version: str, *, timeout: float) -> dict:
        raise NotImplementedError

    def sums(self, version: str, *, timeout: float) -> str:
        raise NotImplementedError


class HttpSource(ReleaseSource):
    """The real one. Every byte goes through `openai4s.webtools`."""

    def project(self, index: str, *, timeout: float) -> dict:
        from openai4s import webtools

        return webtools.fetch_json(
            _PYPI_PROJECT_URL.format(index=index, project=PROJECT),
            timeout=timeout,
            max_bytes=verify.MAX_JSON_BYTES,
        )

    def release(self, index: str, version: str, *, timeout: float) -> dict:
        from openai4s import webtools

        return webtools.fetch_json(
            _PYPI_VERSION_URL.format(index=index, project=PROJECT, version=version),
            timeout=timeout,
            max_bytes=verify.MAX_JSON_BYTES,
        )

    def sums(self, version: str, *, timeout: float) -> str:
        """`SHA256SUMS` through `web_download`, not through a text fetch.

        It is a small text file, so streaming it to a temporary directory looks
        like the long way round. It is the one with a real ceiling:
        `web_download` takes `max_bytes` and enforces it while reading, while
        the text path would read up to `webtools.MAX_FETCH_BYTES` into memory
        and truncate afterwards — a description of the allocation rather than a
        bound on it. A manifest is an untrusted release asset; it gets the
        bounded reader.
        """
        from openai4s import webtools

        url = _RELEASE_SUMS_URL.format(version=version)
        with tempfile.TemporaryDirectory(prefix="openai4s-update-sums-") as scratch:
            target = Path(scratch) / "SHA256SUMS"
            webtools.web_download(
                url,
                target,
                timeout=timeout,
                max_bytes=verify.MAX_MANIFEST_BYTES,
            )
            return target.read_text("utf-8", errors="replace")


class OfflineSource(ReleaseSource):
    """The pin, as an object. Every read refuses; nothing is dialled."""

    def project(self, index: str, *, timeout: float) -> dict:
        raise _Offline(_OFFLINE_DETAIL)

    def release(self, index: str, version: str, *, timeout: float) -> dict:
        raise _Offline(_OFFLINE_DETAIL)

    def sums(self, version: str, *, timeout: float) -> str:
        raise _Offline(_OFFLINE_DETAIL)


#: The module-level seam. `resolved_source()` is what callers use; this is what
#: a test replaces.
_SOURCE: ReleaseSource = HttpSource()
_OFFLINE_SOURCE: ReleaseSource = OfflineSource()


def offline_pinned() -> bool:
    """Whether `OPENAI4S_UPDATE_SOURCE=offline` forbids contacting anything.

    Read per call rather than at import: `tests/conftest.py` pins it for the
    whole suite, and a value frozen at import is a value no fixture can reach.
    """
    return os.environ.get("OPENAI4S_UPDATE_SOURCE", "").strip().lower() == "offline"


def resolved_source() -> ReleaseSource:
    """The source this process will read from, pin included."""
    return _OFFLINE_SOURCE if offline_pinned() else _SOURCE


# --------------------------------------------------------------------------- #
#  the index
# --------------------------------------------------------------------------- #


def resolved_index() -> str:
    """`OPENAI4S_UPDATE_INDEX`, validated, or the default.

    Raises `UpdateError` rather than returning a fallback. An index variable
    that is silently ignored is worse than one that refuses: the operator who
    set it believes they are on their mirror.
    """
    raw = os.environ.get("OPENAI4S_UPDATE_INDEX", "").strip()
    if not raw:
        return DEFAULT_INDEX
    return _validate_index(raw)


def _validate_index(raw: str) -> str:
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme != "https":
        raise UpdateError(
            f"OPENAI4S_UPDATE_INDEX must be an https URL, not {raw!r}; plain "
            "HTTP would let anything on the path choose the version we install"
        )
    if "@" in parsed.netloc:
        raise UpdateError(
            "OPENAI4S_UPDATE_INDEX must not carry userinfo in its authority: "
            "a credential in an index URL is logged, echoed and cached"
        )
    if not parsed.hostname:
        raise UpdateError(f"OPENAI4S_UPDATE_INDEX names no host: {raw!r}")
    if parsed.query or parsed.fragment:
        raise UpdateError(
            "OPENAI4S_UPDATE_INDEX must be a bare origin and path, with no "
            f"query or fragment: {raw!r}"
        )
    return raw.rstrip("/")


# --------------------------------------------------------------------------- #
#  versions
# --------------------------------------------------------------------------- #


def parse_version(text: object) -> tuple[int, int, int] | None:
    """`X.Y.Z` as a 3-tuple of ints, or None.

    No `packaging`: `pyproject.toml` declares ``dependencies = []`` and the
    release tag grammar is fixed and prerelease-free, so a resolver that
    understands epochs and local versions would be answering a question this
    project does not ask.
    """
    if not verify.is_version(text):
        return None
    assert isinstance(text, str)
    major, minor, patch = text.split(".")
    return (int(major), int(minor), int(patch))


def is_newer(candidate: object, running: object) -> bool:
    """Whether ``candidate`` is a release strictly newer than ``running``."""
    left = parse_version(candidate)
    right = parse_version(running)
    if left is None or right is None:
        return False
    return left > right


def running_version() -> str:
    """This process's own `openai4s.__version__`."""
    from openai4s import __version__

    return str(__version__)


# --------------------------------------------------------------------------- #
#  documents
# --------------------------------------------------------------------------- #


def parse_sha256sums(text: object) -> dict[str, str]:
    """`{name: digest}` from a coreutils-style manifest.

    Unparseable lines are dropped rather than refused, and the names are
    required to be bare: a manifest entry called ``../../etc/x`` describes a
    path, and this dict is consulted by name elsewhere. A dropped line costs a
    `witness_missing` refusal later, which is the outcome we want anyway.
    """
    digests: dict[str, str] = {}
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _SUMS_LINE.match(line)
        if match is None:
            continue
        digest, name = match.group(1), match.group(2)
        if "/" in name or "\\" in name or name in (".", ".."):
            continue
        digests.setdefault(name, digest)
    return digests


def _asset_url(raw: object) -> str:
    """A per-file download URL, kept only when its host is one we decided."""
    if not isinstance(raw, str) or not raw:
        return ""
    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme != "https" or "@" in parsed.netloc:
        return ""
    host = (parsed.hostname or "").lower()
    return raw if host in ASSET_HOSTS else ""


def _pick_wheel(assets: list[dict]) -> str:
    """The wheel's filename: the one artifact both witnesses describe.

    A universal ``py3-none-any`` wheel is preferred and, for this project, is
    the only wheel a release produces — but preferring rather than requiring
    it means a future platform wheel does not silently become "no wheel", which
    would read as a missing witness rather than as the packaging change it is.
    """
    wheels = [
        asset
        for asset in assets
        if asset["packagetype"] == "bdist_wheel"
        and str(asset["filename"]).endswith(".whl")
    ]
    if not wheels:
        return ""
    for asset in wheels:
        if str(asset["filename"]).endswith("-py3-none-any.whl"):
            return str(asset["filename"])
    return str(sorted(wheels, key=lambda item: str(item["filename"]))[0]["filename"])


def read_release(document: object) -> tuple[list[dict], dict[str, str], str]:
    """`(assets, pypi_digests, wheel_filename)` from a per-version document.

    Yanked files are dropped here rather than filtered by the caller, so a
    withdrawn artifact cannot reach the digest map at all. A version whose
    files are *all* yanked is not offered: `_AllYanked` rather than an empty
    list, because an empty list downstream reads as "no files described",
    which is a different fact.
    """
    if not isinstance(document, Mapping):
        raise _Malformed("the per-version document from pypi.org is not an object")
    urls = document.get("urls")
    if not isinstance(urls, list):
        raise _Malformed("the per-version document from pypi.org carries no 'urls'")
    assets: list[dict] = []
    withdrawn = 0
    for entry in urls:
        if not isinstance(entry, Mapping):
            continue
        filename = entry.get("filename")
        if not isinstance(filename, str) or not filename:
            continue
        if "/" in filename or "\\" in filename or filename in (".", ".."):
            continue
        if bool(entry.get("yanked")):
            withdrawn += 1
            continue
        raw_digests = entry.get("digests")
        sha256 = ""
        if isinstance(raw_digests, Mapping):
            candidate = raw_digests.get("sha256")
            if verify.is_digest(candidate):
                sha256 = str(candidate)
        if not sha256:
            continue
        try:
            size = int(entry.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        assets.append(
            {
                "filename": filename,
                "packagetype": str(entry.get("packagetype") or ""),
                "size": max(0, size),
                "sha256": sha256,
                "yanked": False,
                "url": _asset_url(entry.get("url")),
            }
        )
    if not assets:
        if withdrawn:
            raise _AllYanked(
                f"every file of this version is withdrawn from pypi.org "
                f"({withdrawn} yanked), so it is not offered"
            )
        raise _Malformed(
            "pypi.org describes no usable file for this version: every entry "
            "was missing a filename or a sha256 digest"
        )
    assets.sort(key=lambda item: str(item["filename"]))
    digests = {str(asset["filename"]): str(asset["sha256"]) for asset in assets}
    return assets, digests, _pick_wheel(assets)


# --------------------------------------------------------------------------- #
#  the cache
# --------------------------------------------------------------------------- #


def data_dir_for(cfg: object | None) -> Path:
    """The data directory, from ``cfg`` when there is one.

    ``cfg`` is any object carrying ``data_dir`` — `openai4s.config.Config` in
    production. With none, the read-only config accessor answers, because the
    "which directory" rule belongs to `openai4s.config` and a second copy of it
    here is a second thing to keep true.
    """
    raw = getattr(cfg, "data_dir", None)
    if raw is not None:
        try:
            return Path(raw)
        except TypeError:  # pragma: no cover - a cfg with a nonsense data_dir
            pass
    from openai4s.config import get_config

    return Path(get_config(initialize_dirs=False).data_dir)


def cache_path(cfg: object | None = None) -> Path:
    """`<data_dir>/updates/check.json`."""
    return data_dir_for(cfg) / "updates" / "check.json"


def read_cache(path: "os.PathLike[str] | str") -> dict | None:
    """The cached answer, or None when there is not a usable one.

    A document whose ``status`` or ``reason`` is outside the frozen tuples is
    treated as absent. The file is 0600 in the user's own data directory, so
    this is not a trust boundary — it is the same rule the rest of the module
    obeys, applied to its own output, so a hand-edited or half-written cache
    cannot introduce a state the surfaces have never seen.
    """
    try:
        text = Path(os.fspath(path)).read_text("utf-8")
    except (OSError, ValueError):
        return None
    try:
        document = json.loads(text)
    except ValueError:
        return None
    if not isinstance(document, dict):
        return None
    if document.get("status") not in STATUSES:
        return None
    if document.get("reason") not in REASONS:
        return None
    if document.get("source") not in SOURCES:
        return None
    return document


def write_cache(path: "os.PathLike[str] | str", document: Mapping[str, Any]) -> bool:
    """Publish the answer atomically and owner-only. Best-effort by contract.

    A full disk must not be able to fail a check that otherwise succeeded, so
    this returns a boolean instead of raising. 0600 because the document names
    the version this machine runs, which is an inventory fact about a host.
    """
    from openai4s.security.permissions import fsync_dir, harden_dir, harden_file

    target = Path(os.fspath(path))
    temporary = target.with_name(target.name + ".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        harden_dir(target.parent)
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(dict(document), handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        harden_file(temporary)
        os.replace(temporary, target)
        fsync_dir(target.parent)
        harden_file(target)
        return True
    except OSError:
        try:
            temporary.unlink()
        except OSError:
            pass
        return False


def previous_known(cached: Mapping[str, Any] | None, running: str, index: str) -> dict:
    """The last answer that was actually an answer, for a failure to carry.

    A failure overwrites the cache — it has to, because `retry_after_at` is
    what stops a broken network being re-dialled on every invocation — and
    without this the surfaces lose the thing they are documented to print:
    "last known 0.4.0, checked three hours ago". Three flat keys, never nested
    further than one level, and only from a document describing *this* install
    against *this* index.
    """
    if not isinstance(cached, Mapping):
        return {}
    if str(cached.get("running") or "") != running:
        return {}
    if str(cached.get("index") or "") != index:
        return {}
    if cached.get("status") in ("update_available", "up_to_date"):
        try:
            checked_at = int(cached.get("checked_at_ms") or 0)
        except (TypeError, ValueError):
            return {}
        return {
            "status": str(cached["status"]),
            "latest": str(cached.get("latest") or ""),
            "checked_at_ms": checked_at,
        }
    nested = cached.get("previous")
    return dict(nested) if isinstance(nested, Mapping) and nested else {}


def _fresh(document: Mapping[str, Any], now_ms: int, running: str, index: str) -> bool:
    """Whether a cached answer still describes this install.

    Three ways a cache stops being an answer, all of them silent otherwise: it
    expired; the running version changed underneath it (after an upgrade, "an
    update is available" describes the version you just left); or the index was
    repointed, which makes "latest" a fact about a different host.
    """
    if str(document.get("running") or "") != running:
        return False
    if str(document.get("index") or "") != index:
        return False
    try:
        checked_at = int(document.get("checked_at_ms") or 0)
        retry_after = int(document.get("retry_after_at") or 0)
    except (TypeError, ValueError):
        return False
    if checked_at > now_ms:
        # A clock that moved backwards, or a cache copied from another
        # machine. Either way the age is not computable, so it is not fresh.
        return False
    return now_ms < retry_after


# --------------------------------------------------------------------------- #
#  classification
# --------------------------------------------------------------------------- #


def _line(error: BaseException, limit: int = 240) -> str:
    body = " ".join(f"{type(error).__name__}: {error}".split())
    return body[:limit]


def _chain(error: BaseException, depth: int = 8) -> list[str]:
    """The class names down one exception chain, bounded.

    `urllib.error.URLError` carries the real failure in ``reason``, and a
    timeout arrives as ``URLError(TimeoutError(...))`` rather than as a
    `TimeoutError`. Classifying on the outermost class alone reports every
    timeout as "unreachable", which sends the operator to look at DNS.
    """
    names: list[str] = []
    cursor: BaseException | None = error
    seen: set[int] = set()
    while cursor is not None and len(names) < depth and id(cursor) not in seen:
        seen.add(id(cursor))
        names.append(type(cursor).__name__)
        nxt = getattr(cursor, "reason", None)
        if not isinstance(nxt, BaseException):
            nxt = cursor.__cause__ or cursor.__context__
        cursor = nxt if isinstance(nxt, BaseException) else None
    return names


_TIMEOUT_NAMES = frozenset(
    {"TimeoutError", "Timeout", "ConnectTimeout", "ReadTimeout", "timeout"}
)
_UNREACHABLE_NAMES = frozenset(
    {
        "URLError",
        "ConnectionError",
        "ConnectionResetError",
        "ConnectionRefusedError",
        "ConnectionAbortedError",
        "gaierror",
        "herror",
        "SSLError",
        "SSLCertVerificationError",
        "ProxyError",
        "OSError",
    }
)
_MALFORMED_NAMES = frozenset(
    {"JSONDecodeError", "ValueError", "KeyError", "TypeError", "UnicodeDecodeError"}
)


def classify(error: BaseException) -> tuple[str, str]:
    """`(reason, detail)` for anything a read can raise.

    One classifier, because the alternative is each of the three reads deciding
    what a timeout is called. The reason is from `REASONS`; the detail is one
    line with no traceback and no local path.
    """
    from openai4s import egress, webtools

    if isinstance(error, _Offline):
        return "offline_pin", _OFFLINE_DETAIL
    if isinstance(error, _AllYanked):
        return "yanked", str(error)
    if isinstance(error, _Malformed):
        return "malformed", str(error)
    if isinstance(error, UpdateRefusal):
        code = error.code if error.code in REASONS else "unknown"
        return code, str(error)
    if isinstance(error, webtools.NetworkDisabled):
        return "network_disabled", _line(error)
    if isinstance(error, egress.EgressBlocked):
        return "egress_blocked", _line(error)
    if isinstance(error, webtools.SSRFBlocked):
        return "ssrf_blocked", _line(error)
    if isinstance(error, webtools.ResponseTooLarge):
        return "too_large", _line(error)

    names = _chain(error)
    if any(name in _TIMEOUT_NAMES for name in names):
        return "timeout", _line(error)
    if "HTTPError" in names:
        code = getattr(error, "code", None) or getattr(error, "status", None)
        detail = _line(error)
        if code:
            detail = f"HTTP {code}: {detail}"
        return "http_error", detail
    if any(name in _UNREACHABLE_NAMES for name in names):
        return "unreachable", _line(error)
    if any(name in _MALFORMED_NAMES for name in names):
        return "malformed", _line(error)
    return "unknown", _line(error)


# --------------------------------------------------------------------------- #
#  the answer
# --------------------------------------------------------------------------- #


def _now_ms() -> int:
    return int(time.time() * 1000)


def _document(
    *,
    status: str,
    reason: str,
    running: str,
    checked_at_ms: int,
    retry_after_at: int,
    latest: str = "",
    detail: str = "",
    source: str = "pypi",
    index: str = DEFAULT_INDEX,
    assets: list[dict] | None = None,
    witnesses: dict | None = None,
    previous: Mapping[str, Any] | None = None,
) -> dict:
    """The one shape every caller of this module receives.

    Every key is always present. A projection that has to test for a key is a
    projection whose consumer will forget to, and the forgotten branch is
    always the failure one.
    """
    if status not in STATUSES:  # pragma: no cover - guards a bug here
        raise UpdateError(f"not a discovery status: {status!r}")
    if reason not in REASONS:  # pragma: no cover - guards a bug here
        raise UpdateError(f"not a discovery reason: {reason!r}")
    if source not in SOURCES:  # pragma: no cover - guards a bug here
        raise UpdateError(f"not a discovery source: {source!r}")
    return {
        "checked_at_ms": int(checked_at_ms),
        "retry_after_at": int(retry_after_at),
        "source": source,
        "index": index,
        "latest": latest,
        "running": running,
        "status": status,
        "reason": reason,
        "detail": detail,
        "update_available": status == "update_available",
        "assets": list(assets or []),
        "witnesses": dict(witnesses or {}),
        "previous": dict(previous or {}),
    }


def _live(
    *,
    index: str,
    running: str,
    timeout: float,
    now_ms: int,
    allow_single_witness: bool,
) -> dict:
    source = resolved_source()

    def unknown(reason: str, detail: str) -> dict:
        return _document(
            status="unknown",
            reason=reason,
            running=running,
            detail=detail,
            index=index,
            checked_at_ms=now_ms,
            retry_after_at=now_ms + FAILURE_RETRY_MS,
        )

    try:
        project = source.project(index, timeout=timeout)
    except Exception as error:  # noqa: BLE001 - every failure has a reason code
        return unknown(*classify(error))

    info = project.get("info") if isinstance(project, Mapping) else None
    if not isinstance(info, Mapping):
        return unknown(
            "malformed", f"the project document at {index} carries no 'info' object"
        )
    latest = info.get("version")
    if not verify.is_version(latest):
        return unknown(
            "malformed",
            f"{index} reports a version the release grammar does not admit: "
            f"{latest!r}",
        )
    latest = str(latest)
    if bool(info.get("yanked")):
        return unknown(
            "yanked",
            f"version {latest} is withdrawn from {index} and will not be offered",
        )

    if not is_newer(latest, running):
        return _document(
            status="up_to_date",
            reason="ok",
            running=running,
            latest=latest,
            detail=(
                f"{running} is the newest release"
                if latest == running
                else f"the newest release is {latest}; this install runs {running}"
            ),
            index=index,
            checked_at_ms=now_ms,
            retry_after_at=now_ms + SUCCESS_TTL_MS,
        )

    try:
        release = source.release(index, latest, timeout=timeout)
        assets, pypi_digests, wheel = read_release(release)
    except Exception as error:  # noqa: BLE001 - every failure has a reason code
        return unknown(*classify(error))
    if not wheel:
        return unknown(
            "witness_missing",
            f"{index} describes no wheel for version {latest}, so the two "
            "witnesses have nothing in common to compare",
        )

    github: dict[str, str] = {}
    try:
        github = parse_sha256sums(source.sums(latest, timeout=timeout))
    except Exception as error:  # noqa: BLE001 - every failure has a reason code
        reason, detail = classify(error)
        if not allow_single_witness:
            return unknown(reason, detail)
        github = {}

    try:
        witnesses = verify.agree(
            latest,
            pypi_digests,
            github,
            wheel=wheel,
            allow_single_witness=allow_single_witness,
        )
    except UpdateRefusal as error:
        return unknown(*classify(error))

    trust = witnesses.trust
    return _document(
        status="update_available",
        reason="ok",
        running=running,
        latest=latest,
        detail=(
            f"{latest} is available (running {running}); "
            f"{trust['integrity']}; {trust['authorship_detail']}"
        ),
        index=index,
        assets=assets,
        witnesses=witnesses.as_dict(),
        checked_at_ms=now_ms,
        retry_after_at=now_ms + SUCCESS_TTL_MS,
    )


def check(
    cfg: object | None = None,
    *,
    refresh: bool = False,
    running: str | None = None,
    timeout: float = 10.0,
    clock: Callable[[], int] | None = None,
    allow_single_witness: bool = True,
) -> dict:
    """Ask whether a newer release exists, and never guess.

    ``clock`` returns milliseconds and exists so a test can pin the cache's
    freshness arithmetic instead of sleeping. ``allow_single_witness`` is True
    here because a *read* must still report a new version when github.com is
    unreachable — it records the weaker trust line rather than pretending.
    `apply` passes False, where a missing witness installs nothing.

    Returns the document `_document` describes. It never raises for a network
    failure, a malformed document or a witness disagreement; each of those is a
    ``status="unknown"`` with its own `REASONS` code. The only exceptions that
    escape are programming errors.
    """
    now = int((clock or _now_ms)())
    version = str(running if running is not None else running_version())

    try:
        index = resolved_index()
    except UpdateError as error:
        return _uncached(
            _document(
                status="unknown",
                reason="bad_index",
                running=version,
                detail=str(error),
                source="none",
                index=DEFAULT_INDEX,
                checked_at_ms=now,
                retry_after_at=now + FAILURE_RETRY_MS,
            ),
            now,
        )

    # Before the cache, on purpose. See the module docstring.
    if offline_pinned():
        return _uncached(
            _document(
                status="unknown",
                reason="offline_pin",
                running=version,
                detail=_OFFLINE_DETAIL,
                source="offline",
                index=index,
                checked_at_ms=now,
                retry_after_at=now + FAILURE_RETRY_MS,
            ),
            now,
        )

    if not verify.is_version(version):
        # Abstain. Comparing an unparseable running version against a release
        # would produce an ordering nobody can defend, and the honest answer to
        # "are you behind" is that we cannot tell.
        return _uncached(
            _document(
                status="unknown",
                reason="unparsable_running",
                running=version,
                detail=(
                    f"this install reports version {version!r}, which is not "
                    "X.Y.Z, so it cannot be compared with a release"
                ),
                source="none",
                index=index,
                checked_at_ms=now,
                retry_after_at=now + FAILURE_RETRY_MS,
            ),
            now,
        )

    path = cache_path(cfg)
    # Read unconditionally, `refresh` included: a forced re-check that fails
    # must still be able to say what the last known answer was.
    cached = read_cache(path)
    if not refresh and cached is not None and _fresh(cached, now, version, index):
        # Rebuilt through `_document` rather than returned as it was stored, so
        # a cache written by an older build cannot hand a caller a document
        # missing a key the current shape promises is always there.
        stored_assets = cached.get("assets")
        stored_witnesses = cached.get("witnesses")
        stored_previous = cached.get("previous")
        answer = _document(
            status=str(cached["status"]),
            reason=str(cached["reason"]),
            running=version,
            latest=str(cached.get("latest") or ""),
            detail=str(cached.get("detail") or ""),
            source=str(cached["source"]),
            index=index,
            assets=stored_assets if isinstance(stored_assets, list) else [],
            witnesses=stored_witnesses if isinstance(stored_witnesses, dict) else {},
            previous=stored_previous if isinstance(stored_previous, dict) else {},
            checked_at_ms=int(cached.get("checked_at_ms") or 0),
            retry_after_at=int(cached.get("retry_after_at") or 0),
        )
        answer["cached"] = True
        answer["age_s"] = max(0, (now - int(cached.get("checked_at_ms") or 0)) // 1000)
        return answer

    document = _live(
        index=index,
        running=version,
        timeout=timeout,
        now_ms=now,
        allow_single_witness=allow_single_witness,
    )
    if document["status"] == "unknown":
        document["previous"] = previous_known(cached, version, index)
    write_cache(path, document)
    return _uncached(document, now)


def _uncached(document: dict, now_ms: int) -> dict:
    """Stamp a document that was just read live rather than served from
    the cache. Named apart from `_fresh` above, which answers the other
    question: whether a *stored* document is still an answer."""
    answer = dict(document)
    answer["cached"] = False
    answer["age_s"] = 0
    return answer


__all__ = [
    "ASSET_HOSTS",
    "DEFAULT_INDEX",
    "FAILURE_RETRY_MS",
    "HttpSource",
    "OfflineSource",
    "PROJECT",
    "REASONS",
    "ReleaseSource",
    "SOURCES",
    "STATUSES",
    "SUCCESS_TTL_MS",
    "cache_path",
    "check",
    "classify",
    "data_dir_for",
    "is_newer",
    "offline_pinned",
    "parse_sha256sums",
    "parse_version",
    "previous_known",
    "read_cache",
    "read_release",
    "resolved_index",
    "resolved_source",
    "running_version",
    "write_cache",
]
