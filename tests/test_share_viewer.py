from __future__ import annotations

import hashlib
from pathlib import Path

from openai4s.cli.main import build_parser
from openai4s.server.gateway import _load_share_assets
from openai4s.server.session_domain import SessionDomainService
from openai4s.server.share_projection import ShareProjectionBuilder
from openai4s.server.share_router import ShareRouter
from openai4s.server.share_service import ShareService
from openai4s.store import Store


def test_real_viewer_assets_load():
    assets = _load_share_assets()
    assert "share.html" in assets
    assert "share.js" in assets
    assert "share.css" in assets
    assert b"OpenAI4S" in assets["share.html"]
    # scientific renderers + 3Dmol are reused from the main webui dir
    assert "scientific_renderers.js" in assets


def test_router_serves_real_viewer_shell(tmp_path):
    store = Store(tmp_path / "openai4s.db")

    def workspace(root_frame_id, branch_id):
        path = tmp_path / "ws" / root_frame_id / branch_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    domain = SessionDomainService(store, data_dir=tmp_path, workspace=workspace)
    project = store.create_project(name="Study")
    root = store.new_frame(project_id=project["project_id"], kind="turn", status="done")
    workspace(root, root)
    store.add_message(
        root_frame_id=root, branch_id=root, frame_id=root, role="user", content="hi"
    )
    builder = ShareProjectionBuilder(
        store, data_dir=tmp_path, workspace=workspace, cas=domain.cas
    )
    service = ShareService(
        store,
        builder=builder,
        shares_dir=tmp_path / "shares",
        public_url=lambda s: f"https://{s}.example.org/",
        active_branch=store.active_session_branch,
    )
    router = ShareRouter(service, _load_share_assets())
    sid = service.create(root)["share_id"]

    def req(path, method="GET"):
        return router.handle(
            {
                "share_id": sid,
                "method": method,
                "path": path,
                "query": "",
                "headers": {},
            }
        )

    shell = req("/")
    assert shell["status"] == 200
    assert b"/static/share.js" in shell["body"]
    assert "Content-Security-Policy" in shell["headers"]
    js = req("/static/share.js")
    assert js["status"] == 200 and b"read-only share viewer" in js["body"]
    css = req("/static/share.css")
    assert css["status"] == 200
    sci = req("/static/scientific_renderers.js")
    assert sci["status"] == 200


def test_cli_share_parser_dispatch():
    p = build_parser()
    a = p.parse_args(["share", "create", "root-1", "--title", "T", "--json"])
    assert a.fn.__name__ == "cmd_share" and a.share_action == "create"
    assert a.session == "root-1" and a.title == "T" and a.json is True
    assert p.parse_args(["share", "revoke", "sid"]).share_action == "revoke"
    assert p.parse_args(["share", "import", "http://h/"]).url == "http://h/"
    assert p.parse_args(["share", "enable"]).share_action == "enable"
    assert p.parse_args(["relay", "gen-token"]).fn.__name__ == "cmd_relay_gen_token"


def test_cli_gen_token(capsys):
    from openai4s.cli.main import cmd_relay_gen_token

    assert cmd_relay_gen_token(None) == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("openai4s_pub_") and len(out) > 30
