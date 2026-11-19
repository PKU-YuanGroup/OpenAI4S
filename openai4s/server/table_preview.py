"""Bounded first-rows previews of large, compressed and Excel tables (ART-11).

The flag-off table viewer reads a whole file in the browser under an 8 MiB
budget, so a 40 MB CSV got no preview at all, an ``.xlsx`` had no viewer, and a
``.csv.gz`` was decoded as if it were text. This module answers one question
for each of them -- what are the first rows, and how many rows are there? --
and keeps every step bounded, because the bytes are untrusted:

* **delimited text** (CSV/TSV in any encoding ``decode_text`` detects): the
  header and the first rows from a streaming decode, then an exact record count
  with ``csv`` until a time bound, else a lower bound;
* **gzip**, recognised by its magic bytes and never by a name or a stored type:
  the same, through a decompressor that never inflates past a fixed budget;
* **xlsx**: the worksheet names and the first rows of one sheet, read with
  ``zipfile`` and ``ElementTree`` under zip-bomb, DOCTYPE/entity, shared-string
  and row caps.

Legacy binary ``.xls`` (and password-protected workbooks, which share its
container) is out of scope and refused with ``unsupported_format``. Stdlib only.
"""

from __future__ import annotations

import codecs
import csv
import gzip
import io
import math
import os
import posixpath
import re
import time
import zipfile
import zlib
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO
from xml.etree import ElementTree

from openai4s.server.artifact_workbench import WorkbenchError
from openai4s.server.text_decoding import GB18030, UTF8, UTF16BE, UTF16LE, decode_text

#: Data rows a preview returns.
PREVIEW_ROWS = 100
#: Columns, characters per cell and characters in total that one response carries.
MAX_PREVIEW_COLUMNS = 512
MAX_CELL_CHARS = 2_000
MAX_PREVIEW_CHARS = 2_000_000
#: Leading (decompressed) bytes that decide the encoding and the delimiter.
SNIFF_BYTES = 1 << 20
#: One physical line and one CSV record, in characters. ``csv`` builds a whole
#: record before it yields it, so a row of commas would otherwise grow a list of
#: millions of fields; these caps are what bound that.
MAX_LINE_CHARS = 4 << 20
MAX_RECORD_CHARS = 8 << 20
#: Wall-clock budgets from the start of a request: the head may take this long,
#: and counting stops here with a lower bound instead of an exact total.
HEAD_SECONDS = 10.0
COUNT_SECONDS = 5.0
#: Decompressed bytes one gzip preview may inflate, head and count together.
MAX_GZIP_INFLATED_BYTES = 512 << 20
#: A workbook larger than this is not opened at all.
MAX_XLSX_BYTES = 128 << 20
MAX_XLSX_MEMBERS = 10_000
#: Workbook, relationship and style parts are small; a large one is hostile.
MAX_XLSX_META_BYTES = 8 << 20
#: XML one worksheet or shared-string read may inflate.
MAX_XLSX_PART_BYTES = 512 << 20
MAX_XLSX_STYLES = 65_536
MAX_SHEETS = 256
_CHUNK = 1 << 16

_GZIP_MAGIC = b"\x1f\x8b"
_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06")
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_OTHER_COMPRESSION = (
    (b"BZh", "bzip2"),
    (b"\xfd7zXZ\x00", "xz"),
    (b"\x28\xb5\x2f\xfd", "zstd"),
)
_CODECS = {
    UTF8: "utf-8-sig",
    GB18030: "gb18030",
    UTF16LE: "utf-16-le",
    UTF16BE: "utf-16-be",
}
_DELIMITED_NAMES = ((".tsv", "\t"), (".tab", "\t"), (".csv", ","))
_SNIFFED_DELIMITERS = ("\t", ",", ";", "|")


class TablePreviewError(WorkbenchError):
    """A preview refusal with a stable code, answered as JSON by the route."""


def _not_tabular(message: str) -> TablePreviewError:
    return TablePreviewError(415, message, "not_tabular")


def _unsupported(message: str) -> TablePreviewError:
    return TablePreviewError(415, message, "unsupported_format")


def _too_large(message: str) -> TablePreviewError:
    return TablePreviewError(413, message, "too_large_to_inspect")


def _invalid(message: str) -> TablePreviewError:
    return TablePreviewError(422, message, "invalid_file")


