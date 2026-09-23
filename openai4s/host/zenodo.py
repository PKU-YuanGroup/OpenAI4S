"""Bounded, metadata-only discovery of published Zenodo datasets.

The caller supplies ScienceConnectorService._json, so retrieval keeps the
shared network policy and response-byte provenance. A record DOI identifies
one published record; a concept DOI groups versions. File checksums below are
source declarations, never claims that this process downloaded those files.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from typing import Any, Callable

from openai4s.host.science import ScienceConnectorError

MAX_PAGE_SIZE = 25  # The public API's anonymous-request limit.
MAX_RECORD_FILES = 1000
MAX_RESULT_BYTES = 80_000  # Leave room for the common tool/provenance envelope.
# Zenodo answers HTTP 400 for a page starting at or past this many hits, yet
# still advertises `links.next` on the last page inside the window.
MAX_RESULT_WINDOW = 10_000
_DOI = re.compile(r"10\.\d{4,9}/\S+", re.IGNORECASE)
_CHECKSUM = re.compile(r"(?:md5:[a-fA-F0-9]{32}|sha256:[a-fA-F0-9]{64})")


def dataset_file_url(record_id: str, file_key: str) -> str:
    """Address an exact file without following metadata-supplied links."""
    return f"https://zenodo.org/api/records/{record_id}/files/{urllib.parse.quote(file_key, safe='')}/content"


def _text(value: Any, name: str, limit: int = 1000) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise ScienceConnectorError(f"Zenodo {name} is invalid or too large")
    return value.strip() or None


def _doi(value: Any, name: str) -> str | None:
    result = _text(value, name, 500)
    if result is not None and not _DOI.fullmatch(result):
        raise ScienceConnectorError(f"Zenodo {name} is not a DOI")
    return result


def _integer(value: Any, name: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScienceConnectorError(f"Zenodo {name} must be an integer")
    if value < (1 if positive else 0):
        raise ScienceConnectorError(f"Zenodo {name} is out of range")
    return value


def _files(
    row: dict[str, Any], record_id: str, access_right: str | None
) -> dict[str, Any]:
    raw = row.get("files")
    # Zenodo lists no files for restricted or embargoed records that do have
    # files. Only an open record's empty list declares an empty inventory.
    if raw is None or (raw == [] and access_right != "open"):
        return {"files": None, "file_count": None, "declared_total_bytes": None}
    if not isinstance(raw, list) or len(raw) > MAX_RECORD_FILES:
        raise ScienceConnectorError("Zenodo file inventory is invalid or too large")
    files = []
    names: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ScienceConnectorError("Zenodo file inventory contains a non-object")
        raw_name = item.get("key")
        if not _text(raw_name, "file key", 512) or raw_name in names:
            raise ScienceConnectorError("Zenodo file key is missing or duplicated")
        # Whitespace is part of a remote object key. Validate its shape without
        # changing the identity a later explicit file selection must name.
        name = raw_name
        names.add(name)
        size = item.get("size")
        if size is not None:
            size = _integer(size, "file size")
        checksum = _text(item.get("checksum"), "file checksum", 80)
        if checksum is not None and not _CHECKSUM.fullmatch(checksum):
            raise ScienceConnectorError(
                "Zenodo file checksum is unsupported or invalid"
            )
        # Keep the source's file key as metadata, never as a local path. The
        # metadata read does not authorize following a file URL or importing it.
        files.append(
            {
                "key": name,
                "declared_size_bytes": size,
                "declared_checksum": checksum.lower() if checksum else None,
                "record_id": record_id,
                "download_url": dataset_file_url(record_id, name),
            }
        )
    complete_sizes = all(item["declared_size_bytes"] is not None for item in files)
    return {
        "files": files,
        "file_count": len(files),
        "declared_total_bytes": (
            sum(item["declared_size_bytes"] for item in files)
            if complete_sizes
            else None
        ),
    }


def _record(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ScienceConnectorError("Zenodo result must be an object")
    record_id = str(_integer(row.get("id"), "record id", positive=True))
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        raise ScienceConnectorError("Zenodo record metadata is missing")
    resource_type = metadata.get("resource_type")
    if not isinstance(resource_type, dict) or resource_type.get("type") != "dataset":
        raise ScienceConnectorError("Zenodo returned a non-dataset record")
    title = _text(metadata.get("title"), "title")
    if title is None:
        raise ScienceConnectorError("Zenodo dataset title is missing")
    doi = _doi(row.get("doi") or metadata.get("doi"), "record DOI")
    concept_doi = _doi(row.get("conceptdoi"), "concept DOI")
    license_record = metadata.get("license")
    if license_record is not None and not isinstance(license_record, dict):
        raise ScienceConnectorError("Zenodo license must be an object or unknown")
    license_id = _text((license_record or {}).get("id"), "license id", 256)
    access_right = _text(metadata.get("access_right"), "access right", 128)
    return {
        "id": record_id,
        "title": title,
        "url": f"https://zenodo.org/records/{record_id}",
        "type": "dataset",
        "attributes": {
            "record_doi": doi,
            "concept_doi": concept_doi,
            "version": _text(metadata.get("version"), "version", 256),
            "publication_date": _text(metadata.get("publication_date"), "date", 64),
            "access_right": access_right,
            "declared_license": license_id,
            "license_status": "declared_by_source" if license_id else "unknown",
            "file_verification": "not_downloaded",
            **_files(row, record_id, access_right),
        },
    }


def search(
    fetch_json: Callable[..., Any], query: str, limit: int, cursor: str, timeout: float
) -> tuple[list[dict[str, Any]], str, str]:
    """Return one bounded page; a cursor cannot change the original query/size."""
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise ScienceConnectorError("Zenodo public searches support a limit of 1 to 25")
    binding = hashlib.sha256(f"{query}\0{limit}".encode()).hexdigest()[:24]
    page = 1
    if cursor:
        match = re.fullmatch(r"zenodo:([1-9][0-9]{0,5}):([a-f0-9]{24})", cursor)
        if match is None or match.group(2) != binding:
            raise ScienceConnectorError(
                "Zenodo cursor does not match the query and limit"
            )
        page = int(match.group(1))
        if (page - 1) * limit >= MAX_RESULT_WINDOW:
            raise ScienceConnectorError(
                "Zenodo cursor is past the 10,000-result search window"
            )
    params = urllib.parse.urlencode(
        {"q": query, "type": "dataset", "size": limit, "page": page}
    )
    url = f"https://zenodo.org/api/records?{params}"
    payload = fetch_json(url, timeout)
    if not isinstance(payload, dict) or not isinstance(payload.get("hits"), dict):
        raise ScienceConnectorError("Zenodo returned an unexpected result schema")
    rows = payload["hits"].get("hits")
    if not isinstance(rows, list) or len(rows) > limit:
        raise ScienceConnectorError(
            "Zenodo results are invalid or exceed the page limit"
        )
    results = [_record(row) for row in rows]
    if len({row["id"] for row in results}) != len(results):
        raise ScienceConnectorError("Zenodo returned duplicate record identities")
    if len(json.dumps(results, ensure_ascii=False).encode()) > MAX_RESULT_BYTES:
        raise ScienceConnectorError(
            "Zenodo metadata is too large; use a smaller result limit"
        )
    links = payload.get("links", {})
    if not isinstance(links, dict):
        raise ScienceConnectorError("Zenodo pagination metadata is invalid")
    # A next link is only a continuation hint. Never fetch a supplied URL: the
    # following page is rebuilt against the same fixed public endpoint. Past
    # the search window the hint is stale, so it ends pagination instead.
    next_page = (
        bool(links.get("next")) and bool(rows) and page * limit < MAX_RESULT_WINDOW
    )
    return results, f"zenodo:{page + 1}:{binding}" if next_page else "", url
