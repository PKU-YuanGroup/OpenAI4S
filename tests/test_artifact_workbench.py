"""Stage 9 Artifact workbench Go/No-Go."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from openai4s.config import Config, LLMConfig, RoadmapFeatureFlags
from openai4s.server import gateway as gateway_mod
from openai4s.server.artifact_workbench import (
    is_benzene,
    ketcher_assets_present,
    ketcher_document,
    official_workbench_enabled,
    query_table,
    structure_summary,
)
from openai4s.server.gateway import _format_annotations_block
from openai4s.store import get_store

BENZENE_MOL = """benzene
  OpenAI4S

  6  6  0  0  0  0  0  0  0  0999 V2000
    0.0000    1.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    0.8660    0.5000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    0.8660   -0.5000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    0.0000   -1.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
   -0.8660   -0.5000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
   -0.8660    0.5000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  2  0  0  0  0
  2  3  1  0  0  0  0
  3  4  2  0  0  0  0
  4  5  1  0  0  0  0
  5  6  2  0  0  0  0
  6  1  1  0  0  0  0
M  END
"""


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None

    def has_subscriber(self, root_frame_id):
        return False

    def drop_frame(self, root_frame_id):
        return None


def _cfg(tmp_path, *, workbench: bool = True) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        roadmap_features=RoadmapFeatureFlags(stage9_artifact_workbench=workbench),
    )


def _setup(tmp_path, *, workbench: bool = True):
    cfg = _cfg(tmp_path, workbench=workbench)
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    fid = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    handler = object.__new__(gateway_mod.make_handler(cfg, _Hub(), runner))
    return cfg, runner, handler, fid


def _call(handler, method, path, *, body=None, query=None):
    replies: list[tuple] = []
    handler._query = lambda: query or {}
    handler._body = lambda: body or {}
    handler._json = lambda value, code=200: replies.append((code, value))
    handler._send = lambda code, data, content_type, extra=None: replies.append(
        (code, data, content_type, extra or {})
    )
    handler._api(method, path)
    return replies[-1]


def test_stage9_flag_defaults_off():
    assert official_workbench_enabled(Config()) is False
    assert official_workbench_enabled(
        Config(roadmap_features=RoadmapFeatureFlags(stage9_artifact_workbench=True))
    )


def test_csv_sort_and_filter_apply_to_the_full_dataset():
    rows = [["name", "n"], *[[f"r{i}", str(i)] for i in range(80)]]
    page = query_table(rows, sort="n", descending=True, filters={"n": "7"}, limit=5)
    assert page["total_rows"] == 1
    assert page["rows"][0][0] == "r7"
    page = query_table(rows, sort="n", descending=True, offset=0, limit=3)
    assert page["total_rows"] == 80
    assert [int(row[1]) for row in page["rows"]] == [79, 78, 77]


def test_text_edit_creates_v2_and_identical_bytes_do_not(tmp_path):
    _cfg, runner, handler, fid = _setup(tmp_path)
    workspace = runner.workspace_for_branch(fid, fid)
    workspace.mkdir(parents=True, exist_ok=True)
    path = workspace / "notes.md"
    path.write_text("alpha\n", encoding="utf-8")
    first = runner.store.save_artifact(
        path=str(path),
        filename="notes.md",
        content_type="text/markdown",
        size_bytes=6,
        checksum="ab" * 32,
        frame_id=fid,
        project_id="default",
    )
    edited = runner.edit_artifact(first["artifact_id"], "beta\n")
    assert edited["unchanged"] is False
    assert edited["version_id"] != first["version_id"]
    versions = runner.store.list_versions(first["artifact_id"])
    assert len(versions) == 2
    same = runner.edit_artifact(first["artifact_id"], "beta\n")
    assert same["unchanged"] is True
    assert same["version_id"] == edited["version_id"]
    assert len(runner.store.list_versions(first["artifact_id"])) == 2
    diff = runner.workbench_artifacts.diff(first["artifact_id"])
    assert diff["changed"] is True
    assert "alpha" in diff["diff"]
    assert "beta" in diff["diff"]
    runner.close()


def test_benzene_structure_saves_six_carbons_and_reopens(tmp_path):
    assert ketcher_assets_present()
    summary = structure_summary(BENZENE_MOL, "benzene.mol")
    assert is_benzene(summary)
    assert summary["carbon_count"] == 6
    _cfg, runner, handler, fid = _setup(tmp_path)
    workspace = runner.workspace_for_branch(fid, fid)
    workspace.mkdir(parents=True, exist_ok=True)
    path = workspace / "benzene.mol"
    path.write_text("empty\n", encoding="utf-8")
    first = runner.store.save_artifact(
        path=str(path),
        filename="benzene.mol",
        content_type="chemical/x-mdl-molfile",
        size_bytes=6,
        checksum="cd" * 32,
        frame_id=fid,
        project_id="default",
    )
    saved = runner.workbench_artifacts.save_structure(
        first["artifact_id"], content=BENZENE_MOL, fmt="mol"
    )
    assert saved["unchanged"] is False
    assert is_benzene(saved["structure"])
    reopened = path.read_text(encoding="utf-8")
    again = structure_summary(reopened, "benzene.mol")
    assert again["carbon_count"] == 6
    assert is_benzene(again)
    code, payload = _call(
        handler,
        "POST",
        f"/artifacts/{first['artifact_id']}/structure",
        body={"content": BENZENE_MOL, "format": "mol"},
    )
    assert code == 200
    assert payload["unchanged"] is True
    runner.close()


def test_pdf_and_html_comments_are_quoted_into_the_next_turn(tmp_path):
    _cfg, runner, handler, fid = _setup(tmp_path)
    workspace = runner.workspace_for_branch(fid, fid)
    workspace.mkdir(parents=True, exist_ok=True)
    pdf = workspace / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\nBT /F1 12 Tf (select this sentence) Tj ET\n%%EOF\n")
    html = workspace / "page.html"
    html.write_text(
        "<html><body><p id='hit'>element text</p></body></html>", encoding="utf-8"
    )
    pdf_art = runner.store.save_artifact(
        path=str(pdf),
        filename="paper.pdf",
        content_type="application/pdf",
        size_bytes=pdf.stat().st_size,
        checksum="11" * 32,
        frame_id=fid,
        project_id="default",
    )
    html_art = runner.store.save_artifact(
        path=str(html),
        filename="page.html",
        content_type="text/html",
        size_bytes=html.stat().st_size,
        checksum="22" * 32,
        frame_id=fid,
        project_id="default",
    )
    code, pages = _call(handler, "GET", f"/artifacts/{pdf_art['artifact_id']}/pdf-text")
    assert code == 200
    assert "select this sentence" in pages["pages"][0]["text"]
    code, outline = _call(
        handler, "GET", f"/artifacts/{html_art['artifact_id']}/html-outline"
    )
    assert code == 200
    assert any(item.get("id") == "hit" for item in outline["elements"])
    code, created = _call(
        handler,
        "POST",
        f"/frames/{fid}/annotations",
        body={
            "artifact_id": pdf_art["artifact_id"],
            "artifact_name": "paper.pdf",
            "kind": "pdf",
            "body": "fix the methods claim",
            "locator": {"page": 1, "quote": "select this sentence"},
        },
    )
    assert code == 201
    block = _format_annotations_block(
        [
            {
                "kind": "pdf",
                "artifact_name": "paper.pdf",
                "version_id": pdf_art["version_id"],
                "number": 1,
                "body": "fix the methods claim",
                "locator": {"page": 1, "quote": "select this sentence"},
            },
            {
                "kind": "html",
                "artifact_name": "page.html",
                "version_id": html_art["version_id"],
                "number": 1,
                "body": "tighten this paragraph",
                "locator": {"selector": "#hit", "quote": "element text"},
            },
        ]
    )
    assert "select this sentence" in block
    assert "#hit" in block
    assert "fix the methods claim" in block
    runner.close()


def test_ketcher_is_placeholder_off_and_real_assets_on():
    off = ketcher_document(Config()).decode("utf-8")
    assert "placeholder" in off.lower()
    on = ketcher_document(
        Config(roadmap_features=RoadmapFeatureFlags(stage9_artifact_workbench=True)),
        {"artifact_id": ["art-1"]},
    ).decode("utf-8")
    lowered = on.lower()
    assert "placeholder" not in lowered
    assert "openai4s-artifact" in lowered
    assert "ketcher-core" in lowered
    assert "ketcher.js" in lowered
    assert "3.7.0" in on
    assert (
        Path(ketcher_document.__globals__["KETCHER_VENDOR"])
        / "static"
        / "js"
        / "main.8617f334.js"
    ).is_file()


def test_workbench_routes_are_forbidden_when_the_flag_is_off(tmp_path):
    _cfg, runner, handler, fid = _setup(tmp_path, workbench=False)
    code, payload = _call(handler, "GET", "/artifacts/missing/table")
    assert code == 403
    assert payload["code"] == "workbench_disabled"
    runner.close()
