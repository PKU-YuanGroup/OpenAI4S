from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from openai4s.server.session_domain import SessionDomainService
from openai4s.server.share_projection import ShareProjectionBuilder
from openai4s.server.share_service import ShareConflict, ShareService, new_share_id
from openai4s.store import Store


class FakeTunnel:
    def __init__(self) -> None:
        self.added: list[str] = []
        self.removed: list[str] = []

    def add_share(self, share_id: str) -> None:
        self.added.append(share_id)

    def remove_share(self, share_id: str) -> None:
        self.removed.append(share_id)


def _make(tmp_path: Path):
    store = Store(tmp_path / "openai4s.db")
    root_dir = tmp_path / "workspaces"

    def workspace(root_frame_id, branch_id):
        path = root_dir / root_frame_id / branch_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    domain = SessionDomainService(store, data_dir=tmp_path, workspace=workspace)
    project = store.create_project(name="Study")
    root = store.new_frame(project_id=project["project_id"], kind="turn", status="done")
    workspace(root, root)
    store.add_message(
        root_frame_id=root, branch_id=root, frame_id=root, role="user", content="hello"
    )
    store.log_cell(
        frame_id=root,
        root_frame_id=root,
        project_id=project["project_id"],
        code="x = 1",
        result={"id": "c1", "stdout": "ok\n", "stderr": "", "error": None},
        cell_index=1,
        state_revision=1,
        visibility="scientific",
    )
    builder = ShareProjectionBuilder(
        store, data_dir=tmp_path, workspace=workspace, cas=domain.cas
    )
    tunnel = FakeTunnel()
    service = ShareService(
        store,
        builder=builder,
        shares_dir=tmp_path / "shares",
        public_url=lambda sid: f"https://{sid}.example.org/",
        active_branch=store.active_session_branch,
        tunnel=tunnel,
    )
    return store, service, tunnel, root


def test_create_publishes_ready_snapshot(tmp_path):
    store, service, tunnel, root = _make(tmp_path)
    rec = service.create(root, title="My share")
    assert rec["status"] == "ready"
    assert rec["url"] == f"https://{rec['share_id']}.example.org/"
    assert tunnel.added == [rec["share_id"]]
    row = store.get_share(rec["share_id"])
    assert row["status"] == "ready"
    snap_dir = tmp_path / "shares" / rec["share_id"] / "snapshots" / row["snapshot_id"]
    assert (snap_dir / "bundle.zip").is_file()
    assert (snap_dir / "view.json").is_file()
    assert (snap_dir / "meta.json").is_file()


def test_one_active_share_per_frame(tmp_path):
    store, service, tunnel, root = _make(tmp_path)
    service.create(root)
    with pytest.raises(ShareConflict):
        service.create(root)


def test_bundle_and_view_share_one_projection_identity(tmp_path):
    store, service, tunnel, root = _make(tmp_path)
    rec = service.create(root)
    row = store.get_share(rec["share_id"])
    snap = tmp_path / "shares" / rec["share_id"] / "snapshots" / row["snapshot_id"]
    meta = json.loads((snap / "meta.json").read_bytes())
    view = json.loads((snap / "view.json").read_bytes())
    assert meta["projection_id"] == view["projection_id"] == row["projection_id"]
    # every artifact the viewer references is present as a snapshot byte file
    for artifact in view["artifacts"]:
        assert (snap / "artifacts" / artifact["sha256"]).is_file()
    # bundle sha in meta matches the actual bytes
    assert (
        meta["bundle"]["sha256"]
        == hashlib.sha256((snap / "bundle.zip").read_bytes()).hexdigest()
    )


def test_update_replaces_snapshot_and_keeps_url(tmp_path):
    store, service, tunnel, root = _make(tmp_path)
    rec = service.create(root)
    first_snapshot = store.get_share(rec["share_id"])["snapshot_id"]
    # add content, then update
    store.add_message(
        root_frame_id=root,
        branch_id=root,
        frame_id=root,
        role="assistant",
        content="updated answer",
    )
    updated = service.update(rec["share_id"])
    assert updated["share_id"] == rec["share_id"]
    assert updated["url"] == rec["url"]
    second_snapshot = store.get_share(rec["share_id"])["snapshot_id"]
    assert second_snapshot != first_snapshot


