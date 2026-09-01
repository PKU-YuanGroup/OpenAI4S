"""Stage 9 table profile and CSV export (flag-gated, stats never persisted).

The HTTP query parser here is the lock on the existing ``GET .../table``
five-parameter contract: ``sort`` (header-identical), ``dir=desc`` (the
literal only), ``q_{column_header}``, ``offset`` (non-integer 400), ``limit``
(non-integer 400). Pagination clamps stay in ``query_table``; this module
parses the wire and refuses parameters the profile/export surfaces do not
accept. Statistics live in a process LRU keyed by checksum + canonical
filters + schema version. They are not written to SQLite.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import threading
import unicodedata
from collections import Counter, OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from openai4s.server.artifact_workbench import (
    MAX_WORKBENCH_ARTIFACT_BYTES,
    WorkbenchError,
    infer_column_type,
)

TABLE_QUERY_PARSER_VERSION = 2
TABLE_PROFILE_SCHEMA_VERSION = 1
MAX_TABLE_PROFILE_BINS = 50
MAX_TABLE_EXPORT_CHUNK_BYTES = 1 * 1024 * 1024
# Exact distinct-value tracking is bounded inside the workbench resource
# envelope so a 250k-row text column cannot retain every unique cell. Hitting
# the cap reports a lower bound with ``approximate: true`` rather than a
# pretended exact unique count.
MAX_TABLE_PROFILE_UNIQUE_EXACT = 10_000
_PROFILE_CACHE_MAX_ENTRIES = 32
_PROFILE_CACHE_LOCK = threading.Lock()
_PROFILE_CACHE: OrderedDict[str, str] = OrderedDict()

TableQueryMode = Literal["page", "profile", "export"]
_PROFILE_FORBIDDEN = frozenset({"sort", "dir", "offset", "limit"})
_EXPORT_FORBIDDEN = frozenset({"offset", "limit"})
RESOURCE_MANIFEST_REQUIRED_FIELDS = (
    "machine",
    "os",
    "dependency_versions",
    "fixture_checksum",
    "warmup",
    "measurement_count",
    "rss_method",
    "wall_time_method",
    "thresholds",
    "approver",
)
RESOURCE_MANIFEST_MIN_MEASUREMENTS = 30


@dataclass(frozen=True)
class ParsedTableQuery:
    """Wire query after the shared parser. Pagination is unclamped here."""

    sort: str
    descending: bool
    filters: dict[str, str]
    offset: int
    limit: int
    version_id: str
    spreadsheet_safe: bool


def parquet_engine_available() -> bool:
    """True only when the optional Parquet engine can be imported.

    A missing science extra is ``unavailable``, never an advertised table
    capability. Import is attempted on each call so a test that stubs
    ``sys.modules`` is observed.
    """

    try:
        import pyarrow.parquet as _parquet  # type: ignore[import-not-found]
    except ImportError:
        return False
    return _parquet is not None


def integer_query(q: Mapping[str, Any], name: str, default: int) -> int:
    """Exact replica of the historical ``/table`` integer parser."""

    raw = (q.get(name) or [str(default)])[0]
    try:
        return int(raw or default)
    except (TypeError, ValueError) as error:
        raise WorkbenchError(
            400, f"{name} must be an integer", "invalid_query"
        ) from error


def _query_filters(q: Mapping[str, Any]) -> dict[str, str]:
    return {
        key[2:]: values[0]
        for key, values in q.items()
        if key.startswith("q_") and values
    }


def _version_id(q: Mapping[str, Any]) -> str:
    return str((q.get("version_id") or [""])[0] or "")


def _spreadsheet_safe(q: Mapping[str, Any], *, mode: TableQueryMode) -> bool:
    if "spreadsheet_safe" not in q:
        return False
    if mode != "export":
        raise WorkbenchError(
            400,
            "spreadsheet_safe is only accepted for CSV export",
            "invalid_query",
        )
    raw = str((q.get("spreadsheet_safe") or [""])[0] or "")
    if raw == "1":
        return True
    if raw == "0":
        return False
    raise WorkbenchError(
        400,
        "spreadsheet_safe must be 0 or 1",
        "invalid_query",
    )


def parse_table_query(
    q: Mapping[str, Any] | None,
    *,
    mode: TableQueryMode,
) -> ParsedTableQuery:
    """Parse one table workbench query, locking the historical ``/table`` rules.

    * ``page``: the five historical parameters, plus optional ``version_id``.
    * ``profile``: ``version_id`` required; ``sort``/``dir``/``offset``/``limit``
      are 400.
    * ``export``: ``version_id`` required; ``offset``/``limit`` are 400;
      ``sort``/``dir``/``q_`` use the same parser as ``page``;
      ``spreadsheet_safe=1`` opts into formula neutralization. Omission keeps
      the historical byte-faithful cell values.
    """

    query = dict(q or {})
    if mode == "profile":
        forbidden = sorted(name for name in _PROFILE_FORBIDDEN if name in query)
        if forbidden:
            raise WorkbenchError(
                400,
                "profile does not accept sort, dir, offset, or limit",
                "invalid_query",
            )
    elif mode == "export":
        forbidden = sorted(name for name in _EXPORT_FORBIDDEN if name in query)
        if forbidden:
            raise WorkbenchError(
                400,
                "export does not accept offset or limit",
                "invalid_query",
            )
    elif mode != "page":
        raise WorkbenchError(400, "invalid table query mode", "invalid_query")

    version_id = _version_id(query)
    spreadsheet_safe = _spreadsheet_safe(query, mode=mode)
    if mode in {"profile", "export"} and not version_id:
        raise WorkbenchError(400, "version_id is required", "invalid_query")

    if mode == "profile":
        return ParsedTableQuery(
            sort="",
            descending=False,
            filters=_query_filters(query),
            offset=0,
            limit=50,
            version_id=version_id,
            spreadsheet_safe=False,
        )

    return ParsedTableQuery(
        sort=(query.get("sort") or [""])[0],
        descending=(query.get("dir") or ["asc"])[0] == "desc",
        filters=_query_filters(query),
        offset=integer_query(query, "offset", 0) if mode == "page" else 0,
        limit=integer_query(query, "limit", 50) if mode == "page" else 50,
        version_id=version_id,
        spreadsheet_safe=spreadsheet_safe,
    )


def canonical_filter_key(filters: Mapping[str, str]) -> str:
    items = [{"name": name, "value": str(filters[name])} for name in sorted(filters)]
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


def canonical_profile_key(checksum: str, filters: Mapping[str, str]) -> str:
    return f"{checksum}|{TABLE_PROFILE_SCHEMA_VERSION}|{canonical_filter_key(filters)}"


def profile_cache_get(key: str) -> dict[str, Any] | None:
    with _PROFILE_CACHE_LOCK:
        encoded = _PROFILE_CACHE.get(key)
        if encoded is None:
            return None
        _PROFILE_CACHE.move_to_end(key)
    try:
        payload = json.loads(encoded)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def profile_cache_put(key: str, payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with _PROFILE_CACHE_LOCK:
        _PROFILE_CACHE[key] = encoded
        _PROFILE_CACHE.move_to_end(key)
        while len(_PROFILE_CACHE) > _PROFILE_CACHE_MAX_ENTRIES:
            _PROFILE_CACHE.popitem(last=False)


def profile_cache_clear() -> None:
    with _PROFILE_CACHE_LOCK:
        _PROFILE_CACHE.clear()


def _finite_numbers(values: Sequence[str], kind: str) -> list[float]:
    out: list[float] = []
    for raw in values:
        text = str(raw).strip()
        if text == "":
            continue
        try:
            number = float(int(text)) if kind == "integer" else float(text)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            out.append(number)
    return out


def _histogram(values: Sequence[float]) -> list[dict[str, Any]]:
    if not values:
        return []
    lo = min(values)
    hi = max(values)
    if lo == hi:
        return [{"start": lo, "end": hi, "count": len(values)}]
    bins = MAX_TABLE_PROFILE_BINS
    width = (hi - lo) / bins
    counts = [0] * bins
    for value in values:
        if value >= hi:
            index = bins - 1
        else:
            index = int((value - lo) / width)
            if index < 0:
                index = 0
            elif index >= bins:
                index = bins - 1
        counts[index] += 1
    return [
        {
            "start": lo + index * width,
            "end": lo + (index + 1) * width,
            "count": counts[index],
        }
        for index in range(bins)
    ]


def _text_histogram(values: Sequence[str]) -> tuple[list[dict[str, Any]], bool]:
    counts = Counter(values)
    approximate = len(counts) > MAX_TABLE_PROFILE_BINS
    ranked = counts.most_common(MAX_TABLE_PROFILE_BINS)
    return (
        [{"value": name, "count": count} for name, count in ranked],
        approximate,
    )


def profile_columns(
    columns: Sequence[str],
    types: Sequence[str],
    rows: Sequence[Sequence[str]],
) -> tuple[list[dict[str, Any]], bool]:
    """Per-column profile for one already-filtered rectangular table."""

    approximate = False
    unique_cap = MAX_TABLE_PROFILE_UNIQUE_EXACT
    profiles: list[dict[str, Any]] = []
    for index, name in enumerate(columns):
        kind = types[index] if index < len(types) else infer_column_type([])
        cells = [str(row[index] if index < len(row) else "") for row in rows]
        missing = sum(1 for cell in cells if cell.strip() == "")
        nonempty = [cell for cell in cells if cell.strip() != ""]
        seen: set[str] = set()
        unique_exact = True
        for cell in nonempty:
            if len(seen) >= unique_cap and cell not in seen:
                unique_exact = False
                break
            seen.add(cell)
        if not unique_exact:
            approximate = True
        unique = len(seen)
        column: dict[str, Any] = {
            "name": name,
            "type": kind,
            "missing": missing,
            "unique": unique,
            "min": None,
            "max": None,
            "mean": None,
            "histogram": [],
        }
        if kind in {"integer", "number"}:
            numbers = _finite_numbers(nonempty, kind)
            if numbers:
                lo = min(numbers)
                hi = max(numbers)
                mean = sum(numbers) / len(numbers)
                if kind == "integer" and all(
                    float(value).is_integer() for value in (lo, hi)
                ):
                    column["min"] = int(lo)
                    column["max"] = int(hi)
                else:
                    column["min"] = lo
                    column["max"] = hi
                column["mean"] = mean
                column["histogram"] = _histogram(numbers)
        else:
            histogram, hist_approx = _text_histogram(nonempty)
            column["histogram"] = histogram
            if hist_approx:
                approximate = True
        profiles.append(column)
    return profiles, approximate


def profile_from_prepared(prepared: Mapping[str, Any]) -> dict[str, Any]:
    columns = list(prepared.get("columns") or [])
    types = list(prepared.get("column_types") or [])
    rows = list(prepared.get("rows") or [])
    column_profiles, approximate = profile_columns(columns, types, rows)
    return {
        "filtered_rows": int(prepared.get("total_rows") or len(rows)),
        "approximate": approximate,
        "schema_version": TABLE_PROFILE_SCHEMA_VERSION,
        "columns": column_profiles,
        "filters": dict(prepared.get("filters") or {}),
    }


_SIGNED_NUMBER_RE = re.compile(
    r"[+-](?:(?:[0-9]+(?:\.[0-9]*)?)|(?:\.[0-9]+))(?:[eE][+-]?[0-9]+)?\Z"
)


def _formula_padding(character: str) -> bool:
    return character.isspace() or unicodedata.category(character) in {"Cc", "Cf"}


def _trim_formula_padding(value: str) -> str:
    start = 0
    end = len(value)
    while start < end and _formula_padding(value[start]):
        start += 1
    while end > start and _formula_padding(value[end - 1]):
        end -= 1
    return value[start:end]


def _spreadsheet_safe_csv_cell(value: str) -> str:
    """Neutralize spreadsheet formulas without rewriting numeric literals."""

    text = str(value)
    significant = _trim_formula_padding(text)
    if not significant or significant[0] not in "=@+-":
        return text
    if significant[0] in "+-" and _SIGNED_NUMBER_RE.fullmatch(significant):
        return text
    return "'" + text


def _encode_csv_row(row: Sequence[str], *, spreadsheet_safe: bool = False) -> bytes:
    buf = io.StringIO()
    values = [str(item) for item in row]
    if spreadsheet_safe:
        values = [_spreadsheet_safe_csv_cell(item) for item in values]
    csv.writer(buf, dialect=csv.excel).writerow(values)
    return buf.getvalue().encode("utf-8")


def export_csv_chunks(
    columns: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    chunk_bytes: int | None = None,
    total_bytes: int | None = None,
    spreadsheet_safe: bool = False,
) -> list[bytes]:
    """Serialize the full filtered table as CSV in bounded chunks.

    A single chunk never exceeds ``MAX_TABLE_EXPORT_CHUNK_BYTES`` (row
    boundaries preserved). The concatenated output never exceeds the 32 MiB
    workbench cap; overflow is 413 rather than a truncated file.
    """

    limit = MAX_TABLE_EXPORT_CHUNK_BYTES if chunk_bytes is None else int(chunk_bytes)
    cap = MAX_WORKBENCH_ARTIFACT_BYTES if total_bytes is None else int(total_bytes)
    if limit < 1 or cap < 1:
        raise WorkbenchError(413, "export limit is too small", "artifact_too_large")

    chunks: list[bytes] = []
    current = bytearray()
    produced = 0

    def flush() -> None:
        nonlocal produced
        if not current:
            return
        piece = bytes(current)
        if len(piece) > limit:
            raise WorkbenchError(
                413,
                f"export chunk exceeds the {limit}-byte workbench limit",
                "artifact_too_large",
            )
        produced += len(piece)
        if produced > cap:
            raise WorkbenchError(
                413,
                f"export exceeds the {cap}-byte workbench limit",
                "artifact_too_large",
            )
        chunks.append(piece)
        current.clear()

    def push(data: bytes) -> None:
        if len(data) > limit:
            raise WorkbenchError(
                413,
                f"export row exceeds the {limit}-byte chunk limit",
                "artifact_too_large",
            )
        if current and len(current) + len(data) > limit:
            flush()
        current.extend(data)

    push(_encode_csv_row(columns, spreadsheet_safe=spreadsheet_safe))
    for row in rows:
        push(_encode_csv_row(row, spreadsheet_safe=spreadsheet_safe))
    flush()
    return chunks


def csv_export_filename(filename: str) -> str:
    name = Path(str(filename or "table")).name
    stem = name.rsplit(".", 1)[0] if "." in name else name
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", stem)[:80] or "table"
    return f"{safe}.csv"


def export_response_headers(
    *,
    artifact_id: str,
    version_id: str,
    checksum: str,
    filtered_rows: int,
    filename: str,
    approximate: bool = False,
) -> dict[str, str]:
    return {
        "Content-Disposition": f'attachment; filename="{csv_export_filename(filename)}"',
        "X-Artifact-Id": str(artifact_id),
        "X-Version-Id": str(version_id),
        "X-Checksum": str(checksum),
        "X-Filtered-Rows": str(int(filtered_rows)),
        "X-Approximate": "true" if approximate else "false",
    }


def default_resource_manifest_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "table-workbench-resource-manifest.json"
    )


def resource_manifest_ready(path: Path | None = None) -> bool:
    """GA must not flip on while this returns false.

    The measurement test and the filled manifest are a separate decision
    after B-01/B-02/B-03. A missing or incomplete file is not ready.
    """

    target = default_resource_manifest_path() if path is None else Path(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError:
        return False
    try:
        payload = json.loads(raw)
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    for field in RESOURCE_MANIFEST_REQUIRED_FIELDS:
        if field not in payload:
            return False
    try:
        count = int(payload["measurement_count"])
    except (TypeError, ValueError):
        return False
    if count < RESOURCE_MANIFEST_MIN_MEASUREMENTS:
        return False
    if not str(payload.get("approver") or "").strip():
        return False
    if not str(payload.get("fixture_checksum") or "").strip():
        return False
    return True


def table_workbench_ga_blocked() -> bool:
    """True until a complete resource-acceptance manifest exists."""

    return not resource_manifest_ready()


__all__ = [
    "MAX_TABLE_EXPORT_CHUNK_BYTES",
    "MAX_TABLE_PROFILE_BINS",
    "MAX_TABLE_PROFILE_UNIQUE_EXACT",
    "ParsedTableQuery",
    "TABLE_PROFILE_SCHEMA_VERSION",
    "TABLE_QUERY_PARSER_VERSION",
    "canonical_profile_key",
    "csv_export_filename",
    "export_csv_chunks",
    "export_response_headers",
    "integer_query",
    "parse_table_query",
    "parquet_engine_available",
    "profile_cache_clear",
    "profile_cache_get",
    "profile_cache_put",
    "profile_from_prepared",
    "resource_manifest_ready",
    "table_workbench_ga_blocked",
]
