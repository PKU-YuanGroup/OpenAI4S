"""Stage 9 Artifact workbench: tables, diffs, locators, and Ketcher.

The official workbench is opt-in through ``stage9_artifact_workbench``. Flag-off
behaviour is unchanged: Ketcher stays the historical placeholder, tables stay
client-capped, and annotations stay image pins.
"""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import math
import os
import re
from collections.abc import Mapping, Sequence
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

KETCHER_VERSION = "3.7.0"
KETCHER_VENDOR = Path(__file__).resolve().parent / "webui" / "vendor" / "ketcher"
ANNOTATION_KINDS = frozenset({"image", "pdf", "html"})
_SMILES_ATOM = re.compile(r"Br|Cl|[A-Z][a-z]?")
_MAX_MOL_ATOMS = 2_000
# V2000's fixed-width field tops out at 999.  The permissive whitespace
# fallback supports larger historical files, but still needs a hard work bound
# before it enters the bond loop on untrusted HTTP content.
_MAX_MOL_BONDS = 20_000
_MAX_PDF_TEXT_CHARS = 20_000

# Workbench projections run inside the shared daemon.  Keep exact-version
# snapshots useful for moderate scientific results while preventing one team
# user from asking the daemon to materialize a 512 MiB upload.  Thirty-two MiB
# is four times the evidence adapter's general table budget; the lower diff cap
# accounts for difflib retaining both inputs plus its matching/output state.
MAX_WORKBENCH_ARTIFACT_BYTES = 32 * 1024 * 1024
MAX_WORKBENCH_DIFF_BYTES_PER_VERSION = 8 * 1024 * 1024
MAX_WORKBENCH_DIFF_LINES_PER_VERSION = 50_000
MAX_WORKBENCH_TABLE_ROWS = 250_000
MAX_WORKBENCH_TABLE_COLUMNS = 256
MAX_WORKBENCH_TABLE_CELLS = 2_000_000
MAX_WORKBENCH_PARQUET_ROW_GROUPS = 512
MAX_WORKBENCH_PARQUET_DECODED_BYTES = 64 * 1024 * 1024
MAX_WORKBENCH_HTML_ELEMENTS = 20_000
MAX_WORKBENCH_HTML_DEPTH = 256
MAX_WORKBENCH_HTML_OUTLINE_ELEMENTS = 200
_HTML_VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


class WorkbenchError(Exception):
    def __init__(self, status: int, message: str, code: str = "workbench_error"):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


def _artifact_too_large(message: str) -> WorkbenchError:
    return WorkbenchError(413, message, "artifact_too_large")


