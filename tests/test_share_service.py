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
