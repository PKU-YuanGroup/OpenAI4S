"""Stage 10 ClinVar, PubMed, and ClinicalTrials.gov connectors.

These sources stay out of the default catalog.  Enabling
``stage10_scientific_connectors`` adds them to the same envelope as UniProt,
with pagination, a short response cache, honest empty/429/schema errors, and
an Artifact-shaped provenance payload.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
from collections.abc import Mapping
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
_CACHE: dict[str, tuple[float, Any]] = {}


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
    search = _json(service, search_url, timeout)
    result = search.get("esearchresult") if isinstance(search, dict) else None
    if not isinstance(result, dict):
        raise ScienceConnectorError("ClinVar returned an unexpected search schema")
    ids = [str(item) for item in (result.get("idlist") or []) if item]
    if not ids:
        return [], "", search_url
    summary_url = _ncbi(
        {"op": "esummary.fcgi", "db": "clinvar", "id": ",".join(ids[:limit])}
    )
    summary = _json(service, summary_url, timeout)
    payload = summary.get("result") if isinstance(summary, dict) else None
    if not isinstance(payload, dict):
        raise ScienceConnectorError("ClinVar returned an unexpected summary schema")
    records = []
    for uid in ids[:limit]:
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
    return records, "", search_url


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
    search = _json(service, search_url, timeout)
    result = search.get("esearchresult") if isinstance(search, dict) else None
    if not isinstance(result, dict):
        raise ScienceConnectorError("PubMed returned an unexpected search schema")
    ids = [str(item) for item in (result.get("idlist") or []) if item]
    count = int(result.get("count") or 0)
    if not ids:
        return [], "", search_url
    summary_url = _ncbi(
        {"op": "esummary.fcgi", "db": "pubmed", "id": ",".join(ids[:limit])}
    )
    summary = _json(service, summary_url, timeout)
    payload = summary.get("result") if isinstance(summary, dict) else None
    if not isinstance(payload, dict):
        raise ScienceConnectorError("PubMed returned an unexpected summary schema")
    records = []
    for pmid in ids[:limit]:
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
    return records, next_cursor, search_url


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
    payload = _json(service, url, timeout)
    if not isinstance(payload, dict) or not isinstance(payload.get("studies"), list):
        raise ScienceConnectorError(
            "ClinicalTrials.gov returned an unexpected result schema"
        )
    records = []
    for study in payload.get("studies") or []:
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
    return records[:limit], _string(payload.get("nextPageToken") or ""), url


def _json(service: Any, url: str, timeout: float) -> Any:
    now = time.time()
    cached = _CACHE.get(url)
    if cached and now - cached[0] < _CACHE_TTL_S:
        return cached[1]
    try:
        payload = service._json(url, timeout)
    except ScienceConnectorError as error:
        message = str(error).lower()
        if "429" in message or "too many requests" in message:
            raise ScienceConnectorError(
                "upstream rate limited (429); retry later"
            ) from error
        raise
    _CACHE[url] = (now, payload)
    return payload


def record_search_artifact(
    store: Any,
    workspace: Path,
    result: Mapping[str, Any],
    *,
    root_frame_id: str,
    project_id: str = "default",
) -> dict[str, Any]:
    """Persist one search as a versioned Artifact with source provenance."""

    database = _string(result.get("database") or "science")
    digest = hashlib.sha256(
        json.dumps(result, ensure_ascii=False, sort_keys=True, default=str).encode(
            "utf-8"
        )
    ).hexdigest()
    filename = f"science-{database}-{digest[:12]}.json"
    path = Path(workspace) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "query": result.get("query"),
        "endpoint": result.get("request_url"),
        "retrieved_at": (result.get("provenance") or {}).get("retrieved_at"),
        "source_checksum": (result.get("provenance") or {}).get("response_sha256"),
        "accessions": [item.get("id") for item in result.get("results") or []],
        "result": result,
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    path.write_bytes(encoded)
    return store.save_artifact(
        path=str(path),
        filename=filename,
        content_type="application/json",
        size_bytes=len(encoded),
        checksum=hashlib.sha256(encoded).hexdigest(),
        frame_id=root_frame_id,
        project_id=project_id,
        source={
            "kind": "science_search",
            "database": database,
            "query": result.get("query"),
            "endpoint": result.get("request_url"),
            "retrieved_at": payload["retrieved_at"],
            "source_checksum": payload["source_checksum"],
            "accessions": payload["accessions"],
        },
    )


SEARCHERS = {
    "clinvar": search_clinvar,
    "pubmed": search_pubmed,
    "clinicaltrials": search_clinicaltrials,
}