def _bounded_snapshot_bytes(path: Path, *, max_bytes: int | None = None) -> bytes:
    """Read one immutable snapshot without ever materializing past the cap."""

    limit = MAX_WORKBENCH_ARTIFACT_BYTES if max_bytes is None else max_bytes
    with path.open("rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        if size > limit:
            raise _artifact_too_large(
                f"artifact exceeds the {limit}-byte workbench limit"
            )
        data = handle.read(limit + 1)
    if len(data) > limit:
        # Defend the read even if a legacy snapshot is replaced or grows after
        # fstat; a size precheck alone is not a materialization bound.
        raise _artifact_too_large(f"artifact exceeds the {limit}-byte workbench limit")
    return data


def official_workbench_enabled(config: Any) -> bool:
    flags = getattr(config, "roadmap_features", None)
    return bool(
        flags is not None and getattr(flags, "stage9_artifact_workbench", False)
    )


def require_workbench(config: Any) -> None:
    if not official_workbench_enabled(config):
        raise WorkbenchError(
            403,
            "artifact workbench is disabled",
            "workbench_disabled",
        )


def parse_delimited(text: str, filename: str = "") -> list[list[str]]:
    sample = text[:4096]
    name = str(filename or "").lower()
    dialect = csv.excel_tab if name.endswith(".tsv") else csv.excel
    if "\t" in sample and sample.count("\t") > sample.count(","):
        dialect = csv.excel_tab
    reader = csv.reader(io.StringIO(text), dialect=dialect)
    rows: list[list[str]] = []
    cell_count = 0
    header_width = 0
    try:
        for row_index, raw_row in enumerate(reader):
            # row 0 is the header, so the public row budget describes data
            # rows rather than unexpectedly consuming one slot for metadata.
            if row_index > MAX_WORKBENCH_TABLE_ROWS:
                raise _artifact_too_large(
                    f"table exceeds {MAX_WORKBENCH_TABLE_ROWS} data rows"
                )
            row = list(raw_row)
            if len(row) > MAX_WORKBENCH_TABLE_COLUMNS:
                raise _artifact_too_large(
                    f"table exceeds {MAX_WORKBENCH_TABLE_COLUMNS} columns"
                )
            if row_index == 0:
                header_width = len(row)
            # query_table rectangularizes short rows to the header width.
            # Budget that real allocation, not only the sparse source cells.
            cell_count += len(row) if row_index == 0 else max(len(row), header_width)
            if cell_count > MAX_WORKBENCH_TABLE_CELLS:
                raise _artifact_too_large(
                    f"table exceeds {MAX_WORKBENCH_TABLE_CELLS} cells"
                )
            rows.append(row)
    except csv.Error as error:
        raise WorkbenchError(400, "invalid delimited table", "invalid_table") from error
    return rows


def infer_column_type(values: Sequence[str]) -> str:
    nonempty = [item.strip() for item in values if str(item).strip() != ""]
    if not nonempty:
        return "text"
    if all(re.fullmatch(r"[+-]?\d+", item) for item in nonempty):
        return "integer"
    if all(
        re.fullmatch(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?", item)
        for item in nonempty
    ):
        return "number"
    return "text"


def _coerce(value: str, kind: str) -> tuple[int, Any]:
    """Return one homogeneous, total ordering key for a typed column.

    Type inference deliberately ignores empty cells.  Returning a bare int or
    float for the populated rows and the original string for an empty/malformed
    cell therefore made Python 3 compare unlike values while sorting.  The
    leading discriminator keeps numeric and fallback keys in separate buckets;
    values within either bucket always share a comparable type.
    """

    text = str(value)
    if kind == "integer":
        try:
            return (0, int(text))
        except ValueError:
            return (1, text.lower())
    if kind == "number":
        try:
            return (0, float(text))
        except ValueError:
            return (1, text.lower())
    return (0, text.lower())


def query_table(
    rows: Sequence[Sequence[str]],
    *,
    sort: str = "",
    descending: bool = False,
    filters: Mapping[str, str] | None = None,
    offset: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    if not rows:
        return {
            "columns": [],
            "column_types": [],
            "rows": [],
            "total_rows": 0,
            "offset": 0,
            "limit": limit,
            "sorted_by": None,
            "filters": {},
        }
    header = [str(name or f"col_{index}") for index, name in enumerate(rows[0])]
    body = [
        (list(row) + [""] * max(0, len(header) - len(row)))[: len(header)]
        for row in rows[1:]
    ]
    types = [
        infer_column_type([row[index] if index < len(row) else "" for row in body])
        for index in range(len(header))
    ]
    filtered = body
    applied: dict[str, str] = {}
    for name, needle in dict(filters or {}).items():
        if name not in header or needle == "":
            continue
        index = header.index(name)
        applied[name] = str(needle)
        hay = str(needle).lower()
        kind = types[index]
        if kind in {"integer", "number"}:
            filtered = [
                row
                for row in filtered
                if str(row[index] if index < len(row) else "").strip()
                == str(needle).strip()
            ]
        else:
            filtered = [
                row
                for row in filtered
                if hay in str(row[index] if index < len(row) else "").lower()
            ]
    sort_name = sort if sort in header else None
    if sort_name:
        index = header.index(sort_name)
        kind = types[index]
        filtered = sorted(
            filtered,
            key=lambda row: _coerce(row[index] if index < len(row) else "", kind),
            reverse=bool(descending),
        )
    start = max(0, int(offset))
    size = max(1, min(int(limit), 500))
    page = filtered[start : start + size]
    return {
        "columns": header,
        "column_types": types,
        "rows": page,
        "total_rows": len(filtered),
        "offset": start,
        "limit": size,
        "sorted_by": sort_name,
        "descending": bool(descending) if sort_name else False,
        "filters": applied,
    }


def _metadata_int(value: Any) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    return value


def _parquet_shape(parquet_file: Any) -> tuple[int, int]:
    """Prove a bounded Parquet decode from metadata before reading columns."""

    metadata = getattr(parquet_file, "metadata", None)
    rows = _metadata_int(getattr(metadata, "num_rows", None))
    columns = _metadata_int(getattr(metadata, "num_columns", None))
    row_groups = _metadata_int(getattr(metadata, "num_row_groups", None))
    if rows is None or columns is None or row_groups is None:
        raise WorkbenchError(415, "invalid parquet metadata", "invalid_parquet")
    if rows > MAX_WORKBENCH_TABLE_ROWS:
        raise _artifact_too_large(f"table exceeds {MAX_WORKBENCH_TABLE_ROWS} data rows")
    if columns > MAX_WORKBENCH_TABLE_COLUMNS:
        raise _artifact_too_large(
            f"table exceeds {MAX_WORKBENCH_TABLE_COLUMNS} columns"
        )
    if rows * columns > MAX_WORKBENCH_TABLE_CELLS:
        raise _artifact_too_large(f"table exceeds {MAX_WORKBENCH_TABLE_CELLS} cells")
    if row_groups > MAX_WORKBENCH_PARQUET_ROW_GROUPS:
        raise _artifact_too_large(
            f"parquet exceeds {MAX_WORKBENCH_PARQUET_ROW_GROUPS} row groups"
        )

    total_uncompressed = 0
    observed_columns = 0
    try:
        for row_group_index in range(row_groups):
            row_group = metadata.row_group(row_group_index)
            for column_index in range(columns):
                column = row_group.column(column_index)
                size = _metadata_int(getattr(column, "total_uncompressed_size", None))
                if size is None:
                    raise WorkbenchError(
                        415, "invalid parquet metadata", "invalid_parquet"
                    )
                observed_columns += 1
                total_uncompressed += size
                if total_uncompressed > MAX_WORKBENCH_PARQUET_DECODED_BYTES:
                    raise _artifact_too_large(
                        "parquet decoded size exceeds the "
                        f"{MAX_WORKBENCH_PARQUET_DECODED_BYTES}-byte workbench limit"
                    )
    except WorkbenchError:
        raise
    except Exception as error:  # malformed optional-engine metadata
        raise WorkbenchError(
            415, "invalid parquet metadata", "invalid_parquet"
        ) from error
    if observed_columns != row_groups * columns or (rows and total_uncompressed == 0):
        raise WorkbenchError(415, "invalid parquet metadata", "invalid_parquet")
    return rows, columns


def read_parquet_rows(path: Path) -> list[list[str]]:
    # Bound compressed bytes before importing an optional engine or asking it
    # to inspect metadata.  BytesIO pins that exact bounded snapshot throughout
    # the preflight and decode instead of reopening a mutable path.
    compressed = _bounded_snapshot_bytes(path)
    try:
        import pyarrow.parquet as parquet  # type: ignore[import-not-found]
    except ImportError as error:
        raise WorkbenchError(
            415,
            "parquet requires pyarrow from the science extra",
            "parquet_unavailable",
        ) from error
    try:
        parquet_file = parquet.ParquetFile(io.BytesIO(compressed))
        expected_rows, expected_columns = _parquet_shape(parquet_file)
        table = parquet_file.read()
    except WorkbenchError:
        raise
    except Exception as error:  # malformed optional-engine input
        raise WorkbenchError(415, "invalid parquet", "invalid_parquet") from error
    decoded_bytes = _metadata_int(getattr(table, "nbytes", None))
    if decoded_bytes is None:
        raise WorkbenchError(415, "invalid parquet size", "invalid_parquet")
    if decoded_bytes > MAX_WORKBENCH_PARQUET_DECODED_BYTES:
        # Dictionary/RLE metadata describes the encoded column pages, not
        # necessarily the buffers Arrow materializes. Keep the metadata
        # preflight above (so oversized input is refused before decode), and
        # verify the representation the optional engine actually produced too.
        raise _artifact_too_large(
            "parquet decoded table exceeds the "
            f"{MAX_WORKBENCH_PARQUET_DECODED_BYTES}-byte workbench limit"
        )
    header = [str(name) for name in table.column_names]
    if table.num_rows != expected_rows or len(header) != expected_columns:
        raise WorkbenchError(415, "invalid parquet shape", "invalid_parquet")
    projected_bytes = sum(len(value.encode("utf-8", "replace")) for value in header)
    if projected_bytes > MAX_WORKBENCH_PARQUET_DECODED_BYTES:
        raise _artifact_too_large(
            "parquet projected text exceeds the "
            f"{MAX_WORKBENCH_PARQUET_DECODED_BYTES}-byte workbench limit"
        )
    rows = [header]
    projected_rows = 0
    try:
        for batch in table.to_batches(max_chunksize=1024):
            batch_rows = _metadata_int(getattr(batch, "num_rows", None))
            if batch_rows is None:
                raise WorkbenchError(415, "invalid parquet batch", "invalid_parquet")
            columns = [batch.column(index) for index in range(expected_columns)]
            for row_index in range(batch_rows):
                projected_rows += 1
                if projected_rows > expected_rows:
                    raise WorkbenchError(
                        415, "invalid parquet shape", "invalid_parquet"
                    )
                row: list[str] = []
                for column in columns:
                    value = column[row_index].as_py()
                    rendered = "" if value is None else str(value)
                    projected_bytes += len(rendered.encode("utf-8", "replace"))
                    if projected_bytes > MAX_WORKBENCH_PARQUET_DECODED_BYTES:
                        # Convert one scalar at a time and check before retaining
                        # its row. ``batch.to_pylist()`` can expand one compact
                        # dictionary value into 1,024 large Python strings before
                        # a cumulative check gets a chance to reject it.
                        raise _artifact_too_large(
                            "parquet projected text exceeds the "
                            f"{MAX_WORKBENCH_PARQUET_DECODED_BYTES}-byte "
                            "workbench limit"
                        )
                    row.append(rendered)
                rows.append(row)
    except WorkbenchError:
        raise
    except Exception as error:  # malformed optional-engine projection
        raise WorkbenchError(415, "invalid parquet", "invalid_parquet") from error
    if projected_rows != expected_rows:
        raise WorkbenchError(415, "invalid parquet shape", "invalid_parquet")
    return rows


def unified_diff(old: str, new: str, *, from_label: str, to_label: str) -> str:
    import difflib

    def line_count(text: str) -> int:
        if not text:
            return 0
        separators = (
            text.count("\n")
            + text.count("\r")
            - text.count("\r\n")
            + sum(text.count(char) for char in "\v\f\x1c\x1d\x1e\x85\u2028\u2029")
        )
        terminators = (
            "\n",
            "\r",
            "\v",
            "\f",
            "\x1c",
            "\x1d",
            "\x1e",
            "\x85",
            "\u2028",
            "\u2029",
        )
        return separators + (0 if text.endswith(terminators) else 1)

    if (
        line_count(old) > MAX_WORKBENCH_DIFF_LINES_PER_VERSION
        or line_count(new) > MAX_WORKBENCH_DIFF_LINES_PER_VERSION
    ):
        raise _artifact_too_large(
            "diff input exceeds "
            f"{MAX_WORKBENCH_DIFF_LINES_PER_VERSION} lines per version"
        )
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=from_label,
            tofile=to_label,
        )
    )


def parse_molfile(text: str) -> dict[str, Any] | None:
    first = str(text or "").split("$$$$", 1)[0]
    lines = first.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if len(lines) < 4 or "V3000" in (lines[3] or "").upper():
        return None
    counts = lines[3]
    try:
        atom_count = int(counts[0:3])
        bond_count = int(counts[3:6])
    except ValueError:
        pieces = counts.split()
        if len(pieces) < 2:
            return None
        try:
            atom_count, bond_count = int(pieces[0]), int(pieces[1])
        except ValueError:
            return None
    if not 1 <= atom_count <= _MAX_MOL_ATOMS:
        return None
    if not 0 <= bond_count <= _MAX_MOL_BONDS:
        return None
    atoms: list[dict[str, Any]] = []
    for index in range(atom_count):
        line = lines[4 + index] if 4 + index < len(lines) else ""
        pieces = line.split()
        element = (line[31:34].strip() if len(line) >= 34 else "") or (
            pieces[3] if len(pieces) > 3 else "C"
        )
        element = re.sub(r"[^A-Za-z]", "", element)[:3] or "C"
        atoms.append({"element": element})
    bonds: list[dict[str, Any]] = []
    for index in range(bond_count):
        line = (
            lines[4 + atom_count + index] if 4 + atom_count + index < len(lines) else ""
        )
        pieces = line.split()
        try:
            left = int(line[0:3] if len(line) >= 3 else pieces[0]) - 1
            right = int(line[3:6] if len(line) >= 6 else pieces[1]) - 1
            order = int(line[6:9] if len(line) >= 9 else pieces[2])
        except (ValueError, IndexError):
            continue
        if 0 <= left < len(atoms) and 0 <= right < len(atoms):
            bonds.append({"a": left, "b": right, "order": order})
    return {"atoms": atoms, "bonds": bonds, "title": (lines[0] or "Molecule").strip()}


def smiles_carbon_count(text: str) -> int:
    return sum(1 for token in _SMILES_ATOM.findall(text) if token == "C")


def structure_summary(content: str, filename: str = "") -> dict[str, Any]:
    name = str(filename or "").lower()
    if name.endswith((".smi", ".smiles")) or (
        "\n" not in content and re.search(r"[cC]\d*", content)
    ):
        line = content.strip().splitlines()[0] if content.strip() else ""
        smiles = line.split()[0] if line else ""
        carbons = smiles_carbon_count(smiles)
        return {
            "format": "smiles",
            "smiles": smiles,
            "carbon_count": carbons,
            "bond_count": None,
            "atoms": [],
        }
    parsed = parse_molfile(content)
    if parsed is None:
        raise WorkbenchError(400, "unrecognized structure file", "invalid_structure")
    carbons = sum(1 for atom in parsed["atoms"] if atom["element"].upper() == "C")
    return {
        "format": "mol",
        "carbon_count": carbons,
        "bond_count": len(parsed["bonds"]),
        "atoms": parsed["atoms"],
        "bonds": parsed["bonds"],
        "title": parsed.get("title") or "",
    }


def is_benzene(summary: Mapping[str, Any]) -> bool:
    if summary.get("format") == "smiles":
        compact = re.sub(r"\s+", "", str(summary.get("smiles") or ""))
        return compact in {"c1ccccc1", "C1=CC=CC=C1", "c1ccccc1"}
    if int(summary.get("carbon_count") or 0) != 6:
        return False
    bonds = list(summary.get("bonds") or [])
    if len(bonds) < 6:
        return False
    graph: dict[int, list[int]] = {}
    for bond in bonds:
        graph.setdefault(int(bond["a"]), []).append(int(bond["b"]))
        graph.setdefault(int(bond["b"]), []).append(int(bond["a"]))
    carbons = [
        index
        for index, atom in enumerate(summary.get("atoms") or [])
        if str(atom.get("element") or "").upper() == "C"
    ]
    return len(carbons) == 6 and all(
        len(graph.get(index, [])) >= 2 for index in carbons
    )


def _pdf_literal(text: str, start: int) -> tuple[str, int] | None:
    """Read one balanced PDF literal string in linear time.

    PDF strings may contain escaped or nested parentheses.  Keeping this tiny
    scanner here is both more accurate and safer than asking a nested regular
    expression to backtrack over an attacker-controlled Artifact.
    """

    if start >= len(text) or text[start] != "(":
        return None
    out: list[str] = []
    depth = 1
    index = start + 1
    escapes = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 1
            if index >= len(text):
                break
            escaped = text[index]
            if escaped == "\r":
                index += 1
                if index < len(text) and text[index] == "\n":
                    index += 1
                continue
            if escaped == "\n":
                index += 1
                continue
            if escaped in "01234567":
                end = index + 1
                while end < min(index + 3, len(text)) and text[end] in "01234567":
                    end += 1
                if len(out) < _MAX_PDF_TEXT_CHARS:
                    out.append(chr(int(text[index:end], 8) & 0xFF))
                index = end
                continue
            if len(out) < _MAX_PDF_TEXT_CHARS:
                out.append(escapes.get(escaped, escaped))
            index += 1
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return "".join(out), index + 1
        if len(out) < _MAX_PDF_TEXT_CHARS:
            out.append(char)
        index += 1
    return None


def _pdf_space_end(text: str, start: int) -> int:
    index = start
    while index < len(text) and text[index] in " \t\r\n\f\x00":
        index += 1
    return index


def _pdf_operator_at(text: str, start: int, operator: str) -> bool:
    end = start + len(operator)
    if text[start:end] != operator:
        return False
    return end >= len(text) or text[end] in " \t\r\n\f\x00[]<>{}()/%"


def extract_pdf_text(data: bytes) -> list[dict[str, Any]]:
    if not data.startswith(b"%PDF"):
        raise WorkbenchError(415, "not a PDF", "not_pdf")
    decoded = data.decode("latin-1", "replace")
    chunks: list[str] = []
    captured = 0

    def append(value: str) -> None:
        nonlocal captured
        if not value.strip() or captured >= _MAX_PDF_TEXT_CHARS:
            return
        room = _MAX_PDF_TEXT_CHARS - captured - (1 if chunks else 0)
        if room <= 0:
            return
        value = value[:room]
        chunks.append(value)
        captured += len(value) + (1 if len(chunks) > 1 else 0)

    index = 0
    while index < len(decoded) and captured < _MAX_PDF_TEXT_CHARS:
        if decoded[index] == "(":
            literal = _pdf_literal(decoded, index)
            if literal is None:
                break
            value, end = literal
            operator = _pdf_space_end(decoded, end)
            if _pdf_operator_at(decoded, operator, "Tj"):
                append(value)
                index = operator + 2
            else:
                index = end
            continue
        if decoded[index] == "[":
            cursor = index + 1
            parts: list[str] = []
            parts_chars = 0
            closed = False
            while cursor < len(decoded):
                cursor = _pdf_space_end(decoded, cursor)
                if cursor >= len(decoded):
                    break
                if decoded[cursor] == "]":
                    closed = True
                    break
                if decoded[cursor] == "(":
                    literal = _pdf_literal(decoded, cursor)
                    if literal is None:
                        break
                    value, cursor = literal
                    if parts_chars < _MAX_PDF_TEXT_CHARS:
                        value = value[: _MAX_PDF_TEXT_CHARS - parts_chars]
                        parts.append(value)
                        parts_chars += len(value)
                    continue
                # Numeric kerning values and other non-string array tokens do
                # not contribute text, but they are legal between strings.
                cursor += 1
            if not closed:
                break
            operator = _pdf_space_end(decoded, cursor + 1)
            if _pdf_operator_at(decoded, operator, "TJ"):
                append("".join(parts))
                index = operator + 2
            else:
                index = cursor + 1
            continue
        index += 1
    return [{"page": 1, "index": 0, "text": " ".join(chunks)}]


class _OutlineParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.outline: list[dict[str, str]] = []
        self._stack: list[str] = []
        self._skip = 0
        self._element_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._element_count += 1
        if self._element_count > MAX_WORKBENCH_HTML_ELEMENTS:
            raise _artifact_too_large(
                f"HTML exceeds {MAX_WORKBENCH_HTML_ELEMENTS} elements"
            )
        if tag in {"script", "style"}:
            self._skip += 1
            return
        if len(self._stack) >= MAX_WORKBENCH_HTML_DEPTH:
            raise _artifact_too_large(
                f"HTML exceeds a nesting depth of {MAX_WORKBENCH_HTML_DEPTH}"
            )
        mapping = {name: value or "" for name, value in attrs}
        ident = mapping.get("id")
        selector = f"#{ident}" if ident else tag
        if ident:
            path = selector
        else:
            path = ">".join([*self._stack, tag]) if self._stack else tag
        if tag not in _HTML_VOID_ELEMENTS:
            self._stack.append(tag)
        if len(self.outline) < MAX_WORKBENCH_HTML_OUTLINE_ELEMENTS:
            self.outline.append(
                {
                    "tag": tag,
                    "selector": path[:240],
                    "id": ident or "",
                    "text": "",
                }
            )

    def handle_data(self, data: str) -> None:
        if (
            self._skip
            or not self.outline
            or self._element_count > MAX_WORKBENCH_HTML_OUTLINE_ELEMENTS
        ):
            return
        text = " ".join(data.split())
        if text and not self.outline[-1]["text"]:
            self.outline[-1]["text"] = text[:240]

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1
            return
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()


def html_outline(text: str) -> list[dict[str, str]]:
    parser = _OutlineParser()
    parser.feed(text)
    parser.close()
    return [
        item
        for item in parser.outline
        if item["tag"] not in {"html", "head", "meta", "link"}
    ][:MAX_WORKBENCH_HTML_OUTLINE_ELEMENTS]


def normalize_locator(kind: str, locator: Any) -> dict[str, Any]:
    if kind not in ANNOTATION_KINDS:
        raise WorkbenchError(400, "kind must be image, pdf, or html", "invalid_kind")
    data = dict(locator or {}) if isinstance(locator, Mapping) else {}
    if kind == "image":
        try:
            rel_x = float(data.get("rel_x") or data.get("x") or 0)
            rel_y = float(data.get("rel_y") or data.get("y") or 0)
        except (TypeError, ValueError) as error:
            raise WorkbenchError(
                400, "image locator coordinates must be numbers", "invalid_locator"
            ) from error
        # Python's JSON encoder otherwise emits bare NaN/Infinity tokens.
        # Those are not JSON and browser response.json() rejects the entire
        # annotation/workbench response, so refuse them at the API boundary.
        if not math.isfinite(rel_x) or not math.isfinite(rel_y):
            raise WorkbenchError(
                400,
                "image locator coordinates must be finite numbers",
                "invalid_locator",
            )
        return {"rel_x": rel_x, "rel_y": rel_y}
    if kind == "pdf":
        quote = str(data.get("quote") or data.get("text") or "").strip()
        if not quote:
            raise WorkbenchError(400, "pdf locator requires quote", "invalid_locator")
        try:
            return {
                "page": int(data.get("page") or 1),
                "start": int(data.get("start") or 0),
                "end": int(data.get("end") or 0),
                "quote": quote[:2000],
            }
        except (TypeError, ValueError) as error:
            raise WorkbenchError(
                400, "pdf locator offsets must be integers", "invalid_locator"
            ) from error
    selector = str(data.get("selector") or "").strip()
    quote = str(data.get("quote") or data.get("text") or "").strip()
    if not selector and not quote:
        raise WorkbenchError(
            400, "html locator requires selector or quote", "invalid_locator"
        )
    return {"selector": selector[:500], "quote": quote[:2000]}


def format_located_annotations(annos: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "【Workbench 标注反馈】用户在 Artifact 的精确位置写下了意见。",
        "请按 version 与 locator 修改对应文件，不要改到别的版本。",
    ]
    for item in annos:
        kind = str(item.get("kind") or "image")
        locator = item.get("locator") or {}
        if isinstance(locator, str):
            try:
                locator = json.loads(locator)
            except json.JSONDecodeError:
                locator = {}
        name = item.get("artifact_name") or item.get("artifact_id") or "artifact"
        version = item.get("version_id") or ""
        if kind == "pdf":
            where = f"PDF p.{locator.get('page', 1)} " f"«{locator.get('quote', '')}»"
        elif kind == "html":
            where = (
                f"HTML {locator.get('selector') or ''} " f"«{locator.get('quote', '')}»"
            )
        else:
            where = f"image x={locator.get('rel_x', item.get('rel_x'))} y={locator.get('rel_y', item.get('rel_y'))}"
        lines.append(
            f"• {name}#{version} [{item.get('number')}] {where}: "
            f"{str(item.get('body') or '').strip()}"
        )
    return "\n".join(lines)


def ketcher_assets_present() -> bool:
    return (KETCHER_VENDOR / "static" / "js" / "main.8617f334.js").is_file()


def ketcher_document(config: Any, query: Mapping[str, Any] | None = None) -> bytes:
    if not official_workbench_enabled(config) or not ketcher_assets_present():
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>Ketcher</title></head><body style='font:14px system-ui;"
            "padding:2rem;color:#444'><p>Chemical structure editor placeholder. "
            "Bundle Ketcher assets here to enable in-browser structure drawing."
            "</p></body></html>"
        ).encode("utf-8")
    params = query or {}

    def _one(name: str) -> str:
        value = params.get(name)
        if isinstance(value, list):
            value = value[0] if value else ""
        text = str(value or "")
        return re.sub(r"[^A-Za-z0-9._:-]", "", text)[:128]

    artifact_id = _one("artifact_id") or _one("artifact")
    return _KETCHER_WRAPPER.replace(
        "__ARTIFACT__", html.escape(artifact_id, quote=True)
    ).encode("utf-8")


