from __future__ import annotations

import inspect
from pathlib import Path

from openai4s.server import share_router as share_router_mod
from openai4s.server.session_domain import SessionDomainService
from openai4s.server.share_projection import ShareProjectionBuilder
from openai4s.server.share_router import ShareRouter
from openai4s.server.share_service import ShareService
from openai4s.store import Store

_ASSETS = {
    "share.html": b"<!doctype html><title>share</title>",
    "share.js": b"console.log('share')",
    "share.css": b"body{}",
    "md_renderer.js": b"// md",
    "scientific_renderers.js": b"// sci",
    "vendor/3Dmol-min.js": b"// 3dmol",
}


def _setup(tmp_path: Path):
    store = Store(tmp_path / "openai4s.db")
    root_dir = tmp_path / "workspaces"

    def workspace(root_frame_id, branch_id):
        path = root_dir / root_frame_id / branch_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    domain = SessionDomainService(store, data_dir=tmp_path, workspace=workspace)
    project = store.create_project(name="Study")
    root = store.new_frame(project_id=project["project_id"], kind="turn", status="done")
    ws = workspace(root, root)
    store.add_message(
        root_frame_id=root, branch_id=root, frame_id=root, role="user", content="hi"
    )
    cell = store.log_cell(
        frame_id=root,
        root_frame_id=root,
        project_id=project["project_id"],
        code="plot()",
        result={"id": "c1", "stdout": "", "stderr": "", "error": None},
        cell_index=1,
        state_revision=1,
        visibility="scientific",
    )
    import hashlib

    art = ws / "fig.png"
    art.write_bytes(b"\x89PNG\r\n\x1a\nHELLO")
    store.save_artifact(
        path=str(art),
        filename="fig.png",
        content_type="image/png",
        size_bytes=art.stat().st_size,
        checksum=hashlib.sha256(art.read_bytes()).hexdigest(),
        producing_cell_id=cell,
        frame_id=root,
        root_frame_id=root,
        project_id=project["project_id"],
    )
    builder = ShareProjectionBuilder(
        store, data_dir=tmp_path, workspace=workspace, cas=domain.cas
    )
    service = ShareService(
        store,
        builder=builder,
        shares_dir=tmp_path / "shares",
        public_url=lambda sid: f"https://{sid}.example.org/",
        active_branch=store.active_session_branch,
    )
    router = ShareRouter(service, _ASSETS)
    rec = service.create(root)
    return store, service, router, rec["share_id"]


def _req(share_id, path, method="GET", headers=None):
    return {
        "share_id": share_id,
        "method": method,
        "path": path,
        "query": "",
        "headers": headers or {},
    }


def _drain(resp):
    body = resp.get("body")
    if body is None:
        return b""
    if isinstance(body, (bytes, bytearray)):
        return bytes(body)
    return b"".join(body)


def test_viewer_shell_has_csp_and_security_headers(tmp_path):
    _, _, router, sid = _setup(tmp_path)
    resp = router.handle(_req(sid, "/"))
    assert resp["status"] == 200
    assert "Content-Security-Policy" in resp["headers"]
    assert resp["headers"]["X-Content-Type-Options"] == "nosniff"
    assert resp["headers"]["Referrer-Policy"] == "no-referrer"
    assert resp["headers"]["Cache-Control"] == "no-store"
    assert _drain(resp) == _ASSETS["share.html"]


def test_static_whitelist(tmp_path):
    _, _, router, sid = _setup(tmp_path)
    ok = router.handle(_req(sid, "/static/share.js"))
    assert ok["status"] == 200 and _drain(ok) == _ASSETS["share.js"]
    bad = router.handle(_req(sid, "/static/../secret"))
    assert bad["status"] == 404


def test_view_and_meta(tmp_path):
    _, _, router, sid = _setup(tmp_path)
    view = router.handle(_req(sid, "/api/view"))
    assert view["status"] == 200
    assert b"projection_id" in _drain(view)
    meta = router.handle(_req(sid, "/api/meta"))
    assert meta["status"] == 200


def test_artifact_inline_and_range(tmp_path):
    store, _, router, sid = _setup(tmp_path)
    view = router.handle(_req(sid, "/api/view"))
    import json

    sha = json.loads(_drain(view))["artifacts"][0]["sha256"]
    full = router.handle(_req(sid, f"/api/artifacts/{sha}"))
    assert full["status"] == 200
    assert full["headers"]["Content-Type"] == "image/png"
    assert full["headers"]["Accept-Ranges"] == "bytes"
    body = _drain(full)
    part = router.handle(
        _req(sid, f"/api/artifacts/{sha}", headers={"range": "bytes=0-3"})
    )
    assert part["status"] == 206
    assert part["headers"]["Content-Range"].endswith(f"/{len(body)}")
    assert _drain(part) == body[:4]


def test_artifact_bad_id_is_404(tmp_path):
    _, _, router, sid = _setup(tmp_path)
    assert router.handle(_req(sid, "/api/artifacts/not-a-hash"))["status"] == 404


def test_bundle_download(tmp_path):
    _, _, router, sid = _setup(tmp_path)
    resp = router.handle(_req(sid, "/bundle"))
    assert resp["status"] == 200
    assert resp["headers"]["Content-Type"] == "application/vnd.openai4s.session+zip"
    assert "attachment" in resp["headers"]["Content-Disposition"]
    assert _drain(resp).startswith(b"PK")
    assert "X-Content-SHA256" in resp["headers"]


def test_head_has_no_body_but_length(tmp_path):
    _, _, router, sid = _setup(tmp_path)
    resp = router.handle(_req(sid, "/bundle", method="HEAD"))
    assert resp["status"] == 200
    assert resp["body"] is None
    assert int(resp["headers"]["Content-Length"]) > 0


def test_method_not_allowed(tmp_path):
    _, _, router, sid = _setup(tmp_path)
    assert router.handle(_req(sid, "/", method="POST"))["status"] == 405


def test_unknown_and_revoked_share_are_identical_404(tmp_path):
    store, service, router, sid = _setup(tmp_path)
    unknown = router.handle(_req("zzzzzzzzzzzzzzzzzzzzzzzzzz", "/"))
    service.revoke(sid)
    revoked = router.handle(_req(sid, "/"))
    assert unknown["status"] == revoked["status"] == 404
    assert _drain(unknown) == _drain(revoked)
    assert unknown["headers"]["Content-Type"] == revoked["headers"]["Content-Type"]


def test_router_source_has_no_forbidden_dependencies():
    # negative list: the read-only router must not import execution surfaces.
    import ast

    tree = ast.parse(inspect.getsource(share_router_mod))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    forbidden = {"subprocess", "openai4s.host_dispatch"}
    assert not (imported & forbidden), f"forbidden imports: {imported & forbidden}"
    assert not any(
        name.startswith("openai4s.kernel") for name in imported
    ), "share_router must not import kernel surfaces"
