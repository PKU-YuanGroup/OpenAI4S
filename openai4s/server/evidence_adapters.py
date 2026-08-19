"""Read-only evidence adapters for frozen Artifact versions.

Filenames are never sufficient. Each adapter either returns a complete
representation or an honest incomplete coverage record. Optional science
libraries are imported only inside a try/except; missing them is recorded as
incomplete coverage rather than as a silent filename-only pass.
"""

from __future__ import annotations

import csv
import io
import json
import re
import struct
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_TABLE_SUFFIXES = (".csv", ".tsv", ".tab", ".json")
_PDF_SUFFIXES = (".pdf",)
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp")
_STRUCTURE_SUFFIXES = (".mol", ".sdf", ".smi", ".smiles")


def classify_artifact(filename: str, content_type: str = "") -> str | None:
    """Return the adapter kind required for this artifact, if any."""

    name = str(filename or "").lower()
    ctype = str(content_type or "").lower()
    if name.endswith(_PDF_SUFFIXES) or ctype == "application/pdf":
        return "pdf"
    if name.endswith(_IMAGE_SUFFIXES) or ctype.startswith("image/"):
        return "image"
    if name.endswith(_STRUCTURE_SUFFIXES) or ctype in {
        "chemical/x-mdl-molfile",
        "chemical/x-mdl-sdfile",
        "chemical/x-daylight-smiles",
    }:
        return "structure"
    if name.endswith(_TABLE_SUFFIXES) or ctype in {
        "text/csv",
        "text/tab-separated-values",
        "application/json",
    }:
        return "table"
    if name.endswith((".parquet", ".pq")) or ctype == "application/vnd.apache.parquet":
        return "table"
    return None


def _base(
    *,
    kind: str,
    version_id: str,
    artifact_id: str,
    complete: bool,
    summary: dict[str, Any],
    omission_reason: str | None = None,
) -> dict[str, Any]:
    row = {
        "adapter": kind,
        "version_id": version_id,
        "artifact_id": artifact_id,
        "complete": complete,
        "summary": summary,
    }
    if omission_reason:
        row["omission_reason"] = omission_reason
    return row


def adapt_table(path: Path, *, version_id: str, artifact_id: str) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix in {".parquet", ".pq"}:
        try:
            import pandas as pd  # type: ignore
        except ImportError:
            return _base(
                kind="table",
                version_id=version_id,
                artifact_id=artifact_id,
                complete=False,
                summary={"format": "parquet"},
                omission_reason="table_adapter_unavailable",
            )
        frame = pd.read_parquet(path)
        columns = {}
        for name in list(frame.columns)[:64]:
            series = frame[name]
            numeric = False
            try:
                numeric = bool(
                    getattr(series, "dtype", None) and series.dtype.kind in "iuf"
                )
            except Exception:  # noqa: BLE001
                numeric = False
            item: dict[str, Any] = {
                "null_count": (
                    int(series.isna().sum()) if hasattr(series, "isna") else 0
                ),
            }
            if numeric:
                item["min"] = float(series.min())
                item["max"] = float(series.max())
                item["mean"] = float(series.mean())
            columns[str(name)] = item
        return _base(
            kind="table",
            version_id=version_id,
            artifact_id=artifact_id,
            complete=True,
            summary={
                "format": "parquet",
                "row_count": int(len(frame)),
                "column_count": int(len(frame.columns)),
                "columns": columns,
            },
        )
    raw = path.read_bytes()[: 8 * 1024 * 1024]
    text = raw.decode("utf-8", errors="replace")
    if suffix == ".json":
        try:
            payload = json.loads(text)
        except ValueError:
            return _base(
                kind="table",
                version_id=version_id,
                artifact_id=artifact_id,
                complete=False,
                summary={"format": "json"},
                omission_reason="table_unreadable",
            )
        # `json.loads` returns whatever the file held: a scalar, a string, or
        # null are all valid JSON. Only a mapping has `.get`, and the caller
        # guards OSError alone, so an AttributeError here escapes all the way
        # out of evidence collection and disables the completion gate for the
        # whole turn.
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, Mapping):
            rows = payload.get("rows")
        else:
            rows = None
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            return _base(
                kind="table",
                version_id=version_id,
                artifact_id=artifact_id,
                complete=False,
                summary={"format": "json"},
                omission_reason="table_not_tabular",
            )
        fieldnames = list(rows[0].keys())
        values = {name: [] for name in fieldnames}
        for row in rows:
            if not isinstance(row, dict):
                continue
            for name in fieldnames:
                values[name].append(row.get(name))
        return _base(
            kind="table",
            version_id=version_id,
            artifact_id=artifact_id,
            complete=True,
            summary=_column_stats(values, format_name="json", row_count=len(rows)),
        )
    delimiter = "\t" if suffix in {".tsv", ".tab"} else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    fieldnames = list(reader.fieldnames or [])
    if not fieldnames:
        return _base(
            kind="table",
            version_id=version_id,
            artifact_id=artifact_id,
            complete=False,
            summary={"format": "csv"},
            omission_reason="table_unreadable",
        )
    values = {name: [] for name in fieldnames[:64]}
    row_count = 0
    for row in reader:
        row_count += 1
        for name in values:
            values[name].append(row.get(name))
    return _base(
        kind="table",
        version_id=version_id,
        artifact_id=artifact_id,
        complete=True,
        summary=_column_stats(
            values,
            format_name="tsv" if delimiter == "\t" else "csv",
            row_count=row_count,
        ),
    )


