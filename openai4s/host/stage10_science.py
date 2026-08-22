"""Stage 10 ClinVar, PubMed, and ClinicalTrials.gov connectors.

These sources stay out of the default catalog.  Enabling
``stage10_scientific_connectors`` adds them to the same envelope as UniProt,
with pagination, a short response cache, honest empty/429/schema errors, and
an Artifact-shaped provenance payload.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
import time
import urllib.parse
import uuid
from collections import OrderedDict
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from openai4s.host.science import (
    ScienceConnectorError,
    ScienceDatabase,
    _record,
    _string,
)

STAGE10_DATABASES: tuple[ScienceDatabase, ...] = (
    ScienceDatabase(
        "clinvar",
        "ClinVar",
        "Clinically observed variants, accessions, and interpretation summaries.",
        ("biology",),
        "variant",
        "Variant accession (VCV/RCV), rs id, gene, or ClinVar text query.",
    ),
    ScienceDatabase(
        "pubmed",
        "PubMed",
        "Biomedical literature citations from MEDLINE and PubMed Central.",
        ("literature", "biology"),
        "article",
        "PubMed query, PMID, author, journal, or MeSH term.",
    ),
    ScienceDatabase(
        "clinicaltrials",
        "ClinicalTrials.gov",
        "Registered interventional and observational studies.",
        ("biology", "literature"),
        "study",
        "Condition, intervention, NCT id, or free-text study query.",
    ),
)

_NCBI = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_CTGOV = "https://clinicaltrials.gov/api/v2/studies"
_CACHE_TTL_S = 60.0
_CACHE_MAX_ENTRIES = 32
#: The cache holds canonical JSON bytes, not upstream Python object graphs.
#: Normalized Stage 10 records are deliberately small, so a response larger
#: than this is useful to its caller but is not worth retaining process-wide.
_CACHE_MAX_ENTRY_BYTES = 256_000
#: A count cap alone is not a memory cap: 32 near-limit upstream responses used
#: to retain hundreds of megabytes of parsed lists/dicts in the shared daemon.
#: This budget counts keys plus encoded values and evicts by the same LRU order.
_CACHE_MAX_BYTES = 2_000_000
_CACHE_CLOCK: Callable[[], float] = time.monotonic
_CACHE_LOCK = threading.RLock()
_CACHE: OrderedDict[str, tuple[float, bytes]] = OrderedDict()


def _cache_size_bytes_locked() -> int:
    return sum(
        len(key.encode("utf-8")) + len(encoded)
        for key, (_stored_at, encoded) in _CACHE.items()
    )


def _prune_cache_locked(now: float) -> None:
    """Discard expired entries and enforce both LRU cache bounds."""

    expired = [
        key
        for key, (stored_at, _encoded) in _CACHE.items()
        if now - stored_at >= _CACHE_TTL_S
    ]
    for key in expired:
        _CACHE.pop(key, None)
    while len(_CACHE) > _CACHE_MAX_ENTRIES or (
        _CACHE and _cache_size_bytes_locked() > _CACHE_MAX_BYTES
    ):
        _CACHE.popitem(last=False)


def _decode_cached_search(
    encoded: bytes,
) -> tuple[list[dict[str, Any]], str, str, tuple[dict[str, Any], ...]] | None:
    """Decode one internally-produced, normalized search result."""

    try:
        envelope = json.loads(encoded.decode("utf-8"))
    except (TypeError, UnicodeError, ValueError):
        return None
    if not isinstance(envelope, dict):
        return None
    result = envelope.get("result")
    observations = envelope.get("observations")
    if not isinstance(result, dict) or not isinstance(observations, list):
        return None
    records = result.get("records")
    next_cursor = result.get("next_cursor")
    request_url = result.get("request_url")
    if (
        not isinstance(records, list)
        or any(not isinstance(item, dict) for item in records)
        or not isinstance(next_cursor, str)
        or not isinstance(request_url, str)
        or any(not isinstance(item, dict) for item in observations)
    ):
        return None
    return (
        [dict(item) for item in records],
        next_cursor,
        request_url,
        tuple(dict(item) for item in observations),
    )


def _cached_search(
    service: Any, key: str
) -> tuple[list[dict[str, Any]], str, str] | None:
    """Return a caller-owned normalized result without decoding under the lock."""

    now = _CACHE_CLOCK()
    with _CACHE_LOCK:
        _prune_cache_locked(now)
        cached = _CACHE.get(key)
        if cached is None:
            return None
        _CACHE.move_to_end(key)
        _stored_at, encoded = cached

    # JSON decoding creates the caller-owned copy and can allocate. Keep that
    # work outside the global cache lock so another session never waits behind
    # a cache hit's materialisation.
    decoded = _decode_cached_search(encoded)
    if decoded is None:
        # Internal corruption is a miss, never partially trusted evidence.
        with _CACHE_LOCK:
            current = _CACHE.get(key)
            if current is not None and current[1] == encoded:
                _CACHE.pop(key, None)
        return None
    records, next_cursor, request_url, observations = decoded
    responses = getattr(service, "_responses", None)
    if isinstance(responses, list):
        responses.extend(observations)
    return records, next_cursor, request_url


def _store_cached_search(
    key: str,
    result: tuple[list[dict[str, Any]], str, str],
    observations: tuple[dict[str, Any], ...],
) -> None:
    """Store one schema-checked, normalized result within the byte budget."""

    records, next_cursor, request_url = result
    envelope = {
        "result": {
            "records": records,
            "next_cursor": next_cursor,
            "request_url": request_url,
        },
        "observations": list(observations),
    }
    try:
        encoded = json.dumps(
            envelope,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError):
        # A normalized result should always be JSON-safe. If a future adapter
        # changes that contract, serve the response but do not retain it.
        return
    entry_bytes = len(key.encode("utf-8")) + len(encoded)
    if entry_bytes > _CACHE_MAX_ENTRY_BYTES or entry_bytes > _CACHE_MAX_BYTES:
        return
    now = _CACHE_CLOCK()
    with _CACHE_LOCK:
        _prune_cache_locked(now)
        _CACHE[key] = (now, encoded)
        _CACHE.move_to_end(key)
        _prune_cache_locked(now)


def _cache_observations(service: Any, before: int) -> tuple[dict[str, Any], ...]:
    responses = getattr(service, "_responses", None)
    if not isinstance(responses, list):
        return ()
    return tuple(dict(item) for item in responses[before:] if isinstance(item, dict))


def official_stage10_enabled(config: Any) -> bool:
    flags = getattr(config, "roadmap_features", None)
    return bool(
        flags is not None and getattr(flags, "stage10_scientific_connectors", False)
    )


def _ncbi(params: Mapping[str, Any]) -> str:
    query = dict(params)
    query.setdefault("tool", "openai4s")
    query.setdefault("retmode", "json")
    return f"{_NCBI}/{query.pop('op')}?{urllib.parse.urlencode(query)}"


def search_clinvar(
    service: Any,
    query: str,
    limit: int,
    cursor: str,
    filters: Mapping[str, Any],
    timeout: float,
):
    del cursor, filters
    search_url = _ncbi(
        {
            "op": "esearch.fcgi",
            "db": "clinvar",
            "term": query,
            "retmax": limit,
            "retstart": 0,
        }
    )
    cache_key = f"clinvar:{search_url}"
    cached = _cached_search(service, cache_key)
    if cached is not None:
        return cached
    responses = getattr(service, "_responses", None)
    before = len(responses) if isinstance(responses, list) else 0
    search = _json(service, search_url, timeout)
    result = search.get("esearchresult") if isinstance(search, dict) else None
    raw_ids = result.get("idlist") if isinstance(result, dict) else None
    if not isinstance(result, dict) or not isinstance(raw_ids, list):
        raise ScienceConnectorError("ClinVar returned an unexpected search schema")
    ids = [str(item) for item in raw_ids[:limit] if item]
    if not ids:
        normalized = ([], "", search_url)
        _store_cached_search(
            cache_key, normalized, _cache_observations(service, before)
        )
        return normalized
    summary_url = _ncbi({"op": "esummary.fcgi", "db": "clinvar", "id": ",".join(ids)})
    summary = _json(service, summary_url, timeout)
    payload = summary.get("result") if isinstance(summary, dict) else None
    if not isinstance(payload, dict):
        raise ScienceConnectorError("ClinVar returned an unexpected summary schema")
    records = []
    for uid in ids:
        row = payload.get(uid)
        if not isinstance(row, dict):
            continue
        accession = _string(row.get("accession") or row.get("accession_version") or uid)
        if not accession:
            continue
        title = _string(row.get("title") or row.get("variation_set_name") or accession)
        records.append(
            _record(
                accession,
                title,
                f"https://www.ncbi.nlm.nih.gov/clinvar/variation/{urllib.parse.quote(uid)}/",
                "variant",
                {
                    "uid": uid,
                    "accession": accession,
                    "gene": _string(row.get("genes") or row.get("gene_sort")),
                    "clinical_significance": _string(
                        row.get("clinical_significance")
                        or row.get("germline_classification")
                    ),
                    "review_status": _string(row.get("review_status")),
                },
            )
        )
    normalized = (records[:limit], "", search_url)
    _store_cached_search(cache_key, normalized, _cache_observations(service, before))
    return normalized


def search_pubmed(
    service: Any,
    query: str,
    limit: int,
    cursor: str,
    filters: Mapping[str, Any],
    timeout: float,
):
    del filters
    start = int(cursor or 0)
    search_url = _ncbi(
        {
            "op": "esearch.fcgi",
            "db": "pubmed",
            "term": query,
            "retmax": limit,
            "retstart": start,
        }
    )
    cache_key = f"pubmed:{search_url}"
    cached = _cached_search(service, cache_key)
    if cached is not None:
        return cached
    responses = getattr(service, "_responses", None)
    before = len(responses) if isinstance(responses, list) else 0
    search = _json(service, search_url, timeout)
    result = search.get("esearchresult") if isinstance(search, dict) else None
    raw_ids = result.get("idlist") if isinstance(result, dict) else None
    if not isinstance(result, dict) or not isinstance(raw_ids, list):
        raise ScienceConnectorError("PubMed returned an unexpected search schema")
    ids = [str(item) for item in raw_ids[:limit] if item]
    try:
        count = int(result.get("count") or 0)
    except (TypeError, ValueError) as error:
        raise ScienceConnectorError(
            "PubMed returned an unexpected search schema"
        ) from error
    if not ids:
        normalized = ([], "", search_url)
        _store_cached_search(
            cache_key, normalized, _cache_observations(service, before)
        )
        return normalized
    summary_url = _ncbi({"op": "esummary.fcgi", "db": "pubmed", "id": ",".join(ids)})
    summary = _json(service, summary_url, timeout)
    payload = summary.get("result") if isinstance(summary, dict) else None
    if not isinstance(payload, dict):
        raise ScienceConnectorError("PubMed returned an unexpected summary schema")
    records = []
    for pmid in ids:
        row = payload.get(pmid)
        if not isinstance(row, dict):
            continue
        title = _string(row.get("title") or pmid)
        records.append(
            _record(
                pmid,
                title,
                f"https://pubmed.ncbi.nlm.nih.gov/{urllib.parse.quote(pmid)}/",
                "article",
                {
                    "pmid": pmid,
                    "journal": _string(row.get("fulljournalname") or row.get("source")),
                    "pubdate": _string(row.get("pubdate")),
                    "doi": _string((row.get("elocationid") or "").replace("doi: ", "")),
                },
            )
        )
    next_cursor = str(start + len(ids)) if start + len(ids) < count else ""
    normalized = (records[:limit], next_cursor, search_url)
    _store_cached_search(cache_key, normalized, _cache_observations(service, before))
    return normalized


def search_clinicaltrials(
    service: Any,
    query: str,
    limit: int,
    cursor: str,
    filters: Mapping[str, Any],
    timeout: float,
):
    del filters
    params = {
        "query.term": query,
        "pageSize": limit,
        "countTotal": "true",
        "format": "json",
    }
    if cursor:
        params["pageToken"] = cursor
    url = f"{_CTGOV}?{urllib.parse.urlencode(params)}"
    cache_key = f"clinicaltrials:{url}"
    cached = _cached_search(service, cache_key)
    if cached is not None:
        return cached
    responses = getattr(service, "_responses", None)
    before = len(responses) if isinstance(responses, list) else 0
    payload = _json(service, url, timeout)
    if not isinstance(payload, dict) or not isinstance(payload.get("studies"), list):
        raise ScienceConnectorError(
            "ClinicalTrials.gov returned an unexpected result schema"
        )
    records = []
    for study in (payload.get("studies") or [])[:limit]:
        if not isinstance(study, dict):
            continue
        ident = (study.get("protocolSection") or {}).get("identificationModule") or {}
        nct = _string(ident.get("nctId"))
        if not nct:
            continue
        title = _string(ident.get("briefTitle") or ident.get("officialTitle") or nct)
        records.append(
            _record(
                nct,
                title,
                f"https://clinicaltrials.gov/study/{urllib.parse.quote(nct)}",
                "study",
                {
                    "nct_id": nct,
                    "organization": _string(
                        ((ident.get("organization") or {}).get("fullName"))
                    ),
                    "overall_status": _string(
                        (
                            (study.get("protocolSection") or {}).get("statusModule")
                            or {}
                        ).get("overallStatus")
                    ),
                },
            )
        )
    normalized = (
        records[:limit],
        _string(payload.get("nextPageToken") or ""),
        url,
    )
    _store_cached_search(cache_key, normalized, _cache_observations(service, before))
    return normalized


def _json(service: Any, url: str, timeout: float) -> Any:
    """Fetch one upstream payload without retaining its untrusted object graph.

    Search adapters cache only after the complete response schema has been
    checked and projected into bounded normalized records. This helper must not
    cache: doing so would retain invalid schemas before their caller rejects
    them and would keep fields that never appear in the public result.
    """

    try:
        return service._json(url, timeout)
    except ScienceConnectorError as error:
        message = str(error).lower()
        if "429" in message or "too many requests" in message:
            raise ScienceConnectorError(
                "upstream rate limited (429); retry later"
            ) from error
        raise


def write_search_artifact(workspace: Path, result: Mapping[str, Any]) -> dict[str, Any]:
    """Write one deterministic result beneath the trusted workspace boundary.

    This is intentionally only a file callback.  It cannot reach the Store or
    mint Artifact identities; the Gateway's declared native-writer capture
    performs that trusted conversion after the tool returns.
    """

    database = _string(result.get("database") or "science")
    digest = hashlib.sha256(
        json.dumps(result, ensure_ascii=False, sort_keys=True, default=str).encode(
            "utf-8"
        )
    ).hexdigest()
    filename = f"science-{database}-{digest[:12]}.json"
    payload = {
        "query": result.get("query"),
        "endpoint": result.get("request_url"),
        "retrieved_at": (result.get("provenance") or {}).get("retrieved_at"),
        "source_checksum": (result.get("provenance") or {}).get("response_sha256"),
        "accessions": [item.get("id") for item in result.get("results") or []],
        "result": result,
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    checksum = hashlib.sha256(encoded).hexdigest()

    # Reuse the same fail-closed workspace traversal as native file tools. The
    # parent directory remains pinned from staged creation through publication,
    # so replacing the workspace pathname with a symlink cannot redirect the
    # write. Keep this import lazy: ordinary Stage 10 searches do no file I/O.
    from openai4s.host.files import UnsafeWorkspaceCandidate, WorkspaceFileService

    requested_workspace = Path(workspace).expanduser()
    files = WorkspaceFileService(
        data_dir=requested_workspace,
        frame_id=lambda: None,
        workspace=lambda: requested_workspace,
    )
    with files.secure_parent(filename, create_parents=True) as parent:
        existing = parent.target_metadata()
        if existing is not None and stat.S_ISLNK(existing.st_mode):
            raise UnsafeWorkspaceCandidate(
                "science Artifact target must not be a symlink"
            )

        descriptor, staged = parent.create_staged(
            suffix=f".{uuid.uuid4().hex}.science.part"
        )
        published_to_target = False
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or int(opened.st_nlink) != 1:
                raise UnsafeWorkspaceCandidate(
                    "science Artifact staging file is not a private regular file"
                )

            view = memoryview(encoded)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:  # pragma: no cover - OS write contract
                    raise OSError("science Artifact write made no progress")
                view = view[written:]
            os.fsync(descriptor)

            # An O_EXCL open protects only creation. Before a pathname-based
            # rename, prove that the sibling name still denotes the exact open
            # descriptor and that nobody added another hardlink to its inode.
            opened = os.fstat(descriptor)
            try:
                named = os.stat(staged, dir_fd=parent.fd, follow_symlinks=False)
            except FileNotFoundError:
                raise UnsafeWorkspaceCandidate(
                    "science Artifact staging file changed before publication"
                ) from None
            if (
                not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(named.st_mode)
                or int(opened.st_nlink) != 1
                or int(named.st_nlink) != 1
                or not os.path.samestat(opened, named)
                or int(opened.st_size) != len(encoded)
            ):
                raise UnsafeWorkspaceCandidate(
                    "science Artifact staging file changed before publication"
                )

            current = parent.target_metadata()
            if current is not None and stat.S_ISLNK(current.st_mode):
                raise UnsafeWorkspaceCandidate(
                    "science Artifact target became a symlink"
                )
            parent.publish(staged)
            published_to_target = True

            # Close the last race between the name check and rename. Read the
            # published target through the same pinned parent, then bind the
            # returned receipt to both the intended inode and exact bytes.
            published_digest = hashlib.sha256()
            published_bytes = 0
            with parent.open_verified_read() as published:
                if not os.path.samestat(opened, published.metadata):
                    raise UnsafeWorkspaceCandidate(
                        "science Artifact target changed during publication"
                    )
                while True:
                    chunk = published.handle.read(256 * 1024)
                    if not chunk:
                        break
                    published_bytes += len(chunk)
                    published_digest.update(chunk)
            if (
                published_bytes != len(encoded)
                or published_digest.hexdigest() != checksum
            ):
                raise UnsafeWorkspaceCandidate(
                    "science Artifact bytes changed during publication"
                )
            os.fsync(parent.fd)
        except BaseException:
            # A late substitution can win after the name/inode check but
            # before rename. Post-publication verification above refuses its
            # receipt; remove whichever writer-owned name the rename reached
            # so an external hardlink is not left aliased into the workspace.
            parent.discard(parent.leaf if published_to_target else staged)
            raise
        finally:
            os.close(descriptor)
    source = {
        "kind": "science_search",
        "database": database,
        "query": result.get("query"),
        "endpoint": result.get("request_url"),
        "retrieved_at": payload["retrieved_at"],
        "source_checksum": payload["source_checksum"],
        "accessions": payload["accessions"],
    }
    return {
        "filename": filename,
        "checksum": checksum,
        "source": source,
    }


SEARCHERS = {
    "clinvar": search_clinvar,
    "pubmed": search_pubmed,
    "clinicaltrials": search_clinicaltrials,
}