_KETCHER_WRAPPER = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>Ketcher v3.7.0</title>
  <style>
    html,body{height:100%;margin:0;font:13px system-ui,sans-serif}
    #openai4s-artifact{display:flex;gap:8px;align-items:center;padding:8px 12px;border-bottom:1px solid #ddd}
    iframe{border:0;width:100%;height:calc(100% - 46px)}
    button{font:inherit}
  </style>
</head>
<body>
  <div id="openai4s-artifact" data-ketcher-core="ketcher-core" data-ketcher-js="ketcher.js">
    <strong>Ketcher v3.7.0</strong>
    <button type="button" id="ketcher-save">Save artifact version</button>
    <span id="ketcher-status">loading real editor assets</span>
  </div>
  <iframe id="ketcher-frame" title="Ketcher" src="/static/vendor/ketcher/index.html"></iframe>
  <script>
    const artifactId = "__ARTIFACT__";
    const status = document.getElementById("ketcher-status");
    const frame = document.getElementById("ketcher-frame");
    function ketcher() {
      try { return frame.contentWindow && frame.contentWindow.ketcher; } catch (e) { return null; }
    }
    async function loadArtifact() {
      if (!artifactId) { status.textContent = "ready"; return; }
      const response = await fetch("/api/v1/artifacts/" + encodeURIComponent(artifactId));
      if (!response.ok) { status.textContent = "artifact load failed"; return; }
      const text = await response.text();
      const editor = ketcher();
      if (editor && editor.setMolecule) await editor.setMolecule(text);
      status.textContent = "loaded " + artifactId;
    }
    window.addEventListener("message", (event) => {
      if (event.data && event.data.eventType === "init") loadArtifact();
    });
    frame.addEventListener("load", () => setTimeout(loadArtifact, 400));
    document.getElementById("ketcher-save").onclick = async () => {
      const editor = ketcher();
      if (!editor || !artifactId) { status.textContent = "nothing to save"; return; }
      const mol = editor.getMolfile ? await editor.getMolfile() : "";
      const response = await fetch("/api/v1/artifacts/" + encodeURIComponent(artifactId) + "/structure", {
        method: "POST",
        headers: {"content-type": "application/json"},
        body: JSON.stringify({content: mol, format: "mol"})
      });
      const payload = await response.json().catch(() => ({}));
      status.textContent = response.ok
        ? ("saved " + (payload.version_id || "") + (payload.unchanged ? " (unchanged)" : ""))
        : ("save failed: " + (payload.error || response.status));
    };
  </script>
