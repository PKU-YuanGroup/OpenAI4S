"""Read-only adapters must not treat a filename as complete evidence."""

from __future__ import annotations

from openai4s.server.evidence_adapters import (
    adapt_artifact,
    adapt_image,
    adapt_pdf,
    adapt_structure,
    adapt_table,
    classify_artifact,
)


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