class _Budget:
    """The two wall-clock deadlines of one request."""

    def __init__(self, clock: Callable[[], float]) -> None:
        started = clock()
        self._clock = clock
        self._head = started + HEAD_SECONDS
        self._count = started + COUNT_SECONDS

    def head_over(self) -> bool:
        return self._clock() > self._head

    def count_over(self) -> bool:
        return self._clock() > self._count


def preview_table(
    path: Path | str,
    *,
    filename: str = "",
    content_type: str = "",
    sheet: str | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """The first rows of one immutable snapshot, and how many rows it has.

    The container is decided by the bytes: gzip and zip by their magic, the
    legacy ``.xls`` container by its OLE2 signature, anything else as
    delimited text. ``filename``/``content_type`` only choose a delimiter.
    """

    budget = _Budget(clock)
    with Path(path).open("rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        magic = handle.read(8)
        if magic.startswith(_GZIP_MAGIC):
            inner = _inner_name(filename)
            result = _preview_delimited(
                handle, name=inner, content_type="", budget=budget, compressed=True
            )
            result["inner_filename"] = inner or None
        elif magic.startswith(_ZIP_MAGICS):
            result = _preview_xlsx(handle, size=size, sheet=sheet, budget=budget)
        elif magic.startswith(_OLE2_MAGIC):
            raise _unsupported(
                "legacy .xls workbooks (and password-protected ones) are not "
                "previewed; save the file as .xlsx or CSV"
            )
        else:
            for prefix, label in _OTHER_COMPRESSION:
                if magic.startswith(prefix):
                    raise _unsupported(
                        f"{label}-compressed files are not previewed; only gzip is"
                    )
            result = _preview_delimited(
                handle,
                name=posixpath.basename(filename or ""),
                content_type=content_type,
                budget=budget,
                compressed=False,
            )
    result["size_bytes"] = size
    return result


# ---------------------------------------------------------------------------
# delimited text, plain or gzip
# ---------------------------------------------------------------------------


def _inner_name(filename: str) -> str:
    """``counts.csv.gz`` -> ``counts.csv``: the name of what gzip wraps."""

    base = posixpath.basename(filename or "")
    lower = base.lower()
    if lower.endswith(".tgz") and len(base) > 4:
        return base[:-4] + ".tar"
    for suffix in (".gzip", ".gz"):
        if lower.endswith(suffix) and len(base) > len(suffix):
            return base[: -len(suffix)]
    return base


class _Limited(io.RawIOBase):
    """``source`` read-only, ending after ``limit`` bytes.

    ``exhausted`` turns true only when the limit was reached *and* the source
    had more, so a stream that ends exactly at the limit still counts as read
    to the end.
    """

    def __init__(self, source: BinaryIO, limit: int | None) -> None:
        super().__init__()
        self._source = source
        self._left = limit
        self.exhausted = False

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        view = memoryview(buffer)
        if self._left is not None:
            if self._left <= 0:
                if not self.exhausted and self._source.read(1):
                    self.exhausted = True
                return 0
            view = view[: self._left]
        data = self._source.read(len(view))
        size = len(data)
        view[:size] = data
        if self._left is not None:
            self._left -= size
        return size


class _Bound(Exception):
    """A line or record larger than the preview will hold."""


class _LineFeed:
    """``csv.reader``'s line source, with every line and record bounded."""

    def __init__(self, text: io.TextIOBase) -> None:
        self._readline = text.readline
        self._chars = 0
        self._record_start = 0

    def __iter__(self) -> _LineFeed:
        return self

    def __next__(self) -> str:
        line = self._readline(MAX_LINE_CHARS + 1)
        if not line:
            raise StopIteration
        if len(line) > MAX_LINE_CHARS:
            raise _Bound(f"a line is longer than {MAX_LINE_CHARS} characters")
        self._chars += len(line)
        if self._chars - self._record_start > MAX_RECORD_CHARS:
            raise _Bound(f"a record is longer than {MAX_RECORD_CHARS} characters")
        return line

    def mark(self) -> None:
        """A record ended: the next one starts its own budget."""

        self._record_start = self._chars


def _open_stream(handle: BinaryIO, compressed: bool) -> BinaryIO:
    handle.seek(0)
    if compressed:
        return gzip.GzipFile(fileobj=handle, mode="rb")  # type: ignore[return-value]
    return handle


def _read_head(handle: BinaryIO, compressed: bool) -> bytes:
    stream = _open_stream(handle, compressed)
    parts: list[bytes] = []
    total = 0
    while total < SNIFF_BYTES:
        chunk = stream.read(min(_CHUNK, SNIFF_BYTES - total))
        if not chunk:
            break
        parts.append(chunk)
        total += len(chunk)
    return b"".join(parts)


def _binary(sample: str) -> bool:
    """Control characters or U+FFFD over 12% of the first 4 KiB (the client's rule)."""

    window = sample[:4096]
    if not window:
        return False
    bad = sum(
        1
        for char in window
        if (char < " " and char not in "\t\n\r")
        or char in "\x7f\N{REPLACEMENT CHARACTER}"
    )
    return bad / len(window) > 0.12


def _first_filled_line(sample: str) -> str:
    for line in sample.splitlines():
        if line.strip():
            return line
    return ""


def _delimiter(name: str, content_type: str, sample: str) -> str | None:
    """The client's ``delimiterFor`` rule: the name, the type, then a sniff."""

    lower = name.lower()
    for suffix, delimiter in _DELIMITED_NAMES:
        if lower.endswith(suffix):
            return delimiter
    declared = content_type.lower()
    if "tab-separated" in declared:
        return "\t"
    if "csv" in declared:
        return ","
    line = _first_filled_line(sample)
    best: str | None = None
    width = 1
    for candidate in _SNIFFED_DELIMITERS:
        fields = len(next(csv.reader([line], delimiter=candidate), []))
        if fields > width:
            best, width = candidate, fields
    return best


def _clip_cells(record: list[str]) -> tuple[list[str], int, bool]:
    """At most ``MAX_PREVIEW_COLUMNS`` cells of at most ``MAX_CELL_CHARS`` each."""

    clipped = len(record) > MAX_PREVIEW_COLUMNS
    cells: list[str] = []
    used = 0
    for cell in record[:MAX_PREVIEW_COLUMNS]:
        if len(cell) > MAX_CELL_CHARS:
            cell = cell[:MAX_CELL_CHARS]
            clipped = True
        cells.append(cell)
        used += len(cell)
    return cells, used, clipped


def _preview_delimited(
    handle: BinaryIO,
    *,
    name: str,
    content_type: str,
    budget: _Budget,
    compressed: bool,
) -> dict[str, Any]:
    what = (
        f"the data inside this gzip file ({name or 'unnamed'})"
        if compressed
        else (name or "this file")
    )
    try:
        head = _read_head(handle, compressed)
    except (OSError, EOFError, zlib.error) as error:
        raise _invalid("the gzip stream cannot be decompressed") from error
    whole = len(head) < SNIFF_BYTES
    sample, encoding = decode_text(head, partial=not whole)
    sample = sample[:65536]
    if _binary(sample):
        raise _not_tabular(f"{what} is binary, not a CSV/TSV table")
    delimiter = _delimiter(name, content_type, sample)
    if delimiter is None:
        raise _not_tabular(f"{what} is not a CSV/TSV table")

    limited = _Limited(
        _open_stream(handle, compressed),
        (MAX_GZIP_INFLATED_BYTES if compressed else None),
    )
    buffered = io.BufferedReader(limited, buffer_size=_CHUNK)
    if encoding in (UTF16LE, UTF16BE):
        buffered.read(2)  # the BOM that decided the encoding
    text = io.TextIOWrapper(
        buffered, encoding=_CODECS[encoding], errors="replace", newline=""
    )
    feed = _LineFeed(text)
    reader = csv.reader(feed, dialect="excel", delimiter=delimiter)
    header: list[str] | None = None
    rows: list[list[str]] = []
    widest = 0
    count = 0
    room = MAX_PREVIEW_CHARS
    clipped = False
    stopped = False
    try:
        for record in reader:
            feed.mark()
            if not record:
                continue  # a blank line is not a row, as in pandas
            if header is None:
                header = record
                continue
            count += 1
            if len(rows) < PREVIEW_ROWS and room > 0:
                cells, used, cut = _clip_cells(record)
                rows.append(cells)
                widest = max(widest, len(record))
                room -= used
                clipped = clipped or cut
                if budget.head_over():
                    stopped = True
                    break
                continue
            if not count & 0x3FF and budget.count_over():
                stopped = True
                break
    except (_Bound, csv.Error) as error:
        if header is None:
            raise _too_large(f"{what}: {error}") from error
        stopped = True
    except (OSError, EOFError, zlib.error) as error:
        if header is None:
            raise _invalid("the gzip stream cannot be decompressed") from error
        stopped = True
    if limited.exhausted:
        stopped = True
    columns, _used, header_cut = _clip_cells(header or [])
    exact = not stopped
    return {
        "kind": "table",
        "format": "tsv" if delimiter == "\t" else "csv",
        "delimiter": delimiter,
        "encoding": encoding,
        "compression": "gzip" if compressed else None,
        "inner_filename": None,
        "sheets": [],
        "sheet": None,
        "columns": columns,
        "row_numbers": None,
        "rows": rows,
        "total_rows": count if exact else None,
        "total_rows_lower_bound": None if exact else count,
        "total_columns": max(len(header or []), widest),
        "truncated": not exact or count > len(rows) or clipped or header_cut,
    }


# ---------------------------------------------------------------------------
# xlsx
# ---------------------------------------------------------------------------

_FORBIDDEN_XML = re.compile(rb"<!(?:DOCTYPE|ENTITY)")
_XML_ENCODING = re.compile(
    rb"\s*(?:\xef\xbb\xbf)?<\?xml[^>]*?encoding\s*=\s*[\"']([A-Za-z0-9._-]+)[\"']"
)


def _local(name: str) -> str:
    return name.rpartition("}")[2]


def _small_int(text: str | None, default: int = 0) -> int:
    value = (text or "").strip()
    if not value.isdigit() or len(value) > 9:
        return default
    return int(value)


def _column_index(ref: str | None) -> int | None:
    """``"AB12"`` -> 27 (0-based); None when ``ref`` does not name a column."""

    index = 0
    letters = 0
    for char in ref or "":
        upper = char.upper()
        if "A" <= upper <= "Z" and char.isascii():
            index = index * 26 + ord(upper) - 64
            letters += 1
            if letters > 3:
                return None
            continue
        break
    if not letters or index > 16_384:
        return None
    return index - 1


def column_letters(index: int) -> str:
    """0 -> ``A``, 27 -> ``AB``."""

    name = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


class _Target:
    """An ``ElementTree.XMLParser`` target that refuses any DOCTYPE."""

    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        raise _invalid("workbook XML declares a DOCTYPE; refused")

    def close(self) -> None:
        return None


class _XmlPart:
    """One zip member streamed into a target, bounded and DTD-free."""

    def __init__(
        self,
        archive: zipfile.ZipFile,
        info: zipfile.ZipInfo,
        target: _Target,
        *,
        limit: int,
    ) -> None:
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            # Excel writes deflate. bzip2/lzma members decompress without an
            # output bound per call, which is exactly what a zip bomb wants.
            raise _unsupported("workbook parts must be stored or deflate-compressed")
        self._parser = ElementTree.XMLParser(target=target)  # type: ignore[arg-type]
        self._stream = archive.open(info)
        self._limit = limit
        self._read = 0
        self._tail = b""
        self._first = True
        self.finished = False
        self.exhausted = False

    def __enter__(self) -> _XmlPart:
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stream.close()

    def _screen(self, chunk: bytes) -> None:
        if self._first:
            self._first = False
            if chunk.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) or (
                b"\x00" in chunk[:4]
            ):
                raise _invalid("workbook XML must be UTF-8")
            declared = _XML_ENCODING.match(chunk)
            if declared and declared.group(1).lower() not in (b"utf-8", b"utf8"):
                raise _invalid("workbook XML must be UTF-8")
        window = self._tail + chunk
        if _FORBIDDEN_XML.search(window):
            raise _invalid("workbook XML declares a DOCTYPE or ENTITY; refused")
        self._tail = window[-16:]

    def feed_until(self, stop: Callable[[], bool]) -> bool:
        """Feed chunks until the part ends (True) or ``stop``/the cap (False)."""

        while not self.finished:
            if self.exhausted:
                return False
            chunk = self._stream.read(_CHUNK)
            if not chunk:
                self._parser.close()
                self.finished = True
                break
            self._screen(chunk)
            self._read += len(chunk)
            if self._read > self._limit:
                self.exhausted = True
                return False
            self._parser.feed(chunk)
            if stop():
                return False
        return True


def _read_small(
    archive: zipfile.ZipFile, info: zipfile.ZipInfo, target: _Target
) -> None:
    if info.file_size > MAX_XLSX_META_BYTES:
        raise _too_large(f"workbook part {info.filename} is too large to inspect")
    with _XmlPart(archive, info, target, limit=MAX_XLSX_META_BYTES) as part:
        if not part.feed_until(lambda: False):
            raise _too_large(f"workbook part {info.filename} is too large to inspect")


class _Relationships(_Target):
    def __init__(self) -> None:
        self.items: list[tuple[str, str, str, bool]] = []

    def start(self, tag: str, attrib: dict[str, str]) -> None:
        if _local(tag) == "Relationship" and len(self.items) < MAX_XLSX_MEMBERS:
            self.items.append(
                (
                    attrib.get("Id") or "",
                    attrib.get("Type") or "",
                    attrib.get("Target") or "",
                    (attrib.get("TargetMode") or "").lower() == "external",
                )
            )


def _resolve_part(directory: str, target: str) -> str | None:
    if not target:
        return None
    joined = (
        target.lstrip("/")
        if target.startswith("/")
        else posixpath.join(directory, target)
    )
    normal = posixpath.normpath(joined)
    if normal in (".", "..") or normal.startswith(("../", "/")):
        return None
    return normal


def _relationships(
    archive: zipfile.ZipFile, members: dict[str, zipfile.ZipInfo], part: str
) -> dict[str, tuple[str, str]]:
    """``rId`` -> (relationship type, resolved member name) for one part."""

    directory, base = posixpath.split(part)
    rels_name = posixpath.join(directory, "_rels", base + ".rels")
    if rels_name not in members:
        return {}
    target = _Relationships()
    _read_small(archive, members[rels_name], target)
    resolved: dict[str, tuple[str, str]] = {}
    for rid, kind, raw, external in target.items:
        if external:
            continue
        name = _resolve_part(directory, raw)
        if rid and name:
            resolved[rid] = (kind, name)
    return resolved


class _Workbook(_Target):
    def __init__(self) -> None:
        self.sheets: list[tuple[str, str]] = []
        self.date1904 = False

    def start(self, tag: str, attrib: dict[str, str]) -> None:
        name = _local(tag)
        if name == "sheet" and len(self.sheets) < MAX_SHEETS:
            rid = next(
                (
                    value
                    for key, value in attrib.items()
                    if key.startswith("{") and _local(key) == "id"
                ),
                "",
            )
            self.sheets.append(((attrib.get("name") or "")[:256], rid))
        elif name == "workbookPr":
            self.date1904 = (attrib.get("date1904") or "").lower() in ("1", "true")


class _SheetGrid(_Target):
    """The first rows of one worksheet, and a count of every row with a value."""

    def __init__(self) -> None:
        self.rows: list[tuple[int, dict[int, tuple[str, str, int]]]] = []
        self.count = 0
        self.max_column = -1
        self.needed_strings: set[int] = set()
        self.styled: set[int] = set()
        self.clipped = False
        self._chars = 0
        self._row = 0
        self._cells: dict[int, tuple[str, str, int]] | None = None
        self._row_has_value = False
        self._column = -1
        self._type = "n"
        self._style = 0
        self._has_value = False
        self._text: list[str] | None = None
        self._text_len = 0
        self._capture = False
        self._inline = False
        self._phonetic = 0

    @property
    def full(self) -> bool:
        return len(self.rows) >= PREVIEW_ROWS

    def start(self, tag: str, attrib: dict[str, str]) -> None:
        name = _local(tag)
        if name == "c":
            column = _column_index(attrib.get("r"))
            self._column = self._column + 1 if column is None else column
            self._type = attrib.get("t") or "n"
            self._style = _small_int(attrib.get("s"))
            self._has_value = False
            self._text = None
            self._text_len = 0
        elif name == "v":
            self._has_value = True
            self._capture = True
            if self._cells is not None:
                self._text = []
        elif name == "is":
            self._has_value = True
            self._inline = True
            if self._cells is not None:
                self._text = []
        elif name == "t":
            if self._inline:
                self._capture = True
        elif name == "rPh":
            self._phonetic += 1
        elif name == "row":
            number = _small_int(attrib.get("r"), self._row + 1)
            self._row = number if number > 0 else self._row + 1
            self._cells = None if self.full else {}
            self._row_has_value = False
            self._column = -1

    def data(self, text: str) -> None:
        if not self._capture or self._phonetic or self._text is None:
            return
        room = MAX_CELL_CHARS - self._text_len
        if len(text) > room:
            self.clipped = True
            text = text[: max(0, room)]
        if text:
            self._text.append(text)
            self._text_len += len(text)

    def end(self, tag: str) -> None:
        name = _local(tag)
        if name == "v":
            self._capture = False
        elif name == "t":
            if self._inline:
                self._capture = False
        elif name == "rPh":
            self._phonetic = max(0, self._phonetic - 1)
        elif name == "is":
            self._inline = False
            self._capture = False
        elif name == "c":
            self._end_cell()
        elif name == "row":
            if self._row_has_value:
                self.count += 1
                if self._cells is not None:
                    self.rows.append((self._row, self._cells))
            self._cells = None

    def _end_cell(self) -> None:
        if not self._has_value:
            return
        self._row_has_value = True
        self.max_column = max(self.max_column, self._column)
        if self._cells is None or self._text is None:
            return
        if self._column >= MAX_PREVIEW_COLUMNS or self._chars >= MAX_PREVIEW_CHARS:
            self.clipped = True
            return
        value = "".join(self._text)
        self._chars += len(value)
        self._cells[self._column] = (self._type, value, self._style)
        if self._type == "s":
            index = _small_int(value, -1)
            if index >= 0:
                self.needed_strings.add(index)
        elif self._type == "n" and self._style:
            self.styled.add(self._style)


class _SharedStrings(_Target):
    """Only the shared strings the preview rows refer to."""

    def __init__(self, needed: set[int]) -> None:
        self._needed = needed
        self._last = max(needed) if needed else -1
        self.found: dict[int, str] = {}
        self.clipped = False
        self._index = -1
        self._parts: list[str] | None = None
        self._length = 0
        self._in_text = False
        self._phonetic = 0

    @property
    def done(self) -> bool:
        return self._index > self._last or self._last in self.found

    def start(self, tag: str, attrib: dict[str, str]) -> None:
        name = _local(tag)
        if name == "si":
            self._index += 1
            self._parts = [] if self._index in self._needed else None
            self._length = 0
        elif name == "t":
            self._in_text = True
        elif name == "rPh":
            self._phonetic += 1

    def data(self, text: str) -> None:
        if self._parts is None or not self._in_text or self._phonetic:
            return
        room = MAX_CELL_CHARS - self._length
        if len(text) > room:
            self.clipped = True
            text = text[: max(0, room)]
        if text:
            self._parts.append(text)
            self._length += len(text)

    def end(self, tag: str) -> None:
        name = _local(tag)
        if name == "si":
            if self._parts is not None:
                self.found[self._index] = "".join(self._parts)
            self._parts = None
        elif name == "t":
            self._in_text = False
        elif name == "rPh":
            self._phonetic = max(0, self._phonetic - 1)


#: Built-in number formats that show a date, a time, or both (ECMA-376
#: 18.8.30, including the East Asian date/time ids 27-36 and 50-58).
_DATE_FORMAT_IDS = frozenset(
    {14, 15, 16, 17, 27, 28, 29, 30, 31, 36, 50, 51, 52, 53, 54, 55, 56, 57, 58}
)
_TIME_FORMAT_IDS = frozenset({18, 19, 20, 21, 32, 33, 34, 35, 45, 47})
_DATETIME_FORMAT_IDS = frozenset({22})
_FORMAT_LITERALS = re.compile(r'"[^"]*"|\\.|[_*].|\[[^\]]*\]')
_ELAPSED = re.compile(r"\[(?:h+|m+|s+)\]", re.IGNORECASE)


def _date_kind(format_id: int, code: str | None) -> tuple[bool, bool] | None:
    """(shows a date, shows a time) for a date/time number format, else None."""

    if code is None:
        if format_id in _DATETIME_FORMAT_IDS:
            return (True, True)
        if format_id in _DATE_FORMAT_IDS:
            return (True, False)
        if format_id in _TIME_FORMAT_IDS:
            return (False, True)
        return None
    if _ELAPSED.search(code):
        return None  # a duration, not a clock time: keep the number
    section = _FORMAT_LITERALS.sub("", code.split(";", 1)[0]).lower()
    section = section.replace("am/pm", "").replace("a/p", "")
    has_time = "h" in section or "s" in section
    has_date = "y" in section or "d" in section or ("m" in section and not has_time)
    if not has_date and not has_time:
        return None
    return (has_date, has_time)


class _Styles(_Target):
    def __init__(self) -> None:
        self.codes: dict[int, str] = {}
        self.cell_formats: list[int] = []
        self._in_cell_xfs = False

    def start(self, tag: str, attrib: dict[str, str]) -> None:
        name = _local(tag)
        if name == "numFmt" and len(self.codes) < MAX_XLSX_STYLES:
            format_id = _small_int(attrib.get("numFmtId"), -1)
            if format_id >= 0:
                self.codes[format_id] = (attrib.get("formatCode") or "")[:256]
        elif name == "cellXfs":
            self._in_cell_xfs = True
        elif (
            name == "xf"
            and self._in_cell_xfs
            and len(self.cell_formats) < MAX_XLSX_STYLES
        ):
            self.cell_formats.append(_small_int(attrib.get("numFmtId")))

    def end(self, tag: str) -> None:
        if _local(tag) == "cellXfs":
            self._in_cell_xfs = False

    def date_kinds(self, styles: set[int]) -> dict[int, tuple[bool, bool]]:
        kinds: dict[int, tuple[bool, bool]] = {}
        for style in styles:
            if style >= len(self.cell_formats):
                continue
            format_id = self.cell_formats[style]
            kind = _date_kind(format_id, self.codes.get(format_id))
            if kind is not None:
                kinds[style] = kind
        return kinds


#: Serial 2958466 is 10000-01-01 in the 1900 system.
_MAX_SERIAL = 2_958_466


def excel_datetime(raw: str, kind: tuple[bool, bool], date1904: bool) -> str | None:
    """ISO text for an Excel date serial, or None to keep the number."""

    try:
        serial = float(raw)
    except ValueError:
        return None
    if not math.isfinite(serial) or serial < 0 or serial >= _MAX_SERIAL:
        return None
    has_date, has_time = kind
    days = int(serial)
    seconds = int(round((serial - days) * 86_400))
    if seconds >= 86_400:
        days += 1
        seconds -= 86_400
    clock = f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
    if not has_date:
        return clock
    try:
        if date1904:
            day = (datetime(1904, 1, 1) + timedelta(days=days)).date().isoformat()
        elif days == 60:
            day = "1900-02-29"  # the leap day Excel keeps for Lotus 1-2-3
        elif days == 0:
            return clock if has_time else None
        elif days < 60:
            day = (datetime(1899, 12, 31) + timedelta(days=days)).date().isoformat()
        else:
            day = (datetime(1899, 12, 30) + timedelta(days=days)).date().isoformat()
    except OverflowError:
        return None
    return f"{day} {clock}" if has_time else day


_NUMBER_TEXT = re.compile(r"[+-]?(\d*)\.?(\d*)(?:[eE][+-]?\d+)?")


def excel_number(raw: str) -> str:
    """A stored number as Excel shows it: at most 15 significant digits.

    Writers may store 17 digits, so a 9.3 arrives as ``9.300000000000001``;
    the text is kept as written unless it has more digits than Excel shows.
    """

    match = _NUMBER_TEXT.fullmatch(raw.strip())
    if not match or len((match.group(1) + match.group(2)).lstrip("0")) <= 15:
        return raw
    value = float(raw)
    return format(value, ".15g") if math.isfinite(value) else raw


def _cell_text(
    kind: str,
    raw: str,
    style: int,
    strings: dict[int, str],
    date_kinds: dict[int, tuple[bool, bool]],
    date1904: bool,
) -> str:
    if kind == "s":
        return strings.get(_small_int(raw, -1), "")
    if kind == "b":
        return "TRUE" if raw.strip().lower() in ("1", "true") else "FALSE"
    if kind == "n":
        if style in date_kinds:
            converted = excel_datetime(raw, date_kinds[style], date1904)
            if converted is not None:
                return converted
        return excel_number(raw)
    return raw


def _preview_xlsx(
    handle: BinaryIO, *, size: int, sheet: str | None, budget: _Budget
) -> dict[str, Any]:
    if size > MAX_XLSX_BYTES:
        raise _too_large(
            f"workbooks larger than {MAX_XLSX_BYTES} bytes are not opened for preview"
        )
    handle.seek(0)
    try:
        archive = zipfile.ZipFile(handle)
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        OSError,
        EOFError,
        ValueError,
        NotImplementedError,
    ) as error:
        raise _invalid("the file is not a readable zip archive") from error
    try:
        return _read_workbook(archive, sheet=sheet, budget=budget)
    except TablePreviewError:
        raise
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        ElementTree.ParseError,
        OSError,
        EOFError,
        ValueError,
        RuntimeError,
        NotImplementedError,
        zlib.error,
    ) as error:
        raise _invalid(
            "the workbook is damaged or is not a valid .xlsx file"
        ) from error
    finally:
        archive.close()


