"""The executed-code view and the hierarchical sources export (D10).

A session's executed code lives in ``execution_log`` rows keyed per frame:
the root Notebook cells under the root frame, and every delegated child Cell
under its own ``kind='delegate'`` frame (S3's recorder).  Before this service
there was no projection or download that put the whole hierarchy in one
place: the Notebook exports read the root frame only, and child code was
reachable solely by knowing each child frame id.

``ExecutionSourcesService`` adds two read-only surfaces over the SAME rows:

* ``GET /frames/{fid}/execution-sources`` — a bounded typed JSON tree
  (frames + per-frame cell metadata, no code text), and
* ``GET /frames/{fid}/execution-sources/export`` — ``sources.zip`` with the
  executed source files themselves plus a new ``manifest.json``.

Everything here runs against the real Store (and, for the route tests, the
real gateway handler).  No backends are stubbed, so the captured response
shapes are real.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

from openai4s.agent.cell_record import DelegatedCellRecorder
from openai4s.config import Config, LLMConfig, get_config
from openai4s.server.execution_sources import ExecutionSourcesService
from openai4s.server.execution_views import ExecutionViewService
from openai4s.store import get_store

# --------------------------------------------------------------------------
# seeding helpers (real Store, the same writers production uses)
# --------------------------------------------------------------------------


def _store():
    return get_store(get_config().db_path)


def _cell_result(cell_id, stdout="", error=None, interrupted=False):
    result = {"id": cell_id, "stdout": stdout, "stderr": "", "error": error}
    if interrupted:
        result["interrupted"] = True
    return result


def _log_root_cell(store, root, cell_id, code, *, index, language="python", **kw):
    store.log_cell(
        frame_id=root,
        root_frame_id=root,
        code=code,
        result=_cell_result(cell_id, **kw),
        origin="agent",
        cell_index=index,
        kernel_id="r" if language == "r" else "python",
        language=language,
    )


class _Recorded:
    """One recorded child cell, via the exact S3-era recorder mechanism."""

    def __init__(self, store, frame_id, generation_id=None):
        self._recorder = DelegatedCellRecorder(
            store,
            frame_id,
            generation_id_for=(
                (lambda language: generation_id) if generation_id else None
            ),
        )

    def cell(self, cell_id, code, *, language="python", error=None, interrupted=False):
        action = type("A", (), {"code": code, "language": language})()
        self._recorder.after(
            action, None, _cell_result(cell_id, error=error, interrupted=interrupted)
        )


def _seed_hierarchy(store):
    """Root (py+R) + child (ok/failed/R) + nested grandchild, S3-shaped rows."""

    root = store.new_frame(kind="turn", status="ready", name="analysis")
    _log_root_cell(store, root, "cell-root-1", "import math\nprint('root')", index=1)
    _log_root_cell(store, root, "cell-root-2", "summary(cars)", index=2, language="r")

    child = store.new_frame(
        parent_id=root, kind="delegate", name="assay-reader", depth=1, status="done"
    )
    rec = _Recorded(store, child)
    rec.cell("cell-child-1", "data = load('assay.csv')")
    rec.cell(
        "cell-child-2",
        "raise ValueError('bad assay')",
        error="ValueError: bad assay",
    )
    rec.cell("cell-child-3", "plot(assay)", language="r")

    grandchild = store.new_frame(
        parent_id=child, kind="delegate", name="curve fitter", depth=2, status="failed"
    )
    _Recorded(store, grandchild).cell("cell-grand-1", "fit = fit_curve(data)")
    return root, child, grandchild


def _frame_by_id(payload, frame_id):
    return next(f for f in payload["frames"] if f["frame_id"] == frame_id)


def _zip_names(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return sorted(archive.namelist())


def _zip_read(data, name):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return archive.read(name).decode("utf-8")


def _manifest(data):
    return json.loads(_zip_read(data, "manifest.json"))


# --------------------------------------------------------------------------
# JSON projection
# --------------------------------------------------------------------------


def test_sources_projection_walks_root_and_nested_children():
    store = _store()
    root, child, grandchild = _seed_hierarchy(store)
    service = ExecutionSourcesService(store)

    payload = service.projection(root)

    assert payload["root_frame_id"] == root
    assert payload["truncated"] is False
    assert [f["frame_id"] for f in payload["frames"]] == [root, child, grandchild]

    top = _frame_by_id(payload, root)
    assert top["parent_id"] is None
    assert top["kind"] == "turn"
    assert top["depth"] == 0
    assert top["order"] == 0
    assert top["counts"] == {"cells": 2, "ok": 2, "error": 0, "interrupted": 0}
    assert [c["id"] for c in top["cells"]] == ["cell-root-1", "cell-root-2"]
    assert [c["language"] for c in top["cells"]] == ["python", "r"]
    assert [c["seq"] for c in top["cells"]] == [1, 2]

    mid = _frame_by_id(payload, child)
    assert mid["parent_id"] == root
    assert mid["root_frame_id"] == root
    assert mid["name"] == "assay-reader"
    assert mid["kind"] == "delegate"
    assert mid["depth"] == 1
    assert mid["order"] == 1
    assert mid["status"] == "done"
    assert mid["counts"] == {"cells": 3, "ok": 2, "error": 1, "interrupted": 0}
    failed = mid["cells"][1]
    assert failed["id"] == "cell-child-2"
    assert failed["status"] == "error"
    assert failed["interrupted"] is False
    assert (
        failed["source_sha256"]
        == hashlib.sha256(b"raise ValueError('bad assay')\n").hexdigest()
    )

    deep = _frame_by_id(payload, grandchild)
    assert deep["parent_id"] == child
    assert deep["depth"] == 2
    assert deep["status"] == "failed"

    # Code text is never inlined here -- the per-frame /execution-log route
    # serves it.  Serialize and look for the seeded sources.
    blob = json.dumps(payload)
    assert "load('assay.csv')" not in blob
    assert "import math" not in blob


def test_sources_projection_populates_generation_and_environment():
    store = _store()
    root = store.new_frame(kind="turn", status="ready")
    child = store.new_frame(parent_id=root, kind="delegate", name="env-child", depth=1)
    generation = store.create_kernel_generation(
        root_frame_id=child,
        branch_id=child,
        language="python",
        environment={
            "runtime": "python",
            "interpreter": "/envs/sci/bin/python",
            "environment_name": "sci-env",
            "environment_root": "/envs/sci",
        },
        bootstrap={"status": "agent_managed", "loaded_sidecars": []},
        state="active",
    )
    generation_id = generation["generation_id"]
    _Recorded(store, child, generation_id=generation_id).cell(
        "cell-env-1", "print('env')"
    )

    payload = ExecutionSourcesService(store).projection(root)
    cell = _frame_by_id(payload, child)["cells"][0]
    assert cell["generation_id"] == generation_id
    assert cell["environment"] == {
        "name": "sci-env",
        "interpreter": "/envs/sci/bin/python",
    }

    # A cell with no generation carries an honest null, not a borrowed env.
    _Recorded(store, child).cell("cell-env-2", "print('bare')")
    payload = ExecutionSourcesService(store).projection(root)
    bare = _frame_by_id(payload, child)["cells"][1]
    assert bare["generation_id"] is None
    assert bare["environment"] is None


def test_sources_projection_links_artifact_versions(tmp_path):
    store = _store()
    root = store.new_frame(kind="turn", status="ready")
    child = store.new_frame(parent_id=root, kind="delegate", name="writer", depth=1)
    rec = _Recorded(store, child)
    rec.cell("cell-art-1", "open('out.txt','w').write('x')")
    rec.cell("cell-art-2", "open('out.txt','w').write('x')  # same bytes")

    payload_file = tmp_path / "out.txt"
    payload_file.write_text("x", "utf-8")
    checksum = hashlib.sha256(b"x").hexdigest()
    version = store.record_cell_artifact(
        path=str(payload_file),
        filename="out.txt",
        content_type="text/plain",
        size_bytes=1,
        checksum=checksum,
        producing_cell_id="cell-art-1",
        frame_id=child,
        root_frame_id=root,
    )
    version_id = version["version_id"]
    # A second Cell re-observing the same bytes gets a capture observation,
    # not a new version; the link must still be attributed to that Cell.
    store.record_cell_artifact(
        path=str(payload_file),
        filename="out.txt",
        content_type="text/plain",
        size_bytes=1,
        checksum=checksum,
        producing_cell_id="cell-art-2",
        frame_id=child,
        root_frame_id=root,
        reuse_matching_head=True,
    )

    payload = ExecutionSourcesService(store).projection(root)
    cells = _frame_by_id(payload, child)["cells"]
    assert cells[0]["artifacts"] == [version_id]
    assert cells[1]["artifacts"] == [version_id]


def test_interrupted_cells_carry_the_stored_interrupted_flag():
    """The ``interrupted`` field must agree with the stored row, not default.

    Regression: ``list_cells`` did not project the ``interrupted`` column, so
    the projection and the manifest asserted ``interrupted: false`` for a
    cell whose own ``status`` said "interrupted" — wrong provenance, not
    absent provenance.
    """

    store = _store()
    root = store.new_frame(kind="turn", status="ready")
    _log_root_cell(
        store, root, "cell-int-1", "while True: pass", index=1, interrupted=True
    )
    child = store.new_frame(parent_id=root, kind="delegate", name="slow-child", depth=1)
    rec = _Recorded(store, child)
    rec.cell("cell-int-2", "sleep_forever()", interrupted=True)
    rec.cell("cell-int-3", "print('fine')")

    service = ExecutionSourcesService(store)
    payload = service.projection(root)

    top = _frame_by_id(payload, root)
    assert top["counts"] == {"cells": 1, "ok": 0, "error": 0, "interrupted": 1}
    assert top["cells"][0]["status"] == "interrupted"
    assert top["cells"][0]["interrupted"] is True

    mid = _frame_by_id(payload, child)
    assert mid["counts"] == {"cells": 2, "ok": 1, "error": 0, "interrupted": 1}
    stopped, fine = mid["cells"]
    assert stopped["status"] == "interrupted"
    assert stopped["interrupted"] is True
    assert fine["interrupted"] is False

    exported = service.export(root)
    names = _zip_names(exported["data"])
    child_dir = f"children/1_slow-child_{child[:8]}"
    assert "root/cell_0001_interrupted.py" in names
    assert f"{child_dir}/cell_0001_interrupted.py" in names

    manifest = _manifest(exported["data"])
    row = next(c for c in manifest["cells"] if c["id"] == "cell-int-2")
    assert row["status"] == "interrupted"
    assert row["interrupted"] is True
    assert row["path"] == f"{child_dir}/cell_0001_interrupted.py"
    untouched = next(c for c in manifest["cells"] if c["id"] == "cell-int-3")
    assert untouched["interrupted"] is False


def test_legacy_session_projects_root_only_without_error():
    store = _store()
    root = store.new_frame(kind="turn", status="ready")
    _log_root_cell(store, root, "cell-old-1", "print('legacy')", index=1)

    payload = ExecutionSourcesService(store).projection(root)
    assert [f["frame_id"] for f in payload["frames"]] == [root]
    assert payload["frames"][0]["counts"]["cells"] == 1


def test_branch_and_notebook_projections_are_unaffected():
    store = _store()
    root, child, _grandchild = _seed_hierarchy(store)
    service = ExecutionSourcesService(store)

    def notebook_view():
        return ExecutionViewService(
            store=store, format_timestamp=lambda value: None
        ).execution_log(root, branch_id=root)

    before = notebook_view()

    service.projection(root)
    service.export(root)

    after = notebook_view()
    assert after == before
    assert [e["producing_cell_id"] for e in after["entries"]] == [
        "cell-root-1",
        "cell-root-2",
    ]
    # Child cells stay child-keyed: reading sources creates no root rows.
    assert {c["producing_cell_id"] for c in store.list_cells(child)} == {
        "cell-child-1",
        "cell-child-2",
        "cell-child-3",
    }


# --------------------------------------------------------------------------
# sources.zip export
# --------------------------------------------------------------------------


def test_sources_export_zip_layout_manifest_and_readmes():
    store = _store()
    root, child, grandchild = _seed_hierarchy(store)

    exported = ExecutionSourcesService(store).export(root)
    assert exported["content_type"] == "application/zip"
    assert exported["filename"].endswith(".sources.zip")
    assert exported["sha256"] == hashlib.sha256(exported["data"]).hexdigest()

    data = exported["data"]
    names = _zip_names(data)
    child_dir = f"children/1_assay-reader_{child[:8]}"
    grand_dir = f"{child_dir}/children/1_curve-fitter_{grandchild[:8]}"
    assert "root/cell_0001_ok.py" in names
    assert "root/cell_0002_ok.R" in names
    assert "root/session.py" in names
    assert "root/session.R" in names
    assert f"{child_dir}/cell_0001_ok.py" in names
    # Failed cells are included and marked, never dropped.
    assert f"{child_dir}/cell_0002_error.py" in names
    assert f"{child_dir}/cell_0003_ok.R" in names
    assert f"{child_dir}/session.py" in names
    assert f"{grand_dir}/cell_0001_ok.py" in names
    assert "manifest.json" in names
    assert "README.md" in names
    assert "README_zh.md" in names

    # The cell files are the executed source, exactly.
    assert _zip_read(data, f"{child_dir}/cell_0002_error.py") == (
        "raise ValueError('bad assay')\n"
    )
    session = _zip_read(data, f"{child_dir}/session.py")
    assert "# %%" in session
    assert "data = load('assay.csv')" in session
    assert "raise ValueError('bad assay')" in session
    assert "error" in session  # the failed cell is marked in its separator

    readme = _zip_read(data, "README.md")
    assert "persistent kernel" in readme
    assert "not guaranteed" in readme
    readme_zh = _zip_read(data, "README_zh.md")
    assert "持久" in readme_zh

    manifest = _manifest(data)
    assert manifest["version"] == 1
    assert manifest["root_frame_id"] == root
    assert "generated_at" in manifest
    assert [f["frame_id"] for f in manifest["frames"]] == [root, child, grandchild]
    frame_row = manifest["frames"][1]
    assert frame_row["parent_id"] == root
    assert frame_row["root_frame_id"] == root
    assert frame_row["name"] == "assay-reader"
    assert frame_row["kind"] == "delegate"
    assert frame_row["order"] == 1
    assert frame_row["path"] == child_dir

    failed = next(c for c in manifest["cells"] if c["id"] == "cell-child-2")
    assert failed["frame_id"] == child
    assert failed["order"] == 2
    assert failed["language"] == "python"
    assert failed["status"] == "error"
    assert failed["interrupted"] is False
    assert (
        failed["source_sha256"]
        == hashlib.sha256(b"raise ValueError('bad assay')\n").hexdigest()
    )
    assert failed["path"] == f"{child_dir}/cell_0002_error.py"


def test_sources_export_carries_only_execution_log_fields():
    """No prompts, no host payloads, no stdout/stderr, no credentials."""

    store = _store()
    root = store.new_frame(kind="turn", status="ready")
    store.add_message(
        root_frame_id=root, role="user", content="SECRET-PROMPT sk-live-abcdef"
    )
    store.log_cell(
        frame_id=root,
        root_frame_id=root,
        code="print('visible source')",
        result={
            "id": "cell-sec-1",
            "stdout": "STDOUT-SHOULD-NOT-EXPORT",
            "stderr": "STDERR-SHOULD-NOT-EXPORT",
            "error": None,
        },
        origin="agent",
        cell_index=1,
    )

    exported = ExecutionSourcesService(store).export(root)
    with zipfile.ZipFile(io.BytesIO(exported["data"])) as archive:
        everything = b"".join(archive.read(n) for n in archive.namelist())
    assert b"visible source" in everything
    assert b"SECRET-PROMPT" not in everything
    assert b"sk-live-abcdef" not in everything
    assert b"STDOUT-SHOULD-NOT-EXPORT" not in everything
    assert b"STDERR-SHOULD-NOT-EXPORT" not in everything


def test_sources_export_is_byte_deterministic_and_survives_restart():
    store = _store()
    db_path = get_config().db_path
    root, _child, _grandchild = _seed_hierarchy(store)

    first = ExecutionSourcesService(store).export(root)
    second = ExecutionSourcesService(store).export(root)
    assert first["data"] == second["data"]
    assert first["sha256"] == second["sha256"]

    # A daemon restart is a new Store generation over the same path; the
    # export must reproduce the identical archive from durable rows alone.
    store.close()
    reopened = get_store(db_path)
    third = ExecutionSourcesService(reopened).export(root)
    assert third["data"] == first["data"]


def test_legacy_session_exports_root_only():
    store = _store()
    root = store.new_frame(kind="turn", status="ready")
    _log_root_cell(store, root, "cell-solo-1", "print('solo')", index=1)

    exported = ExecutionSourcesService(store).export(root)
    names = _zip_names(exported["data"])
    assert "root/cell_0001_ok.py" in names
    assert not any(name.startswith("children/") for name in names)
    manifest = _manifest(exported["data"])
    assert [f["frame_id"] for f in manifest["frames"]] == [root]


# --------------------------------------------------------------------------
# real routes (status codes matter -> the real _route driver)
# --------------------------------------------------------------------------


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None

    def has_subscriber(self, root_frame_id):
        return False

    def drop_frame(self, root_frame_id):
        return None


class _Client:
    def __init__(self, tmp_path):
        from openai4s.server import gateway as gateway_mod
        from openai4s.server import local_auth

        self.cfg = Config(
            data_dir=tmp_path,
            llm=LLMConfig(provider="deepseek", api_key="test-key"),
        )
        self.runner = gateway_mod.SessionRunner(
            self.cfg, _Hub(), start_idle_sweeper=False
        )
        self.store = self.runner.store
        self._handler_class = gateway_mod.make_handler(self.cfg, _Hub(), self.runner)
        self._token = local_auth.read_token(tmp_path) or ""
        self._token_header = local_auth.TOKEN_HEADER

    def get(self, path):
        handler = object.__new__(self._handler_class)
        handler._correlation_id = "req-1"
        sent = {}

        def _send(code, payload, ctype, extra=None):
            sent["code"] = code
            sent["body"] = payload
            sent["content_type"] = ctype
            sent["headers"] = dict(extra or {})

        handler._send = _send
        handler.command = "GET"
        handler.path = f"/api/v1{path}"
        handler.headers = {"Content-Length": "0", self._token_header: self._token}
        handler._body = lambda: {}
        handler._route("GET")
        return sent


def test_execution_sources_routes_serve_json_zip_and_404(tmp_path):
    client = _Client(tmp_path)
    try:
        store = client.store
        root, child, _grandchild = _seed_hierarchy(store)

        reply = client.get(f"/frames/{root}/execution-sources")
        assert reply["code"] == 200
        payload = json.loads(reply["body"].decode("utf-8"))
        assert payload["root_frame_id"] == root
        assert [f["frame_id"] for f in payload["frames"]][:2] == [root, child]

        exported = client.get(f"/frames/{root}/execution-sources/export")
        assert exported["code"] == 200
        assert exported["content_type"] == "application/zip"
        disposition = exported["headers"].get("Content-Disposition", "")
        assert "sources.zip" in disposition
        digest = hashlib.sha256(exported["body"]).hexdigest()
        assert exported["headers"].get("X-Content-SHA256") == digest
        assert "manifest.json" in _zip_names(exported["body"])

        missing = client.get("/frames/no-such-frame/execution-sources")
        assert missing["code"] == 404
        missing_zip = client.get("/frames/no-such-frame/execution-sources/export")
        assert missing_zip["code"] == 404
    finally:
        client.runner.close()
