"""ART-11: bounded previews of large CSV, xlsx workbooks and gzip files.

A 40 MB CSV used to get no preview at all (the browser's 8 MiB read budget
refused it), an ``.xlsx`` had no viewer, and a ``.csv.gz`` was typed
``text/csv`` and decoded as text. ``GET /artifacts/{id}/preview-table`` reads
the exact version on the server under time and size bounds; these tests pin
its counting, its hostile-input refusals, its version and team scoping, and
the gzip typing at upload and capture.
"""

from __future__ import annotations

import base64
import bz2
import codecs
import csv
import gzip
import io
import zipfile
from pathlib import Path

import pytest

from openai4s.config import RoadmapFeatureFlags
from openai4s.server import table_preview
from openai4s.server.artifacts import is_text_editable
from openai4s.server.gateway import _guess_ctype
from openai4s.server.renderers import RendererRegistry
from openai4s.server.table_preview import (
    TablePreviewError,
    column_letters,
    excel_datetime,
    excel_number,
    preview_table,
)
from tests.test_artifact_manager import ArtifactHarness
from tests.test_artifact_workbench import _call, _save_snapshot, _setup
from tests.test_team_auth_routes import (  # noqa: F401  (fixture reuse)
    _fast_pbkdf2,
    _get,
    _login,
    _TeamDaemon,
)
from tests.test_team_isolation_hardening import _body, _create_session, _seed_artifact

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


def _write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def _csv_bytes(rows: list[list[str]], encoding: str = "utf-8") -> bytes:
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    return buffer.getvalue().encode(encoding)


def _refusal(path: Path, **kwargs) -> TablePreviewError:
    with pytest.raises(TablePreviewError) as caught:
        preview_table(path, filename=kwargs.pop("filename", path.name), **kwargs)
    return caught.value


# ---------------------------------------------------------------------------
# delimited text
# ---------------------------------------------------------------------------


def test_large_csv_returns_the_head_and_the_exact_row_count(tmp_path):
    rows = [["sample", "value"], *[[f"s{i}", str(i)] for i in range(5_000)]]
    path = _write(tmp_path / "big.csv", _csv_bytes(rows))

    result = preview_table(path, filename="big.csv")

    assert result["format"] == "csv" and result["delimiter"] == ","
    assert result["columns"] == ["sample", "value"]
    assert len(result["rows"]) == table_preview.PREVIEW_ROWS
    assert result["rows"][0] == ["s0", "0"] and result["rows"][-1] == ["s99", "99"]
    assert result["total_rows"] == 5_000
    assert result["total_rows_lower_bound"] is None
    assert result["total_columns"] == 2
    assert result["truncated"] is True
    assert result["size_bytes"] == path.stat().st_size
    assert result["compression"] is None and result["sheets"] == []


def test_records_are_counted_not_lines(tmp_path):
    """A quoted newline is one record; an inch mark mid-field is not a quote;
    a blank line is not a row (pandas' ``skip_blank_lines``)."""

    text = (
        "id,note\r\n"
        '1,"two\nline note"\r\n'
        '2,"three\r\nline\nnote"\r\n'
        "\r\n"
        '3,a 5" disk\r\n'
        '4,"say ""hi"""\r\n'
    )
    path = _write(tmp_path / "notes.csv", text.encode("utf-8"))

    result = preview_table(path, filename="notes.csv")

    assert result["total_rows"] == 4
    assert result["rows"] == [
        ["1", "two\nline note"],
        ["2", "three\r\nline\nnote"],
        ["3", 'a 5" disk'],
        ["4", 'say "hi"'],
    ]
    assert result["truncated"] is False