def _read_workbook(
    archive: zipfile.ZipFile, *, sheet: str | None, budget: _Budget
) -> dict[str, Any]:
    infos = archive.infolist()
    if len(infos) > MAX_XLSX_MEMBERS:
        raise _too_large("the zip archive has too many members to inspect")
    members: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        if info.filename in members:
            raise _invalid("the zip archive repeats a member name")
        members[info.filename] = info
    package = _relationships(archive, members, "")
    workbook_part = next(
        (name for kind, name in package.values() if kind.endswith("/officeDocument")),
        "xl/workbook.xml",
    )
    if workbook_part not in members:
        if "xl/workbook.bin" in members:
            raise _unsupported("binary .xlsb workbooks are not previewed")
        raise _not_tabular("this zip archive is not an Excel .xlsx workbook")
    workbook = _Workbook()
    _read_small(archive, members[workbook_part], workbook)
    rels = _relationships(archive, members, workbook_part)
    worksheets: list[tuple[str, str]] = []
    for name, rid in workbook.sheets:
        kind, part = rels.get(rid, ("", ""))
        if kind.endswith("/worksheet") and part in members:
            worksheets.append((name, part))
    if not worksheets:
        raise _not_tabular("the workbook has no worksheets")
    if sheet:
        chosen = next((item for item in worksheets if item[0] == sheet), None)
        if chosen is None:
            raise TablePreviewError(
                404, f"the workbook has no sheet named {sheet!r}", "sheet_not_found"
            )
    else:
        chosen = worksheets[0]

    def related(suffix: str, default: str) -> str:
        return next(
            (name for kind, name in rels.values() if kind.endswith(suffix)), default
        )

    shared_part = related("/sharedStrings", "xl/sharedStrings.xml")
    styles_part = related("/styles", "xl/styles.xml")
    grid = _SheetGrid()
    strings: dict[int, str] = {}
    unresolved = False
    date_kinds: dict[int, tuple[bool, bool]] = {}
    with _XmlPart(
        archive, members[chosen[1]], grid, limit=MAX_XLSX_PART_BYTES
    ) as sheet_part:
        sheet_part.feed_until(lambda: grid.full or budget.head_over())
        if grid.needed_strings:
            lookup = _SharedStrings(grid.needed_strings)
            if shared_part in members:
                with _XmlPart(
                    archive, members[shared_part], lookup, limit=MAX_XLSX_PART_BYTES
                ) as strings_part:
                    strings_part.feed_until(lambda: lookup.done or budget.head_over())
            strings = lookup.found
            unresolved = len(strings) < len(grid.needed_strings) or lookup.clipped
        if grid.styled and styles_part in members:
            styles = _Styles()
            _read_small(archive, members[styles_part], styles)
            date_kinds = styles.date_kinds(grid.styled)
        exact = sheet_part.finished or sheet_part.feed_until(budget.count_over)

    width = min(
        MAX_PREVIEW_COLUMNS,
        1 + max((max(cells) for _number, cells in grid.rows if cells), default=-1),
    )
    rows: list[list[str]] = []
    for _number, cells in grid.rows:
        line = [""] * width
        for column, (kind, raw, style) in cells.items():
            line[column] = _cell_text(
                kind, raw, style, strings, date_kinds, workbook.date1904
            )
        rows.append(line)
    total_columns = grid.max_column + 1
    return {
        "kind": "table",
        "format": "xlsx",
        "delimiter": None,
        "encoding": None,
        "compression": None,
        "inner_filename": None,
        "sheets": [name for name, _part in worksheets],
        "sheet": chosen[0],
        "columns": [column_letters(index) for index in range(width)],
        "row_numbers": [number for number, _cells in grid.rows],
        "rows": rows,
        "total_rows": grid.count if exact else None,
        "total_rows_lower_bound": None if exact else grid.count,
        "total_columns": total_columns,
        "truncated": (
            not exact
            or grid.count > len(rows)
            or grid.clipped
            or unresolved
            or width < total_columns
        ),
    }


__all__ = [
    "MAX_CELL_CHARS",
    "MAX_PREVIEW_COLUMNS",
    "PREVIEW_ROWS",
    "TablePreviewError",
    "column_letters",
    "excel_datetime",
    "excel_number",
    "preview_table",
]
