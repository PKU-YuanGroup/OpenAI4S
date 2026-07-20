from __future__ import annotations

from pathlib import Path

from openai4s.cli.main import _parse_duration
from openai4s.server.session_domain import SessionDomainService
from openai4s.server.share_projection import ShareProjectionBuilder
from openai4s.server.share_service import ShareService
from openai4s.store import Store


def _make(tmp_path: Path):
    store = Store(tmp_path / "openai4s.db")

    def workspace(root_frame_id, branch_id):
        p = tmp_path / "ws" / root_frame_id / branch_id
        p.mkdir(parents=True, exist_ok=True)
        return p

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
    return store, service, root


def test_create_with_expiry_records_expires_at(tmp_path):
    store, service, root = _make(tmp_path)
    rec = service.create(root, expires_at=10_000)
    assert rec["expires_at"] == 10_000
    assert store.get_share(rec["share_id"])["expires_at"] == 10_000


def test_sweep_revokes_expired_and_keeps_live(tmp_path):
    store, service, root = _make(tmp_path)
    # already-expired share (expires_at in the past)
    rec = service.create(root, expires_at=1)
    sid = rec["share_id"]
    assert store.get_share(sid)["status"] == "ready"
    revoked = service.sweep_expired(now_ms=1000)
    assert sid in revoked
    assert store.get_share(sid)["status"] == "revoked"
    assert service.acquire(sid) is None


def test_sweep_keeps_unexpired(tmp_path):
    store, service, root = _make(tmp_path)
    rec = service.create(root, expires_at=10**13)  # far future
    assert service.sweep_expired(now_ms=1000) == []
    assert store.get_share(rec["share_id"])["status"] == "ready"


def test_no_expiry_never_swept(tmp_path):
    store, service, root = _make(tmp_path)
    rec = service.create(root)  # no expiry
    assert rec["expires_at"] is None
    assert service.sweep_expired(now_ms=10**13) == []
    assert store.get_share(rec["share_id"])["status"] == "ready"


def test_update_keeps_expiry_by_default_and_can_clear(tmp_path):
    store, service, root = _make(tmp_path)
    rec = service.create(root, expires_at=10**13)
    sid = rec["share_id"]
    # bare update keeps expiry
    assert service.update(sid)["expires_at"] == 10**13
    # explicit clear
    assert service.update(sid, expires_at=None)["expires_at"] is None


def test_restore_revokes_expired(tmp_path):
    store, service, root = _make(tmp_path)
    rec = service.create(root, expires_at=1)  # in the past
    sid = rec["share_id"]
    desired = service.restore()
    assert sid not in desired
    assert store.get_share(sid)["status"] == "revoked"


def test_parse_duration():
    assert _parse_duration("30m") == 1800
    assert _parse_duration("24h") == 86400
    assert _parse_duration("7d") == 604800
    assert _parse_duration("1w") == 604800
    assert _parse_duration("3600") == 3600