def test_lease_blocks_gc_of_old_snapshot(tmp_path):
    store, service, tunnel, root = _make(tmp_path)
    rec = service.create(root)
    sid = rec["share_id"]
    first_snapshot = store.get_share(sid)["snapshot_id"]
    # pin the current snapshot with a reader lease
    acquired = service.acquire(sid)
    assert acquired is not None
    leased_snapshot, _ = acquired
    assert leased_snapshot == first_snapshot
    # update -> new snapshot; GC must NOT delete the leased old one
    store.add_message(
        root_frame_id=root,
        branch_id=root,
        frame_id=root,
        role="assistant",
        content="more",
    )
    service.update(sid)
    old_dir = tmp_path / "shares" / sid / "snapshots" / first_snapshot
    assert old_dir.is_dir()
    # release + GC -> old snapshot reclaimed
    service.release(sid, leased_snapshot)
    service._gc(sid)
    assert not old_dir.exists()


def test_revoke_marks_revoked_and_blocks_acquire(tmp_path):
    store, service, tunnel, root = _make(tmp_path)
    rec = service.create(root)
    sid = rec["share_id"]
    result = service.revoke(sid)
    assert result["ok"] is True
    assert store.get_share(sid)["status"] == "revoked"
    assert service.acquire(sid) is None
    assert tunnel.removed == [sid]
    # idempotent
    assert service.revoke(sid)["already"] is True


def test_revoke_for_session_removes_rows_and_files(tmp_path):
    store, service, tunnel, root = _make(tmp_path)
    rec = service.create(root)
    sid = rec["share_id"]
    service.revoke_for_session(root)
    assert store.get_share(sid) is None
    assert not (tmp_path / "shares" / sid).exists()


# --------------------------------------------------------------------------- #
#  crash recovery
# --------------------------------------------------------------------------- #
def test_restore_promotes_publishing_with_complete_snapshot(tmp_path):
    store, service, tunnel, root = _make(tmp_path)
    rec = service.create(root)
    sid = rec["share_id"]
    row = store.get_share(sid)
    # simulate a crash *after* the FS commit but *before* the DB ready write
    store.begin_share_publish(
        share_id=sid,
        root_frame_id=root,
        title=None,
        pending_snapshot_id=row["snapshot_id"],
    )
    assert store.get_share(sid)["status"] == "publishing"
    desired = service.restore()
    assert sid in desired
    assert store.get_share(sid)["status"] == "ready"


def test_restore_fails_ready_row_with_missing_snapshot(tmp_path):
    store, service, tunnel, root = _make(tmp_path)
    rec = service.create(root)
    sid = rec["share_id"]
    # delete the snapshot bytes out from under a ready row
    import shutil

    shutil.rmtree(tmp_path / "shares" / sid / "snapshots")
    desired = service.restore()
    assert sid not in desired
    assert store.get_share(sid)["status"] == "failed"


def test_restore_sweeps_tmp(tmp_path):
    store, service, tunnel, root = _make(tmp_path)
    rec = service.create(root)
    sid = rec["share_id"]
    stray = tmp_path / "shares" / sid / "tmp" / "leftover"
    stray.mkdir(parents=True)
    (stray / "junk").write_text("x")
    service.restore()
    assert not stray.exists()


def test_new_share_id_is_dns_safe():
    for _ in range(20):
        sid = new_share_id()
        assert len(sid) == 26
        assert all(c in "abcdefghijklmnopqrstuvwxyz234567" for c in sid)


# --- EXP-3: the session as one self-contained read-only web page -------------

_PNG = b"\x89PNG\r\n\x1a\n" + b"example-pixels"
_HOSTILE = '<script>alert(1)</script><img src=x onerror="alert(2)">'


def _page_session(tmp_path: Path):
    """A session whose text carries markup, local paths and a figure."""
    store, service, _tunnel, root = _make(tmp_path)
    project_id = store.get_frame(root)["project_id"]
    workspace = tmp_path / "workspaces" / root / root
    store.add_message(
        root_frame_id=root,
        branch_id=root,
        frame_id=root,
        role="assistant",
        content="**Lag doubles** in minimal medium.\n\n"
        f"- hostile: {_HOSTILE}\n"
        "- see [the paper](https://example.org/paper) and "
        "[fig.png](/api/v1/artifacts/a-1) and [x](javascript:alert(3))\n\n"
        "```python\nprint('<b>code</b>')\n```",
    )
    cell = store.log_cell(
        frame_id=root,
        root_frame_id=root,
        project_id=project_id,
        code="fig.savefig('fig.png')  # <not a tag>",
        result={
            "id": "c2",
            "stdout": f"wrote {workspace}/fig.png\n"
            f"db at {tmp_path}/openai4s.db\nhome {Path.home()}/lab/data.csv\n",
            "stderr": "",
            "error": None,
        },
        figures=["fig.png"],
        cell_index=2,
        state_revision=2,
        visibility="scientific",
    )
    (workspace / "fig.png").write_bytes(_PNG)
    store.save_artifact(
        path=str(workspace / "fig.png"),
        filename="fig.png",
        content_type="image/png",
        size_bytes=len(_PNG),
        checksum=hashlib.sha256(_PNG).hexdigest(),
        producing_cell_id=cell,
        frame_id=root,
        root_frame_id=root,
        project_id=project_id,
    )
    return store, service, root