</body>
</html>
"""


def checksum_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ArtifactWorkbenchService:
    """Session-facing table/diff/structure/locator operations."""

    def __init__(self, *, store: Any, artifacts: Any, broadcast: Any = None) -> None:
        self.store = store
        self.artifacts = artifacts
        self.broadcast = broadcast

    def _artifact(self, artifact_id: str) -> dict[str, Any]:
        artifact = self.store.get_artifact(artifact_id)
        if not artifact:
            raise WorkbenchError(404, "artifact not found", "artifact_not_found")
        return artifact

    def _version_snapshot(
        self, artifact: Mapping[str, Any], version_id: str | None = None
    ) -> tuple[str, Path]:
        version = version_id or artifact.get("latest_version_id")
        if version:
            meta = self.store.version_meta(str(version))
            # Version ids are globally unique, while this operation is scoped
            # to one Artifact.  Never let a caller use another Artifact's
            # otherwise-valid version id as a read capability.  Unknown and
            # foreign ids deliberately share the same response so this check
            # does not become an existence oracle.
            if not meta or meta.get("artifact_id") != artifact.get("artifact_id"):
                raise WorkbenchError(
                    404, "artifact version not found", "artifact_version_not_found"
                )
            # A version response must come from that version's immutable
            # snapshot.  ``path`` is the mutable workspace head and can point
            # at completely different bytes after a later Cell or external
            # edit.  Legacy rows without a snapshot are unavailable rather
            # than being served under an exact version id with false identity.
            path = meta.get("snapshot_path")
            if path and Path(path).is_file():
                return str(version), Path(path)
            raise WorkbenchError(
                404, "artifact version not found", "artifact_version_not_found"
            )
        raise WorkbenchError(404, "artifact bytes not found", "artifact_missing")

    def _bytes(
        self,
        artifact: Mapping[str, Any],
        version_id: str | None = None,
        *,
        max_bytes: int | None = None,
    ) -> bytes:
        _version, snapshot = self._version_snapshot(artifact, version_id)
        return _bounded_snapshot_bytes(snapshot, max_bytes=max_bytes)

    def table(
        self,
        artifact_id: str,
        *,
        sort: str = "",
        descending: bool = False,
        filters: Mapping[str, str] | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        artifact = self._artifact(artifact_id)
        name = str(artifact.get("filename") or "")
        version_id, snapshot = self._version_snapshot(artifact)
        if name.lower().endswith(".parquet"):
            rows = read_parquet_rows(snapshot)
        else:
            raw = _bounded_snapshot_bytes(snapshot)
            rows = parse_delimited(raw.decode("utf-8", "replace"), name)
        result = query_table(
            rows,
            sort=sort,
            descending=descending,
            filters=filters,
            offset=offset,
            limit=limit,
        )
        result["artifact_id"] = artifact_id
        result["version_id"] = version_id
        result["filename"] = name
        return result

    def diff(
        self,
        artifact_id: str,
        *,
        from_version: str | None = None,
        to_version: str | None = None,
    ) -> dict[str, Any]:
        artifact = self._artifact(artifact_id)
        versions = self.store.list_versions(artifact_id) or []
        if not versions:
            raise WorkbenchError(404, "no versions", "no_versions")
        # ``Store.list_versions`` is newest-first.  The endpoint default is a
        # chronological comparison (first version -> current version), so do
        # not accidentally render the whole history backwards when callers
        # omit the explicit query parameters.
        oldest = versions[-1]["version_id"]
        newest = versions[0]["version_id"]
        left_id = from_version or oldest
        right_id = to_version or newest
        left = self._bytes(
            artifact,
            left_id,
            max_bytes=MAX_WORKBENCH_DIFF_BYTES_PER_VERSION,
        ).decode("utf-8", "replace")
        right = self._bytes(
            artifact,
            right_id,
            max_bytes=MAX_WORKBENCH_DIFF_BYTES_PER_VERSION,
        ).decode("utf-8", "replace")
        return {
            "artifact_id": artifact_id,
            "from_version_id": left_id,
            "to_version_id": right_id,
            "changed": left != right,
            "diff": unified_diff(
                left, right, from_label=str(left_id), to_label=str(right_id)
            ),
        }

    def save_structure(
        self, artifact_id: str, *, content: str, fmt: str = "mol"
    ) -> dict[str, Any]:
        artifact = self._artifact(artifact_id)
        text = str(content or "")
        summary = structure_summary(text, artifact.get("filename") or f"struct.{fmt}")
        current_id = artifact.get("latest_version_id")
        current = self.store.version_meta(current_id) if current_id else None
        digest = checksum_text(text)
        if current and current.get("checksum") == digest:
            return {
                "ok": True,
                "artifact_id": artifact_id,
                "version_id": current_id,
                "unchanged": True,
                "structure": summary,
            }
        try:
            uploaded = self.artifacts.replace_artifact_text(
                artifact_id,
                text,
                broadcast=self.broadcast,
            )
        except Exception as error:
            # Preserve public Artifact errors without importing the gateway
            # facade into this focused service.
            from openai4s.server.artifacts import ArtifactOperationError

            if isinstance(error, ArtifactOperationError):
                raise WorkbenchError(
                    error.code, error.message, "structure_save_failed"
                ) from error
            raise
        if uploaded.get("artifact_id") != artifact_id:
            # The scope+filename lookup above is the transaction's identity
            # check.  Reaching here would mean the Artifact changed under an
            # uncoordinated caller; do not misreport a different object as the
            # requested one.
            raise WorkbenchError(
                409, "artifact changed before structure save", "artifact_conflict"
            )
        record = self.store.version_meta(str(uploaded.get("version_id") or ""))
        if not record:
            raise WorkbenchError(500, "structure save failed", "structure_save_failed")
        return {
            "ok": True,
            "artifact_id": artifact_id,
            "version_id": record["version_id"],
            "unchanged": False,
            "structure": summary,
        }

    def pdf_text(self, artifact_id: str) -> dict[str, Any]:
        artifact = self._artifact(artifact_id)
        pages = extract_pdf_text(self._bytes(artifact))
        return {
            "artifact_id": artifact_id,
            "version_id": artifact.get("latest_version_id"),
            "pages": pages,
        }

    def html_outline(self, artifact_id: str) -> dict[str, Any]:
        artifact = self._artifact(artifact_id)
        outline = html_outline(self._bytes(artifact).decode("utf-8", "replace"))
        return {
            "artifact_id": artifact_id,
            "version_id": artifact.get("latest_version_id"),
            "elements": outline,
        }