def test_gbk_and_utf16_tables_decode_as_themselves(tmp_path):
    gbk = _write(
        tmp_path / "cohort_gbk.csv",
        _csv_bytes([["患者编号", "年龄"], ["P001", "38"], ["P002", "56"]], "gbk"),
    )
    result = preview_table(gbk, filename="cohort_gbk.csv")
    assert result["encoding"] == "gb18030"
    assert result["columns"] == ["患者编号", "年龄"]
    assert result["total_rows"] == 2

    utf16 = _write(
        tmp_path / "export.tsv",
        codecs.BOM_UTF16_LE + "基因\tlog2FC\nTP53\t-1.2\n".encode("utf-16-le"),
    )
    result = preview_table(utf16, filename="export.tsv")
    assert result["encoding"] == "utf-16le"
    assert result["format"] == "tsv"
    assert result["columns"] == ["基因", "log2FC"]
    assert result["rows"] == [["TP53", "-1.2"]]


def test_delimiter_follows_the_name_then_a_sniff(tmp_path):
    semicolons = _write(tmp_path / "eu.dat", b"a;b;c\n1;2;3\n")
    result = preview_table(semicolons, filename="eu.dat")
    assert result["delimiter"] == ";" and result["rows"] == [["1", "2", "3"]]

    tabs = _write(tmp_path / "counts.txt", b"gene\tS1\tS2\nTP53\t5\t7\n")
    assert preview_table(tabs, filename="counts.txt")["format"] == "tsv"

    prose = _write(tmp_path / "readme.txt", b"just some words\nnothing tabular\n")
    assert _refusal(prose).code == "not_tabular"

    single = _write(tmp_path / "ids.csv", b"id\nA\nB\n")
    assert preview_table(single, filename="ids.csv")["total_rows"] == 2


def test_empty_and_header_only_tables(tmp_path):
    empty = preview_table(_write(tmp_path / "empty.csv", b""), filename="empty.csv")
    assert empty["columns"] == [] and empty["rows"] == [] and empty["total_rows"] == 0
    header = preview_table(_write(tmp_path / "h.csv", b"a,b\n"), filename="h.csv")
    assert header["columns"] == ["a", "b"] and header["total_rows"] == 0


def test_binary_bytes_are_not_a_table(tmp_path):
    path = _write(tmp_path / "blob.csv", bytes(range(256)) * 16)
    error = _refusal(path)
    assert (error.status, error.code) == (415, "not_tabular")


def test_counting_stops_at_its_time_bound_with_a_lower_bound(tmp_path):
    rows = [["n"], *[[str(i)] for i in range(5_000)]]
    path = _write(tmp_path / "slow.csv", _csv_bytes(rows))
    calls = {"n": 0}

    def clock() -> float:
        # The budget is set on the first call; the head checks its own
        # deadline once per row. Only counting runs out of time.
        calls["n"] += 1
        return 0.0 if calls["n"] <= 1 + table_preview.PREVIEW_ROWS else 1e9

    result = preview_table(path, filename="slow.csv", clock=clock)

    assert len(result["rows"]) == table_preview.PREVIEW_ROWS
    assert result["total_rows"] is None
    assert table_preview.PREVIEW_ROWS <= result["total_rows_lower_bound"] < 5_000
    assert result["truncated"] is True


def test_oversized_lines_and_records_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(table_preview, "MAX_LINE_CHARS", 64)
    monkeypatch.setattr(table_preview, "MAX_RECORD_CHARS", 128)
    header = _write(tmp_path / "wide.csv", ("x," * 100 + "\n").encode())
    error = _refusal(header)
    assert (error.status, error.code) == (413, "too_large_to_inspect")

    # A quoted field that never closes keeps the record growing line by line;
    # the head before it is still shown, and the count is only a lower bound.
    body = 'a,b\n1,2\n3,"' + "short line\n" * 50
    runaway = _write(tmp_path / "runaway.csv", body.encode())
    result = preview_table(runaway, filename="runaway.csv")
    assert result["rows"] == [["1", "2"]]
    assert result["total_rows"] is None and result["total_rows_lower_bound"] == 1


# ---------------------------------------------------------------------------
# gzip, by magic
# ---------------------------------------------------------------------------