def test_the_exported_page_is_self_contained_and_everything_in_it_is_text(tmp_path):
    """EXP-3: one .html that opens from file:// with no network request, no
    script, and the session's own text never passed through as markup."""
    import base64
    import re

    from openai4s.server.share_page import CONTENT_SECURITY_POLICY

    _store, service, root = _page_session(tmp_path)
    exported = service.export_page(root)
    page = exported["data"].decode("utf-8")

    assert exported["content_type"] == "text/html; charset=utf-8"
    assert exported["filename"] == f"{root}.session.html"
    assert exported["sha256"] == hashlib.sha256(exported["data"]).hexdigest()
    assert page.startswith("<!doctype html>")
    assert "default-src 'none'" in CONTENT_SECURITY_POLICY
    assert "script-src" not in CONTENT_SECURITY_POLICY
    assert 'http-equiv="Content-Security-Policy"' in page

    # Nothing executes: no script element, no handler attribute, no markup
    # taken from the session.
    assert "<script" not in page.lower()
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<img src=x" not in page and 'onerror="alert' not in page
    assert not re.search(r"<[a-z][^>]*\son[a-z]+\s*=", page)
    assert "&lt;b&gt;code&lt;/b&gt;" in page and "&lt;not a tag&gt;" in page
    assert 'href="javascript' not in page

    # Nothing loads: the only src is the inlined figure, the only href the
    # link the author wrote, and a link into the daemon is left as its text.
    assert re.findall(r'src="([^"]*)"', page) == [
        "data:image/png;base64," + base64.b64encode(_PNG).decode("ascii")
    ]
    assert re.findall(r'href="([^"]*)"', page) == ["https://example.org/paper"]
    assert "/api/v1/artifacts" not in page
    for loader in ("<link", "<iframe", "<object", "<embed", "@import", "url("):
        assert loader not in page

    # The conversation, the cell and the artifact list are all there.
    assert "<strong>Lag doubles</strong> in minimal medium." in page
    assert "hello" in page and "x = 1" in page
    assert "<details><summary>Cell 2 · python</summary>" in page
    assert "<td>fig.png</td><td>image/png</td>" in page

    zh = service.export_page(root, language="zh")["data"].decode("utf-8")
    assert "只读导出" in zh and "<h2>产物</h2>" in zh and "单元 2" in zh


def test_the_exported_page_carries_neither_the_data_dir_nor_the_home_dir(tmp_path):
    """Owner decision 7 applied to EXP-3: a file made to be handed to someone
    else does not carry this machine's paths, by the session package's rules."""
    _store, service, root = _page_session(tmp_path)
    page = service.export_page(root)["data"].decode("utf-8")

    assert str(tmp_path) not in page
    assert str(Path(tmp_path).resolve()) not in page
    assert str(Path.home()) not in page
    assert "wrote ./fig.png" in page
    assert "db at &lt;data_dir&gt;/openai4s.db" in page
    assert "home ~/lab/data.csv" in page


def test_the_exported_page_says_what_its_budget_left_out(tmp_path, monkeypatch):
    from openai4s.server import share_page

    _store, service, root = _page_session(tmp_path)
    monkeypatch.setattr(share_page, "MAX_IMAGES_BYTES", 4)
    monkeypatch.setattr(share_page, "MAX_FIELD_CHARS", 24)
    page = service.export_page(root)["data"].decode("utf-8")

    assert "data:image" not in page
    assert "1 figure(s) are listed by name only" in page
    assert "Long text was cut in" in page
    assert "(cut here to keep this file small)" in page
    assert '<div class="label">fig.png</div>' in page
