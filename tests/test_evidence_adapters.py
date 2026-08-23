"""Read-only adapters must not treat a filename as complete evidence."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from openai4s.server import evidence_adapters as adapter_mod
from openai4s.server.evidence_adapters import (
    adapt_artifact,
    adapt_image,
    adapt_pdf,
    adapt_structure,
    adapt_table,
    classify_artifact,
)


def _forbid_whole_file_path_reads(monkeypatch):
    def forbidden(self, *args, **kwargs):
        raise AssertionError(f"unbounded whole-file read called for {self}")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)


def test_csv_table_reports_full_column_stats(tmp_path):
    path = tmp_path / "resid.csv"
    path.write_text("value\n1\n3\n5\n", encoding="utf-8")
    row = adapt_table(path, version_id="ver-1", artifact_id="art-1")
    assert row["complete"] is True
    assert row["summary"]["row_count"] == 3
    assert row["summary"]["columns"]["value"]["mean"] == 3.0


def test_pdf_without_extractable_text_is_incomplete(tmp_path):
    path = tmp_path / "note.pdf"
    path.write_bytes(b"%PDF-1.4\n1 0 obj<<>>endobj\n")
    row = adapt_pdf(path, version_id="ver-pdf", artifact_id="art-pdf")
    assert row["complete"] is False
    assert row["omission_reason"] == "pdf_text_unavailable"


def test_png_reports_dimensions(tmp_path):
    path = tmp_path / "plot.png"
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + b"\x00\x00\x00\x02\x00\x00\x00\x03"
        + b"\x00" * 8
    )
    row = adapt_image(path, version_id="ver-img", artifact_id="art-img")
    assert row["complete"] is True
    assert row["summary"]["width"] == 2
    assert row["summary"]["height"] == 3


def test_mol_counts_atoms_and_bonds(tmp_path):
    path = tmp_path / "benzene.mol"
    path.write_text("\n\n\n  6  6  0  0  0  0\n", encoding="utf-8")
    row = adapt_structure(path, version_id="ver-mol", artifact_id="art-mol")
    assert row["complete"] is True
    assert row["summary"]["atom_count"] == 6
    assert row["summary"]["bond_count"] == 6


def test_filename_alone_is_not_complete_coverage(tmp_path):
    assert classify_artifact("results.csv") == "table"
    missing = adapt_artifact(
        tmp_path / "missing.csv",
        filename="results.csv",
        version_id="ver-missing",
        artifact_id="art-missing",
    )
    assert missing is not None
    assert missing["complete"] is False
    assert missing["omission_reason"] == "artifact_bytes_missing"


def test_an_unparseable_artifact_records_coverage_instead_of_escaping(tmp_path):
    """An adapter parses agent-authored files, so its failures are open-ended.

    `except OSError` caught none of these, and `collect_turn_evidence` calls
    `adapt_artifact` unguarded -- so one malformed artifact propagated out
    through the completion gate, where the gateway swallows it into
    `gate = None` and the turn ships with no review at all.
    """

    from openai4s.server.evidence_adapters import adapt_artifact

    oversized_csv_field = tmp_path / "big.csv"
    oversized_csv_field.write_text(
        'a,b\n"' + ("z" * 200_000) + '",2\n', encoding="utf-8"
    )
    superscript_counts_line = tmp_path / "bad.mol"
    superscript_counts_line.write_text("name\n\n\n² 3  0  0\n", encoding="utf-8")

    for path in (oversized_csv_field, superscript_counts_line):
        adapted = adapt_artifact(
            path, filename=path.name, version_id="v", artifact_id="a"
        )
        assert adapted["complete"] is False, path.name
        assert adapted["omission_reason"] == "artifact_unreadable", path.name


def test_csv_tail_beyond_input_budget_is_explicitly_incomplete(tmp_path, monkeypatch):
    path = tmp_path / "tail.csv"
    with path.open("wb") as handle:
        handle.write(b"value\n1\n")
        handle.seek(8 * 1024 * 1024)
        handle.write(b"999999\n")

    _forbid_whole_file_path_reads(monkeypatch)
    row = adapt_table(path, version_id="ver-csv", artifact_id="art-csv")

    assert row["complete"] is False
    assert row["omission_reason"] == "adapter_input_truncated"
    assert row["summary"]["sampled_bytes"] == 8 * 1024 * 1024


def test_pdf_tail_beyond_input_budget_is_explicitly_incomplete(tmp_path, monkeypatch):
    path = tmp_path / "tail.pdf"
    with path.open("wb") as handle:
        handle.write(b"%PDF-1.4\n/Type /Page\n(first page text)\n")
        handle.seek(4 * 1024 * 1024)
        handle.write(b"/Type /Page\n(decisive omitted tail)\n")

    _forbid_whole_file_path_reads(monkeypatch)
    row = adapt_pdf(path, version_id="ver-pdf", artifact_id="art-pdf")

    assert row["complete"] is False
    assert row["omission_reason"] == "adapter_input_truncated"
    assert row["summary"]["sampled_bytes"] == 4 * 1024 * 1024


def test_structure_tail_beyond_input_budget_is_explicitly_incomplete(
    tmp_path, monkeypatch
):
    path = tmp_path / "tail.mol"
    with path.open("wb") as handle:
        handle.write(b"name\n\n\n  6  6  0  0  0  0\n")
        handle.seek(200_000)
        handle.write(b"decisive omitted tail\n")

    _forbid_whole_file_path_reads(monkeypatch)
    row = adapt_structure(path, version_id="ver-mol", artifact_id="art-mol")

    assert row["complete"] is False
    assert row["omission_reason"] == "adapter_input_truncated"
    assert row["summary"]["sampled_bytes"] == 200_000


def test_sparse_png_uses_only_the_bounded_header(tmp_path, monkeypatch):
    path = tmp_path / "large.png"
    with path.open("wb") as handle:
        handle.write(
            b"\x89PNG\r\n\x1a\n"
            + b"\x00\x00\x00\rIHDR"
            + b"\x00\x00\x02\x80\x00\x00\x01\xe0"
            + b"\x00" * 8
        )
        handle.truncate(32 * 1024 * 1024)

    _forbid_whole_file_path_reads(monkeypatch)
    row = adapt_image(path, version_id="ver-img", artifact_id="art-img")

    assert row["complete"] is True
    assert row["summary"]["width"] == 640
    assert row["summary"]["height"] == 480
    assert row["summary"]["bytes"] == 32 * 1024 * 1024


def test_oversize_parquet_is_rejected_before_pandas(tmp_path, monkeypatch):
    path = tmp_path / "large.parquet"
    with path.open("wb") as handle:
        handle.truncate(8 * 1024 * 1024 + 1)
    calls = []

    def forbidden_read_parquet(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("pandas must not read an over-budget parquet")

    monkeypatch.setitem(
        sys.modules,
        "pandas",
        SimpleNamespace(read_parquet=forbidden_read_parquet),
    )
    row = adapt_table(path, version_id="ver-pq", artifact_id="art-pq")

    assert row["complete"] is False
    assert row["omission_reason"] == "adapter_input_truncated"
    assert row["summary"]["input_budget_bytes"] == 8 * 1024 * 1024
    assert calls == []


@pytest.mark.parametrize("metadata_case", ["api_unavailable", "field_missing"])
def test_parquet_without_metadata_proof_never_calls_pandas(
    tmp_path, monkeypatch, metadata_case
):
    path = tmp_path / "unknown.parquet"
    path.write_bytes(b"PAR1")
    calls = []

    def forbidden_read_parquet(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("pandas must not read without metadata proof")

    metadata = None
    if metadata_case == "field_missing":
        metadata = adapter_mod._parquet_budget(
            rows=1,
            columns=1,
            row_groups=1,
            uncompressed_sizes=[None],
        )
        assert metadata is None
    monkeypatch.setattr(adapter_mod, "_load_parquet_metadata", lambda _path: metadata)
    monkeypatch.setitem(
        sys.modules,
        "pandas",
        SimpleNamespace(read_parquet=forbidden_read_parquet),
    )

    row = adapt_table(path, version_id="ver-pq", artifact_id="art-pq")

    assert row["complete"] is False
    assert row["omission_reason"] == "parquet_metadata_unavailable"
    assert calls == []


@pytest.mark.parametrize(
    ("rows", "columns", "row_groups", "uncompressed_sizes"),
    [
        (adapter_mod._PARQUET_ROW_LIMIT + 1, 1, 1, [1]),
        (1, adapter_mod._PARQUET_COLUMN_LIMIT + 1, 1, [1]),
        (1, 1, adapter_mod._PARQUET_ROW_GROUP_LIMIT + 1, [1]),
        (1, 1, 1, [adapter_mod._PARQUET_UNCOMPRESSED_LIMIT + 1]),
    ],
)
def test_parquet_metadata_bombs_never_materialize(
    tmp_path,
    monkeypatch,
    rows,
    columns,
    row_groups,
    uncompressed_sizes,
):
    path = tmp_path / "bomb.parquet"
    path.write_bytes(b"PAR1")
    metadata = adapter_mod._parquet_budget(
        rows=rows,
        columns=columns,
        row_groups=row_groups,
        uncompressed_sizes=uncompressed_sizes,
    )
    assert metadata is not None
    assert metadata["within_budget"] is False
    calls = []

    def forbidden_read_parquet(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("pandas must not materialize an over-budget parquet")

    monkeypatch.setattr(adapter_mod, "_load_parquet_metadata", lambda _path: metadata)
    monkeypatch.setitem(
        sys.modules,
        "pandas",
        SimpleNamespace(read_parquet=forbidden_read_parquet),
    )

    row = adapt_table(path, version_id="ver-pq", artifact_id="art-pq")

    assert row["complete"] is False
    assert row["omission_reason"] == "adapter_input_truncated"
    assert calls == []


def test_parquet_with_proven_bounded_metadata_preserves_adapter(tmp_path, monkeypatch):
    path = tmp_path / "bounded.parquet"
    path.write_bytes(b"PAR1")
    metadata = adapter_mod._parquet_budget(
        rows=2,
        columns=1,
        row_groups=1,
        uncompressed_sizes=[16],
    )
    assert metadata is not None
    assert metadata["within_budget"] is True

    class Series:
        dtype = SimpleNamespace(kind="i")

        @staticmethod
        def isna():
            return SimpleNamespace(sum=lambda: 0)

        @staticmethod
        def min():
            return 1

        @staticmethod
        def max():
            return 3

        @staticmethod
        def mean():
            return 2

    class Frame:
        columns = ["value"]

        @staticmethod
        def __len__():
            return 2

        @staticmethod
        def __getitem__(name):
            assert name == "value"
            return Series()

    calls = []

    def read_parquet(selected):
        calls.append(selected)
        return Frame()

    monkeypatch.setattr(adapter_mod, "_load_parquet_metadata", lambda _path: metadata)
    monkeypatch.setitem(
        sys.modules,
        "pandas",
        SimpleNamespace(read_parquet=read_parquet),
    )

    row = adapt_table(path, version_id="ver-pq", artifact_id="art-pq")

    assert row["complete"] is True
    assert row["summary"]["row_count"] == 2
    assert calls == [path]