def test_gzip_is_read_by_its_magic_bytes_not_its_name(tmp_path):
    table = _csv_bytes(
        [["cell", "Gene0000"], *[[f"cell_{i}", str(i)] for i in range(1_500)]]
    )
    named = _write(tmp_path / "scrna_counts.csv.gz", gzip.compress(table))
    result = preview_table(named, filename="scrna_counts.csv.gz")
    assert result["compression"] == "gzip"
    assert result["inner_filename"] == "scrna_counts.csv"
    assert result["columns"] == ["cell", "Gene0000"]
    assert result["total_rows"] == 1_500
    assert result["size_bytes"] == named.stat().st_size

    # Typed and named as a plain CSV, the bytes still decide.
    disguised = _write(tmp_path / "data.csv", gzip.compress(table))
    result = preview_table(disguised, filename="data.csv", content_type="text/csv")
    assert result["compression"] == "gzip" and result["total_rows"] == 1_500

    matrix = _write(
        tmp_path / "GSE1_counts.txt.gz", gzip.compress(b"gene\tS1\tS2\nTP53\t1\t2\n")
    )
    assert preview_table(matrix, filename="GSE1_counts.txt.gz")["format"] == "tsv"


def test_gzip_of_non_tabular_content_is_named_as_such(tmp_path):
    path = _write(tmp_path / "small.txt.gz", gzip.compress(b"gz text\n" * 20))
    error = _refusal(path)
    assert (error.status, error.code) == (415, "not_tabular")
    assert "gzip" in error.message


def test_gzip_inflation_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(table_preview, "MAX_GZIP_INFLATED_BYTES", 50_000)
    table = _csv_bytes([["n", "x"], *[[str(i), "y" * 20] for i in range(20_000)]])
    path = _write(tmp_path / "bomb.csv.gz", gzip.compress(table))
    result = preview_table(path, filename="bomb.csv.gz")
    assert len(result["rows"]) == table_preview.PREVIEW_ROWS
    assert result["total_rows"] is None
    assert table_preview.PREVIEW_ROWS <= result["total_rows_lower_bound"] < 20_000


