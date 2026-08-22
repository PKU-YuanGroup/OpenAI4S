"""Stage 9 Artifact workbench Go/No-Go."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

from openai4s.config import Config, LLMConfig, RoadmapFeatureFlags
from openai4s.server import artifact_workbench as workbench_mod
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


def _cfg(tmp_path, *, workbench: bool = True, trusted: bool = False) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        roadmap_features=RoadmapFeatureFlags(
            stage1_trusted_delivery=trusted,
            stage9_artifact_workbench=workbench,
        ),
    )


def _setup(tmp_path, *, workbench: bool = True, trusted: bool = False):
    cfg = _cfg(tmp_path, workbench=workbench, trusted=trusted)
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


def _freeze_version(runner, record: dict, path: Path) -> None:
    runner.artifacts.write_version_snapshot(
        record["version_id"], path.name, src_path=path
    )


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def test_numeric_sort_with_empty_cells_has_one_total_order():
    page = query_table(
        [["name", "n"], ["two", "2"], ["missing", ""], ["one", "1"]],
        sort="n",
    )
    assert [row[0] for row in page["rows"]] == ["one", "two", "missing"]


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
        checksum=_checksum(path),
        frame_id=fid,
        project_id="default",
    )
    _freeze_version(runner, first, path)
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
    assert diff["from_version_id"] == first["version_id"]
    assert diff["to_version_id"] == edited["version_id"]
    assert "-alpha" in diff["diff"]
    assert "+beta" in diff["diff"]
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
        checksum=_checksum(path),
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


def test_stage9_structure_save_preserves_a_nested_artifact_path(tmp_path):
    _cfg, runner, _handler, fid = _setup(tmp_path)
    workspace = runner.workspace_for_branch(fid, fid)
    path = workspace / "structures" / "benzene.mol"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("empty\n", encoding="utf-8")
    first = runner.store.save_artifact(
        path=str(path),
        filename="structures/benzene.mol",
        content_type="chemical/x-mdl-molfile",
        size_bytes=6,
        checksum=_checksum(path),
        frame_id=fid,
        project_id="default",
    )
    _freeze_version(runner, first, path)

    saved = runner.workbench_artifacts.save_structure(
        first["artifact_id"], content=BENZENE_MOL, fmt="mol"
    )

    assert saved["unchanged"] is False
    assert path.read_text(encoding="utf-8") == BENZENE_MOL
    assert not (workspace / "benzene.mol").exists()
    stored = runner.store.get_artifact(first["artifact_id"])
    assert stored["filename"] == "structures/benzene.mol"
    assert stored["latest_version_id"] == saved["version_id"]
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
        checksum=_checksum(pdf),
        frame_id=fid,
        project_id="default",
    )
    _freeze_version(runner, pdf_art, pdf)
    html_art = runner.store.save_artifact(
        path=str(html),
        filename="page.html",
        content_type="text/html",
        size_bytes=html.stat().st_size,
        checksum=_checksum(html),
        frame_id=fid,
        project_id="default",
    )
    _freeze_version(runner, html_art, html)
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


def test_table_and_diff_http_routes(tmp_path):
    _cfg, runner, handler, fid = _setup(tmp_path)
    workspace = runner.workspace_for_branch(fid, fid)
    workspace.mkdir(parents=True, exist_ok=True)
    path = workspace / "table.csv"
    path.write_text("name,n\nr7,7\nr8,8\n", encoding="utf-8")
    first = runner.store.save_artifact(
        path=str(path),
        filename="table.csv",
        content_type="text/csv",
        size_bytes=path.stat().st_size,
        checksum=_checksum(path),
        frame_id=fid,
        project_id="default",
    )
    _freeze_version(runner, first, path)
    code, page = _call(
        handler,
        "GET",
        f"/artifacts/{first['artifact_id']}/table",
        query={"sort": ["n"], "dir": ["desc"]},
    )
    assert code == 200
    assert page["filename"] == "table.csv"
    assert page["total_rows"] == 2
    assert page["rows"][0][0] == "r8"
    runner.edit_artifact(first["artifact_id"], "name,n\nr7,7\nr9,9\n")
    code, diff = _call(handler, "GET", f"/artifacts/{first['artifact_id']}/diff")
    assert code == 200
    assert diff["changed"] is True
    assert "r8" in diff["diff"]
    assert "r9" in diff["diff"]
    runner.close()


def test_diff_rejects_unknown_and_foreign_versions_identically(tmp_path):
    _cfg, runner, handler, fid = _setup(tmp_path)
    workspace = runner.workspace_for_branch(fid, fid)
    workspace.mkdir(parents=True, exist_ok=True)
    own_path = workspace / "own.txt"
    foreign_path = workspace / "foreign.txt"
    own_path.write_text("public\n", encoding="utf-8")
    foreign_path.write_text("FOREIGN_SECRET\n", encoding="utf-8")
    own = runner.store.save_artifact(
        path=str(own_path),
        filename=own_path.name,
        content_type="text/plain",
        size_bytes=own_path.stat().st_size,
        checksum=_checksum(own_path),
        frame_id=fid,
        project_id="default",
    )
    foreign = runner.store.save_artifact(
        path=str(foreign_path),
        filename=foreign_path.name,
        content_type="text/plain",
        size_bytes=foreign_path.stat().st_size,
        checksum=_checksum(foreign_path),
        frame_id=fid,
        project_id="default",
    )
    foreign_reply = _call(
        handler,
        "GET",
        f"/artifacts/{own['artifact_id']}/diff",
        query={"from": [foreign["version_id"]], "to": [own["version_id"]]},
    )
    unknown_reply = _call(
        handler,
        "GET",
        f"/artifacts/{own['artifact_id']}/diff",
        query={"from": ["v-does-not-exist"], "to": [own["version_id"]]},
    )
    assert (
        foreign_reply
        == unknown_reply
        == (
            404,
            {
                "error": "artifact version not found",
                "code": "artifact_version_not_found",
            },
        )
    )
    assert "FOREIGN_SECRET" not in str(foreign_reply)
    runner.close()


def test_workbench_invalid_numbers_are_stable_400s(tmp_path):
    _cfg, runner, handler, fid = _setup(tmp_path)
    workspace = runner.workspace_for_branch(fid, fid)
    workspace.mkdir(parents=True, exist_ok=True)
    path = workspace / "table.csv"
    path.write_text("a\n1\n", encoding="utf-8")
    artifact = runner.store.save_artifact(
        path=str(path),
        filename=path.name,
        content_type="text/csv",
        size_bytes=path.stat().st_size,
        checksum=_checksum(path),
        frame_id=fid,
        project_id="default",
    )
    _freeze_version(runner, artifact, path)
    code, payload = _call(
        handler,
        "GET",
        f"/artifacts/{artifact['artifact_id']}/table",
        query={"offset": ["not-an-integer"]},
    )
    assert (code, payload["code"]) == (400, "invalid_query")
    code, payload = _call(
        handler,
        "POST",
        f"/frames/{fid}/annotations",
        body={
            "artifact_id": artifact["artifact_id"],
            "body": "pin",
            "kind": "image",
            "locator": {"x": "not-a-number", "y": 0.5},
        },
    )
    assert (code, payload["code"]) == (400, "invalid_locator")
    code, payload = _call(
        handler,
        "POST",
        f"/frames/{fid}/annotations",
        body={
            "artifact_id": artifact["artifact_id"],
            "body": "pin",
            "kind": "image",
            "locator": {"x": "NaN", "y": "Infinity"},
        },
    )
    assert (code, payload["code"]) == (400, "invalid_locator")
    runner.close()


def test_legacy_version_without_snapshot_never_falls_back_to_mutable_live_bytes(
    tmp_path,
):
    _cfg, runner, handler, fid = _setup(tmp_path)
    workspace = runner.workspace_for_branch(fid, fid)
    workspace.mkdir(parents=True, exist_ok=True)
    path = workspace / "legacy.csv"
    path.write_text("name,n\nold,1\n", encoding="utf-8")
    artifact = runner.store.save_artifact(
        path=str(path),
        filename=path.name,
        content_type="text/csv",
        size_bytes=path.stat().st_size,
        checksum=_checksum(path),
        frame_id=fid,
        project_id="default",
    )
    assert runner.store.version_meta(artifact["version_id"])["snapshot_path"] is None

    path.write_text("name,n\nNEW_LIVE_BYTES,999\n", encoding="utf-8")
    code, payload = _call(handler, "GET", f"/artifacts/{artifact['artifact_id']}/table")
    assert (code, payload["code"]) == (404, "artifact_version_not_found")
    assert "NEW_LIVE_BYTES" not in str(payload)
    runner.close()


def test_parquet_table_reads_the_exact_version_snapshot(tmp_path, monkeypatch):
    _cfg, runner, _handler, fid = _setup(tmp_path)
    workspace = runner.workspace_for_branch(fid, fid)
    workspace.mkdir(parents=True, exist_ok=True)
    live = workspace / "table.parquet"
    live.write_bytes(b"old parquet bytes")
    artifact = runner.store.save_artifact(
        path=str(live),
        filename=live.name,
        content_type="application/vnd.apache.parquet",
        size_bytes=live.stat().st_size,
        checksum=_checksum(live),
        frame_id=fid,
        project_id="default",
    )
    _freeze_version(runner, artifact, live)
    snapshot = Path(runner.store.version_meta(artifact["version_id"])["snapshot_path"])
    live.write_bytes(b"new mutable bytes")
    observed: list[Path] = []

    def fake_read(path):
        observed.append(path)
        return [["name"], ["snapshot"]]

    monkeypatch.setattr(workbench_mod, "read_parquet_rows", fake_read)
    page = runner.workbench_artifacts.table(artifact["artifact_id"])
    assert page["rows"] == [["snapshot"]]
    assert observed == [snapshot]
    runner.close()


def test_malformed_mol_counts_are_a_stable_invalid_structure_400(tmp_path):
    _cfg, runner, handler, fid = _setup(tmp_path)
    workspace = runner.workspace_for_branch(fid, fid)
    workspace.mkdir(parents=True, exist_ok=True)
    path = workspace / "bad.mol"
    path.write_text("placeholder\n", encoding="utf-8")
    artifact = runner.store.save_artifact(
        path=str(path),
        filename=path.name,
        content_type="chemical/x-mdl-molfile",
        size_bytes=path.stat().st_size,
        checksum=_checksum(path),
        frame_id=fid,
        project_id="default",
    )
    for counts in (
        "not-a-number still-not-a-number",
        "1 -1",
        "1 1000000000",
    ):
        malformed = f"bad\n  OpenAI4S\n\n{counts} V2000\n"
        code, payload = _call(
            handler,
            "POST",
            f"/artifacts/{artifact['artifact_id']}/structure",
            body={"content": malformed, "format": "mol"},
        )
        assert (code, payload["code"]) == (400, "invalid_structure")
    runner.close()


def test_structure_snapshot_failure_leaves_live_head_and_versions_unchanged(
    tmp_path, monkeypatch
):
    _cfg, runner, handler, fid = _setup(tmp_path, trusted=True)
    workspace = runner.workspace_for_branch(fid, fid)
    workspace.mkdir(parents=True, exist_ok=True)
    path = workspace / "structures" / "benzene.mol"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("old structure\n", encoding="utf-8")
    first = runner.store.save_artifact(
        path=str(path),
        filename="structures/benzene.mol",
        content_type="chemical/x-mdl-molfile",
        size_bytes=path.stat().st_size,
        checksum=_checksum(path),
        frame_id=fid,
        project_id="default",
    )
    before_versions = runner.store.list_versions(first["artifact_id"])

    def fail_snapshot(_filename, _data):
        raise OSError("injected snapshot failure")

    monkeypatch.setattr(runner.artifacts, "_stage_version_bytes_pinned", fail_snapshot)
    code, payload = _call(
        handler,
        "POST",
        f"/artifacts/{first['artifact_id']}/structure",
        body={"content": BENZENE_MOL, "format": "mol"},
    )
    assert (code, payload["code"]) == (500, "structure_save_failed")
    assert path.read_text(encoding="utf-8") == "old structure\n"
    assert (
        runner.store.get_artifact(first["artifact_id"])["latest_version_id"]
        == first["version_id"]
    )
    assert runner.store.list_versions(first["artifact_id"]) == before_versions
    runner.close()


def test_nested_structure_save_preserves_identity_filename_and_snapshot(tmp_path):
    _cfg, runner, handler, fid = _setup(tmp_path, trusted=True)
    workspace = runner.workspace_for_branch(fid, fid)
    path = workspace / "structures" / "benzene.mol"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("old structure\n", encoding="utf-8")
    first = runner.store.save_artifact(
        path=str(path),
        filename="structures/benzene.mol",
        content_type="chemical/x-mdl-molfile",
        size_bytes=path.stat().st_size,
        checksum=_checksum(path),
        frame_id=fid,
        project_id="default",
    )
    code, saved = _call(
        handler,
        "POST",
        f"/artifacts/{first['artifact_id']}/structure",
        body={"content": BENZENE_MOL, "format": "mol"},
    )
    assert code == 200
    assert saved["artifact_id"] == first["artifact_id"]
    artifact = runner.store.get_artifact(first["artifact_id"])
    assert artifact["filename"] == "structures/benzene.mol"
    assert artifact["latest_version_id"] == saved["version_id"]
    meta = runner.store.version_meta(saved["version_id"])
    assert Path(meta["snapshot_path"]).read_bytes() == BENZENE_MOL.encode("utf-8")
    assert path.read_text(encoding="utf-8") == BENZENE_MOL
    runner.close()


def test_nested_structure_db_failure_restores_live_snapshot_and_head(
    tmp_path, monkeypatch
):
    _cfg, runner, handler, fid = _setup(tmp_path, trusted=True)
    workspace = runner.workspace_for_branch(fid, fid)
    path = workspace / "structures" / "benzene.mol"
    path.parent.mkdir(parents=True, exist_ok=True)
    old = b"old structure\n"
    path.write_bytes(old)
    first = runner.store.save_artifact(
        path=str(path),
        filename="structures/benzene.mol",
        content_type="chemical/x-mdl-molfile",
        size_bytes=len(old),
        checksum=_checksum(path),
        frame_id=fid,
        project_id="default",
    )
    before_versions = runner.store.list_versions(first["artifact_id"])

    def fail_after_publish(**kwargs):
        kwargs["publish"]("v-injectedfault", kwargs["artifact_id"])
        raise OSError("injected database commit failure")

    monkeypatch.setattr(runner.store, "commit_artifact_upload", fail_after_publish)
    code, payload = _call(
        handler,
        "POST",
        f"/artifacts/{first['artifact_id']}/structure",
        body={"content": BENZENE_MOL, "format": "mol"},
    )
    assert (code, payload["code"]) == (500, "structure_save_failed")
    assert path.read_bytes() == old
    assert (
        runner.store.get_artifact(first["artifact_id"])["latest_version_id"]
        == first["version_id"]
    )
    assert runner.store.list_versions(first["artifact_id"]) == before_versions
    versions_dir = runner.artifacts.versions_dir()
    assert not list(versions_dir.glob(".upload-v-injectedfault.json"))
    assert not list(versions_dir.glob("v-injectedfault__*"))
    runner.close()