def _column_stats(
    values: dict[str, list[Any]], *, format_name: str, row_count: int
) -> dict[str, Any]:
    columns: dict[str, Any] = {}
    for name, cells in values.items():
        numeric: list[float] = []
        null_count = 0
        for cell in cells:
            if cell is None or str(cell).strip() == "":
                null_count += 1
                continue
            try:
                numeric.append(float(cell))
            except (TypeError, ValueError):
                continue
        item: dict[str, Any] = {"null_count": null_count}
        if numeric and len(numeric) == (len(cells) - null_count):
            item["min"] = min(numeric)
            item["max"] = max(numeric)
            item["mean"] = sum(numeric) / len(numeric)
            item["sum"] = sum(numeric)
        columns[str(name)] = item
    return {
        "format": format_name,
        "row_count": row_count,
        "column_count": len(values),
        "columns": columns,
    }


def adapt_pdf(path: Path, *, version_id: str, artifact_id: str) -> dict[str, Any]:
    data = path.read_bytes()[: 4 * 1024 * 1024]
    if not data.startswith(b"%PDF"):
        return _base(
            kind="pdf",
            version_id=version_id,
            artifact_id=artifact_id,
            complete=False,
            summary={},
            omission_reason="pdf_unreadable",
        )
    page_count = len(re.findall(rb"/Type\s*/Page(?!s)", data))
    texts = [
        match.decode("latin-1", errors="replace")
        for match in re.findall(rb"\((?:\\.|[^\\)]){3,400}\)", data)
    ]
    excerpt = " ".join(texts)[:2_000].strip()
    if page_count <= 0 or not excerpt:
        return _base(
            kind="pdf",
            version_id=version_id,
            artifact_id=artifact_id,
            complete=False,
            summary={"page_count": page_count, "bytes": len(data)},
            omission_reason="pdf_text_unavailable",
        )
    return _base(
        kind="pdf",
        version_id=version_id,
        artifact_id=artifact_id,
        complete=True,
        summary={
            "page_count": page_count,
            "text_excerpt": excerpt,
            "bytes": len(data),
        },
    )


def adapt_image(path: Path, *, version_id: str, artifact_id: str) -> dict[str, Any]:
    data = path.read_bytes()[: 32 * 1024]
    width = height = None
    fmt = None
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        fmt = "png"
    elif data.startswith(b"\xff\xd8"):
        fmt = "jpeg"
        offset = 2
        while offset + 9 < len(data):
            if data[offset] != 0xFF:
                break
            marker = data[offset + 1]
            length = struct.unpack(">H", data[offset + 2 : offset + 4])[0]
            if marker in {0xC0, 0xC1, 0xC2} and offset + 9 <= len(data):
                height, width = struct.unpack(">HH", data[offset + 5 : offset + 9])
                break
            offset += 2 + length
    if width is None or height is None:
        return _base(
            kind="image",
            version_id=version_id,
            artifact_id=artifact_id,
            complete=False,
            summary={"format": fmt, "bytes": path.stat().st_size},
            omission_reason="image_dimensions_unavailable",
        )
    return _base(
        kind="image",
        version_id=version_id,
        artifact_id=artifact_id,
        complete=True,
        summary={
            "format": fmt,
            "width": int(width),
            "height": int(height),
            "bytes": path.stat().st_size,
        },
    )