def test_truncated_gzip(tmp_path):
    small = gzip.compress(_csv_bytes([["a", "b"], ["1", "2"]]))
    cut = _write(tmp_path / "cut.csv.gz", small[: len(small) // 2])
    assert _refusal(cut).code == "invalid_file"

    # A long stream cut late still shows its head; its count is a lower bound.
    big = gzip.compress(
        _csv_bytes(
            [["i", "v"], *[[str(i), f"{i * 7919 % 104729}"] for i in range(400_000)]]
        )
    )
    late = _write(tmp_path / "late.csv.gz", big[: int(len(big) * 0.8)])
    result = preview_table(late, filename="late.csv.gz")
    assert len(result["rows"]) == table_preview.PREVIEW_ROWS
    assert result["total_rows"] is None


def test_other_containers_are_refused_by_name(tmp_path):
    xls = _write(
        tmp_path / "old.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512
    )
    error = _refusal(xls)
    assert (error.status, error.code) == (415, "unsupported_format")
    assert ".xls" in error.message
    bzipped = _write(tmp_path / "x.csv.bz2", bz2.compress(b"a,b\n1,2\n"))
    assert _refusal(bzipped).code == "unsupported_format"


# ---------------------------------------------------------------------------
# xlsx
# ---------------------------------------------------------------------------

_STYLES = (
    f'{XML_HEAD}<styleSheet xmlns="{MAIN_NS}">'
    '<numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd hh:mm"/>'
    "</numFmts>"
    '<cellStyleXfs count="1"><xf numFmtId="0"/></cellStyleXfs>'
    '<cellXfs count="4"><xf numFmtId="0"/><xf numFmtId="14"/>'
    '<xf numFmtId="164"/><xf numFmtId="2"/></cellXfs></styleSheet>'
)
_SHARED = (
    f'{XML_HEAD}<sst xmlns="{MAIN_NS}" count="4" uniqueCount="4">'
    "<si><t>基因</t></si>"
    "<si><r><t>Rich </t></r><r><rPr><b/></rPr><t>text</t></r></si>"
    '<si><t>漢字</t><rPh sb="0" eb="2"><t>カンジ</t></rPh></si>'
    "<si><t>plain</t></si></sst>"
)
_SHEET_ONE = (
    '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c>'
    '<c r="C1" t="inlineStr"><is><t>inline</t></is></c>'
    '<c r="D1" t="s"><v>2</v></c><c r="E1" t="s"><v>3</v></c></row>'
    '<row r="3"><c r="A3"><v>42</v></c><c r="B3" t="b"><v>1</v></c>'
    '<c r="C3" t="e"><v>#DIV/0!</v></c><c r="D3" s="1"><v>45292</v></c>'
    '<c r="E3" s="2"><v>45292.5</v></c></row>'
    '<row r="4"><c r="C4"><v>3.5</v></c><c r="D4"><v>9.300000000000001</v></c>'
    '<c r="E4" s="3"><v>2.25</v></c></row>'
    '<row r="5" ht="20" customHeight="1"/>'
    '<row r="6"><c r="A6" s="1"/></row>'
)


def _xlsx(
    path: Path,
    sheets: list[tuple[str, str]],
    *,
    shared: str | None = None,
    styles: str | None = None,
    date1904: bool = False,
    workbook_xml: str | None = None,
    raw_sheets: dict[int, bytes] | None = None,
    compression: int = zipfile.ZIP_DEFLATED,
) -> Path:
    rel_type = DOC_REL
    sheet_tags = "".join(
        f'<sheet name="{name}" sheetId="{index + 1}" r:id="rId{index + 1}"/>'
        for index, (name, _data) in enumerate(sheets)
    )
    flag = '<workbookPr date1904="1"/>' if date1904 else "<workbookPr/>"
    workbook = workbook_xml or (
        f'{XML_HEAD}<workbook xmlns="{MAIN_NS}" xmlns:r="{DOC_REL}">'
        f"{flag}<sheets>{sheet_tags}</sheets></workbook>"
    )
    rels = [
        f'<Relationship Id="rId{index + 1}" Type="{rel_type}/worksheet" '
        f'Target="worksheets/sheet{index + 1}.xml"/>'
        for index in range(len(sheets))
    ]
    parts: dict[str, bytes | str] = {
        "[Content_Types].xml": f'{XML_HEAD}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        "_rels/.rels": (
            f'{XML_HEAD}<Relationships xmlns="{PKG_REL}">'
            f'<Relationship Id="rId1" Type="{rel_type}/officeDocument" '
            'Target="/xl/workbook.xml"/></Relationships>'
        ),
        "xl/workbook.xml": workbook,
    }
    if shared is not None:
        rels.append(
            f'<Relationship Id="rIdS" Type="{rel_type}/sharedStrings" '
            'Target="sharedStrings.xml"/>'
        )
        parts["xl/sharedStrings.xml"] = shared
    if styles is not None:
        rels.append(
            f'<Relationship Id="rIdT" Type="{rel_type}/styles" Target="styles.xml"/>'
        )
        parts["xl/styles.xml"] = styles
    parts["xl/_rels/workbook.xml.rels"] = (
        f'{XML_HEAD}<Relationships xmlns="{PKG_REL}">{"".join(rels)}</Relationships>'
    )
    for index, (_name, data) in enumerate(sheets):
        parts[f"xl/worksheets/sheet{index + 1}.xml"] = (raw_sheets or {}).get(
            index,
            (
                f'{XML_HEAD}<worksheet xmlns="{MAIN_NS}"><dimension ref="A1"/>'
                f"<sheetData>{data}</sheetData></worksheet>"
            ).encode("utf-8"),
        )
    with zipfile.ZipFile(path, "w", compression=compression) as archive:
        for name, body in parts.items():
            archive.writestr(name, body)
    return path


def _rows_xml(count: int, start: int = 1) -> str:
    return "".join(
        f'<row r="{r}"><c r="A{r}" t="inlineStr"><is><t>r{r}</t></is></c>'
        f'<c r="B{r}"><v>{r}</v></c></row>'
        for r in range(start, start + count)
    )


def test_xlsx_lists_sheets_and_reads_cells_like_excel_shows_them(tmp_path):
    path = _xlsx(
        tmp_path / "cohort.xlsx",
        [("数据", _SHEET_ONE), ("Second", _rows_xml(1))],
        shared=_SHARED,
        styles=_STYLES,
    )

    result = preview_table(path, filename="cohort.xlsx")

    assert result["format"] == "xlsx" and result["encoding"] is None
    assert result["sheets"] == ["数据", "Second"]
    assert result["sheet"] == "数据"
    assert result["columns"] == ["A", "B", "C", "D", "E"]
    # Rows that hold no value (a formatted row, a styled empty cell) are skipped
    # and do not count; the row numbers keep the gap visible.
    assert result["row_numbers"] == [1, 3, 4]
    assert result["rows"] == [
        ["基因", "Rich text", "inline", "漢字", "plain"],
        ["42", "TRUE", "#DIV/0!", "2024-01-01", "2024-01-01 12:00:00"],
        # Excel shows at most 15 significant digits of a stored double.
        ["", "", "3.5", "9.3", "2.25"],
    ]
    assert result["total_rows"] == 3 and result["total_columns"] == 5
    assert result["truncated"] is False

    second = preview_table(path, filename="cohort.xlsx", sheet="Second")
    assert second["sheet"] == "Second" and second["rows"] == [["r1", "1"]]

    missing = _refusal(path, sheet="Nope")
    assert (missing.status, missing.code) == (404, "sheet_not_found")


def test_xlsx_head_is_the_first_hundred_rows_and_the_count_is_exact(tmp_path):
    path = _xlsx(tmp_path / "long.xlsx", [("S", _rows_xml(250))])
    result = preview_table(path, filename="long.xlsx")
    assert len(result["rows"]) == 100
    assert result["row_numbers"][:3] == [1, 2, 3]
    assert result["total_rows"] == 250 and result["truncated"] is True


def test_xlsx_without_shared_strings_and_in_the_1904_system(tmp_path):
    styles = (
        f'{XML_HEAD}<styleSheet xmlns="{MAIN_NS}"><cellXfs count="2">'
        '<xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>'
    )
    data = '<row r="1"><c r="A1" s="1"><v>0</v></c><c r="B1" t="inlineStr"><is><t>x</t></is></c></row>'
    path = _xlsx(tmp_path / "mac.xlsx", [("S", data)], styles=styles, date1904=True)
    result = preview_table(path, filename="mac.xlsx")
    assert result["rows"] == [["1904-01-01", "x"]]


def test_xlsx_sheet_reads_stop_at_their_byte_cap(tmp_path, monkeypatch):
    path = _xlsx(tmp_path / "big.xlsx", [("S", _rows_xml(3_000))])
    monkeypatch.setattr(table_preview, "MAX_XLSX_PART_BYTES", 100_000)
    result = preview_table(path, filename="big.xlsx")
    assert len(result["rows"]) == 100
    assert result["total_rows"] is None
    assert 100 <= result["total_rows_lower_bound"] < 3_000


@pytest.mark.parametrize("where", ["sheet", "workbook", "split"])
def test_xlsx_doctype_and_entities_are_refused(tmp_path, where):
    bomb = '<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]>'
    if where == "sheet":
        raw = (
            f'{XML_HEAD}{bomb}<worksheet xmlns="{MAIN_NS}"><sheetData>'
            '<row r="1"><c r="A1" t="inlineStr"><is><t>&lol2;</t></is></c></row>'
            "</sheetData></worksheet>"
        ).encode()
        path = _xlsx(tmp_path / "x.xlsx", [("S", "")], raw_sheets={0: raw})
    elif where == "workbook":
        workbook = (
            f'{XML_HEAD}{bomb}<workbook xmlns="{MAIN_NS}" xmlns:r="{DOC_REL}">'
            '<sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>'
        )
        path = _xlsx(tmp_path / "x.xlsx", [("S", _rows_xml(1))], workbook_xml=workbook)
    else:
        # A lone declaration straddling the scan's 64 KiB read boundary: only
        # the overlap kept between chunks can see it.
        pad = " " * (65_536 - len(XML_HEAD) - 4)
        raw = (
            f'{XML_HEAD}{pad}<!DOCTYPE worksheet SYSTEM "evil.dtd">'
            f'<worksheet xmlns="{MAIN_NS}"><sheetData/></worksheet>'
        ).encode()
        path = _xlsx(tmp_path / "x.xlsx", [("S", "")], raw_sheets={0: raw})
    error = _refusal(path)
    assert (error.status, error.code) == (422, "invalid_file")
    # The byte scan refused it before any byte reached the XML parser.
    assert "DOCTYPE or ENTITY" in error.message


def test_xlsx_parser_target_refuses_a_doctype_the_scan_missed(tmp_path, monkeypatch):
    """Second layer: should the byte scan ever miss a declaration, the parser
    target refuses it at the DOCTYPE's start, before any entity is declared."""

    monkeypatch.setattr(
        table_preview, "_FORBIDDEN_XML", __import__("re").compile(rb"$^")
    )
    raw = (
        f'{XML_HEAD}<!DOCTYPE x [<!ENTITY a "b">]><worksheet xmlns="{MAIN_NS}">'
        "<sheetData/></worksheet>"
    ).encode()
    path = _xlsx(tmp_path / "x.xlsx", [("S", "")], raw_sheets={0: raw})
    error = _refusal(path)
    assert error.code == "invalid_file"
    assert error.message == "workbook XML declares a DOCTYPE; refused"


def test_xlsx_zip_bomb_parts_are_refused_before_they_are_inflated(tmp_path):
    padded = (
        f'{XML_HEAD}<workbook xmlns="{MAIN_NS}" xmlns:r="{DOC_REL}">'
        + " " * (table_preview.MAX_XLSX_META_BYTES + 1)
        + '<sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    path = _xlsx(tmp_path / "bomb.xlsx", [("S", _rows_xml(1))], workbook_xml=padded)
    assert path.stat().st_size < 100_000  # deflate makes it small on disk
    error = _refusal(path)
    assert (error.status, error.code) == (413, "too_large_to_inspect")


def test_xlsx_parts_must_be_deflate_and_utf8(tmp_path):
    bzipped = _xlsx(
        tmp_path / "bz.xlsx", [("S", _rows_xml(1))], compression=zipfile.ZIP_BZIP2
    )
    assert _refusal(bzipped).code == "unsupported_format"
    utf16 = (
        f'<?xml version="1.0" encoding="UTF-16"?><worksheet xmlns="{MAIN_NS}">'
        "<sheetData/></worksheet>"
    ).encode("utf-16")
    wide = _xlsx(tmp_path / "u16.xlsx", [("S", "")], raw_sheets={0: utf16})
    assert _refusal(wide).code == "invalid_file"


def test_zip_files_that_are_not_workbooks(tmp_path):
    plain = tmp_path / "archive.xlsx"
    with zipfile.ZipFile(plain, "w") as archive:
        archive.writestr("data/readme.txt", "hello")
    assert _refusal(plain).code == "not_tabular"
    xlsb = tmp_path / "book.xlsx"
    with zipfile.ZipFile(xlsb, "w") as archive:
        archive.writestr("xl/workbook.bin", b"\x00\x01")
    assert _refusal(xlsb).code == "unsupported_format"
    corrupt = _write(tmp_path / "bad.xlsx", b"PK\x03\x04" + b"\x00garbage" * 40)
    assert _refusal(corrupt).code == "invalid_file"


def test_excel_serials_and_column_letters():
    assert excel_datetime("1", (True, False), False) == "1900-01-01"
    assert excel_datetime("59", (True, False), False) == "1900-02-28"
    assert excel_datetime("60", (True, False), False) == "1900-02-29"
    assert excel_datetime("61", (True, False), False) == "1900-03-01"
    assert excel_datetime("45292", (True, False), False) == "2024-01-01"
    assert excel_datetime("45292.5", (True, True), False) == "2024-01-01 12:00:00"
    assert excel_datetime("0.75", (False, True), False) == "18:00:00"
    assert excel_datetime("0", (True, False), True) == "1904-01-01"
    assert excel_datetime("-1", (True, False), False) is None
    assert excel_datetime("text", (True, False), False) is None
    assert excel_number("9.300000000000001") == "9.3"
    assert excel_number("1E-3") == "1E-3"  # already within 15 digits: as written
    assert excel_number("45292") == "45292"
    assert excel_number("#N/A") == "#N/A"
    assert [column_letters(i) for i in (0, 25, 26, 701, 702)] == [
        "A",
        "Z",
        "AA",
        "ZZ",
        "AAA",
    ]


# ---------------------------------------------------------------------------
# the route, with the Stage 9 workbench flag OFF
# ---------------------------------------------------------------------------


def test_route_serves_the_head_with_the_workbench_off_and_pins_versions(tmp_path):
    _cfg, runner, handler, fid = _setup(tmp_path, workbench=False)
    workspace = runner.workspace_for_branch(fid, fid)
    first = _save_snapshot(
        runner,
        fid,
        workspace / "table.csv",
        _csv_bytes([["name", "n"], ["old", "1"]]),
        content_type="text/csv",
    )
    edited = runner.edit_artifact(first["artifact_id"], "name,n\nnew,2\nnewer,3\n")
    path = f"/artifacts/{first['artifact_id']}/preview-table"

    code, latest = _call(handler, "GET", path)
    assert code == 200
    assert latest["version_id"] == edited["version_id"]
    assert latest["artifact_id"] == first["artifact_id"]
    assert latest["rows"] == [["new", "2"], ["newer", "3"]]

    code, pinned = _call(
        handler, "GET", path, query={"version_id": [first["version_id"]]}
    )
    assert code == 200
    assert pinned["version_id"] == first["version_id"]
    assert pinned["rows"] == [["old", "1"]] and pinned["total_rows"] == 1

    other = _save_snapshot(
        runner,
        fid,
        workspace / "other.csv",
        b"x\n1\n",
        content_type="text/csv",
    )
    code, foreign = _call(
        handler, "GET", path, query={"version_id": [other["version_id"]]}
    )
    assert code == 404 and foreign["code"] == "artifact_version_not_found"
    code, unknown = _call(handler, "GET", "/artifacts/a-missing/preview-table")
    assert code == 404 and unknown["code"] == "artifact_not_found"
    runner.close()


def test_route_reads_xlsx_sheets_and_answers_refusals_as_coded_json(tmp_path):
    _cfg, runner, handler, fid = _setup(tmp_path, workbench=False)
    workspace = runner.workspace_for_branch(fid, fid)
    book = _xlsx(
        tmp_path / "src.xlsx",
        [("临床信息", _rows_xml(2)), ("标志物与疗效", _rows_xml(1, start=7))],
    )
    workbook = _save_snapshot(
        runner,
        fid,
        workspace / "cohort.xlsx",
        book.read_bytes(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    path = f"/artifacts/{workbook['artifact_id']}/preview-table"
    code, chosen = _call(handler, "GET", path, query={"sheet": ["标志物与疗效"]})
    assert code == 200
    assert chosen["sheets"] == ["临床信息", "标志物与疗效"]
    assert chosen["sheet"] == "标志物与疗效" and chosen["row_numbers"] == [7]

    text = _save_snapshot(
        runner,
        fid,
        workspace / "small.txt.gz",
        gzip.compress(b"gz text\n" * 20),
        content_type="application/gzip",
    )
    code, refused = _call(
        handler, "GET", f"/artifacts/{text['artifact_id']}/preview-table"
    )
    assert code == 415
    assert refused["code"] == "not_tabular" and refused["error"]
    runner.close()


def test_preview_route_is_scoped_to_the_callers_sessions(tmp_path):
    """Workbench flag off (the default): the route still answers, and only for
    a session the caller may see, with a version from that same Artifact."""

    node = _TeamDaemon(tmp_path)
    node.seed_user("alice", "fake-pw-a")
    node.seed_user("bob", "fake-pw-b")
    node.store.create_project(name="p", description="", context="")
    try:
        alice = _login(node, "alice", "fake-pw-a")
        bob = _login(node, "bob", "fake-pw-b")
        alice_frame = _create_session(node, alice)
        bob_frame = _create_session(node, bob)
        mine = _seed_artifact(node, alice_frame, "alice.csv", b"who\nALICE_ROW\n")
        theirs = _seed_artifact(node, bob_frame, "bob.csv", b"who\nBOB_PRIVATE_ROW\n")
        for artifact in (mine, theirs):
            node.runner.artifacts.write_version_snapshot(
                artifact["version_id"],
                artifact["filename"],
                src_path=Path(artifact["path"]),
            )
        path = f"/api/v1/artifacts/{mine['artifact_id']}/preview-table"

        status, raw = _get(node.port, path, cookie=alice)
        assert status == 200, raw[:300]
        assert _body(raw)["rows"] == [["ALICE_ROW"]]

        status, raw = _get(node.port, path, cookie=bob)
        assert status == 404
        assert b"ALICE_ROW" not in raw

        status, raw = _get(
            node.port, f"{path}?version_id={theirs['version_id']}", cookie=alice
        )
        assert status == 404
        assert b"BOB_PRIVATE_ROW" not in raw
        assert _body(raw)["code"] == "artifact_version_not_found"
    finally:
        node.close()


# ---------------------------------------------------------------------------
# typing at upload/capture, renderer selection, editability
# ---------------------------------------------------------------------------


def test_compression_suffix_types_the_container():
    assert _guess_ctype("scrna_counts.csv.gz") == "application/gzip"
    assert _guess_ctype("x.gz") == "application/gzip"
    assert _guess_ctype("x.tar.gz") == "application/gzip"
    assert _guess_ctype("x.csv.bz2") == "application/x-bzip2"
    assert _guess_ctype("x.csv") == "text/csv"


def test_uploads_are_typed_gzip_by_magic(tmp_path):
    _cfg, runner, handler, fid = _setup(tmp_path, workbench=False)
    payload = gzip.compress(b"a,b\n1,2\n")

    def upload(name: str, data: bytes) -> str:
        code, body = _call(
            handler,
            "POST",
            "/uploads",
            body={
                "filename": name,
                "content_base64": base64.b64encode(data).decode(),
                "frame_id": fid,
            },
        )
        assert code == 200, body
        return runner.store.get_artifact(body["artifact_id"])["content_type"]

    assert upload("counts.csv.gz", payload) == "application/gzip"
    assert upload("data.csv", payload) == "application/gzip"
    assert upload("plain.csv", b"a,b\n1,2\n") == "text/csv"
    runner.close()


def test_captured_gzip_is_typed_by_magic(tmp_path):
    harness = ArtifactHarness(tmp_path)
    path = harness.workspace / "counts.csv"
    path.write_bytes(gzip.compress(b"a,b\n1,2\n"))
    record = harness.manager.register_file(
        harness.session, path, "cell-1", lambda _e: None
    )
    stored = harness.store.get_artifact(record["artifact_id"])
    assert stored["content_type"] == "application/gzip"


def test_renderer_selection_for_containers():
    registry = RendererRegistry()

    def chosen(**artifact) -> tuple[str, str]:
        selected = registry.select(artifact)
        return selected["renderer"]["renderer_id"], selected["matched_by"]

    # A row typed before gzip typing existed is still sent to the gzip viewer.
    assert chosen(filename="x.csv.gz", content_type="text/csv") == (
        "compressed",
        "extension",
    )
    assert chosen(filename="data.csv", content_type="application/gzip") == (
        "compressed",
        "content_type",
    )
    assert chosen(filename="cohort.xlsx", kind="table") == ("spreadsheet", "extension")
    assert chosen(filename="old.xls", content_type="application/vnd.ms-excel") == (
        "download",
        "extension",
    )
    catalog = {item["renderer_id"]: item for item in registry.catalog()}
    assert list(catalog["spreadsheet"]["capabilities"]) == ["view"]
    assert list(catalog["compressed"]["capabilities"]) == ["view"]


def test_binary_containers_are_never_text_editable():
    xlsx = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert is_text_editable("cohort.xlsx", xlsx) is False
    assert is_text_editable("counts.csv.gz", "text/csv") is False
    assert is_text_editable("data.csv", "application/gzip") is False
    assert is_text_editable("plain.csv", "text/csv") is True