_ELEMENT = re.compile(r"(?:[A-Z][a-z]?|Cl|Br|\[[^\]]+\])")


def adapt_structure(path: Path, *, version_id: str, artifact_id: str) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")[:200_000]
    suffix = path.suffix.lower()
    if suffix in {".smi", ".smiles"}:
        smiles = next((line.strip() for line in text.splitlines() if line.strip()), "")
        atoms = len(_ELEMENT.findall(re.sub(r"\[|\]", "", smiles)))
        if atoms <= 0:
            return _base(
                kind="structure",
                version_id=version_id,
                artifact_id=artifact_id,
                complete=False,
                summary={"format": "smiles"},
                omission_reason="structure_unreadable",
            )
        return _base(
            kind="structure",
            version_id=version_id,
            artifact_id=artifact_id,
            complete=True,
            summary={"format": "smiles", "atom_count": atoms, "smiles": smiles[:400]},
        )
    lines = text.splitlines()
    counts = None
    for line in lines[:8]:
        parts = line.split()
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            counts = (int(parts[0]), int(parts[1]))
            break
    if counts is None:
        return _base(
            kind="structure",
            version_id=version_id,
            artifact_id=artifact_id,
            complete=False,
            summary={"format": suffix.lstrip(".") or "mol"},
            omission_reason="structure_unreadable",
        )
    return _base(
        kind="structure",
        version_id=version_id,
        artifact_id=artifact_id,
        complete=True,
        summary={
            "format": suffix.lstrip(".") or "mol",
            "atom_count": counts[0],
            "bond_count": counts[1],
        },
    )


def adapt_artifact(
    path: str | Path | None,
    *,
    filename: str,
    content_type: str = "",
    version_id: str,
    artifact_id: str,
) -> dict[str, Any] | None:
    """Run the required adapter, or None when the artifact needs none."""

    kind = classify_artifact(filename, content_type)
    if kind is None:
        return None
    if not path:
        return _base(
            kind=kind,
            version_id=version_id,
            artifact_id=artifact_id,
            complete=False,
            summary={},
            omission_reason="artifact_bytes_missing",
        )
    resolved = Path(path)
    if not resolved.is_file():
        return _base(
            kind=kind,
            version_id=version_id,
            artifact_id=artifact_id,
            complete=False,
            summary={},
            omission_reason="artifact_bytes_missing",
        )
    try:
        if kind == "table":
            return adapt_table(resolved, version_id=version_id, artifact_id=artifact_id)
        if kind == "pdf":
            return adapt_pdf(resolved, version_id=version_id, artifact_id=artifact_id)
        if kind == "image":
            return adapt_image(resolved, version_id=version_id, artifact_id=artifact_id)
        return adapt_structure(resolved, version_id=version_id, artifact_id=artifact_id)
    except Exception:  # noqa: BLE001
        # Deliberately broad. An adapter parses agent-authored files, so its
        # failure modes are open-ended: a CSV field over the 128 KiB csv module
        # limit raises `_csv.Error`, a .mol whose counts line starts with a
        # superscript digit raises `ValueError` from `int()`, and a scalar JSON
        # body used to raise `AttributeError`. `except OSError` caught none of
        # them, and `collect_turn_evidence` calls this unguarded, so ONE
        # malformed artifact propagated out through the completion gate --
        # where gateway.py swallows it into `gate = None` and the turn ships
        # with no review at all.
        #
        # An adapter that cannot read a file must say so, which is what this
        # record does: incomplete coverage forces `complete: False` on the
        # snapshot, which is what makes the turn honestly unverifiable rather
        # than silently unreviewed.
        return _base(
            kind=kind,
            version_id=version_id,
            artifact_id=artifact_id,
            complete=False,
            summary={},
            omission_reason="artifact_unreadable",
        )
